"""Fetch Metadata regression tests: real WSGI and loopback HTTP, fictional data only.

No browser, external source, model provider, or deployed-server calls are made.
"""
import http.client
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.parse import urlsplit
from wsgiref.simple_server import make_server
from wsgiref.util import setup_testing_defaults

from applypilot_core.server import create_app, QuietHandler


# Deliberately independent of the implementation's allowlist.
PUBLIC_ROUTES = {
    '/': 'index.html', '/index.html': 'index.html',
    '/app.js': 'app.js', '/style.css': 'style.css',
    '/demo-form': 'demo-form.html', '/demo-form.html': 'demo-form.html',
    '/demo-form.js': 'demo-form.js', '/demo-form.css': 'demo-form.css',
}
NAVIGATION = {'Sec-Fetch-Site': 'cross-site', 'Sec-Fetch-Mode': 'navigate',
              'Sec-Fetch-Dest': 'document'}
SAME_ORIGIN = {'Sec-Fetch-Site': 'same-origin', 'Sec-Fetch-Mode': 'cors',
               'Sec-Fetch-Dest': 'empty'}


class NavigationGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='applypilot-navigation-')
        self.addCleanup(self.temp.cleanup)
        self.origin = 'https://demo.example'
        self.app = create_app({}, data_root=self.temp.name, origin=self.origin,
                              public=True, runtime=object())

    def request(self, path='/', method='GET', headers=None, data=None):
        env = {}
        setup_testing_defaults(env)
        request_path, _, query = path.partition('?')
        env.update(PATH_INFO=request_path, QUERY_STRING=query,
                   REQUEST_METHOD=method, HTTP_HOST=urlsplit(self.origin).netloc,
                   REMOTE_ADDR='127.0.0.1')
        env['wsgi.url_scheme'] = urlsplit(self.origin).scheme
        for key, value in (headers or {}).items():
            env['HTTP_' + key.upper().replace('-', '_')] = value
        body = json.dumps(data).encode() if data is not None else b''
        env.update(CONTENT_TYPE='application/json', CONTENT_LENGTH=str(len(body)))
        env['wsgi.input'] = io.BytesIO(body)
        captured = []
        result = b''.join(self.app(env, lambda status, fields: captured.append((status, fields))))
        status, fields = captured[0]
        return int(status.split()[0]), dict(fields), result

    def assert_denied(self, response, code='cross_site', status=403):
        actual, headers, body = response
        self.assertEqual(actual, status)
        self.assertNotIn('Set-Cookie', headers)
        self.assertNotIn('Access-Control-Allow-Origin', headers)
        if body:
            self.assertEqual(json.loads(body)['code'], code)
        self.assertEqual(headers['Cache-Control'], 'no-store')

    def state(self):
        status, headers, body = self.request('/api/state', headers=SAME_ORIGIN)
        self.assertEqual(status, 200)
        return json.loads(body), headers['Set-Cookie'].split(';', 1)[0]

    def test_cross_site_top_level_get_serves_only_exact_public_static_bytes(self):
        for path, filename in PUBLIC_ROUTES.items():
            with self.subTest(path=path):
                status, headers, body = self.request(path, headers=NAVIGATION)
                self.assertEqual(status, 200)
                self.assertEqual(body, (self.app.web_root / filename).read_bytes())
                self.assertNotIn('Set-Cookie', headers)
                self.assertEqual(headers['X-Frame-Options'], 'DENY')
                self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
                self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])
                self.assertIn('Strict-Transport-Security', headers)
        # Static entry does not create an anonymous workspace or consume its quota.
        self.assertEqual(list(Path(self.temp.name).glob('session-*')), [])

    def test_query_string_does_not_change_public_document_response(self):
        plain = self.request('/', headers=NAVIGATION)
        query = self.request('/?from=chat&next=/api/state', headers=NAVIGATION)
        self.assertEqual(query, plain)

    def test_cross_site_fetch_script_style_and_iframe_are_still_blocked(self):
        for path in PUBLIC_ROUTES:
            for mode, destination in (('cors', 'empty'), ('no-cors', 'script'),
                                      ('no-cors', 'style'), ('navigate', 'iframe'),
                                      ('navigate', 'frame'), ('navigate', ''),
                                      ('', 'document'), ('cors', 'document')):
                with self.subTest(path=path, mode=mode, destination=destination):
                    headers = {**NAVIGATION, 'Sec-Fetch-Mode': mode, 'Sec-Fetch-Dest': destination}
                    self.assert_denied(self.request(path, headers=headers))
        self.assert_denied(self.request(headers={'Sec-Fetch-Site': 'cross-site'}))

    def test_same_origin_assets_and_direct_visits_still_work(self):
        for path in PUBLIC_ROUTES:
            for headers in ({}, SAME_ORIGIN, {**NAVIGATION, 'Sec-Fetch-Site': 'none'}):
                with self.subTest(path=path, headers=headers):
                    self.assertEqual(self.request(path, headers=headers)[0], 200)
            status, headers, body = self.request(path, method='HEAD', headers=SAME_ORIGIN)
            self.assertEqual(status, 200)
            self.assertEqual(body, b'')
            self.assertGreater(int(headers['Content-Length']), 0)

    def test_navigation_never_bypasses_host_or_supplied_origin(self):
        for path in PUBLIC_ROUTES:
            for host in ('evil.invalid', 'demo.example:443', 'demo.example.evil.invalid'):
                with self.subTest(path=path, host=host):
                    self.assert_denied(self.request(path, headers={**NAVIGATION, 'Host': host}), 'invalid_host')
            for origin in ('https://evil.invalid', 'http://demo.example', 'https://demo.example:443',
                           'https://demo.example/', 'null', ''):
                with self.subTest(path=path, origin=origin):
                    self.assert_denied(self.request(path, headers={**NAVIGATION, 'Origin': origin}), 'invalid_origin')
            self.assertEqual(self.request(path, headers={**NAVIGATION, 'Origin': self.origin})[0], 200)

    def test_https_public_configuration_is_still_required(self):
        with self.assertRaises(ValueError):
            create_app({}, data_root=self.temp.name, origin='http://demo.example', public=True)

    def test_unknown_and_api_like_routes_do_not_gain_navigation_exception(self):
        for path in ('/unknown', '/index.html/extra', '/app.js/extra', '//index.html',
                     '/applypilot_web/index.html', '/api/state.html', '/api/state/',
                     '/api/unknown', '/healthz', '/api/health', '/v1/candidates', '/radar'):
            with self.subTest(path=path):
                self.assert_denied(self.request(path, headers=NAVIGATION))
        for path in ('/unknown', '/index.html/extra', '/app.js/extra', '/v1/candidates'):
            with self.subTest(same_origin_path=path):
                self.assert_denied(self.request(path, headers=SAME_ORIGIN), 'not_found', 404)

    def test_non_get_methods_cannot_use_navigation_exception(self):
        for path in ('/', '/index.html', '/app.js', '/api/state', '/unknown'):
            with self.subTest(path=path):
                self.assert_denied(self.request(path, method='HEAD', headers=NAVIGATION))
                self.assert_denied(self.request(path, method='POST', headers={**NAVIGATION, 'Origin': self.origin}, data={}))
                self.assert_denied(self.request(path, method='POST', headers=NAVIGATION, data={}), 'invalid_origin')
            for method in ('PUT', 'PATCH', 'DELETE', 'OPTIONS', 'TRACE'):
                with self.subTest(path=path, method=method):
                    self.assert_denied(self.request(path, method=method, headers=NAVIGATION), 'method_not_allowed', 405)

    def test_cross_site_api_reads_stay_blocked_with_valid_session(self):
        state, cookie = self.state()
        for path in ('/api/state', '/api/jobs/' + state['jobs'][0]['id'] + '/events',
                     '/api/demo/receipts', '/api/health', '/healthz'):
            for metadata in (NAVIGATION, {'Sec-Fetch-Site': 'cross-site',
                                         'Sec-Fetch-Mode': 'cors', 'Sec-Fetch-Dest': 'empty'}):
                with self.subTest(path=path, metadata=metadata):
                    self.assert_denied(self.request(path, headers={**metadata, 'Cookie': cookie}))
        # A rejected state read cannot mint a session even without a cookie.
        self.assert_denied(self.request('/api/state', headers=NAVIGATION))

    def test_same_origin_api_read_and_write_keep_all_guards(self):
        state, cookie = self.state()
        headers = {**SAME_ORIGIN, 'Cookie': cookie, 'Origin': self.origin,
                   'X-CSRF-Token': state['csrf']}
        data = {'profile': state['profile'], 'confirmed': True, 'expected_version': state['profile_version']}
        for change, code in (({'Sec-Fetch-Site': 'cross-site'}, 'cross_site'),
                             (NAVIGATION, 'cross_site'),
                             ({'Origin': 'https://evil.invalid'}, 'invalid_origin'),
                             ({'Origin': 'http://demo.example'}, 'invalid_origin'),
                             ({'Host': 'evil.invalid'}, 'invalid_host'),
                             ({'X-CSRF-Token': ''}, 'csrf'), ({'X-CSRF-Token': 'invalid'}, 'csrf')):
            with self.subTest(change=change):
                self.assert_denied(self.request('/api/profile', 'POST', {**headers, **change}, data), code)
        no_origin = {key: value for key, value in headers.items() if key != 'Origin'}
        self.assert_denied(self.request('/api/profile', 'POST', no_origin, data), 'invalid_origin')
        self.assert_denied(self.request('/api/state', headers={**headers, 'Origin': 'https://evil.invalid'}), 'invalid_origin')
        before = json.loads(self.request('/api/state', headers=headers)[2])
        self.assertEqual(before['profile_version'], state['profile_version'])
        status, _, body = self.request('/api/profile', 'POST', headers, data)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['profile_version'], state['profile_version'] + 1)
        after = json.loads(self.request('/api/state', headers=headers)[2])
        self.assertTrue(after['profile_confirmed'])


class NavigationHTTPTests(unittest.TestCase):
    def test_chat_navigation_assets_api_bootstrap_and_attack_over_real_http(self):
        with tempfile.TemporaryDirectory(prefix='applypilot-navigation-http-') as directory:
            server = make_server('127.0.0.1', 0, lambda environ, start: [], handler_class=QuietHandler)
            origin = 'http://127.0.0.1:' + str(server.server_port)
            app = create_app({}, data_root=directory, origin=origin, runtime=object())
            server.set_app(app)
            thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)
            thread.start()
            def request(path, method='GET', headers=None, data=None):
                connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
                try:
                    fields = dict(headers or {})
                    body = json.dumps(data).encode() if data is not None else None
                    if body is not None:
                        fields['Content-Type'] = 'application/json'
                    connection.request(method, path, body=body, headers=fields)
                    response = connection.getresponse()
                    return response.status, dict(response.getheaders()), response.read()
                finally:
                    connection.close()
            try:
                for path, filename in PUBLIC_ROUTES.items():
                    with self.subTest(public_path=path):
                        status, fields, body = request(path, headers=NAVIGATION)
                        self.assertEqual(status, 200)
                        self.assertEqual(body, (app.web_root / filename).read_bytes())
                        self.assertNotIn('Set-Cookie', fields)
                for path in ('/app.js', '/style.css', '/demo-form.js', '/demo-form.css'):
                    self.assertEqual(request(path, headers=SAME_ORIGIN)[0], 200)
                status, fields, body = request('/api/state', headers=SAME_ORIGIN)
                self.assertEqual(status, 200)
                state = json.loads(body)
                cookie = fields['Set-Cookie'].split(';', 1)[0]
                self.assertIn('SameSite=Lax', fields['Set-Cookie'])
                headers = {**SAME_ORIGIN, 'Origin': origin, 'Cookie': cookie, 'X-CSRF-Token': state['csrf']}
                data = {'profile': state['profile'], 'confirmed': True, 'expected_version': state['profile_version']}
                for path, method, fields, payload, expected, code in (
                    ('/api/state', 'GET', {**NAVIGATION, 'Cookie': cookie}, None, 403, 'cross_site'),
                    ('/api/profile', 'POST', {**headers, **NAVIGATION}, data, 403, 'cross_site'),
                    ('/api/profile', 'POST', {**headers, 'X-CSRF-Token': ''}, data, 403, 'csrf'),
                    ('/unknown', 'GET', NAVIGATION, None, 403, 'cross_site'),
                    ('/', 'OPTIONS', NAVIGATION, None, 405, 'method_not_allowed'),
                    ('/', 'GET', {**NAVIGATION, 'Host': 'evil.invalid'}, None, 403, 'invalid_host'),
                    ('/', 'GET', {**NAVIGATION, 'Origin': 'https://evil.invalid'}, None, 403, 'invalid_origin'),
                    ('/app.js', 'GET', {'Sec-Fetch-Site': 'cross-site', 'Sec-Fetch-Mode': 'no-cors',
                                      'Sec-Fetch-Dest': 'script'}, None, 403, 'cross_site'),
                ):
                    with self.subTest(path=path, method=method, code=code):
                        status, _, body = request(path, method, fields, payload)
                        self.assertEqual(status, expected)
                        self.assertEqual(json.loads(body)['code'], code)
                status, _, body = request('/api/profile', 'POST', headers, data)
                self.assertEqual(status, 200)
                self.assertEqual(json.loads(body)['profile_version'], state['profile_version'] + 1)
                restored = json.loads(request('/api/state', headers=headers)[2])
                self.assertTrue(restored['profile_confirmed'])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
            self.assertFalse(thread.is_alive())


if __name__ == '__main__':
    unittest.main()
