import copy
import io
import json
import re
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlencode
from unittest.mock import patch
from db import ROOT,connect,initialize
from production_app import create_application
from radar_gate import hard_requirements
from radar_privacy import matching_profile
from radar_recommendations import fixture
from state_api import APIError

PROFILE={'profile_id':'test-candidate','education':[{'degree_level':'硕士','major':'英语语言文学','end_month':'2027-07','end_status':'expected'}],
 'experience':[{'workstreams':[{'description':'负责 Agent 产品迭代、用户反馈与数据分析'}]}],'skills':{'data_tools':['SQL']},
 'job_preferences':{'resume_target':{'value':'AI 产品经理'}}}
BASE={'title':'AI 产品经理','description':'正式校招。2027届本科及以上学历。专业不限。','city':'广州'}

class HardGateTests(unittest.TestCase):
    def gate(self,text=None,profile=None,title=None):return hard_requirements({**BASE,**({'description':text} if text else {}),**({'title':title} if title else {})},profile or PROFILE)
    def test_phd_mandatory_cannot_be_positive(self):
        from radar_worker.matching import match
        for suffix in ('必须博士','博士学历','本科及以上学历，必须博士'):
            j={**BASE,'description':'正式校招。2027届。'+suffix}
            self.assertEqual(hard_requirements(j,PROFILE)['status'],'hard_fail')
            with self.assertRaisesRegex(ValueError,'hard_fail'):match(j,PROFILE)
    def test_degree_preferred_is_not_mandatory(self):self.assertEqual(self.gate(BASE['description']+'博士优先。')['status'],'pass')
    def test_degree_minimum_unknown_defers(self):self.assertEqual(self.gate('正式校招。2027届。')['status'],'defer')
    def test_critical_profile_unknown_defers(self):
        p=copy.deepcopy(PROFILE);p['education'][0]['end_month']=None
        self.assertEqual(self.gate(profile=p)['status'],'defer')
    def test_wrong_cohort_rejected(self):self.assertEqual(self.gate('正式校招。2026届本科及以上学历。')['status'],'hard_fail')
    def test_window_excludes_candidate(self):self.assertEqual(self.gate(BASE['description']+'毕业时间2026-09至2027-06。')['status'],'hard_fail')
    def test_window_includes_candidate(self):self.assertEqual(self.gate(BASE['description']+'毕业时间2026年9月至2027年8月。')['status'],'pass')
    def test_partial_month_window_defers(self):self.assertEqual(self.gate(BASE['description']+'毕业时间2027-07-15至2027-08-20。')['status'],'defer')
    def test_explicit_major_mismatch_rejected(self):
        for condition in ('仅限计算机科学与技术专业。','专业必须为计算机科学与技术。'):
            self.assertEqual(self.gate(BASE['description']+condition)['status'],'hard_fail')
    def test_explicit_major_match_allowed(self):self.assertEqual(self.gate(BASE['description']+'仅限英语语言文学专业。')['status'],'pass')
    def test_ambiguous_mandatory_major_defers(self):self.assertEqual(self.gate(BASE['description']+'必须相关专业。')['status'],'defer')
    def test_intern_and_social_rejected(self):
        for text in ('实习生','暑期实习','社会招聘','社招','internship'):
            self.assertEqual(self.gate(title='AI 产品经理 '+text)['status'],'hard_fail')
    def test_unknown_recruitment_type_defers(self):self.assertEqual(self.gate('2027届本科及以上学历。')['status'],'defer')
    def test_future_phd_not_counted(self):
        p=copy.deepcopy(PROFILE);p['education'].append({'degree_level':'博士','end_month':'2028-07','end_status':'expected'})
        self.assertEqual(self.gate(BASE['description']+'必须博士。',p)['status'],'hard_fail')

class RecursivePrivacyTests(unittest.TestCase):
    def test_nested_objects_arrays_unknown_fields_and_forbidden_keys(self):
        p=copy.deepcopy(PROFILE)
        keys=['name','gender','phone','email','identity','address','candidate_answers','application_answers','resume_assets','attachment','password','OTP','cookie','session','token','api_key']
        p['experience'][0]['workstreams'][0].update({key:'SECRET-'+key for key in keys})
        p['job_preferences']['resume_target']['value']=['AI 产品经理',{key:'SECRET-'+key for key in keys}]
        p['skills']['data_tools'].append({'description':'SQL','unknown':{'email':'secret@example.com'},'session':'secret'})
        output=matching_profile(p);encoded=json.dumps(output)
        for key in keys:self.assertNotIn('SECRET-'+key,encoded)
        def check(x):
            if isinstance(x,dict):
                for k,v in x.items():self.assertNotIn(k,keys);check(v)
            elif isinstance(x,list):
                for v in x:check(v)
        check(output);self.assertIn('负责 Agent',output['experience'][0]['workstreams'][0]['description'])
    def test_sensitive_text_dropped(self):
        for value in ('contact me x@example.com','电话：13812345678','identity 110101200001011234','token=secretvalue','地址：某街道','https://example.com?api_key=secret'):
            p=copy.deepcopy(PROFILE);p['experience'][0]['workstreams'][0]['description']=value
            self.assertNotIn(value,json.dumps(matching_profile(p),ensure_ascii=False))
    def test_known_identity_value_removed_from_allowed_text(self):
        p=copy.deepcopy(PROFILE);p['identity']={'name':'测试姓名甲'};p['projects']=[{'description':'测试姓名甲负责原型'}]
        self.assertNotIn('测试姓名甲',json.dumps(matching_profile(p),ensure_ascii=False))
    def test_certification_identifiers_removed(self):
        p=copy.deepcopy(PROFILE);p['certifications']=[{'qualification':'英语水平证书','level':'C1','certificate_number':'PRIVATE','name':'Person','attachment':'PRIVATE'}]
        o=matching_profile(p);self.assertEqual(o['certifications'],[{'qualification':'英语水平证书','level':'C1'}])
    def test_projection_idempotent_and_bounded(self):
        once=matching_profile(PROFILE);self.assertEqual(matching_profile(once),once)
        p=copy.deepcopy(PROFILE);nested={'description':'valid'}
        for _ in range(20):nested={'workstreams':[nested]}
        p['experience']=[nested];self.assertLess(len(json.dumps(matching_profile(p))),2000)

class ConsoleTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'career.sqlite3'
        self.env={'DATABASE_PATH':str(self.path),'PUBLIC_ORIGIN':'https://career.test','PORT':'8080','RECRUITMENT_USER_TOKEN':'u'*32,'RECRUITMENT_WORKER_TOKEN':'w'*32,'RECRUITMENT_RADAR_TOKEN':'r'*32}
        self.app=create_application(self.env);self.cookie='';self.csrf=''
    def tearDown(self):self.temp.cleanup()
    def call(self,path='/radar',method='GET',fields=None,expected=200,cookie=None,origin='https://career.test',token=None,query=''):
        raw=urlencode(fields or {}).encode();captured=[]
        env={'REQUEST_METHOD':method,'PATH_INFO':path,'QUERY_STRING':query,'HTTP_HOST':'career.test','HTTP_ORIGIN':origin,'HTTP_SEC_FETCH_MODE':'navigate','HTTP_SEC_FETCH_SITE':'same-origin','CONTENT_TYPE':'application/x-www-form-urlencoded','CONTENT_LENGTH':str(len(raw)),'wsgi.input':io.BytesIO(raw),'HTTP_COOKIE':self.cookie if cookie is None else cookie}
        if token:env['HTTP_AUTHORIZATION']='Bearer '+token
        body=b''.join(self.app(env,lambda s,h:captured.append((s,dict(h))))).decode()
        self.assertEqual(int(captured[-1][0].split()[0]),expected,body[:500])
        return body,captured[-1][1]
    def login(self):
        body,headers=self.call('/radar/login','POST',{'radar_token':'r'*32},303)
        self.cookie=headers['Set-Cookie'].split(';')[0]
        body,_=self.call();self.csrf=re.search(r'name="csrf" value="([a-f0-9]+)"',body)[1]
        return headers
    def action(self,path,payload=None,expected=200):
        fields={'csrf':self.csrf}
        if payload is not None:fields['payload']=json.dumps(payload,ensure_ascii=False)
        return self.call('/radar/'+path,'POST',fields,expected)
    def start(self):self.login();self.action('start')
    def test_only_radar_can_sign_in(self):
        for token in ('u'*32,'w'*32,'x'*32):self.call('/radar/login','POST',{'radar_token':token},401)
        self.login()
    def test_bearer_does_not_inherit_console_session(self):
        for token in ('u'*32,'w'*32,'r'*32):
            body,_=self.call(token=token);self.assertIn('Radar Console sign in',body);self.assertNotIn('Minimal matching profile',body)
    def test_secure_cookie_session_restart_expiry_tampering_and_logout(self):
        headers=self.login()
        for attr in ('Secure','HttpOnly','SameSite=Strict','Path=/radar','Max-Age=86400'):self.assertIn(attr,headers['Set-Cookie'])
        self.app=create_application(self.env);self.assertIn('Minimal matching profile',self.call()[0])
        self.assertIn('sign in',self.call(cookie=self.cookie+'x')[0])
        self.action('logout',expected=303);self.assertIn('sign in',self.call()[0])
        self.login()
        with connect(self.path) as db:db.execute('UPDATE radar_sessions SET expires_at=0')
        self.assertIn('sign in',self.call()[0])
    def test_forms_preserve_same_origin_without_cross_site_referrers(self):
        body,headers=self.call()
        self.assertEqual(headers['Referrer-Policy'],'same-origin')
        self.login()
        body,headers=self.call()
        self.assertEqual(headers['Referrer-Policy'],'same-origin')
        self.action('start')
    def test_null_origin_remains_rejected_even_with_valid_csrf(self):
        self.call('/radar/login','POST',{'radar_token':'r'*32},403,origin='null')
        self.login()
        self.call('/radar/start','POST',{'csrf':self.csrf},403,origin='null')

    def test_rotation_revokes_sessions(self):
        self.login();self.app=create_application({**self.env,'RECRUITMENT_RADAR_TOKEN':'new-'+'r'*32})
        self.assertIn('sign in',self.call()[0])
    def test_tokens_never_echo_or_persist(self):
        self.login();body,_=self.call()
        self.assertNotIn('localStorage.setItem',body);self.assertNotIn('<script',body)
        for token in ('u'*32,'w'*32,'r'*32):self.assertNotIn(token,body)
        with connect(self.path) as db:dump='\n'.join(db.iterdump())
        for token in ('u'*32,'w'*32,'r'*32):self.assertNotIn(token,dump)
    def test_query_origin_and_csrf_rejected(self):
        self.call(query='token=secret',expected=400)
        self.call('/radar/login','POST',{'radar_token':'r'*32},403,origin='https://evil.test')
        self.login();self.call('/radar/start','POST',{'csrf':'bad'},403)
        self.call('/radar/start','POST',{'csrf':self.csrf},403,origin=None)
    def test_console_privilege_escalation_server_denied(self):
        self.start()
        for action in ('approve','skip','submit','transition','attachments','contacts','application_answers','v1/applications/a/approve'):
            self.action(action,{'expected_revision':1},403)
        for path in ('/radar/contacts','/radar/profile/full','/radar/attachments'):self.call(path,expected=403)
        self.call('/v1/candidates',expected=401)
        for method,path in [('POST','/v1/jobs'),('POST','/v1/applications'),('GET','/v1/candidates'),('GET','/v1/applications/a/answer')]:
            with self.assertRaises(APIError) as e:self.app.api.route(method,path,{}, {},'radar')
            self.assertEqual(e.exception.status,403)
    def test_fixture_import_pending_duplicate_and_user_approval(self):
        self.start();job=fixture('https://career.test');body,_=self.action('recommendation',job)
        self.assertIn('pending_review',body)
        with connect(self.path) as db:
            a=dict(db.execute('SELECT * FROM applications').fetchone());self.assertEqual(a['status'],'pending_review')
            self.assertIn('TEST ONLY',db.execute('SELECT risk_flags FROM job_recommendations').fetchone()[0])
        with self.assertRaises(APIError):self.app.api.route('POST','/v1/applications/'+a['id']+'/approve',{}, {'expected_revision':1},'radar')
        approved=self.app.api.route('POST','/v1/applications/'+a['id']+'/approve',{}, {'expected_revision':1},'user');self.assertEqual(approved['status'],'approved')
        self.action('recommendation',fixture('https://career.test'));self.action('finish')
        with connect(self.path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM applications').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT status FROM applications').fetchone()[0],'approved')
            r=dict(db.execute('SELECT * FROM radar_runs').fetchone());self.assertEqual(r['imported_count'],1);self.assertEqual(r['duplicate_count'],0)
        self.action('recommendation',job,409)
    def test_client_score_and_fake_test_bypass_rejected(self):
        self.start()
        for key in ('match_score','priority','status'):
            j=fixture('https://career.test');j[key]=100;self.action('recommendation',j,400)
        j=fixture('https://career.test');j['canonical_url']='https://evil.test/a';self.action('recommendation',j,400)
    def test_missing_evidence_and_stale_time_defer_without_application(self):
        self.start();j=fixture('https://career.test');j['acquired_at']='2000-01-01T00:00:00Z'
        body,_=self.action('recommendation',j);self.assertIn('evidence_stale_or_future',body)
        with connect(self.path) as db:self.assertEqual(db.execute('SELECT count(*) FROM applications').fetchone()[0],0)
    def test_unregistered_host_defers_not_trusted_from_agent_assertion(self):
        self.start();j=fixture('https://career.test');j['test_only']=False;j['canonical_url']='https://evil.test/job/1'
        for k in ('official_evidence','campus_evidence'):j[k]['url']=j['canonical_url']
        body,_=self.action('recommendation',j);self.assertIn('official_host_not_registered',body)
    def test_current_profile_revision_and_evidence_refs(self):
        self.start();j=fixture('https://career.test');j['candidate_evidence_references']=['/skills/data_tools/0']
        self.action('recommendation',j)
        with connect(self.path) as db:
            row=db.execute('SELECT evidence_json,profile_revision FROM radar_decisions').fetchone();self.assertEqual(row[1],1);self.assertIn('evidence_matches',row[0])
        j['candidate_evidence_references']=['/identity/name'];self.action('recommendation',j,400)
        j['candidate_evidence_references']=['/skills/data_tools/999'];self.action('recommendation',j,400)
    def test_signal_not_job_and_partial_run_counts(self):
        self.start();s={'kind':'recruitment_project','url':'https://example.com/campaign','summary':'待核验'}
        self.action('signal',s);self.action('signal',s)
        with connect(self.path) as db:self.assertEqual(db.execute('SELECT count(*) FROM applications').fetchone()[0],0);self.assertEqual(db.execute('SELECT count(*) FROM radar_decisions').fetchone()[0],1)
        self.action('finish')
        with connect(self.path) as db:r=dict(db.execute('SELECT * FROM radar_runs').fetchone());self.assertEqual(r['discovered_count'],1);self.assertEqual(r['failure_count'],1)
    def test_known_secret_payload_never_stored(self):
        self.start();self.action('signal',{'kind':'unknown','url':'https://example.com/a','summary':'r'*32},400)
        with connect(self.path) as db:self.assertEqual(db.execute('SELECT count(*) FROM radar_decisions').fetchone()[0],0)
    def test_legacy_batch_gate_no_p2_bypass_and_audit_commits(self):
        b=json.loads((ROOT/'radar-batch.json').read_text());b['jobs']=b['jobs'][:1]
        for description,outcome in [('正式校招。2027届必须博士。','hard_fail'),('2027届本科及以上学历。','defer')]:
            b['jobs'][0]['description']=description;b['jobs'][0]['priority']='P2'
            result=self.app.api.route('POST','/v1/radar/batch',{},b,'radar');self.assertEqual(result['items'],[]);self.assertEqual(result['rejections'][0]['status'],outcome)
        with connect(self.path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM applications').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM radar_decisions').fetchone()[0],2)
    def test_policy_is_live_read_only(self):
        self.login()
        with patch('pathlib.Path.read_text',autospec=True,side_effect=lambda path,*a,**k:'LATEST POLICY' if path.name=='RADAR_AGENT_INSTRUCTIONS.md' else original(path,*a,**k)):
            self.assertIn('LATEST POLICY',self.call()[0])
        self.action('policy',{},403)

original=Path.read_text

class UpgradeBackupTests(unittest.TestCase):
    def test_upgrade_backup_contains_previous_state_and_is_private(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'career.sqlite3';initialize(path)
            with connect(path) as db:
                db.execute('DROP TABLE radar_decisions');db.execute('DROP TABLE radar_sessions');db.execute('DELETE FROM schema_migrations WHERE version=4');db.execute('PRAGMA user_version=3')
            env={'DATABASE_PATH':str(path),'PUBLIC_ORIGIN':'https://career.test','PORT':'8080','RECRUITMENT_USER_TOKEN':'u'*32,'RECRUITMENT_WORKER_TOKEN':'w'*32,'RECRUITMENT_RADAR_TOKEN':'r'*32}
            create_application(env);create_application(env)
            backups=list(Path(tmp).glob('*.bak'));self.assertEqual(len(backups),1);self.assertEqual(backups[0].stat().st_mode&0o777,0o600)
            with connect(backups[0]) as db:self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],3)
            with connect(path) as db:self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],4)
