import copy
import io
import json
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import Mock

from db import ROOT, initialize, connect
from state_api import StateAPI, APIError
from radar_worker.http import Transport, FetchError, NoRedirect
from radar_worker.sources import normalize_url, verify, discover
from radar_worker.matching import match
from radar_worker.run import execute, config, CareerAPI
from radar_runs import matching_profile

ID='7667889560824154373'
URL='https://jobs.bytedance.com/campus/position/detail/'+ID

def fixture(id=ID,title='AI 产品经理'):
    return {'code':0,'data':{'job_post_detail':{'id':id,'title':title,'description':'负责 Agent 智能体产品交互迭代与模型评测、用户反馈、数据分析',
        'requirement':'2027届本科及以上学历，欢迎相关产品经验。','channel_online_status':1,
        'recruit_type':{'name':'正式','parent':{'name':'校招'}},'city_list':[{'name':'广州'}]}}}

def http_for(data=None):
    h=Mock();h.get.return_value=json.dumps(data or fixture()).encode();return h

class PipelineTests(unittest.TestCase):
    def setUp(self):self.profile=json.loads((ROOT/'candidate-profile.json').read_text())
    def test_official_identity_and_tracking_normalization(self):
        self.assertEqual(normalize_url('https://jobs.bytedance.com/campus/m/position/detail/'+ID+'?referral=abc#x'),URL)
        for url in ('http://jobs.bytedance.com/campus/position/detail/'+ID,URL.replace('jobs.bytedance.com','jobs.bytedance.com.evil.test'),'https://jobs.bytedance.com/campus/position?keyword=AI','https://127.0.0.1/a'):
            with self.assertRaises(FetchError):normalize_url(url)
    def test_discovery_does_not_validate_snippets(self):
        h=Mock();h.json.return_value={'code':0,'data':{'job_post_detail':{'id':ID,'title':'2027 人才岗位'},'recommend_job_post_List':[]}}
        refs,errors,aliases=discover(h,pause=lambda _:None,source_config={'seed_urls':[URL],'max_discovery_requests':1,'max_verified_references':20})
        self.assertEqual(len(refs),1);self.assertNotIn('description',refs[0]);h.get.assert_not_called()
    def test_closed_intern_wrong_year_and_identity_rejected(self):
        for field,value in [('channel_online_status',0),('channel_online_status',True),('requirement','2026届毕业'),('id','123'),('title','AI 产品经理实习生'),('recruit_type',{'name':'社招'})]:
            data=fixture();data['data']['job_post_detail'][field]=value
            with self.subTest(field=field),self.assertRaises(FetchError):verify({'url':URL},http_for(data))
    def test_site_global_year_cannot_validate_job(self):
        data=fixture();data['data']['job_post_detail']['requirement']='本科毕业';data['banner']='2027届校招'
        with self.assertRaisesRegex(FetchError,'cohort'):verify({'url':URL},http_for(data))
    def test_expiry_and_missing_detail_fail_closed(self):
        data=fixture();data['data']['job_post_detail']['job_post_info']={'expiry_time':1}
        with self.assertRaisesRegex(FetchError,'expired'):verify({'url':URL},http_for(data))
        with self.assertRaises(FetchError):verify({'url':URL},http_for({'code':0,'data':{}}))
    def test_out_of_scope_rejected(self):
        for title in ('Java 工程师','社区运营','策略运营'):
            data=fixture(title=title);data['data']['job_post_detail']['description']='负责活动策划'
            with self.assertRaises(FetchError):verify({'url':URL},http_for(data))
    def test_matching_reads_profile_not_company_reputation(self):
        job=verify({'url':URL},http_for());a=match(job,self.profile)
        b=match({**job,'company_name':'未知小公司'},self.profile)
        self.assertEqual(a['match_score'],b['match_score'])
        blank={**self.profile,'experience':[],'projects':[],'skills':{}}
        self.assertLess(match(job,blank)['match_score'],a['match_score'])
    def test_guangzhou_shenzhen_same_priority(self):
        job=verify({'url':URL},http_for())
        self.assertEqual(match(job,self.profile)['match_score'],match({**job,'city':'深圳'},self.profile)['match_score'])
    def test_dry_run_never_calls_production_even_for_valid_job(self):
        api=Mock()
        report=execute(self.profile,'c',http_for(),api=api,pause=lambda _:None,discover_fn=lambda *a,**k:([{'url':URL}],[],0))
        api.call.assert_not_called();self.assertEqual(report['verified_count'],1);self.assertEqual(report['imported_count'],0)
        self.assertNotIn('description',report['candidates'][0]);self.assertNotIn('identity',json.dumps(report))
    def test_per_job_failure_isolated(self):
        h=http_for();h.get.side_effect=[FetchError('http_503'),json.dumps(fixture()).encode()]
        report=execute(self.profile,'c',h,pause=lambda _:None,discover_fn=lambda *a,**k:([{'url':URL},{'url':URL}],[],0))
        self.assertEqual(report['verified_count'],1);self.assertEqual(report['failure_count'],1);self.assertEqual(report['rejected_count'],1)
    def test_zero_results_is_valid_no_invention(self):
        r=execute(self.profile,'c',Mock(),pause=lambda _:None,discover_fn=lambda *a,**k:([],[],0))
        self.assertEqual(r['candidates'],[]);self.assertEqual(r['verified_count'],0)
    def test_http_denies_redirect_and_foreign_destinations(self):
        h=Transport(['jobs.bytedance.com']);h.opener=Mock()
        for url in ('http://jobs.bytedance.com/a','https://evil.test/a','https://u:p@jobs.bytedance.com/a','https://jobs.bytedance.com:123/a'):
            with self.assertRaises(FetchError):h.get(url)
        h.opener.open.assert_not_called();self.assertIsNone(NoRedirect().redirect_request(None,None,302,None,None,'https://evil.test'))
    def test_network_errors_redact_tokens(self):
        h=Transport(['career.test'],'r'*48);h.opener=Mock();h.opener.open.side_effect=RuntimeError('r'*48)
        with self.assertRaises(FetchError) as e:h.get('https://career.test/v1/radar/profile',authenticated=True)
        self.assertNotIn('r'*48,str(e.exception))
    def test_worker_config_requires_only_radar_secret(self):
        env={'CAREER_DESK_ORIGIN':'https://career.test','RADAR_CAMPAIGN_ID':'c','RECRUITMENT_RADAR_TOKEN':'r'*48}
        self.assertFalse(config(env)[2])
        for key in env:
            bad=env.copy();bad.pop(key)
            with self.assertRaises(ValueError):config(bad)
        for extra in ({'RECRUITMENT_USER_TOKEN':'u'*48},{'RECRUITMENT_WORKER_TOKEN':'w'*48},{'RADAR_LIVE_IMPORT':'yes'},{'CAREER_DESK_ORIGIN':'http://career.test'}):
            with self.assertRaises(ValueError):config({**env,**extra})
    def test_client_has_no_approval_or_transition_routes(self):
        api=CareerAPI('https://career.test','r'*48);api.http=Mock()
        for path in ('/v1/applications/a/approve','/v1/applications/a/transition','/v1/candidates'):
            with self.assertRaises(FetchError):api.call(path,{})
        api.http.json.assert_not_called()
    def test_profile_projection_omits_identity_and_answers(self):
        projected=matching_profile(self.profile)
        for field in ('identity','application_answers','resume_assets','sources'):self.assertNotIn(field,projected)
        self.assertTrue(projected['experience'][0]['workstreams'])

class RunAPITests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.db=Path(self.temp.name)/'career.sqlite3';initialize(self.db)
        self.api=StateAPI(self.db,'u'*32,'w'*32,'r'*32)
        self.batch=json.loads((ROOT/'radar-batch.json').read_text())
        # Run-accounting tests use freshly verified mocked official detail;
        # the bundled fictional batch intentionally remains simulation-only.
        profile=json.loads((ROOT/'candidate-profile.json').read_text())
        self.batch['mode']='live';self.batch['jobs']=[match(verify({'url':URL},http_for()),profile)]
        self.run={k:self.batch[k] for k in ('candidate_id','campaign_id','radar_date')};self.run['id']='run-1'
    def tearDown(self):self.temp.cleanup()
    def call(self,path,body=None,role='radar',method='POST',query=None):return self.api.route(method,path,query or {},body or {},role)
    def start(self):return self.call('/v1/radar/runs/start',self.run)
    def batch_with_run(self):
        batch=copy.deepcopy(self.batch);batch['run_id']=self.run['id'];return batch
    def finish(self,verified=1):return self.call('/v1/radar/runs/finish',{'id':'run-1','discovered_count':verified,'verified_count':verified,'rejected_count':0,'failure_count':0})
    def test_run_import_accounting_idempotent_and_refresh_preserves_approve(self):
        self.start();b=self.batch_with_run();app=self.call('/v1/radar/batch',b)['items'][0]['application']
        self.call('/v1/applications/'+app['id']+'/approve',{'expected_revision':1},'user')
        b['jobs'][0]['match_score']=42;again=self.call('/v1/radar/batch',b)
        self.assertEqual(again['created_applications'],0);self.assertEqual(again['items'][0]['application']['status'],'approved')
        run=self.finish();self.assertEqual(run['imported_count'],1);self.assertEqual(run['duplicate_count'],0)
        with connect(self.db) as db:self.assertEqual(db.execute('SELECT match_score FROM job_recommendations').fetchone()[0],match(b['jobs'][0],json.loads((ROOT/'candidate-profile.json').read_text()))['match_score'])
        self.assertEqual(self.finish(),run)
    def test_new_run_duplicate_preserves_skip(self):
        previous={**self.run,'id':'run-previous','radar_date':'2026-10-08'}
        self.call('/v1/radar/runs/start',previous)
        batch={**self.batch,'run_id':previous['id'],'radar_date':previous['radar_date']}
        app=self.call('/v1/radar/batch',batch)['items'][0]['application']
        self.call('/v1/applications/'+app['id']+'/transition',{'expected_revision':1,'status':'skipped'},'user')
        self.call('/v1/radar/runs/finish',{'id':previous['id'],'discovered_count':1,'verified_count':1,'rejected_count':0,'failure_count':0})
        self.start();self.call('/v1/radar/batch',self.batch_with_run());run=self.finish()
        self.assertEqual(run['imported_count'],0);self.assertEqual(run['duplicate_count'],1)
        with connect(self.db) as db:self.assertEqual(db.execute('SELECT status FROM applications').fetchone()[0],'skipped')
    def test_permissions_radar_still_cannot_approve_skip_submit(self):
        self.start();app=self.call('/v1/radar/batch',self.batch_with_run())['items'][0]['application']
        for role in ('radar','worker'):
            for path in ('approve','transition','submit'):
                with self.assertRaises(APIError):self.call('/v1/applications/'+app['id']+'/'+path,{'expected_revision':1,'status':'skipped'},role)
        for path in ('/v1/radar/runs/start','/v1/radar/runs/finish','/v1/radar/profile'):
            with self.assertRaises(APIError):self.call(path,self.run,'worker')
    def test_profile_narrow_read_uses_current_db(self):
        with connect(self.db) as db:
            profile=json.loads(db.execute('SELECT profile_json FROM candidates').fetchone()[0]);profile['skills']={'data_tools':['changed-in-database']};db.execute('UPDATE candidates SET profile_json=?',(json.dumps(profile),))
        got=self.call('/v1/radar/profile',method='GET',query={'campaign_id':[self.run['campaign_id']]})
        self.assertEqual(got['profile']['skills']['data_tools'],['changed-in-database']);self.assertNotIn('identity',got['profile'])
    def test_stale_evidence_and_finished_run_rejected(self):
        self.start();b=self.batch_with_run();b['jobs'][0]['verification']['verified_at']=(datetime.now(timezone.utc)-timedelta(hours=2)).isoformat()
        with self.assertRaises(APIError):self.call('/v1/radar/batch',b)
        self.finish(0)
        with self.assertRaises(APIError):self.call('/v1/radar/batch',self.batch_with_run())
    def test_run_scope_and_metrics_cannot_be_forged(self):
        self.start();b=self.batch_with_run();b['mode']='simulation'
        with self.assertRaises(APIError):self.call('/v1/radar/batch',b)
        bad={**self.run,'id':'another'}
        with self.assertRaises(APIError):self.call('/v1/radar/runs/start',bad)
        with self.assertRaises(APIError):self.call('/v1/radar/runs/finish',{'id':'run-1','discovered_count':0,'verified_count':1,'rejected_count':0,'failure_count':0})
    def test_migration_repeated_keeps_approval_profile_and_run(self):
        self.start();app=self.call('/v1/radar/batch',self.batch_with_run())['items'][0]['application']
        self.call('/v1/applications/'+app['id']+'/approve',{'expected_revision':1},'user');self.finish()
        with connect(self.db) as db:before=db.execute('SELECT profile_json FROM candidates').fetchone()[0]
        initialize(self.db);initialize(self.db)
        with connect(self.db) as db:
            self.assertEqual(db.execute('SELECT profile_json FROM candidates').fetchone()[0],before)
            self.assertEqual(db.execute('SELECT status FROM applications').fetchone()[0],'approved')
            self.assertEqual(db.execute('SELECT imported_count FROM radar_runs').fetchone()[0],1)
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])
    def test_dashboard_run_visible_only_user(self):
        self.start();self.finish(0)
        dash=self.call('/v1/dashboard',method='GET',role='user');self.assertEqual(dash['radar_run']['status'],'completed')
        for role in ('radar','worker'):
            with self.assertRaises(APIError):self.call('/v1/dashboard',method='GET',role=role)
    def test_official_tracking_alias_reuses_job(self):
        self.start();b=self.batch_with_run();b['jobs'][0]['canonical_url']='https://jobs.bytedance.com/campus/m/position/detail/'+ID+'?referral=abc'
        a=self.call('/v1/radar/batch',b)['items'][0]['application']
        b['jobs'][0]['canonical_url']=URL
        self.assertEqual(self.call('/v1/radar/batch',b)['items'][0]['application']['id'],a['id'])

class DiscoveryGraphTests(unittest.TestCase):
    def test_dynamic_recommendations_are_discovered_then_independently_verified(self):
        h=Mock();new_id='7669645620761020725'
        h.json.return_value={'code':0,'data':{'job_post_detail':fixture()['data']['job_post_detail'],
             'recommend_job_post_List':[{'id':new_id,'title':'新发现，不可信摘要'}]}}
        refs,failures,aliases=discover(h,pause=lambda _:None,source_config={'seed_urls':[URL],'max_discovery_requests':1,'max_verified_references':20})
        self.assertEqual(len(refs),2);self.assertTrue(any(new_id in r['url'] for r in refs));h.get.assert_not_called()
        h.get.return_value=json.dumps(fixture(new_id)).encode()
        verify(refs[1],h);self.assertIn('with_recommend=false',h.get.call_args.args[0])
    def test_unreachable_seeds_do_not_turn_into_jobs(self):
        h=Mock();h.json.side_effect=FetchError('http_404')
        refs,errors,_=discover(h,pause=lambda _:None,source_config={'seed_urls':[URL],'max_discovery_requests':1,'max_verified_references':20})
        self.assertEqual(refs,[]);self.assertEqual(len(errors),1)
    def test_malicious_recommendation_urls_never_followed(self):
        h=Mock();h.json.return_value={'code':0,'data':{'job_post_detail':None,'recommend_job_post_List':[{'id':'https://127.0.0.1/secret','url':'https://evil.test'}]}}
        refs,_,_=discover(h,pause=lambda _:None,source_config={'seed_urls':[URL],'max_discovery_requests':1,'max_verified_references':20})
        self.assertEqual(refs,[])
    def test_source_schema_change_fails_closed(self):
        h=Mock();h.json.return_value={'code':0,'data':{'job_post_detail':fixture()['data']['job_post_detail']}}
        refs,errors,_=discover(h,pause=lambda _:None,source_config={'seed_urls':[URL],'max_discovery_requests':1,'max_verified_references':20})
        self.assertEqual(refs,[]);self.assertEqual(len(errors),1)

class LiveLocalIntegrationTests(unittest.TestCase):
    setUp=RunAPITests.setUp
    tearDown=RunAPITests.tearDown
    call=RunAPITests.call
    # Explicitly local SQLite and fixture HTTP; this is NOT production live acceptance.
    def test_pipeline_partial_import_does_not_block_next_job(self):
        profile=json.loads((ROOT/'candidate-profile.json').read_text());outer=self
        class LocalAPI:
            calls=0
            def call(self,path,body=None):
                if path=='/v1/radar/batch':
                    self.calls+=1
                    if self.calls==1:raise FetchError('http_503')
                return outer.call(path,body)
        h=Mock();h.get.side_effect=[json.dumps(fixture()).encode(),json.dumps(fixture('7669645620761020725')).encode()]
        refs=[{'url':URL},{'url':URL.replace(ID,'7669645620761020725')}]
        r=execute(profile,self.run['campaign_id'],h,api=LocalAPI(),live=True,pause=lambda _:None,discover_fn=lambda *a,**k:(refs,[],0))
        self.assertEqual(r['verified_count'],2);self.assertEqual(r['imported_count'],1);self.assertEqual(r['failure_count'],1)
        self.assertEqual(r['server_run']['status'],'partial')
        with connect(self.db) as db:self.assertEqual(db.execute('SELECT status FROM applications').fetchone()[0],'pending_review')
    def test_actual_v2_upgrade_preserves_existing_decision(self):
        # A synthetic legacy decision needs no run; live imports now always do.
        legacy=json.loads((ROOT/'radar-batch.json').read_text())
        legacy['jobs']=legacy['jobs'][:1]
        app=self.call('/v1/radar/batch',legacy)['items'][0]['application']
        self.call('/v1/applications/'+app['id']+'/approve',{'expected_revision':1},'user')
        with connect(self.db) as db:
            before=db.execute('SELECT profile_json FROM candidates').fetchone()[0]
            db.execute('DROP TABLE radar_observations');db.execute('DROP TABLE radar_runs')
            db.execute('DELETE FROM schema_migrations WHERE version=3');db.execute('PRAGMA user_version=2')
        initialize(self.db);initialize(self.db)
        with connect(self.db) as db:
            self.assertEqual(db.execute('SELECT status FROM applications').fetchone()[0],'approved')
            self.assertEqual(db.execute('SELECT profile_json FROM candidates').fetchone()[0],before)
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],4)
