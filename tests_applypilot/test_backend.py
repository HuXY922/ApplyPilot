"""Local SQLite/WSGI tests. Runtime responses are mocked; no live network claims."""
import base64
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import zipfile

from db import connect
from applypilot_core.server import create_app
from applypilot_core.service import import_text
from applypilot_core.store import DEMO_PROFILE, DomainError, SessionManager, Store, demo_jobs, now


class FakeRuntime:
    configured = False
    provider = 'openai'
    result = None
    error = False

    @classmethod
    def model_status(cls):
        return {'configured':cls.configured,'provider':cls.provider,'mode':'model' if cls.configured else 'rules','status':'test_only'}

    @classmethod
    def discover(cls, **kwargs):
        if cls.error:
            raise RuntimeError('TOP-SECRET-TOKEN must never reach a response')
        return deepcopy(cls.result or {'jobs':[],'failures':[],'source_summary':[{'source':'fixture','status':'ok'}]})

    @staticmethod
    def extract_profile(text):
        return {**deepcopy(DEMO_PROFILE),'name':'','resume_text':text}

    @staticmethod
    def analyze(profile,job):
        return {'eligibility':'unknown','hard_requirements':[],'strengths':[],'gaps':[],
                'questions':['Verify missing facts'],'mode':'rules','model_status':'test_only'}

    @staticmethod
    def suggest_resume(profile,job):
        return {'suggestions':[],'mode':'rules','model_status':'test_only'}


def live_job(id='source-1'):
    value = deepcopy(demo_jobs()[0])
    value.update(provider='fixture-source',external_id=id,id='ignored-provider-id',record_type='verified_job',
                 canonical_url='https://careers.example.com/jobs/'+id,availability='open')
    value['verification'] = {'status':'verified','source':'fixture, not live','verified_at':now(),'evidence':['Test-only verified detail']}
    return value


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = {'PUBLIC_ORIGIN':'http://127.0.0.1:8787','APPLYPILOT_DATA_DIR':self.temp.name,
                    'APPLYPILOT_SEARCHES_PER_HOUR':'100','APPLYPILOT_SESSION_SEARCHES_PER_HOUR':'100'}
        FakeRuntime.configured, FakeRuntime.result, FakeRuntime.error = False,None,False
        FakeRuntime.provider = 'openai'
        self.app = create_app(self.env,runtime=FakeRuntime)
        self.cookie = ''
        self.csrf = ''

    def tearDown(self):
        self.temp.cleanup()

    def request(self, path='/api/state', method='GET', data=None, expected=200, cookie=None, csrf=None,
                origin='http://127.0.0.1:8787',host='127.0.0.1:8787',raw=None, extra=None):
        content = raw if raw is not None else json.dumps(data or {}).encode()
        env = {'PATH_INFO':path,'REQUEST_METHOD':method,'HTTP_HOST':host,'HTTP_ORIGIN':origin,
               'HTTP_COOKIE':self.cookie if cookie is None else cookie,
               'HTTP_X_CSRF_TOKEN':self.csrf if csrf is None else csrf,
               'CONTENT_TYPE':'application/json','CONTENT_LENGTH':str(len(content)),'wsgi.input':io.BytesIO(content),
               'REMOTE_ADDR':'127.0.0.1'}
        env.update(extra or {})
        captured = []
        body = b''.join(self.app(env,lambda status,headers:captured.append((int(status.split()[0]),dict(headers)))))
        headers = captured[0][1]
        parsed = json.loads(body) if body and headers['Content-Type'].startswith('application/json') else body
        self.assertEqual(captured[0][0],expected,parsed)
        return parsed,headers

    def start(self):
        state, headers = self.request()
        self.cookie = headers['Set-Cookie'].split(';')[0]
        self.csrf = state['csrf']
        return state

    def confirm(self, state=None, name=None):
        state = state or self.request()[0]
        profile = deepcopy(state['profile'])
        if name is not None:
            profile['name'] = name
        return self.request('/api/profile','POST',{'profile':profile,'confirmed':True,'expected_version':state['profile_version']})[0]

    def track(self,jid,status,**extra):
        return self.request('/api/jobs/'+jid+'/track','POST',{'status':status,'idempotency_key':'operation-'+status,**extra})[0]

    def store(self):
        token = self.cookie.split('=',1)[1]
        return Store(self.app.sessions.path(self.app.sessions.get(token)['session_hash']))

    def test_fictional_unconfirmed_seed_and_no_legacy_routes(self):
        state = self.start()
        self.assertFalse(state['profile_confirmed'])
        self.assertEqual(state['profile_version'],1)
        self.assertEqual(len(state['jobs']),3)
        self.assertTrue(all(j['record_type']=='demo' for j in state['jobs']))
        self.assertFalse(state['demo']['external_submission_enabled'])
        for path in ('/v1/candidates','/radar','/v1/radar/batch'):
            self.request(path,expected=404)

    def test_cookie_security_and_session_isolation(self):
        state = self.start()
        self.confirm(state,name='Fictional A')
        b,headers = self.request(cookie='')
        self.assertNotEqual(b['csrf'],self.csrf)
        self.assertNotEqual(b['profile']['name'],'Fictional A')
        self.assertIn('HttpOnly',headers['Set-Cookie'])
        self.assertIn('SameSite=Lax',headers['Set-Cookie'])
        self.assertNotIn('Domain=',headers['Set-Cookie'])
        again,_ = self.request()
        self.assertEqual(again['profile']['name'],'Fictional A')

    def test_csrf_origin_host_and_cross_site_rejected(self):
        self.start()
        for kwargs in ({'csrf':''},{'csrf':'incorrect'},{'origin':'https://evil.invalid'},
                       {'host':'evil.invalid'},{'extra':{'HTTP_SEC_FETCH_SITE':'cross-site'}}):
            self.request('/api/search','POST',{},expected=403,**kwargs)
        self.request('/api/state',expected=403,origin='https://evil.invalid')

    def test_profile_confirmation_version_and_atomic_legacy_update(self):
        state = self.start()
        self.request('/api/profile','POST',{'profile':state['profile'],'expected_version':1},expected=409)
        saved = self.confirm(state)
        self.assertEqual(saved['profile_version'],2)
        self.assertTrue(saved['profile_confirmed'])
        self.request('/api/profile','POST',{'profile':state['profile'],'confirmed':True,'expected_version':1},expected=409)
        with connect(self.store().path) as db:
            self.assertEqual(db.execute('SELECT revision FROM candidates').fetchone()[0],2)
            self.assertEqual(db.execute('SELECT count(*) FROM ap_profile_versions').fetchone()[0],2)
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],4)
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_import_is_unreviewed_draft_preserves_confirmed_facts(self):
        state = self.start()
        self.confirm(state)
        before = self.request()[0]
        result,_ = self.request('/api/profile/import','POST',{'text':'Fictional new draft'})
        self.assertTrue(result['requires_review'])
        after = self.request()[0]
        self.assertEqual(after['profile'],before['profile'])
        self.assertEqual(after['profile_version'],before['profile_version'])
        self.assertEqual(after['draft']['resume_text'],'Fictional new draft')
        self.confirm(after)
        self.assertIsNone(self.request()[0]['draft'])

    def test_import_concurrency_rejects_busy_without_queuing(self):
        self.start()
        entered = threading.Event()
        release = threading.Event()
        results = []
        def parse(data):
            entered.set()
            if not release.wait(5):
                raise AssertionError('test parser did not receive release')
            return 'Fictional import','text'
        def first_import():
            results.append(self.request('/api/profile/import','POST',{'text':'Fictional import'})[0])
        with patch('applypilot_core.service.import_text',side_effect=parse) as parser:
            thread = threading.Thread(target=first_import)
            thread.start()
            self.assertTrue(entered.wait(5))
            try:
                result,_ = self.request('/api/profile/import','POST',{'text':'Second import'},expected=429)
                self.assertEqual(result['code'],'import_busy')
                self.assertEqual(parser.call_count,1)
            finally:
                release.set()
                thread.join(5)
            self.assertFalse(thread.is_alive())
        self.assertEqual(len(results),1)
        # Successful completion releases the single parser slot.
        self.request('/api/profile/import','POST',{'text':'Next fictional import'})

    def test_failed_import_releases_process_slot(self):
        self.start()
        with patch('applypilot_core.service.import_text',side_effect=DomainError(400,'document_parse_failed','Test parser failure')):
            self.request('/api/profile/import','POST',{'text':'Fictional import'},expected=400)
        self.request('/api/profile/import','POST',{'text':'Next fictional import'})

    def test_unconfirmed_profile_blocks_analysis(self):
        state = self.start()
        self.request('/api/jobs/'+state['jobs'][0]['id']+'/analyze','POST',{},expected=409)

    def test_outputs_versioned_persistent_and_stale_after_edit(self):
        state = self.start()
        self.confirm(state)
        jid = state['jobs'][0]['id']
        output,_ = self.request('/api/jobs/'+jid+'/analyze','POST',{})
        self.assertEqual(output['analysis']['profile_version'],2)
        self.request('/api/jobs/'+jid+'/resume','POST',{})
        # Restart with the same persistent registry and workspace files.
        self.app = create_app(self.env,runtime=FakeRuntime)
        state = self.request()[0]
        job = next(j for j in state['jobs'] if j['id']==jid)
        self.assertFalse(job['analysis']['stale'])
        self.assertEqual(job['analysis']['id'],output['analysis']['id'])
        self.confirm(state,name='Fictional change')
        job = self.store().job(jid)
        self.assertTrue(job['analysis']['stale'])
        self.assertTrue(job['resume']['stale'])

    def test_manual_submitted_requires_attestation_and_preserves_execution_machine(self):
        jid = self.start()['jobs'][0]['id']
        self.request('/api/jobs/'+jid+'/track','POST',{'status':'submitted','idempotency_key':'manual-submit-1'},expected=409)
        result = self.track(jid,'submitted',attestation=True)
        self.assertEqual(result['events'][-1]['kind'],'manual_attestation')
        with connect(self.store().path) as db:
            self.assertEqual(db.execute('SELECT status FROM applications WHERE id=?',(result['job']['application_id'],)).fetchone()[0],'pending_review')
            self.assertEqual(db.execute('SELECT count(*) FROM submission_attempts').fetchone()[0],0)

    def test_tracking_idempotency_conflict_and_event_log(self):
        jid = self.start()['jobs'][0]['id']
        first = self.track(jid,'preparing')
        again = self.track(jid,'preparing')
        self.assertTrue(again['replayed'])
        self.assertEqual(len(first['events']),len(again['events']))
        self.request('/api/jobs/'+jid+'/track','POST',{'status':'saved','idempotency_key':'operation-preparing'},expected=409)
        events = self.request('/api/jobs/'+jid+'/events')[0]['events']
        self.assertEqual(events[-1]['to_status'],'preparing')

    def test_same_stage_key_replay_does_not_undo_an_intervening_change(self):
        jid = self.start()['jobs'][0]['id']
        first = self.track(jid,'saved',idempotency_key='same-stage-key-a')
        self.assertFalse(first['replayed'])
        self.assertEqual(first['events'][-1]['idempotency_key'],'same-stage-key-a')
        changed = self.track(jid,'preparing',idempotency_key='changed-stage-key-b')
        replay = self.track(jid,'saved',idempotency_key='same-stage-key-a')
        self.assertTrue(replay['replayed'])
        self.assertEqual(replay['job']['tracker_status'],'preparing')
        self.assertEqual(replay['job']['tracker_revision'],changed['job']['tracker_revision'])
        self.assertEqual(replay['events'],changed['events'])

    def test_same_stage_attestation_replay_cannot_clear_a_later_unknown_hold(self):
        jid = self.start()['jobs'][0]['id']
        self.track(jid,'submitted',attestation=True)
        first = self.track(jid,'submitted',attestation=True,idempotency_key='same-attestation-a')
        self.assertFalse(first['replayed'])
        self.assertEqual(first['events'][-1]['idempotency_key'],'same-attestation-a')
        unknown = self.track(jid,'submission_unknown',idempotency_key='later-unknown-key-b')
        replay = self.track(jid,'submitted',attestation=True,idempotency_key='same-attestation-a')
        self.assertTrue(replay['replayed'])
        self.assertTrue(replay['job']['submission_unresolved'])
        self.assertEqual(replay['job']['tracker_status'],'submission_unknown')
        self.assertEqual(replay['job']['tracker_revision'],unknown['job']['tracker_revision'])
        self.assertEqual(replay['events'],unknown['events'])

    def test_unknown_submission_does_not_become_safe_to_retry(self):
        jid = self.start()['jobs'][0]['id']
        self.track(jid,'submission_unknown')
        self.request('/api/jobs/'+jid+'/track','POST',{'status':'filling','idempotency_key':'unsafe-retry'},expected=409)
        self.assertEqual(self.store().job(jid)['tracker_status'],'submission_unknown')

    def test_unknown_submission_blocks_every_unattested_exit_and_indirect_path(self):
        from applypilot_core.service import TRACK_STATUSES
        jid = self.start()['jobs'][0]['id']
        self.track(jid,'submission_unknown')
        for status in sorted(TRACK_STATUSES - {'submission_unknown','submitted'}):
            with self.subTest(status=status):
                result,_ = self.request('/api/jobs/'+jid+'/track','POST',
                    {'status':status,'attestation':True,'idempotency_key':'blocked-exit-'+status},expected=409)
                self.assertEqual(result['code'],'submission_unresolved')
                self.assertEqual(self.store().job(jid)['tracker_status'],'submission_unknown')
        self.request('/api/jobs/'+jid+'/track','POST',
                     {'status':'submitted','idempotency_key':'unattested-submit'},expected=409)
        for status in ('skipped','preparing','filling','saved','assessment','filling'):
            self.request('/api/jobs/'+jid+'/track','POST',
                         {'status':status,'idempotency_key':'indirect-'+status},expected=409)
        self.assertEqual(len(self.store().events(jid)),2)
        result = self.track(jid,'submitted',attestation=True)
        self.assertFalse(result['job']['submission_unresolved'])
        self.assertEqual(result['events'][-1]['kind'],'manual_attestation')
        # A second uncertain attempt re-engages the hold despite older attestation.
        self.request('/api/jobs/'+jid+'/track','POST',
                     {'status':'submission_unknown','idempotency_key':'second-unknown'})
        self.request('/api/jobs/'+jid+'/track','POST',
                     {'status':'skipped','idempotency_key':'second-skip'},expected=409)
        old_attestation = self.track(jid,'submitted',attestation=True)
        self.assertTrue(old_attestation['replayed'])
        self.assertTrue(old_attestation['job']['submission_unresolved'])
        self.assertEqual(old_attestation['job']['tracker_status'],'submission_unknown')

    def test_unknown_safety_hold_survives_restart_rediscovery_and_replay(self):
        self.start()
        FakeRuntime.result = {'jobs':[live_job()],'failures':[],'source_summary':[]}
        jid = self.request('/api/search','POST',{})[0]['jobs'][0]['id']
        self.track(jid,'preparing')
        before = self.track(jid,'submission_unknown')
        self.app = create_app(self.env,runtime=FakeRuntime)
        rediscovered = self.request('/api/search','POST',{})[0]['jobs'][0]
        self.assertEqual(rediscovered['id'],jid)
        self.assertTrue(rediscovered['submission_unresolved'])
        # Retrying an older successful preparation only replays; it cannot undo the latch.
        replay = self.track(jid,'preparing')
        self.assertTrue(replay['replayed'])
        self.assertEqual(replay['job']['tracker_status'],'submission_unknown')
        self.assertEqual(len(replay['events']),len(before['events']))
        self.request('/api/jobs/'+jid+'/track','POST',
                     {'status':'filling','idempotency_key':'post-restart-fill'},expected=409)
        resolved = self.track(jid,'submitted',attestation=True)
        replay = self.track(jid,'submitted',attestation=True)
        self.assertTrue(replay['replayed'])
        self.assertFalse(replay['job']['submission_unresolved'])
        self.assertEqual(len(replay['events']),len(resolved['events']))

    def test_legacy_skip_bypass_history_remains_unresolved_without_rewriting_events(self):
        jid = self.start()['jobs'][0]['id']
        unknown = self.track(jid,'submission_unknown')
        store = self.store()
        # Reproduce a database left by the old implementation's accepted skip path.
        with connect(store.path) as db:
            db.execute("UPDATE ap_tracker SET status='skipped',revision=revision+1 WHERE job_id=?",(jid,))
            db.execute("INSERT INTO ap_tracker_events(application_id,from_status,to_status,kind,note,idempotency_key,request_hash,occurred_at) VALUES(?,'submission_unknown','skipped','manual_tracking','','legacy-bypass','legacy-fingerprint',?)",
                       (unknown['job']['application_id'],now()))
        history = store.events(jid)
        self.app = create_app(self.env,runtime=FakeRuntime)
        state = self.request()[0]
        projected = next(j for j in state['jobs'] if j['id']==jid)
        self.assertTrue(projected['submission_unresolved'])
        self.assertEqual(projected['tracker_status'],'submission_unknown')
        self.assertEqual(projected['tracker_recorded_status'],'skipped')
        self.assertEqual(state['metrics']['by_status']['submission_unknown'],1)
        self.assertEqual(self.store().events(jid),history)
        for status in ('filling','preparing','saved','skipped','rejected','offer'):
            self.request('/api/jobs/'+jid+'/track','POST',
                         {'status':status,'idempotency_key':'legacy-retry-'+status},expected=409)
        result = self.track(jid,'submitted',attestation=True)
        self.assertFalse(result['job']['submission_unresolved'])
        self.assertEqual(result['events'][:-1],history)
        self.assertEqual(result['events'][-1]['from_status'],'skipped')
        self.assertEqual(result['events'][-1]['kind'],'manual_attestation')

    def test_provider_identity_dedup_preserves_tracker(self):
        self.start()
        job = live_job()
        FakeRuntime.result = {'jobs':[job],'failures':[],'source_summary':[]}
        first = self.request('/api/search','POST',{})[0]
        jid = first['jobs'][0]['id']
        self.track(jid,'skipped')
        job['title'] = 'Updated title'
        job['canonical_url'] += '?source=official'
        again = self.request('/api/search','POST',{})[0]
        self.assertEqual(again['jobs'][0]['id'],jid)
        self.assertEqual(again['jobs'][0]['tracker_status'],'skipped')
        self.assertEqual(again['run']['result']['created'],0)
        self.assertEqual(len(self.request()[0]['jobs']),4)
        with connect(self.store().path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM job_sources WHERE provider=?',('fixture-source',)).fetchone()[0],1)

    def test_closed_observation_updates_existing_without_importing_new(self):
        self.start()
        job = live_job()
        FakeRuntime.result = {'jobs':[job],'failures':[],'source_summary':[]}
        jid = self.request('/api/search','POST',{})[0]['jobs'][0]['id']
        self.track(jid,'preparing')
        job['availability'] = 'expired'
        unseen = live_job('filtered-away')
        FakeRuntime.result = {'jobs':[],'observed_jobs':[job,unseen],'failures':[],'source_summary':[]}
        result = self.request('/api/search','POST',{})[0]
        self.assertEqual(result['jobs'],[])
        saved = self.store().job(jid)
        self.assertEqual(saved['availability'],'expired')
        self.assertEqual(saved['tracker_status'],'preparing')
        self.assertEqual(len(self.request()[0]['jobs']),4)
        with connect(self.store().path) as db:
            self.assertEqual(db.execute('SELECT availability FROM jobs WHERE id=?',(jid,)).fetchone()[0],'closed')

    def test_failed_live_source_is_http_error_and_never_fixture_fallback(self):
        self.start()
        FakeRuntime.result = {'jobs':[],'failures':[{'code':'http_503','source':'fixture'}],'source_summary':[]}
        result,_ = self.request('/api/search','POST',{},expected=502)
        self.assertEqual(result['run']['status'],'failed')
        self.assertNotIn('jobs',result)
        self.assertEqual(len(self.request()[0]['jobs']),3)

    def test_empty_live_search_is_valid_no_results(self):
        self.start()
        result,_ = self.request('/api/search','POST',{})
        self.assertEqual(result['jobs'],[])
        self.assertEqual(result['run']['status'],'completed')

    def test_source_failures_redact_exception_details(self):
        self.start()
        FakeRuntime.error = True
        result,_ = self.request('/api/search','POST',{},expected=502)
        self.assertNotIn('TOP-SECRET',json.dumps(result))
        self.assertNotIn('TOP-SECRET',json.dumps(self.request()[0]['runs']))

    def test_source_evidence_missing_rejected_atomically(self):
        self.start()
        job = live_job()
        job['verification']['evidence'] = []
        FakeRuntime.result = {'jobs':[live_job('valid-first'),job],'failures':[],'source_summary':[]}
        self.request('/api/search','POST',{},expected=502)
        self.assertEqual(len(self.request()[0]['jobs']),3)

    def test_malicious_inputs_and_unknown_fields_rejected(self):
        self.start()
        for payload in ({'url':'http://127.0.0.1/secret'},{'query':'https://evil.invalid'},{'query':'mail@example.com'},{'cities':'Shanghai'},{'recruitment_type':'any'}):
            self.request('/api/search','POST',payload,expected=400)
        profile = deepcopy(DEMO_PROFILE)
        profile['password'] = 'do not persist'
        self.request('/api/profile','POST',{'profile':profile,'confirmed':True,'expected_version':1},expected=400)
        self.request('/api/search','POST',raw=b'{"query":"a","query":"b"}',expected=400)
        self.request('/api/search','POST',raw=b'{"query":NaN}',expected=400)
        self.request('/api/search','POST',raw=b'[]',expected=400)

    def test_http_edge_inputs_are_rejected_without_server_errors(self):
        jid = self.start()['jobs'][0]['id']
        self.request('/api/search','POST',{},csrf='非ASCII',expected=403)
        self.request('/api/search','POST',{},extra={'CONTENT_LENGTH':'-1'},expected=413)
        self.request('/api/search','POST',{},extra={'CONTENT_LENGTH':'4000000'},expected=413)
        self.request('/api/search','POST',{},extra={'CONTENT_TYPE':'text/plain'},expected=415)
        self.request('/api/jobs/'+jid+'/track','POST',{'status':[],'idempotency_key':'invalid-status'},expected=400)

    def test_stale_source_verification_is_not_admitted(self):
        self.start()
        job = live_job()
        job['verification']['verified_at'] = '2020-01-01T00:00:00Z'
        FakeRuntime.result = {'jobs':[job],'failures':[],'source_summary':[]}
        self.request('/api/search','POST',{},expected=502)
        self.assertEqual(len(self.request()[0]['jobs']),3)

    def test_session_database_has_hard_size_cap(self):
        from applypilot_core.store import connect as bounded_connect, SESSION_DATABASE_LIMIT
        self.start()
        with bounded_connect(self.store().path) as db:
            maximum = db.execute('PRAGMA max_page_count').fetchone()[0]
            page_size = db.execute('PRAGMA page_size').fetchone()[0]
            self.assertEqual(maximum*page_size,SESSION_DATABASE_LIMIT)

    def test_repeated_concurrent_tracking_creates_one_event(self):
        jid = self.start()['jobs'][0]['id']
        store = self.store()
        outcomes = []
        def update():
            outcomes.append(store.track(jid,'preparing',False,'','concurrent-key'))
        threads = [threading.Thread(target=update) for _ in range(2)]
        for thread in threads:thread.start()
        for thread in threads:thread.join()
        self.assertEqual(len(store.events(jid)),2)
        self.assertEqual(sum(result['replayed'] for result in outcomes),1)

    def test_health_checks_storage_without_session_or_secret(self):
        result,headers = self.request('/healthz')
        self.assertEqual(result['storage'],'writable')
        self.assertNotIn('Set-Cookie',headers)
        with connect(self.app.sessions.registry) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM sessions').fetchone()[0],0)

    def test_public_configuration_and_secure_cookie(self):
        with self.assertRaises(ValueError):
            create_app({'APPLYPILOT_PUBLIC':'1'},data_root=self.temp.name)
        self.app = create_app({'APPLYPILOT_PUBLIC':'1','PUBLIC_ORIGIN':'https://demo.example'},data_root=self.temp.name,runtime=FakeRuntime)
        result,headers = self.request(host='demo.example',origin='https://demo.example')
        self.assertIn('Secure',headers['Set-Cookie'])
        self.assertIn('Strict-Transport-Security',headers)

    def test_railway_storage_valid_mount_uses_data_directory(self):
        env = {'RAILWAY_PROJECT_ID':'test-project','RAILWAY_VOLUME_MOUNT_PATH':'/data','APPLYPILOT_DATA_DIR':'/data'}
        with patch('applypilot_core.server.Path.is_dir',return_value=True), patch('applypilot_core.server.SessionManager') as manager:
            create_app(env,runtime=FakeRuntime)
            self.assertEqual(manager.call_args.args[0],'/data')
        env['APPLYPILOT_DATA_DIR'] = '/data/workspaces'
        with patch('applypilot_core.server.Path.is_dir',return_value=True), patch('applypilot_core.server.SessionManager') as manager:
            create_app(env,runtime=FakeRuntime)
            self.assertEqual(manager.call_args.args[0],'/data/workspaces')

    def test_railway_storage_rejects_missing_wrong_or_nonexistent_mount(self):
        base = {'RAILWAY_ENVIRONMENT_ID':'test-environment','APPLYPILOT_DATA_DIR':'/data'}
        for mount in (None,'/tmp','/data/',''):
            env = dict(base)
            if mount is not None:env['RAILWAY_VOLUME_MOUNT_PATH'] = mount
            with self.subTest(mount=mount), patch('applypilot_core.server.SessionManager') as manager:
                with self.assertRaises(ValueError):create_app(env,runtime=FakeRuntime)
                manager.assert_not_called()
        with patch('applypilot_core.server.Path.is_dir',return_value=False), patch('applypilot_core.server.SessionManager') as manager:
            with self.assertRaises(ValueError):create_app({**base,'RAILWAY_VOLUME_MOUNT_PATH':'/data'},runtime=FakeRuntime)
            manager.assert_not_called()

    def test_railway_storage_rejects_ephemeral_defaults_overrides_and_resolved_escape(self):
        base = {'RAILWAY_PROJECT_ID':'test-project','RAILWAY_VOLUME_MOUNT_PATH':'/data'}
        for directory in (None,'/tmp/applypilot-demo','/database','/data/../tmp/escape'):
            env = dict(base)
            if directory is not None:env['APPLYPILOT_DATA_DIR'] = directory
            with self.subTest(directory=directory), patch('applypilot_core.server.Path.is_dir',return_value=True), patch('applypilot_core.server.SessionManager') as manager:
                with self.assertRaises(ValueError):create_app(env,runtime=FakeRuntime)
                manager.assert_not_called()
        with patch('applypilot_core.server.Path.is_dir',return_value=True), patch('applypilot_core.server.SessionManager') as manager:
            with self.assertRaises(ValueError):
                create_app({**base,'APPLYPILOT_DATA_DIR':'/data'},data_root=self.temp.name,runtime=FakeRuntime)
            manager.assert_not_called()
        # A symlink that resolves outside the mounted directory is also forbidden.
        with patch('applypilot_core.server.Path.is_dir',return_value=True), patch('applypilot_core.server.Path.resolve',return_value=Path('/tmp/escaped')), patch('applypilot_core.server.SessionManager') as manager:
            with self.assertRaises(ValueError):create_app({**base,'APPLYPILOT_DATA_DIR':'/data/link'},runtime=FakeRuntime)
            manager.assert_not_called()

    def test_session_capacity_and_expiry(self):
        manager = SessionManager(Path(self.temp.name)/'limits',max_sessions=1,ttl_seconds=60)
        token,session = manager.create('a')
        with self.assertRaises(DomainError) as error:
            manager.create('b')
        self.assertEqual(error.exception.status,503)
        with connect(manager.registry) as db:
            db.execute('UPDATE sessions SET expires_at=0')
        self.assertIsNone(manager.get(token))
        manager.create('b')
        self.assertFalse(manager.path(session['session_hash']).exists())

    def test_creation_rate_limited(self):
        manager = SessionManager(Path(self.temp.name)/'rates',max_sessions=3,new_per_minute=1)
        manager.create('same-address')
        with self.assertRaises(DomainError) as error:
            manager.create('same-address')
        self.assertEqual(error.exception.status,429)

    def test_model_budget_global_durable_and_rules_do_not_consume(self):
        self.env['APPLYPILOT_MODEL_CALLS_PER_DAY'] = '1'
        self.app = create_app(self.env,runtime=FakeRuntime)
        state = self.start()
        self.confirm(state)
        jid = state['jobs'][0]['id']
        self.request('/api/jobs/'+jid+'/analyze','POST',{})
        FakeRuntime.configured = True
        self.request('/api/jobs/'+jid+'/analyze','POST',{'model_consent':'openai'})
        self.app = create_app(self.env,runtime=FakeRuntime)
        error,_ = self.request('/api/jobs/'+jid+'/resume','POST',{'model_consent':'openai'},expected=429)
        self.assertEqual(error['code'],'model_budget_exhausted')

    def test_configured_model_requires_consent_before_runtime_and_quota(self):
        state = self.start()
        self.confirm(state)
        jid = state['jobs'][0]['id']
        FakeRuntime.configured = True
        for action,method in (('analyze','analyze'),('resume','suggest_resume')):
            with patch.object(FakeRuntime,method) as runtime_call, patch.object(self.app.sessions,'reserve_operation') as reserve:
                for body in ({},{'model_consent':'claude'},{'model_consent':True},{'model_consent':None}):
                    result,_ = self.request('/api/jobs/'+jid+'/'+action,'POST',body,expected=409)
                    self.assertEqual(result['code'],'model_consent_required')
                runtime_call.assert_not_called()
                reserve.assert_not_called()
        self.assertEqual(self.request()[0]['runs'],[])

    def test_model_configuration_change_after_bootstrap_rechecks_consent(self):
        state = self.start()
        self.assertFalse(state['model']['configured'])
        self.confirm(state)
        jid = state['jobs'][0]['id']
        FakeRuntime.configured = True
        self.request('/api/jobs/'+jid+'/analyze','POST',{},expected=409)
        self.request('/api/jobs/'+jid+'/analyze','POST',{'model_consent':'openai'})
        # Consent for one provider does not authorize a later, different provider.
        with patch.object(FakeRuntime,'model_status',return_value={'configured':True,'provider':'different-provider','mode':'model'}), patch.object(FakeRuntime,'analyze') as runtime_call, patch.object(self.app.sessions,'reserve_operation') as reserve:
            self.request('/api/jobs/'+jid+'/analyze','POST',{'model_consent':'openai'},expected=409)
            runtime_call.assert_not_called()
            reserve.assert_not_called()

    def test_deepseek_consent_is_exact_and_old_openai_consent_cannot_cross_providers(self):
        FakeRuntime.configured = True
        state = self.start()
        self.assertEqual(state['model']['provider'],'openai')
        self.confirm(state)
        jid = state['jobs'][0]['id']
        # Simulate the server switching keys/providers after this page bootstrapped.
        FakeRuntime.provider = 'deepseek'
        for action,method in (('analyze','analyze'),('resume','suggest_resume')):
            output = getattr(FakeRuntime,method)({}, {})
            with patch.object(FakeRuntime,method,return_value=output) as runtime_call, patch.object(self.app.sessions,'reserve_operation') as reserve:
                for payload in ({},{'model_consent':'openai'},{'model_consent':'DeepSeek'},
                                {'model_consent':'claude'},{'model_consent':None}):
                    result,_ = self.request('/api/jobs/'+jid+'/'+action,'POST',payload,expected=409)
                    self.assertEqual(result['code'],'model_consent_required')
                runtime_call.assert_not_called()
                reserve.assert_not_called()
                self.request('/api/jobs/'+jid+'/'+action,'POST',{'model_consent':'deepseek'})
                runtime_call.assert_called_once()
                reserve.assert_called_once()
                self.assertEqual(reserve.call_args.args[1],'model')
        # Switching back equally requires fresh consent for OpenAI.
        FakeRuntime.provider = 'openai'
        with patch.object(FakeRuntime,'analyze') as runtime_call, patch.object(self.app.sessions,'reserve_operation') as reserve:
            self.request('/api/jobs/'+jid+'/analyze','POST',{'model_consent':'deepseek'},expected=409)
            runtime_call.assert_not_called()
            reserve.assert_not_called()

    def test_deepseek_without_key_keeps_rules_mode_without_provider_quota(self):
        FakeRuntime.provider = 'deepseek'
        state = self.start()
        self.confirm(state)
        self.assertFalse(state['model']['configured'])
        with patch.object(self.app.sessions,'reserve_operation') as reserve:
            result,_ = self.request('/api/jobs/'+state['jobs'][0]['id']+'/analyze','POST',{})
            self.assertEqual(result['analysis']['mode'],'rules')
            reserve.assert_not_called()

    def test_generation_request_cannot_override_provider_or_base_url(self):
        FakeRuntime.configured = True
        FakeRuntime.provider = 'deepseek'
        state = self.start()
        self.confirm(state)
        jid = state['jobs'][0]['id']
        with patch.object(FakeRuntime,'analyze') as runtime_call, patch.object(self.app.sessions,'reserve_operation') as reserve:
            for override in ({'provider':'openai'},{'base_url':'https://evil.invalid'},{'api_key':'DO-NOT-TRANSMIT'}):
                self.request('/api/jobs/'+jid+'/analyze','POST',{'model_consent':'deepseek',**override},expected=400)
            runtime_call.assert_not_called()
            reserve.assert_not_called()

    def test_model_disabled_after_consent_returns_honest_rules_without_quota(self):
        state = self.start()
        self.confirm(state)
        with patch.object(self.app.sessions,'reserve_operation') as reserve:
            result,_ = self.request('/api/jobs/'+state['jobs'][0]['id']+'/analyze','POST',{'model_consent':'openai'})
            self.assertEqual(result['analysis']['mode'],'rules')
            reserve.assert_not_called()

    def test_model_failure_fallback_is_partial_and_explicit(self):
        state = self.start()
        self.confirm(state)
        output = FakeRuntime.analyze({}, {})
        output['model_status'] = 'fallback_rules:http_503'
        FakeRuntime.configured = True
        with patch.object(FakeRuntime,'analyze',return_value=output):
            result,_ = self.request('/api/jobs/'+state['jobs'][0]['id']+'/analyze','POST',{'model_consent':'openai'})
        self.assertTrue(result['analysis']['degraded'])
        self.assertEqual(result['run']['status'],'partial')
        self.assertEqual(result['analysis']['mode'],'rules')

    def test_railway_health_host_is_narrowly_scoped(self):
        railway_env = {'APPLYPILOT_PUBLIC':'1','PUBLIC_ORIGIN':'https://demo.example','RAILWAY_PROJECT_ID':'test-project',
                       'RAILWAY_VOLUME_MOUNT_PATH':'/data','APPLYPILOT_DATA_DIR':'/data'}
        # Simulate Railway's mount; all actual test writes stay in the temporary directory.
        with patch('applypilot_core.server.Path.is_dir',return_value=True), patch('applypilot_core.server.SessionManager',
                side_effect=lambda _root,*args,**kwargs: SessionManager(self.temp.name,*args,**kwargs)):
            self.app = create_app(railway_env,runtime=FakeRuntime)
        self.request('/healthz',host='healthcheck.railway.app',origin=None)
        self.request('/api/state',host='healthcheck.railway.app',origin=None,expected=403)
        self.request('/healthz','POST',host='healthcheck.railway.app',origin=None,expected=403)
        self.request('/healthz',host='healthcheck.railway.app',origin='https://evil.invalid',expected=403)
        self.app = create_app({'APPLYPILOT_PUBLIC':'1','PUBLIC_ORIGIN':'https://demo.example'},data_root=self.temp.name,runtime=FakeRuntime)
        self.request('/healthz',host='healthcheck.railway.app',origin=None,expected=403)

    def test_search_budget_counts_failed_calls(self):
        self.env['APPLYPILOT_SEARCHES_PER_HOUR'] = '1'
        self.app = create_app(self.env,runtime=FakeRuntime)
        self.start()
        FakeRuntime.error = True
        self.request('/api/search','POST',{},expected=502)
        FakeRuntime.error = False
        error,_ = self.request('/api/search','POST',{},expected=429)
        self.assertEqual(error['code'],'search_budget_exhausted')

    def test_concurrent_profile_saves_have_one_winner(self):
        self.start()
        store = self.store()
        outcomes = []
        def save():
            try:
                store.save_profile(deepcopy(DEMO_PROFILE),1)
                outcomes.append('saved')
            except DomainError as error:
                outcomes.append(error.code)
        threads = [threading.Thread(target=save) for _ in range(2)]
        for thread in threads:thread.start()
        for thread in threads:thread.join()
        self.assertCountEqual(outcomes,['saved','version_conflict'])

    def test_concurrent_budget_reservations_have_one_winner(self):
        self.start()
        outcomes = []
        def reserve():
            try:
                self.app.sessions.reserve_operation('session','model',1,1)
                outcomes.append('allowed')
            except DomainError as error:
                outcomes.append(error.code)
        threads = [threading.Thread(target=reserve) for _ in range(2)]
        for thread in threads:thread.start()
        for thread in threads:thread.join()
        self.assertCountEqual(outcomes,['allowed','model_budget_exhausted'])


class FileImportTests(unittest.TestCase):
    def payload(self, filename, content):
        return {'filename':filename,'content_base64':base64.b64encode(content).decode()}

    def test_txt_unicode_and_size_limits(self):
        self.assertEqual(import_text(self.payload('resume.txt','虚构简历'.encode()))[0],'虚构简历')
        for payload in (self.payload('../resume.txt',b'a'),self.payload('resume.txt',b'\xff'),
                        self.payload('file.exe',b'abc'),self.payload('r.txt',b'x'*(2*1024*1024+1))):
            with self.assertRaises(DomainError):import_text(payload)

    def test_docx_extracts_text_locally(self):
        from docx import Document
        out = io.BytesIO()
        document = Document()
        document.add_paragraph('Fictional profile document')
        document.save(out)
        self.assertIn('Fictional profile',import_text(self.payload('resume.docx',out.getvalue()))[0])

    def test_text_pdf_extracts_under_256_mib_parser_limit(self):
        from pypdf import PdfWriter
        from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
        writer = PdfWriter()
        page = writer.add_blank_page(width=300,height=200)
        font = DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),
                                 NameObject('/BaseFont'):NameObject('/Helvetica')})
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
        content = DecodedStreamObject()
        content.set_data(b'BT /F1 12 Tf 20 100 Td (Fictional resume: product research) Tj ET')
        page[NameObject('/Contents')] = writer._add_object(content)
        out = io.BytesIO()
        writer.write(out)
        text,file_type = import_text(self.payload('fictional.pdf',out.getvalue()))
        self.assertIn('Fictional resume: product research',text)
        self.assertEqual(file_type,'pdf')

    def test_scanned_pdf_reports_missing_text(self):
        from pypdf import PdfWriter
        out = io.BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=100,height=100)
        writer.write(out)
        with self.assertRaises(DomainError) as error:
            import_text(self.payload('scan.pdf',out.getvalue()))
        self.assertEqual(error.exception.code,'no_text')

    def test_pdf_page_limit(self):
        from pypdf import PdfWriter
        out = io.BytesIO()
        writer = PdfWriter()
        for _ in range(31):writer.add_blank_page(width=100,height=100)
        writer.write(out)
        with self.assertRaises(DomainError) as error:import_text(self.payload('large.pdf',out.getvalue()))
        self.assertEqual(error.exception.code,'document_parse_failed')

    def test_docx_macro_entity_and_zip_bomb_rejected(self):
        for name,content in (('word/vbaProject.bin',b'macro'),('word/document.xml',b'<!DOCTYPE test>'),('word/large.xml',b'x'*2000000)):
            out = io.BytesIO()
            with zipfile.ZipFile(out,'w',compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr('[Content_Types].xml','xml')
                if name != 'word/document.xml':archive.writestr('word/document.xml','xml')
                archive.writestr(name,content)
            with self.assertRaises(DomainError):import_text(self.payload('unsafe.docx',out.getvalue()))


if __name__ == '__main__':
    unittest.main()
