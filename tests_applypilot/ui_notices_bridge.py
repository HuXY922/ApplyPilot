"""Offline DOM-test bridge: real notice WSGI/parsers/SQLite, fixed HTML fixtures.

This file is test-only and is never imported by the application. Test controls
live outside the API body. Network sockets, subprocesses, and model/discovery
calls are forbidden even if deployment credentials exist in the environment.
"""
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import html
import io
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests_applypilot'))

from applypilot_core.server import ApplyPilotApp
from applypilot_core import university_notices as notices
from test_university_notices import FixtureTransport, fixture

XSS_TITLE = '<img src=x onerror=alert(1)> 示例公司2027届校园招聘'
XSS_SUMMARY = '<script>alert(2)</script> <img src=x onerror=alert(3)> 仅为离线测试文字。'


class OfflineRuntime:
    @staticmethod
    def model_status():
        return {'configured': False, 'mode': 'rules', 'status': 'offline_test_no_model'}

    @staticmethod
    def forbidden(*args, **kwargs):
        raise AssertionError('Notice UI tests must never invoke a model or job discovery')

    analyze = suggest_resume = extract_profile = discover = forbidden


class NoticeUITransport(FixtureTransport):
    """Reuse the parser fixtures, with explicit synthetic XSS/changed content."""
    def __init__(self, variant):
        super().__init__()
        if variant not in ('normal', 'changed', 'unavailable', 'details_unavailable'):
            raise ValueError('Unknown offline fixture variant')
        self.list_override[notices.SOURCES['nankai']['list_url']] = fixture('nankai-list.html').replace(
            '广州四三九九信息科技有限公司2027届秋招火热招聘中'.encode(),
            html.escape(XSS_TITLE).encode())
        # Put inert encoded markup before the short summary's truncation point.
        detail = fixture('nankai-detail.html').replace(
            b'<div class="zpnr">',
            ('<div class="zpnr"><p>' + html.escape(XSS_SUMMARY) + '</p>').encode())
        for item in notices.parse_list('nankai', self.list_override[notices.SOURCES['nankai']['list_url']])[0]:
            self.detail_override[item['source_url']] = detail
        if variant == 'changed':
            changed = fixture('dlut-detail.html').replace('11月1日'.encode(), '11月2日'.encode())
            for item in notices.parse_list('dlut_panjin', fixture('dlut-list.html'))[0]:
                self.detail_override[item['source_url']] = changed
        if variant == 'unavailable':
            self.error = notices.FetchError('network_timeout')
        elif variant == 'details_unavailable':
            self.detail_override = {}
            self.detail_error = notices.FetchError('network_timeout')

    def get(self, url, *, deadline):
        # The imported transport never uses the network. This allowlist also
        # prevents new/unreviewed URLs silently receiving an unrelated fixture.
        allowed = {'https://' + spec['host'] + '/robots.txt' for spec in notices.SOURCES.values()}
        allowed.update(spec['list_url'] for spec in notices.SOURCES.values())
        for sid, name in [('nankai', 'nankai-list.html'), ('dlut_panjin', 'dlut-list.html')]:
            allowed.update(item['source_url'] for item in notices.parse_list(sid, fixture(name))[0])
        if url not in allowed:
            raise AssertionError('Unexpected fixture URL: ' + url)
        return super().get(url, deadline=deadline)


def main():
    req = json.load(sys.stdin)
    root = Path(os.environ['AP_NOTICE_UI_TEST_DATA']).resolve()
    root.mkdir(parents=True, exist_ok=True)
    epoch = float(req.get('test_epoch', 1791720000))
    observed_at = datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')
    transport = NoticeUITransport(req.get('test_variant', 'normal'))
    with ExitStack() as stack:
        # Patching time.time also fixes the session registry clock. Monotonic
        # deadlines remain real; sleep alone is skipped for fixture performance.
        stack.enter_context(patch.object(notices.time, 'time', return_value=epoch))
        stack.enter_context(patch.object(notices.time, 'sleep', return_value=None))
        stack.enter_context(patch.object(notices, 'utc_now', return_value=observed_at))
        for target in ('socket.socket', 'socket.create_connection', 'socket.getaddrinfo', 'subprocess.Popen'):
            stack.enter_context(patch(target, side_effect=AssertionError('Network/process access is forbidden in this offline bridge')))
        app = ApplyPilotApp(root, runtime=OfflineRuntime)
        app.notice_radar.transport = transport
        body = json.dumps(req['body'], ensure_ascii=False).encode() if 'body' in req else b''
        env = {
            'REQUEST_METHOD': 'POST' if 'body' in req else 'GET', 'PATH_INFO': req['path'],
            'HTTP_HOST': '127.0.0.1:8787', 'HTTP_ORIGIN': 'http://127.0.0.1:8787',
            'HTTP_COOKIE': req.get('cookie', ''), 'HTTP_X_CSRF_TOKEN': req.get('csrf', ''),
            'CONTENT_TYPE': 'application/json', 'CONTENT_LENGTH': str(len(body)),
            'wsgi.input': io.BytesIO(body), 'REMOTE_ADDR': 'offline-notice-ui',
        }
        if req.get('test_cross_site'):
            env['HTTP_SEC_FETCH_SITE'] = 'cross-site'
        response = {}

        def start(status, headers):
            response.update(status=int(status.split()[0]), headers=dict(headers))

        raw = b''.join(app(env, start))
        response['body'] = json.loads(raw)
        response['test_source_calls'] = transport.calls
        response['test_session_fingerprints'] = {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(root.glob('session-*.sqlite3'))
        }
    print(json.dumps(response, ensure_ascii=False))


if __name__ == '__main__':
    main()
