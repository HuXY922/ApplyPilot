"""Offline regressions for audited admission, eligibility, health and image gaps.

All profiles and job responses are fictional; transports never reach the network.
The packaging test reproduces Docker COPY contents, not a Docker build/runtime.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import Mock

from db import ROOT, connect, initialize
from production_app import create_application
from radar_gate import hard_requirements
from radar_recommendations import fixture as console_fixture, safe_url
from radar_worker.matching import match
from radar_worker.run import execute
from radar_worker.sources import verify
from state_api import APIError, StateAPI


URL='https://jobs.bytedance.com/campus/position/detail/7667889560824154373'
PROFILE={'profile_id':'fictional-regression-candidate',
         'education':[{'degree_level':'硕士','major':'英语语言文学',
                       'end_month':'2027-07','end_status':'expected'}],
         'experience':[{'description':'设计 Agent 产品原型、评测并分析用户反馈。'}],
         'skills':{'data_tools':['SQL']},
         'job_preferences':{'resume_target':{'value':'AI 产品经理'}}}


def official_job(profile):
    """Mock the documented adapter contract; this is not a real job claim."""
    raw=json.dumps({'code':0,'data':{'job_post_detail':{
        'id':URL.rsplit('/',1)[-1],'title':'AI 产品经理（虚构回归测试）',
        'description':'虚构测试：负责 Agent 产品原型、模型评测与数据分析。',
        'requirement':'2027届本科及以上学历。专业不限。',
        'channel_online_status':1,'recruit_type':{'name':'正式','parent':{'name':'校招'}},
        'city_list':[{'name':'广州'}]}}}).encode()
    transport=Mock();transport.get.return_value=raw
    return match(verify({'url':URL},transport),profile)


class AmbiguousMajorRegressionTests(unittest.TestCase):
    def gate(self,text,profile=PROFILE):
        return hard_requirements({'title':'AI 产品经理','description':text},profile)

    def test_audited_implicit_major_restriction_defers(self):
        result=self.gate('正式校招。2027届计算机科学与技术专业，本科及以上学历。')
        self.assertEqual(result['status'],'defer')
        self.assertIn('implicit_major_requirement_not_reliably_structured',result['reasons'])

    def test_matching_major_still_requires_interpretation_of_implicit_restriction(self):
        profile=copy.deepcopy(PROFILE);profile['education'][0]['major']='计算机科学与技术'
        self.assertEqual(self.gate('正式校招。2027届计算机科学与技术专业，本科及以上学历。',profile)['status'],'defer')

    def test_implicit_major_cannot_be_rescued_by_scoring(self):
        job={'title':'AI 产品经理','city':'广州','description':'正式校招。2027届计算机科学与技术专业，本科及以上学历。'}
        with self.assertRaisesRegex(ValueError,'^defer:'):
            match(job,PROFILE)

    def test_explicit_unrestricted_preferred_and_competence_remain_pass(self):
        for suffix in ('专业不限。','计算机专业优先。','具备专业能力。'):
            with self.subTest(suffix=suffix):
                self.assertEqual(self.gate('正式校招。2027届本科及以上学历。'+suffix)['status'],'pass')

    def test_preferred_or_unrestricted_clause_does_not_hide_another_restriction(self):
        for suffix in ('计算机专业优先，数学专业。','专业不限。2027届计算机科学与技术专业。',
                       'Bachelor degree in computer science.'):
            with self.subTest(suffix=suffix):
                self.assertEqual(self.gate('正式校招。2027届本科及以上学历。'+suffix)['status'],'defer')


class LiveAdmissionRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'career.sqlite3'
        profile_path=Path(self.temp.name)/'profile.json';profile_path.write_text(json.dumps(PROFILE))
        initialize(self.path,profile_path)
        self.api=StateAPI(self.path,'u'*32,'w'*32,'r'*32,browser_origin='https://career.test')
        self.batch={'candidate_id':PROFILE['profile_id'],'campaign_id':'autumn-2026-'+PROFILE['profile_id'],
                    'radar_date':'2026-10-09','mode':'live','jobs':[official_job(PROFILE)]}

    def tearDown(self):
        self.temp.cleanup()

    def start(self):
        body={k:self.batch[k] for k in ('candidate_id','campaign_id','radar_date')}
        self.api.route('POST','/v1/radar/runs/start',{},dict(body,id='regression-run'),'radar')
        self.batch['run_id']='regression-run'

    def ingest(self,batch=None):
        return self.api.route('POST','/v1/radar/batch',{},batch or self.batch,'radar')

    def assert_empty(self):
        with connect(self.path) as db:
            for table in ('jobs','applications','job_recommendations','radar_observations'):
                self.assertEqual(db.execute('SELECT count(*) FROM '+table).fetchone()[0],0)

    def test_no_run_cannot_import_live_even_with_verified_looking_evidence(self):
        for omit_mode in (False,True):
            body=copy.deepcopy(self.batch)
            if omit_mode:body.pop('mode')
            with self.subTest(omit_mode=omit_mode),self.assertRaisesRegex(APIError,'active scoped run'):
                self.ingest(body)
        self.assert_empty()

    def test_original_unverified_http_bypass_is_closed(self):
        body=copy.deepcopy(self.batch);body['jobs'][0].pop('verification')
        body['jobs'][0]['canonical_url']='http://unregistered.invalid/job/fictional'
        with self.assertRaises(APIError):self.ingest(body)
        self.assert_empty()

    def test_only_explicit_simulation_admits_synthetic_unverified_jobs(self):
        body=copy.deepcopy(self.batch);body['mode']='simulation'
        body['jobs'][0].pop('verification');body['jobs'][0]['canonical_url']='https://example.invalid/job/fictional'
        result=self.ingest(body)
        self.assertEqual(result['created_applications'],1)
        self.assertEqual(result['items'][0]['application']['mode'],'simulation')
        self.assertEqual(result['items'][0]['application']['status'],'pending_review')
        live=self.api.route('GET','/v1/dashboard',{'mode':['live']},{},'user')
        self.assertEqual(live['today'],[])

    def test_timestamp_hash_alone_are_not_source_evidence(self):
        self.start();self.batch['jobs'][0]['verification'].pop('source_evidence')
        with self.assertRaisesRegex(APIError,'Official source verification evidence'):self.ingest()
        self.assert_empty()

    def test_verified_official_job_preserves_evidence_and_recomputes_score(self):
        self.start();expected=self.batch['jobs'][0]['match_score']
        self.batch['jobs'][0]['match_score']=100
        result=self.ingest();self.assertEqual(result['created_applications'],1)
        self.assertEqual(result['items'][0]['application']['status'],'pending_review')
        with connect(self.path) as db:
            self.assertEqual(db.execute('SELECT match_score FROM job_recommendations').fetchone()[0],expected)
            evidence=json.loads(db.execute('SELECT evidence_json FROM radar_decisions').fetchone()[0])
            self.assertEqual(evidence['source']['canonical_url'],URL)
            self.assertIn('official_evidence',evidence['source'])

    def test_unregistered_host_and_http_alias_rejected_before_writes(self):
        self.start()
        for url in ('https://unregistered.invalid/job/1',URL.replace('https:','http:')):
            body=copy.deepcopy(self.batch);item=body['jobs'][0];item['canonical_url']=url
            source=item['verification']['source_evidence'];source['canonical_url']=url
            for key in ('official_evidence','campus_evidence'):source[key]['url']=url
            with self.subTest(url=url),self.assertRaises(APIError):self.ingest(body)
            self.assert_empty()

    def test_valid_source_cannot_admit_substituted_job_or_timestamp(self):
        self.start()
        for field in ('title','city','description','canonical_url','company_name','verified_at'):
            body=copy.deepcopy(self.batch)
            if field=='verified_at':
                stamp=datetime.fromisoformat(body['jobs'][0]['verification'][field].replace('Z','+00:00'))-timedelta(seconds=1)
                body['jobs'][0]['verification'][field]=stamp.isoformat()
            else:body['jobs'][0][field]+='-different'
            with self.subTest(field=field),self.assertRaises(APIError):self.ingest(body)
            self.assert_empty()

    def test_missing_job_specific_or_open_evidence_rejects(self):
        self.start()
        for mutate in (
            lambda source:source.update(availability='closed'),
            lambda source:source['official_evidence'].update(url=URL+'2'),
            lambda source:source['official_evidence'].update(excerpt='AI 产品经理，招聘中'),
            lambda source:source['campus_evidence'].update(excerpt='2027届网站公告'),
        ):
            body=copy.deepcopy(self.batch);mutate(body['jobs'][0]['verification']['source_evidence'])
            with self.assertRaises(APIError):self.ingest(body)
            self.assert_empty()

    def test_live_batch_cannot_select_console_test_fixture_exception(self):
        self.start();body=copy.deepcopy(self.batch);source=console_fixture('https://career.test')
        item=body['jobs'][0]
        item.update(company_name=source['company'],title=source['title'],city=source['city'],canonical_url=source['canonical_url'],
                    description=source['jd_responsibilities']+'\n任职要求：\n'+source['jd_requirements'])
        item['verification'].update(verified_at=source['acquired_at'],source_evidence=source)
        with self.assertRaisesRegex(APIError,'TEST ONLY'):self.ingest(body)
        self.assert_empty()

    def test_invalid_second_job_rolls_back_entire_live_batch(self):
        self.start();body=copy.deepcopy(self.batch)
        bad=copy.deepcopy(body['jobs'][0]);bad['verification'].pop('source_evidence');body['jobs'].append(bad)
        with self.assertRaises(APIError):self.ingest(body)
        self.assert_empty()
        with connect(self.path) as db:self.assertEqual(db.execute('SELECT count(*) FROM radar_decisions').fetchone()[0],0)

    def test_verification_report_does_not_embed_full_source_jd(self):
        raw=json.dumps({'code':0,'data':{'job_post_detail':{
            'id':URL.rsplit('/',1)[-1],'title':'AI 产品经理',
            'description':'UNIQUE_FULL_JD: 负责产品原型与用户反馈。',
            'requirement':'2027届本科及以上学历。专业不限。','channel_online_status':1,
            'recruit_type':{'name':'正式','parent':{'name':'校招'}},'city_list':[{'name':'广州'}]}}}).encode()
        http=Mock();http.get.return_value=raw
        report=execute(PROFILE,self.batch['campaign_id'],http,pause=lambda _:None,discover_fn=lambda *a,**k:([{'url':URL}],[],0))
        self.assertEqual(report['verified_count'],1)
        self.assertNotIn('UNIQUE_FULL_JD',json.dumps(report))
        self.assertEqual(report['candidates'][0]['verification']['evidence_hash'],hashlib.sha256(raw).hexdigest())


class SafeSourceURLRegressionTests(unittest.TestCase):
    def test_alias_normalization_does_not_hide_http_or_secret_query(self):
        for url in (URL.replace('https:','http:'),URL+'?token=do-not-store',URL+'?email=fictional@example.invalid'):
            with self.subTest(url=url),self.assertRaises(APIError):safe_url(url)
        self.assertEqual(safe_url(URL.replace('/position/','/m/position/')+'?referral=public-alias'),URL)


class HealthSchemaRegressionTests(unittest.TestCase):
    def test_each_required_table_missing_is_unavailable_without_repair(self):
        tables=('schema_migrations','candidates','campaigns','companies','jobs','job_sources',
                'applications','allowed_transitions','application_events','user_tasks',
                'candidate_answers','submission_attempts','inbox_messages','job_recommendations',
                'radar_runs','radar_observations','radar_decisions','radar_sessions')
        with tempfile.TemporaryDirectory() as tmp:
            for table in tables:
                with self.subTest(table=table):
                    path=Path(tmp)/(table+'.sqlite3')
                    env={'DATABASE_PATH':str(path),'PUBLIC_ORIGIN':'https://career.test','PORT':'8080',
                         'RECRUITMENT_USER_TOKEN':'u'*32,'RECRUITMENT_WORKER_TOKEN':'w'*32,'RECRUITMENT_RADAR_TOKEN':'r'*32}
                    app=create_application(env)
                    with sqlite3.connect(path) as db:
                        db.execute('DROP TABLE '+table)
                        self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],4)
                    before=path.read_bytes();statuses=[]
                    body=b''.join(app({'HTTP_HOST':'career.test','PATH_INFO':'/healthz','REQUEST_METHOD':'GET'},lambda s,h:statuses.append(s)))
                    self.assertTrue(statuses[0].startswith('503'))
                    self.assertEqual(body,b'{"status":"unavailable"}')
                    self.assertEqual(path.read_bytes(),before)
                    with sqlite3.connect(path) as db:
                        self.assertIsNone(db.execute('SELECT name FROM sqlite_master WHERE type="table" AND name=?',(table,)).fetchone())


class RadarImageLayoutRegressionTests(unittest.TestCase):
    @staticmethod
    def copy_image_payload(dockerfile,root):
        for line in (ROOT/dockerfile).read_text().splitlines():
            if not line.startswith('COPY '):continue
            fields=shlex.split(line)[1:];sources,destination=fields[:-1],fields[-1]
            for name in sources:
                source=ROOT/name;target=root/destination
                if source.is_dir():
                    shutil.copytree(source,target,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
                else:
                    target.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target/source.name)

    def test_declared_docker_copy_layout_executes_matching_without_web_dependencies(self):
        # No checkout paths or ambient PYTHONPATH can rescue a missing COPY.
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            self.copy_image_payload('Dockerfile.radar',root)
            script="""
import json, pathlib, sys
sys.path.insert(0,str(pathlib.Path.cwd()))
from radar_worker import run
from radar_worker.matching import match
profile=json.loads(sys.argv[1])
job={'title':'AI 产品经理','city':'广州','description':'正式校招。2027届本科及以上学历。专业不限。负责 Agent 产品原型与用户反馈。'}
assert 0 <= match(job,profile)['match_score'] <= 100
assert 'state_api' not in sys.modules and 'db' not in sys.modules
assert 'radar_recommendations' not in sys.modules
print('radar-layout-ok')
"""
            env={k:v for k,v in os.environ.items() if k not in ('PYTHONPATH','PYTHONHOME')}
            process=subprocess.run([sys.executable,'-I','-B','-c',script,json.dumps(PROFILE)],cwd=root,env=env,
                                   stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=20)
            self.assertEqual(process.returncode,0,process.stderr)
            self.assertEqual(process.stdout.strip(),'radar-layout-ok')

    def test_web_image_copy_layout_includes_shared_policy_dependency(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.copy_image_payload('Dockerfile',root)
            script="""
import pathlib, sys
sys.path.insert(0,str(pathlib.Path.cwd()))
from production_app import create_application
from radar_recommendations import policy
assert policy()['official_job_hosts']
app=create_application({'DATABASE_PATH':str(pathlib.Path.cwd()/'test.sqlite3'),
    'PUBLIC_ORIGIN':'https://career.test','PORT':'8080',
    'RECRUITMENT_USER_TOKEN':'u'*32,'RECRUITMENT_WORKER_TOKEN':'w'*32,'RECRUITMENT_RADAR_TOKEN':'r'*32})
statuses=[]
body=b''.join(app({'HTTP_HOST':'career.test','PATH_INFO':'/healthz','REQUEST_METHOD':'GET'},lambda s,h:statuses.append(s)))
assert statuses==['200 OK'] and body==b'{"status":"ok"}'
print('web-layout-ok')
"""
            env={k:v for k,v in os.environ.items() if k not in ('PYTHONPATH','PYTHONHOME')}
            process=subprocess.run([sys.executable,'-I','-B','-c',script],cwd=root,env=env,
                                   stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=20)
            self.assertEqual(process.returncode,0,process.stderr)
            self.assertEqual(process.stdout.strip(),'web-layout-ok')
