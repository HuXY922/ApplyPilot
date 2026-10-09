"""Focused controlled simulation contracts; browser proof is a separate check."""
from copy import deepcopy
from pathlib import Path
import sqlite3
import io
import json
import tempfile
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from applypilot_core import form_demo as demo
from scripts.demo_fill import SafetyError, owned_url, request_allowed


class ControlledFormTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.input = {'fields':deepcopy(demo.FICTIONAL_FIELDS), 'destination':demo.DESTINATION}

    def prepared(self):
        return demo.prepare(self.db, self.input)

    def approved(self):
        return {**self.prepared(), 'confirm_simulation':True}

    def assertCode(self, code, operation, *args):
        with self.assertRaises(demo.DemoError) as caught:
            operation(*args)
        self.assertEqual(caught.exception.code, code)

    def test_prepare_does_not_submit(self):
        snapshot = self.prepared()
        self.assertEqual(snapshot['status'], 'waiting_user')
        self.assertTrue(snapshot['simulation'])
        self.assertEqual(demo.receipts(self.db)['receipts'], [])

    def test_explicit_confirmation_required(self):
        payload = self.prepared()
        self.assertCode('demo_confirmation_required', demo.submit, self.db, payload)
        payload['confirm_simulation'] = True
        self.assertCode('demo_confirmation_expired', demo.submit, self.db, payload)

    def test_receipt_and_idempotent_repeat(self):
        payload = self.approved()
        first = demo.submit(self.db, payload)
        again = demo.submit(self.db, payload)
        self.assertEqual(first['id'], again['id'])
        self.assertTrue(again['replayed'])
        self.assertEqual(first['status'], 'simulation_recorded')
        self.assertEqual(len(demo.receipts(self.db)['receipts']), 1)
        self.assertFalse(self.db.execute("SELECT name FROM sqlite_master WHERE name='applications'").fetchall())

    def test_cancel_invalidates(self):
        payload = self.approved()
        demo.cancel(self.db, payload)
        self.assertCode('demo_confirmation_expired', demo.submit, self.db, payload)
        self.assertEqual(demo.receipts(self.db)['receipts'], [])

    def test_changed_fields_invalidates_even_when_restored(self):
        payload = self.approved()
        changed = deepcopy(payload)
        changed['fields']['role'] = demo.ALLOWED_ROLES[1]
        self.assertCode('demo_confirmation_changed', demo.submit, self.db, changed)
        self.assertCode('demo_confirmation_expired', demo.submit, self.db, payload)

    def test_changed_destination_invalidates(self):
        payload = self.approved()
        changed = {**payload,'destination':'https://careers.example/submit'}
        self.assertCode('demo_destination_forbidden', demo.submit, self.db, changed)
        self.assertCode('demo_confirmation_expired', demo.submit, self.db, payload)

    def test_changed_hash_invalidates(self):
        payload = self.approved()
        self.assertCode('demo_confirmation_changed', demo.submit, self.db, {**payload,'content_hash':'0'*64})
        self.assertCode('demo_confirmation_expired', demo.submit, self.db, payload)

    def test_changed_key_invalidates(self):
        payload = self.approved()
        self.assertCode('demo_confirmation_changed', demo.submit, self.db, {**payload,'idempotency_key':'different'})
        self.assertCode('demo_confirmation_expired', demo.submit, self.db, payload)

    def test_new_review_invalidates_old(self):
        old = self.approved()
        new = self.approved()
        self.assertCode('demo_confirmation_required', demo.submit, self.db, old)
        self.assertEqual(demo.submit(self.db,new)['status'], 'simulation_recorded')

    def test_expired_token(self):
        with patch.object(demo.time, 'time', return_value=1000):
            payload = self.approved()
        with patch.object(demo.time, 'time', return_value=1600):
            self.assertCode('demo_confirmation_expired', demo.submit, self.db, payload)

    def test_cross_session_token_rejected(self):
        payload = self.approved()
        other = sqlite3.connect(':memory:')
        self.addCleanup(other.close)
        self.assertCode('demo_confirmation_required', demo.submit, other, payload)
        self.assertEqual(demo.receipts(other)['receipts'], [])

    def test_real_or_extra_data_rejected(self):
        changed = deepcopy(self.input)
        changed['fields']['email'] = 'person@realcompany.com'
        self.assertCode('fictional_data_only', demo.prepare, self.db, changed)
        changed = deepcopy(self.input)
        changed['fields']['phone'] = '1234567890'
        self.assertCode('demo_fields_invalid', demo.prepare, self.db, changed)

    def test_bool_confirmation_must_be_true(self):
        for invalid in ('true', 1, [], None):
            payload = self.approved()
            payload['confirm_simulation'] = invalid
            self.assertCode('demo_confirmation_required', demo.submit, self.db, payload)

    def test_receipt_limit(self):
        with patch.object(demo, 'MAX_RECEIPTS', 1):
            demo.submit(self.db,self.approved())
            self.assertCode('demo_capacity', demo.prepare, self.db, self.input)

    def test_pending_review_count_bounded(self):
        for _ in range(100):
            self.prepared()
        self.assertEqual(self.db.execute('SELECT count(*) FROM ap_demo_confirmations').fetchone()[0],1)

    def test_hash_binds_every_field_and_destination(self):
        baseline = demo.content_hash(self.input['fields'])
        for key in self.input['fields']:
            modified = {**self.input['fields'],key:self.input['fields'][key]+'x'}
            self.assertNotEqual(baseline, demo.content_hash(modified))
        self.assertNotEqual(baseline, demo.content_hash(self.input['fields'],'/another'))

    def test_rejects_active_outer_transaction(self):
        self.db.execute('BEGIN')
        self.assertCode('demo_transaction_configuration', demo.prepare, self.db, self.input)
        self.db.rollback()


class BrowserScriptSafetyTests(unittest.TestCase):
    def test_owned_loopback_urls_only(self):
        for url in ('http://127.0.0.1:8787','http://localhost:8787/','http://[::1]:8787/demo-form'):
            self.assertTrue(owned_url(url)[1].endswith('/demo-form'))
        for url in ('https://example.com/demo-form', 'http://localhost.evil:8787',
                    'http://127.0.0.1:8787/other', 'file:///etc/passwd',
                    'http://user:pass@localhost:8787', 'http://localhost:8787/?external=1',
                    'http://localhost', 'http://localhost:bad'):
            with self.subTest(url=url), self.assertRaises(SafetyError):
                owned_url(url)

    def test_dry_run_blocks_all_post_and_external_requests(self):
        origin='http://127.0.0.1:8787'
        self.assertTrue(request_allowed(origin+'/demo-form','GET',origin))
        self.assertFalse(request_allowed(origin+'/api/demo/prepare','POST',origin))
        self.assertFalse(request_allowed(origin+'/api/demo/submit','POST',origin))
        self.assertFalse(request_allowed('https://careers.example/','GET',origin,True))
        self.assertFalse(request_allowed(origin+'/api/profile','POST',origin,True))
        self.assertTrue(request_allowed(origin+'/api/demo/submit','POST',origin,True))
        self.assertFalse(request_allowed(origin+'/api/demo/submit?to=external','POST',origin,True))


class DemoHTTPIntegrationTests(unittest.TestCase):
    """Real WSGI/session/SQLite routing in process, not a browser/network test."""
    def setUp(self):
        from applypilot_core.server import ApplyPilotApp
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.app = ApplyPilotApp(self.directory.name, runtime=object())
        self.cookie = ''
        self.csrf = ''
        state, headers = self.request('/api/state')
        self.cookie = headers['Set-Cookie'].split(';')[0]
        self.csrf = state['csrf']

    def request(self, path, data=None, expected=200, csrf=None, origin='http://127.0.0.1:8787'):
        raw = json.dumps(data or {}).encode()
        env = {'REQUEST_METHOD':'GET' if data is None else 'POST', 'PATH_INFO':path,
               'HTTP_HOST':'127.0.0.1:8787', 'HTTP_ORIGIN':origin, 'REMOTE_ADDR':'127.0.0.1',
               'HTTP_COOKIE':self.cookie,'HTTP_X_CSRF_TOKEN':self.csrf if csrf is None else csrf,
               'CONTENT_TYPE':'application/json','CONTENT_LENGTH':str(len(raw)),
               'wsgi.input':io.BytesIO(raw)}
        captured = []
        raw_response = b''.join(self.app(env,lambda code,headers:captured.append((int(code[:3]),dict(headers)))))
        code,headers = captured[0]
        result = json.loads(raw_response) if headers['Content-Type'].startswith('application/json') else raw_response
        self.assertEqual(code,expected,result)
        return result,headers

    def test_static_demo_csp_and_assets(self):
        html,headers = self.request('/demo-form')
        self.assertIn(b'/demo-form.js',html)
        self.assertIn(b'/demo-form.css',html)
        self.assertNotIn(b'style=',html)
        self.assertIn("style-src 'self'",headers['Content-Security-Policy'])
        self.request('/demo-form.css')
        self.request('/demo-form.js')

    def test_demo_write_uses_same_csrf_and_origin_guards(self):
        payload = {'fields':demo.FICTIONAL_FIELDS,'destination':demo.DESTINATION}
        self.assertEqual(self.request('/api/demo/prepare',payload,403,csrf='wrong')[0]['code'],'csrf')
        self.assertEqual(self.request('/api/demo/prepare',payload,403,origin='https://evil.example')[0]['code'],'invalid_origin')
        self.assertEqual(self.request('/api/demo/receipts')[0]['receipts'],[])

    def test_receipts_persist_across_route_connections_without_real_tracker_change(self):
        before,_ = self.request('/api/state')
        prepared,_ = self.request('/api/demo/prepare',{'fields':demo.FICTIONAL_FIELDS,'destination':demo.DESTINATION})
        self.assertEqual(self.request('/api/demo/receipts')[0]['receipts'],[])
        payload = {**prepared,'confirm_simulation':True}
        first,_ = self.request('/api/demo/submit',payload)
        repeated,_ = self.request('/api/demo/submit',payload)
        self.assertEqual(first['id'],repeated['id'])
        self.assertEqual(len(self.request('/api/demo/receipts')[0]['receipts']),1)
        after,_ = self.request('/api/state')
        self.assertEqual(before['metrics'],after['metrics'])
        self.assertEqual(before['jobs'],after['jobs'])

    def test_route_rejection_commits_confirmation_invalidation(self):
        prepared,_ = self.request('/api/demo/prepare',{'fields':demo.FICTIONAL_FIELDS,'destination':demo.DESTINATION})
        payload = {**prepared,'confirm_simulation':True}
        changed = {**payload,'content_hash':'0'*64}
        self.assertEqual(self.request('/api/demo/submit',changed,409)[0]['code'],'demo_confirmation_changed')
        self.assertEqual(self.request('/api/demo/submit',payload,409)[0]['code'],'demo_confirmation_expired')

    def test_route_receipts_and_tokens_isolated_by_cookie(self):
        prepared,_ = self.request('/api/demo/prepare',{'fields':demo.FICTIONAL_FIELDS,'destination':demo.DESTINATION})
        self.request('/api/demo/submit',{**prepared,'confirm_simulation':True})
        self.cookie = ''
        state,headers = self.request('/api/state')
        self.cookie = headers['Set-Cookie'].split(';')[0]
        self.csrf = state['csrf']
        self.assertEqual(self.request('/api/demo/receipts')[0]['receipts'],[])
        self.assertEqual(self.request('/api/demo/submit',{**prepared,'confirm_simulation':True},409)[0]['code'],'demo_confirmation_required')


if __name__ == '__main__':
    unittest.main()
