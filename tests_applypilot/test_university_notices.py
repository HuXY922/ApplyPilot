"""Offline fixtures + real SQLite/WSGI; network fixtures are not live evidence."""
from copy import deepcopy
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from applypilot_core import university_notices as notices
from applypilot_core.notice_transport import FetchError, Transport, validate_url, NoRedirect, PinnedHTTPSConnection
from applypilot_core.server import create_app

FIXTURES = Path(__file__).parent / 'fixtures' / 'university_notices'


def fixture(name):
    return (FIXTURES / name).read_bytes()


class FixtureTransport:
    def __init__(self):
        self.calls = []
        self.error = None
        self.robots = None
        self.detail_error = None
        self.list_override = {}
        self.detail_override = {}

    def get(self, url, *, deadline):
        self.calls.append(url)
        if self.error:
            raise self.error
        if url.endswith('/robots.txt'):
            if self.robots is None:
                raise FetchError('http_404')
            return self.robots
        if url in self.list_override:
            return self.list_override[url]
        if url in self.detail_override:
            return self.detail_override[url]
        if url == notices.SOURCES['nankai']['list_url']:
            return fixture('nankai-list.html')
        if url == notices.SOURCES['dlut_panjin']['list_url']:
            return fixture('dlut-list.html')
        if self.detail_error:
            raise self.detail_error
        return fixture('nankai-detail.html' if 'nankai' in url else 'dlut-detail.html')


class NoticeParserTests(unittest.TestCase):
    def test_observed_source_list_structures_and_school_dates(self):
        n, count = notices.parse_list('nankai', fixture('nankai-list.html'))
        self.assertEqual(count, 2)
        self.assertEqual(n[0]['university_published_on'], '2026-10-11')
        self.assertEqual(n[0]['id'], 'nankai:102975')
        d, _ = notices.parse_list('dlut_panjin', fixture('dlut-list.html'))
        self.assertEqual(d[0]['university_published_on'], '2026-10-10')
        self.assertNotIn('2026-10 10', d[0]['title'])
        self.assertEqual(d[1]['source_url'], 'https://pjcareer.dlut.edu.cn/info/1051/48012.htm')

    def test_not_every_title_is_campus_recruitment_or_known_cohort(self):
        value = notices.facts('化工工艺、设备人才储备岗')
        self.assertEqual(value['record_type'], 'university_notice')
        self.assertIsNone(value['company_hint'])
        self.assertEqual(value['explicit_cohorts'], [])
        self.assertEqual(notices.facts('示例公司2027年校园招聘')['explicit_cohorts'], [])
        self.assertEqual(notices.facts('示例公司2027届校园招聘')['explicit_cohorts'], ['2027'])
        self.assertEqual(value['campus_restriction'], 'unknown')

    def test_deadline_full_year_only_never_implied_from_title(self):
        n = notices.parse_detail('nankai', fixture('nankai-detail.html'), '虎牙2027届校园招聘')
        self.assertEqual(n['deadline_status'], 'unknown')
        self.assertIsNone(n['deadline_on'])
        self.assertIn('10月31日', n['deadline_quote'])
        d = notices.parse_detail('dlut_panjin', fixture('dlut-detail.html'), '重庆银行2027届校园招聘公告')
        self.assertEqual(d['deadline_on'], '2026-11-01')
        self.assertEqual(d['explicit_cohorts'], ['2027'])
        self.assertEqual(d['employer_published_on'] if 'employer_published_on' in d else None, None)
        for text in ('截止时间待定', '预计截止2026年11月1日', '报名时间2026年10月1日至2026年11月1日', '截止2026年2月30日', '截至10月31日'):
            self.assertEqual(notices.facts('2027校招', text)['deadline_status'], 'unknown', text)
        self.assertEqual(notices.facts('', '申请截至2026年11月1日')['deadline_on'], '2026-11-01')

    def test_malicious_html_contacts_and_related_content_not_extracted(self):
        raw = '<div id="vsb_content"><script>evil(); 2030届截止2026年1月1日</script><p>2027届</p><p>联系人：张三 13812345678 x@example.com</p><p>&lt;img onerror=evil()&gt; 普通文字</p><a href="x.pdf">附件</a></div><aside>2031届 截止2026年2月1日</aside>'
        value = notices.parse_detail('dlut_panjin', raw.encode(), '测试标题')
        self.assertEqual(value['explicit_cohorts'], ['2027'])
        self.assertNotIn('13812345678', value['summary'])
        self.assertNotIn('x@example.com', value['summary'])
        self.assertNotIn('evil();', value['summary'])
        self.assertEqual(value['detail_status'], 'html_parsed_attachments_unparsed')
        self.assertIsNone(value['deadline_on'])

    def test_non_application_dates_and_truncated_ambiguity_stay_unknown(self):
        for text in ('截至2026年9月1日，已有500名毕业生申请本次招聘', '招聘工作截至2026年9月1日已完成', '网申截至2026年9月1日已有500人报名', '截至2026年9月1日，集团拥有5000名员工', '申请截至2026年11月1日' + '甲' * 150 + '另一个截止2026年12月1日'):
            self.assertEqual(notices.facts('校招', text)['deadline_status'], 'unknown')

    def test_contact_sentences_are_excluded(self):
        self.assertNotIn('张三', notices.clean('示例公司2027届招聘 联系人：张三'))
        self.assertNotIn('Alice', notices.clean('招聘 Contact: Alice Smith'))
        for text in ('联系方式：Alice', '电话：010-1234 5678', 'Contact: Alice Smith, Tel: +1 (212) 555-0123'):
            result = notices.parse_detail('dlut_panjin', ('<div id="vsb_content"><p>' + text + '</p><p>2027届校园招聘</p></div>').encode(), '校招')
            self.assertNotIn('Alice', result['summary'])
            self.assertNotIn('1234', result['summary'])
            self.assertNotIn('555', result['summary'])

    def test_duplicate_urls_and_navigation_are_not_items(self):
        raw = fixture('nankai-list.html') + b'<a href="http://[">malformed</a>'
        items, count = notices.parse_list('nankai', raw + raw)
        self.assertEqual(count, 2)
        self.assertEqual(len(items), 2)
        canonical = notices.canonical('nankai', '/correcruit/content/id/123.html?utm_source=x#foo')
        self.assertEqual(canonical, 'https://career.nankai.edu.cn/correcruit/content/id/123.html')

    def test_date_validation_and_schema_change(self):
        self.assertIsNone(notices.source_date('2026-02-30'))
        self.assertEqual(notices.source_date('10 2026.10'), '2026-10-10')
        with self.assertRaises(FetchError):
            notices.parse_list('nankai', b'<html>blocked</html>')
        with self.assertRaises(FetchError):
            notices.parse_detail('nankai', b'<p>unreviewed selector</p>', 'test')
        with self.assertRaises(FetchError):
            notices.Tree(('<div>' * 90).encode())

    def test_fixed_paths_no_ssrf_query_ports_credentials_or_redirects(self):
        validate_url('https://career.nankai.edu.cn/correcruit/content/id/123.html')
        validate_url('https://pjcareer.dlut.edu.cn/info/1051/48012.htm')
        bad = ('http://career.nankai.edu.cn/correcruit/index.html', 'https://127.0.0.1/robots.txt',
               'https://career.nankai.edu.cn:443/robots.txt', 'https://user@career.nankai.edu.cn/robots.txt',
               'https://career.nankai.edu.cn/robots.txt?url=http://localhost',
               'https://career.nankai.edu.cn/../robots.txt', 'https://career.nankai.edu.cn/correcruit/index/p/2.html',
               'https://pjcareer.dlut.edu.cn/xsfw/qyzp/137.htm')
        for url in bad:
            with self.assertRaises(FetchError, msg=url):
                validate_url(url)
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, '', {}, 'https://evil.test'))
        with patch('socket.getaddrinfo', return_value=[(2, 1, 6, '', ('127.0.0.1', 443))]):
            with self.assertRaises(FetchError):
                PinnedHTTPSConnection('career.nankai.edu.cn').connect()

    def test_parser_deadline_and_repeated_anchor_cpu_bound(self):
        start = notices.time.monotonic()
        self.assertIsNone(notices.CONTACT.search('A' * 100000))
        self.assertLess(notices.time.monotonic() - start, 1)
        self.assertEqual(len(notices.clean('A' * 500000)), 220)
        with self.assertRaisesRegex(FetchError, 'time_budget_exhausted'):
            notices.parse_list('nankai', fixture('nankai-list.html'), deadline=notices.time.monotonic() - 1)
        raw = ('<li><div class="date">10 2026.10</div>' + ''.join('<div class="title1"><a href="/correcruit/content/id/%s.html">招聘</a></div>' % i for i in range(5000)) + '</li>').encode()
        start = notices.time.monotonic()
        result = notices.parse_list('nankai', raw, deadline=start + 2)
        self.assertEqual(len(result[0]), 1)
        self.assertLess(notices.time.monotonic() - start, 2)

    def test_transport_parent_deadline_kills_and_reaps_worker(self):
        child = MagicMock()
        child.communicate.side_effect = subprocess.TimeoutExpired('worker', .01)
        child.poll.return_value = None
        with patch('subprocess.Popen', return_value=child) as create:
            with self.assertRaisesRegex(FetchError, 'network_timeout'):
                Transport(proxy='').get('https://career.nankai.edu.cn/robots.txt', deadline=notices.time.monotonic() + 1)
        self.assertEqual(create.call_args.kwargs['env'], {})
        self.assertIn('-I', create.call_args.args[0])
        child.kill.assert_called_once()
        child.wait.assert_called_once()


class NoticeStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.transport = FixtureTransport()
        self.radar = notices.NoticeRadar(self.temp.name, self.transport)
        self.sleep = patch.object(notices.time, 'sleep', return_value=None)
        self.sleep.start()

    def tearDown(self):
        self.sleep.stop()
        self.temp.cleanup()

    def expire(self):
        with notices.connect(self.radar.path) as db:
            for row in db.execute('SELECT * FROM un_sources').fetchall():
                state = json.loads(row['state_json']); state['next_refresh_epoch'] = 0
                db.execute('UPDATE un_sources SET state_json=? WHERE id=?', (json.dumps(state), row['id']))

    def test_refresh_persistent_sorted_independent_public_cache(self):
        state = self.radar.refresh({})
        self.assertEqual(state['total'], 4)
        self.assertEqual(state['notices'][0]['university_published_on'], '2026-10-11')
        self.assertTrue(all(n['record_type'] == 'university_notice' for n in state['notices']))
        self.assertTrue(all(n['first_seen_at'] == n['last_seen_at'] for n in state['notices']))
        self.assertTrue(all(n['employer_published_on'] is None for n in state['notices']))
        self.assertEqual(state['run']['requests'], 8)
        self.assertEqual(notices.NoticeRadar(self.temp.name).state()['total'], 4)
        with notices.connect(self.radar.path) as db:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue(all(t.startswith('un_') for t in tables))

    def test_robots_404_and_refresh_cooldown_persist_across_instances(self):
        self.radar.refresh({'sources': ['nankai']})
        count = len(self.transport.calls)
        other = notices.NoticeRadar(self.temp.name, self.transport)
        result = other.refresh({'sources': ['nankai']})
        self.assertEqual(result['run']['sources'][0]['status'], 'cooldown')
        self.assertEqual(count, len(self.transport.calls))
        self.assertEqual(result['sources'][0]['robots_status'], 'http_404_no_rules_found')
        self.assertNotIn('robots_text', result['sources'][0])
        self.assertEqual(len(result['runs']), 1)

    def test_repeat_notice_observations_and_changed_content_history(self):
        first = self.radar.refresh({'sources': ['dlut_panjin']})
        item = first['notices'][0]
        self.expire(); self.radar.refresh({'sources': ['dlut_panjin']})
        self.assertEqual(self.radar.state()['total'], 2)
        self.assertEqual(self.radar.events(item['id'])['events'][0]['event_type'], 'observed')
        self.transport.detail_override[item['source_url']] = fixture('dlut-detail.html').replace('11月1日'.encode(), '11月2日'.encode())
        self.expire(); result = self.radar.refresh({'sources': ['dlut_panjin']})
        current = next(n for n in result['notices'] if n['id'] == item['id'])
        self.assertEqual(current['first_seen_at'], item['first_seen_at'])
        self.assertEqual(current['deadline_on'], '2026-11-02')
        event = self.radar.events(item['id'])['events'][0]
        self.assertEqual(event['event_type'], 'content_changed')
        self.assertEqual(event['changes']['deadline_on']['before'], '2026-11-01')

    def test_failure_never_clears_cached_notices(self):
        before = self.radar.refresh({})['notices']
        self.expire(); self.transport.error = FetchError('network_timeout')
        result = self.radar.refresh({})
        self.assertEqual(result['notices'], before)
        self.assertEqual(result['run']['status'], 'unavailable')

    def test_403_is_persistently_paused_no_login_retry(self):
        self.transport.error = FetchError('http_403')
        self.radar.refresh({'sources': ['nankai']})
        count = len(self.transport.calls)
        self.expire(); self.transport.error = None
        result = notices.NoticeRadar(self.temp.name, self.transport).refresh({'sources': ['nankai']})
        self.assertEqual(len(self.transport.calls), count)
        self.assertEqual(result['run']['sources'][0]['status'], 'paused')

    def test_429_respects_long_retry_after_and_preserves_other_source(self):
        self.transport.detail_error = FetchError('http_429', '86400')
        result = self.radar.refresh({})
        self.assertEqual(result['total'], 4)
        self.assertEqual(result['run']['status'], 'partial')
        for source in result['sources']:
            self.assertGreater(source['next_refresh_epoch'], notices.time.time() + 86000)
            self.assertFalse(source.get('paused', False))
        count = len(self.transport.calls)
        self.radar.refresh({})
        self.assertEqual(len(self.transport.calls), count)

    def test_robots_disallow_or_delay_fails_closed(self):
        for body, reason in [(b'User-agent: *\nDisallow: /', 'robots_disallowed'), (b'User-agent: *\nCrawl-delay: 10\n', 'robots_rate_requires_operator_review')]:
            with tempfile.TemporaryDirectory() as folder:
                transport = FixtureTransport(); transport.robots = body
                result = notices.NoticeRadar(folder, transport).refresh({'sources': ['nankai']})
                self.assertEqual(result['total'], 0)
                self.assertEqual(result['run']['sources'][0]['reason'], reason)
                self.assertEqual(len(transport.calls), 1)

    def test_partial_details_preserve_old_results_with_explicit_error(self):
        self.radar.refresh({'sources': ['dlut_panjin']})
        self.expire(); self.transport.detail_error = FetchError('network_timeout')
        result = self.radar.refresh({'sources': ['dlut_panjin']})
        self.assertEqual(result['run']['status'], 'partial')
        self.assertTrue(all(n['deadline_on'] == '2026-11-01' and n['last_detail_error'] == 'network_timeout' for n in result['notices']))

    def test_cross_school_company_hint_never_merges_notice_identity(self):
        n = notices.parse_list('nankai', fixture('nankai-list.html'))[0][1]
        d = notices.parse_list('dlut_panjin', fixture('dlut-list.html'))[0][1]
        n['title'] = d['title'] = '示例公司2027届招聘'
        with notices.connect(self.radar.path) as db:
            self.radar._observe(db, n, '2026-10-11T00:00:00Z')
            self.radar._observe(db, d, '2026-10-11T00:00:00Z')
        result = self.radar.state()
        self.assertEqual(result['total'], 2)
        self.assertTrue(all(n['related_school_count'] == 2 for n in result['notices']))

    def test_capacity_failure_keeps_existing_rows(self):
        self.radar.refresh({'sources': ['nankai']})
        before = self.radar.state()['notices']; self.expire()
        with patch.object(notices, 'MAX_EVENTS', 1):
            result = self.radar.refresh({'sources': ['nankai']})
        self.assertEqual(result['notices'], before)
        self.assertEqual(result['run']['sources'][0]['reason'], 'storage_capacity_reached')

    def test_lease_cleanup_error_never_poison_process_lock(self):
        real_connect = notices.connect
        class Proxy:
            def __init__(inner, path):
                inner.db = real_connect(path)
            def __enter__(inner):
                inner.db.__enter__()
                return inner
            def execute(inner, sql, *args):
                if sql.startswith('UPDATE un_lease SET until_epoch=0'):
                    raise sqlite3.OperationalError('test only cleanup failure')
                return inner.db.execute(sql, *args)
            def __exit__(inner, *args):
                try:
                    return inner.db.__exit__(*args)
                finally:
                    inner.db.close()
        with patch.object(notices, 'connect', side_effect=Proxy):
            with self.assertRaises(sqlite3.OperationalError):
                self.radar.refresh({})
        self.assertFalse(notices._REFRESH_LOCK.locked())

    def test_invalid_user_supplied_sources_urls_and_concurrent_lease(self):
        for payload in ({'url': 'http://localhost'}, {'sources': [[]]}, {'sources': ['unknown']}, {'sources': ['nankai', 'nankai']}, {'sources': []}):
            with self.assertRaises(notices.DomainError):
                self.radar.refresh(payload)
        with notices.connect(self.radar.path) as db:
            db.execute('UPDATE un_lease SET until_epoch=?', (notices.time.time() + 100,))
        with self.assertRaises(notices.DomainError) as result:
            self.radar.refresh({})
        self.assertEqual(result.exception.code, 'notice_refresh_busy')
        self.assertEqual(self.transport.calls, [])


class NoticeWSGITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app({'APPLYPILOT_DATA_DIR': self.temp.name})
        self.app.notice_radar.transport = FixtureTransport()
        self.cookie, self.csrf = '', ''
        state, headers = self.request('/api/state')
        self.cookie, self.csrf = headers['Set-Cookie'].split(';')[0], state['csrf']

    def tearDown(self):
        self.temp.cleanup()

    def request(self, path, method='GET', data=None, expected=200, **extra):
        raw = json.dumps(data or {}).encode()
        env = {'REQUEST_METHOD': method, 'PATH_INFO': path, 'HTTP_HOST': '127.0.0.1:8787',
               'HTTP_ORIGIN': 'http://127.0.0.1:8787', 'HTTP_COOKIE': self.cookie, 'HTTP_X_CSRF_TOKEN': self.csrf,
               'CONTENT_TYPE': 'application/json', 'CONTENT_LENGTH': str(len(raw)), 'wsgi.input': io.BytesIO(raw)}
        env.update(extra)
        captured = []
        result = b''.join(self.app(env, lambda status, headers: captured.append((int(status.split()[0]), dict(headers)))))
        result = json.loads(result)
        self.assertEqual(captured[0][0], expected, result)
        return result, captured[0][1]

    def test_end_to_end_notice_api_no_jobs_applications_profile_mutation(self):
        before = self.request('/api/state')[0]
        session_files = list(Path(self.temp.name).glob('session-*.sqlite3'))
        before_bytes = {p.name: p.read_bytes() for p in session_files}
        with patch.object(notices.time, 'sleep', return_value=None):
            result = self.request('/api/notices/refresh', 'POST', {})[0]
        self.assertEqual(result['total'], 4)
        self.assertEqual(self.request('/api/state')[0]['jobs'], before['jobs'])
        self.assertEqual(self.request('/api/state')[0]['profile'], before['profile'])
        self.assertEqual({p.name: p.read_bytes() for p in session_files}, before_bytes)
        # Initialization and migration are repeatable; original DB user_version
        # and all original rows remain intact.
        other = create_app({'APPLYPILOT_DATA_DIR': self.temp.name})
        self.assertEqual(other.notice_radar.state()['total'], 4)
        result = self.request('/api/notices/dlut_panjin:48012/events')[0]
        self.assertEqual(result['events'][0]['event_type'], 'discovered')
        self.request('/api/notices/nankai:99999/events', expected=404)

    def test_api_reuses_same_origin_csrf_session_controls(self):
        self.request('/api/notices', expected=401, HTTP_COOKIE='')
        self.request('/api/notices', expected=403, HTTP_SEC_FETCH_SITE='cross-site')
        self.request('/api/notices/refresh', 'POST', expected=403, HTTP_X_CSRF_TOKEN='')
        self.request('/api/notices/refresh', 'POST', {'url': 'https://evil.test'}, expected=400)
        self.assertEqual(self.app.notice_radar.transport.calls, [])


if __name__ == '__main__':
    unittest.main()
