"""Public university notices, deliberately independent of jobs and applications.

Only fixed reviewed sources are fetched, manually and within strict bounds.
University publication dates are not employer publication dates. Remote text is
never treated as instructions or sent to a model. No applicant data is read.
"""
from __future__ import annotations
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sqlite3
import threading
import time
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

from .notice_transport import FetchError, Transport, validate_url
from .store import DomainError, require

SOURCES = {
    'nankai': {'id': 'nankai', 'name': '南开大学就业指导中心', 'host': 'career.nankai.edu.cn',
               'list_url': 'https://career.nankai.edu.cn/correcruit/index.html', 'page_size': 20,
               'policy_reviewed_at': '2026-10-11', 'detail_ids': (), 'detail_classes': ('zpnr',), 'detail_enabled': True},
    'dlut_panjin': {'id': 'dlut_panjin', 'name': '大连理工大学盘锦校区就业网', 'host': 'pjcareer.dlut.edu.cn',
                    'list_url': 'https://pjcareer.dlut.edu.cn/xsfw/qyzp.htm', 'page_size': 10,
                    'policy_reviewed_at': '2026-10-11', 'detail_ids': ('vsb_content', 'vsb_content_2'), 'detail_classes': ('v_news_content',), 'detail_enabled': True},
}
MAX_LIST_ITEMS = 20
MAX_DETAIL_ITEMS = 3
MAX_SECONDS = 35
MIN_REFRESH_SECONDS = 15 * 60
MAX_REQUESTS = 10
MAX_NOTICES, MAX_EVENTS, MAX_RUNS = 4000, 20000, 2000
_REFRESH_LOCK = threading.Lock()
IGNORE = frozenset(('script', 'style', 'iframe', 'object', 'svg', 'form', 'template', 'noscript'))
CONTACT = re.compile(r'联系(?:人|方式|电话|邮箱)?|邮箱|电子邮件|咨询电话|电话|手机|微信|传真|QQ|contact|e-?mail|telephone|phone|\btel\b|\bfax\b|[\w.+-]{1,64}@[\w.-]{1,253}\.[A-Za-z]{2,63}|(?:\+?86[- ]?)?1[3-9]\d{9}|(?:0\d{2,3}[- —]?)?\d{7,8}|(?<!\d)(?:\+\d{1,3}[-.\s]?)?(?:\(?\d{2,4}\)?[-.\s])(?:\d[-.\s]?){6,10}(?!\d)', re.I)


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def clean(text, limit=220):
    text = re.sub(r'[\x00-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]', ' ', str(text)[:max(1024, limit * 4)])
    text = re.sub(r'\s+', ' ', text).strip()
    contact = CONTACT.search(text)
    if contact:
        text = text[:contact.start()].rstrip(' ,，;；:：') + ' [联系方式已省略]'
    return text[:limit]


class Node:
    def __init__(self, tag='', attrs=None, parent=None):
        self.tag, self.attrs, self.parent = tag, dict(attrs or []), parent
        self.children = []

    def text(self, limit=100001):
        # Iterative bounded flattening avoids repeated large intermediate
        # strings on deeply nested attacker-controlled markup.
        chunks, stack, size = [], [self], 0
        while stack and size < limit:
            item = stack.pop()
            if isinstance(item, Node):
                if item.tag not in IGNORE:
                    stack.extend(reversed(item.children))
            else:
                value = item[:limit - size]
                chunks.append(value)
                size += len(value) + 1
        return ' '.join(chunks)[:limit]

    def walk(self):
        if self.tag in IGNORE:
            return
        yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.walk()


class Tree(HTMLParser):
    def __init__(self, raw, deadline=None):
        self.deadline = deadline
        super().__init__(convert_charrefs=True)
        self.root = Node()
        self.stack = [self.root]
        self.count = 0
        # The reviewed pages are UTF-8. Reject undecodable pages instead of
        # silently guessing a charset and misreading dates or requirements.
        try:
            self.feed(raw.decode('utf-8-sig'))
        except (UnicodeError, RecursionError):
            raise FetchError('html_encoding_or_depth') from None

    def handle_starttag(self, tag, attrs):
        self.count += 1
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise FetchError('time_budget_exhausted')
        if self.count > 30000 or len(self.stack) > 80:
            raise FetchError('html_too_complex')
        node = Node(tag, attrs, self.stack[-1])
        self.stack[-1].children.append(node)
        if tag not in ('area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'):
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise FetchError('time_budget_exhausted')
        self.stack[-1].children.append(data)


def canonical(source_id, href):
    source = SOURCES[source_id]
    try:
        p = urlsplit(urljoin(source['list_url'], href.strip()))
    except (ValueError, TypeError):
        raise FetchError('destination_denied') from None
    # Tracking fragments/queries on the known notice path are discarded for
    # identity, but credentials, other hosts/ports and protocols are rejected.
    if p.hostname != source['host'] or p.netloc != p.hostname or p.scheme not in ('https', 'http'):
        raise FetchError('destination_denied')
    url = urlunsplit(('https', p.netloc, p.path, '', ''))
    validate_url(url)
    if url == source['list_url'] or p.path == '/robots.txt':
        raise FetchError('not_notice_url')
    return url


def source_date(value):
    value = re.sub(r'\s+', ' ', value)
    match = re.search(r'(?<!\d)(20\d{2})\s*[.年/-]\s*(\d{1,2})\s*[.月/-]\s*(\d{1,2})(?:日)?(?!\d)', value)
    if match:
        parts = match.groups()
    else:
        # Both reviewed lists show day separately before YYYY.MM / YYYY-MM.
        match = re.search(r'(?<!\d)(\d{1,2})\s+(20\d{2})[.\-/](\d{1,2})(?!\d)', value)
        if not match:
            return None
        parts = (match[2], match[3], match[1])
    try:
        return date(*map(int, parts)).isoformat()
    except ValueError:
        return None


def parse_list(source_id, raw, deadline=None):
    root = Tree(raw, deadline).root
    rows, seen, processed_rows = [], set(), set()
    for anchor in root.walk():
        if deadline is not None and time.monotonic() >= deadline:
            raise FetchError('time_budget_exhausted')
        if anchor.tag != 'a' or not anchor.attrs.get('href'):
            continue
        try:
            url = canonical(source_id, anchor.attrs['href'])
        except FetchError:
            continue
        if url in seen:
            continue
        if source_id == 'dlut_panjin':
            if not re.fullmatch(r'line_u5_[0-9]+', anchor.attrs.get('id', '')):
                continue
            row = anchor
            headings = [n for n in row.walk() if n.tag == 'h3']
            if not headings:
                continue
            title = clean(headings[0].text(), 220)
        else:
            if not anchor.parent or 'title1' not in anchor.parent.attrs.get('class', '').split():
                continue
            row = anchor.parent
            for _ in range(5):
                if row is None or row.tag == 'li':
                    break
                row = row.parent
            if row is None or row.tag != 'li':
                continue
            title = clean(anchor.attrs.get('title') or anchor.text(), 220)
        if id(row) in processed_rows:
            continue
        processed_rows.add(id(row))
        date_nodes = [n for n in row.walk() if set(n.attrs.get('class', '').split()).intersection(('date', 'time-box'))]
        published = source_date(date_nodes[0].text()) if date_nodes else None
        if not title or not published:
            continue
        seen.add(url)
        ident = urlsplit(url).path.rsplit('/', 1)[-1].split('.')[0]
        rows.append({'id': source_id + ':' + ident, 'source_id': source_id, 'source_notice_id': ident,
                     'source_url': url, 'title': title, 'university_published_on': published,
                     'employer_published_on': None})
        if len(rows) >= SOURCES[source_id]['page_size']:
            break
    if not rows:
        raise FetchError('list_schema_changed_or_empty')
    rows.sort(key=lambda row: (row['university_published_on'], row['id']), reverse=True)
    return rows[:SOURCES[source_id]['page_size']], len(rows)


def facts(title, body=''):
    text = title + '\n' + body
    cohorts = sorted(set(re.findall(r'(?<!\d)(20\d{2})\s*届', text)))
    company = None
    # This is a title-derived lead, not verified employer identity. Distinct
    # university notices/batches are never merged on this value.
    match = re.match(r'^([\u4e00-\u9fffA-Za-z0-9（）()· &.-]{2,65}?)(?:20\d{2}(?:年|届)?|校园招聘|秋季招聘)', title)
    if match:
        company = match[1].strip(' ·-')
    batch = title if re.search(r'校(?:园)?招|秋招|春招|实习|招聘', title) else None
    deadline_quote, deadline_on, deadline_status = None, None, 'unknown'
    sentences = re.split(r'[\n。；;！？]', body)
    candidates = [s.strip() for s in sentences if re.search(r'截止|截至|报名时间|申请时间|网申时间|简历投递|投递时间', s) and not CONTACT.search(s)]
    if candidates:
        deadline_quote = clean(candidates[0], 140)
        evidence = candidates[0]
        # Only one unambiguous full-year date adjacent to a deadline marker.
        date_matches = list(re.finditer(r'(20\d{2})[年./-](\d{1,2})[月./-](\d{1,2})(?:日)?', evidence))
        dates = [match.groups() for match in date_matches]
        deadline_prefix = (re.search(r'(?:(?:报名|网申|申请|简历(?:投递)?|投递)(?:时间)?\s*(?:截止|截至)(?:时间|日期)?|截止时间|截止日期)\s*(?:为|是|于)?\s*[:：]?\s*$', evidence[:date_matches[0].start()]) if date_matches else None)
        deadline_suffix = (re.fullmatch(r'\s*(?:\d{1,2}[:：时点]\d{1,2}(?:分)?(?:[:：]\d{1,2})?)?\s*(?:前|止)?\s*(?:[（(]北京时间[）)])?\s*', evidence[date_matches[0].end():]) if date_matches else None)
        if (len(candidates) == 1 and len(dates) == 1 and len(evidence) <= 140
                and re.search(r'截止|截至', evidence)
                and deadline_prefix and deadline_suffix
                and not re.search(r'另行|待定|预计|暂定|各|部分|[起至到]|延期|统计|员工|营业|增长', re.sub(r'截止|截至', '', evidence))):
            try:
                deadline_on = date(*map(int, dates[0])).isoformat()
                deadline_status = 'explicit_date'
            except ValueError:
                pass
    graduation_window = next((clean(s, 140) for s in sentences if '毕业' in s and re.search(r'20\d{2}年', s) and not CONTACT.search(s)), None)
    return {'graduation_window_quote': graduation_window, 'company_hint': company, 'company_evidence': 'title_only' if company else 'unknown',
            'batch_text': batch, 'explicit_cohorts': cohorts, 'deadline_on': deadline_on,
            'deadline_quote': deadline_quote, 'deadline_status': deadline_status,
            'campus_restriction': 'unknown', 'availability': 'unknown', 'record_type': 'university_notice'}


def parse_detail(source_id, raw, title, deadline=None):
    source = SOURCES[source_id]
    root = Tree(raw, deadline).root
    candidates = [n for n in root.walk() if n.attrs.get('id') in source['detail_ids']
                  or set(n.attrs.get('class', '').split()).intersection(source['detail_classes'])]
    if not candidates:
        raise FetchError('detail_schema_changed')
    # Prefer the first reviewed body container, not nested duplicates; avoids
    # repeatedly walking attacker-controlled trees to compare text lengths.
    container = candidates[0]
    # Insert paragraph boundaries to keep date evidence local and short.
    paragraphs, size = [], 0
    for node in container.walk():
        if node.tag not in ('p', 'li'):
            continue
        ancestor = node.parent
        nested_paragraph = False
        while ancestor and ancestor is not container:
            if ancestor.tag in ('p', 'li'):
                nested_paragraph = True
                break
            ancestor = ancestor.parent
        if nested_paragraph:
            continue
        part = node.text(max(1, 100001 - size))
        paragraphs.append(part)
        size += len(part) + 1
        if size > 100000:
            break
    body = '\n'.join(paragraphs) if paragraphs else container.text()
    truncated = len(body) > 100000 or size > 100000
    body = body[:100000]
    if deadline is not None and time.monotonic() >= deadline:
        raise FetchError('time_budget_exhausted')
    parts = [clean(s, 180) for s in re.split(r'[\n。；;！？]', body) if s.strip() and not CONTACT.search(s)]
    summary = '。'.join(parts)[:220]
    attachments = any(n.tag == 'a' and re.search(r'\.(pdf|docx?|jpe?g|png)(?:[?#]|$)', n.attrs.get('href', ''), re.I) for n in container.walk())
    detail_date = None
    if source_id == 'nankai':
        date_nodes = [n for n in root.walk() if 'date' in n.attrs.get('class', '').split() and '发布时间' in n.text()]
        detail_date = source_date(date_nodes[0].text()) if date_nodes else None
    extracted = facts(title, body)
    if truncated:
        extracted.update(deadline_status='unknown', deadline_on=None)
    return {**extracted, 'summary': summary, 'detail_university_published_on': detail_date,
            'detail_text_truncated': truncated,
            'detail_status': 'html_parsed_attachments_unparsed' if attachments else 'html_parsed',
            'content_hash': hashlib.sha256(body.encode()).hexdigest()}


def connect(path):
    db = sqlite3.connect(path, timeout=2)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('PRAGMA busy_timeout=2000')
    page_size = db.execute('PRAGMA page_size').fetchone()[0]
    db.execute('PRAGMA max_page_count=' + str(32 * 1024 * 1024 // page_size))
    return db


class NoticeRadar:
    def __init__(self, data_root, transport=None):
        self.path = Path(data_root) / 'university-notices.sqlite3'
        self.transport = transport or Transport()
        with connect(self.path) as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS un_notices (
              id TEXT PRIMARY KEY, source_id TEXT NOT NULL, source_url TEXT NOT NULL,
              university_published_on TEXT, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
              last_detail_seen_at TEXT, content_hash TEXT NOT NULL, data_json TEXT NOT NULL,
              UNIQUE(source_id, source_url));
            CREATE INDEX IF NOT EXISTS un_notices_date ON un_notices(university_published_on DESC);
            CREATE TABLE IF NOT EXISTS un_events (
              event_id INTEGER PRIMARY KEY, notice_id TEXT NOT NULL REFERENCES un_notices(id),
              observed_at TEXT NOT NULL, event_type TEXT NOT NULL, content_hash TEXT NOT NULL, changes_json TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS un_events_notice ON un_events(notice_id,event_id);
            CREATE TABLE IF NOT EXISTS un_sources (id TEXT PRIMARY KEY, state_json TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS un_runs (run_id INTEGER PRIMARY KEY, started_at TEXT NOT NULL,
              finished_at TEXT, data_json TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS un_lease (id INTEGER PRIMARY KEY CHECK(id=1), until_epoch REAL NOT NULL);
            INSERT OR IGNORE INTO un_lease VALUES(1,0);
            ''')

    def _source_state(self, db, source_id):
        row = db.execute('SELECT state_json FROM un_sources WHERE id=?', (source_id,)).fetchone()
        return json.loads(row[0]) if row else {'status': 'never_refreshed', 'next_refresh_epoch': 0}

    def _save_source(self, db, source_id, state):
        db.execute('INSERT INTO un_sources VALUES(?,?) ON CONFLICT(id) DO UPDATE SET state_json=excluded.state_json', (source_id, dump(state)))

    def state(self):
        with connect(self.path) as db:
            rows = db.execute('SELECT * FROM un_notices ORDER BY university_published_on DESC,last_seen_at DESC,id DESC LIMIT 200').fetchall()
            total = db.execute('SELECT COUNT(*) FROM un_notices').fetchone()[0]
            sources = [{**{k: v for k, v in spec.items() if k not in ('detail_ids', 'detail_classes', 'host', 'detail_enabled')},
                        **{k: v for k, v in self._source_state(db, sid).items() if k != 'robots_text'}} for sid, spec in SOURCES.items()]
            runs = db.execute('SELECT data_json FROM un_runs ORDER BY run_id DESC LIMIT 10').fetchall()
            notices = [self._public_notice(row) for row in rows]
            company_counts = {}
            for item in notices:
                clue = item.get('company_hint')
                if clue:
                    company_counts.setdefault(clue, set()).add(item['source_id'])
            for item in notices:
                item['related_school_count'] = len(company_counts.get(item.get('company_hint'), set()))
            return {'notices': notices, 'total': total, 'visible_limit': 200, 'sources': sources,
                    'runs': [json.loads(r[0]) for r in runs], 'shared_public_cache': True,
                    'scope': {'list_pages_per_source': 1, 'max_detail_pages_per_source': MAX_DETAIL_ITEMS,
                              'max_requests': MAX_REQUESTS, 'max_seconds': MAX_SECONDS,
                              'refresh_interval_seconds': MIN_REFRESH_SECONDS, 'automatic_refresh': False}}

    @staticmethod
    def _public_notice(row):
        return {**json.loads(row['data_json']), 'first_seen_at': row['first_seen_at'],
                'last_seen_at': row['last_seen_at'], 'last_detail_seen_at': row['last_detail_seen_at']}

    def events(self, notice_id):
        with connect(self.path) as db:
            require(db.execute('SELECT 1 FROM un_notices WHERE id=?', (notice_id,)).fetchone(), 'not_found', '公告不存在。', 404)
            rows = db.execute('SELECT * FROM un_events WHERE notice_id=? ORDER BY event_id DESC LIMIT 100', (notice_id,)).fetchall()
            return {'events': [{**{k: v for k, v in dict(row).items() if k != 'changes_json'}, 'changes': json.loads(row['changes_json'])} for row in rows], 'limit': 100}

    def _observe(self, db, item, observed_at, detail=None, detail_error=None):
        old = db.execute('SELECT * FROM un_notices WHERE id=?', (item['id'],)).fetchone()
        prior = json.loads(old['data_json']) if old else {}
        data = {**(prior or facts(item['title'])), **item}
        if old and prior.get('title') != item['title'] and not detail:
            data.update(facts(item['title']), summary='', detail_status='not_fetched')
        if detail:
            data.update(detail)
        elif not prior:
            data.update(summary='', detail_status='not_fetched' if SOURCES[item['source_id']]['detail_enabled'] else 'list_only')
        if detail_error:
            data['last_detail_error'] = detail_error
        elif detail:
            data.pop('last_detail_error', None)
        content_hash = data.pop('content_hash', None) or (old['content_hash'] if old else hashlib.sha256(item['title'].encode()).hexdigest())
        changed = {key: {'before': prior.get(key), 'after': value} for key, value in data.items() if prior.get(key) != value}
        detail_at = observed_at if detail else old['last_detail_seen_at'] if old else None
        if not old and db.execute('SELECT COUNT(*) FROM un_notices').fetchone()[0] >= MAX_NOTICES:
            raise FetchError('storage_capacity_reached')
        if db.execute('SELECT COUNT(*) FROM un_events').fetchone()[0] >= MAX_EVENTS:
            raise FetchError('storage_capacity_reached')
        db.execute('''INSERT INTO un_notices VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
          source_url=excluded.source_url,university_published_on=excluded.university_published_on,
          last_seen_at=excluded.last_seen_at,last_detail_seen_at=excluded.last_detail_seen_at,
          content_hash=excluded.content_hash,data_json=excluded.data_json''',
                   (item['id'], item['source_id'], item['source_url'], item['university_published_on'],
                    old['first_seen_at'] if old else observed_at, observed_at, detail_at, content_hash, dump(data)))
        event = 'discovered' if not old else 'content_changed' if changed or old['content_hash'] != content_hash else 'observed'
        # Events preserve metadata changes and hashes, never entire source pages.
        db.execute('INSERT INTO un_events(notice_id,observed_at,event_type,content_hash,changes_json) VALUES(?,?,?,?,?)',
                   (item['id'], observed_at, event, content_hash, dump(changed)))
        return event

    def _robots(self, sid, state, fetch):
        if state.get('robots_checked_epoch', 0) + 86400 <= time.time():
            try:
                raw = fetch('https://' + SOURCES[sid]['host'] + '/robots.txt')
                text = raw.decode('utf-8-sig')
                if '<html' in text[:1000].lower() or len(text) > 65536:
                    raise FetchError('robots_invalid')
                state['robots_text'] = text
                state['robots_status'] = 'rules_read'
            except FetchError as error:
                if error.code not in ('http_404', 'http_410'):
                    raise
                state['robots_text'] = ''
                state['robots_status'] = error.code + '_no_rules_found'
            state['robots_checked_epoch'] = time.time()
        robots = RobotFileParser()
        robots.parse(state.get('robots_text', '').splitlines())
        return robots

    def refresh(self, data):
        require(not set(data) - {'sources'}, 'invalid_request', '仅支持选择已配置的高校来源。')
        ids = data.get('sources', list(SOURCES))
        require(isinstance(ids, list) and 0 < len(ids) <= len(SOURCES) and all(isinstance(s, str) and s in SOURCES for s in ids)
                and len(set(ids)) == len(ids), 'invalid_sources', '请选择已配置的高校来源。')
        require(_REFRESH_LOCK.acquire(blocking=False), 'notice_refresh_busy', '校招资讯正在刷新，请稍后读取结果。', 409)
        lease = False
        started, start_epoch = utc_now(), time.time()
        deadline = time.monotonic() + MAX_SECONDS
        run = {'started_at': started, 'status': 'running', 'sources': [], 'requests': 0}
        try:
            with connect(self.path) as db:
                db.execute('BEGIN IMMEDIATE')
                require(db.execute('SELECT until_epoch FROM un_lease WHERE id=1').fetchone()[0] < start_epoch,
                        'notice_refresh_busy', '校招资讯正在刷新，请稍后读取结果。', 409)
                eligible = [sid for sid in ids if not (self._source_state(db, sid).get('paused')
                            or self._source_state(db, sid).get('next_refresh_epoch', 0) > start_epoch)]
                if eligible:
                    require(db.execute('SELECT COUNT(*) FROM un_runs').fetchone()[0] < MAX_RUNS,
                            'notice_capacity', '资讯历史存储已到维护阈值，请联系维护者。', 503)
                db.execute('UPDATE un_lease SET until_epoch=? WHERE id=1', (start_epoch + MAX_SECONDS + 10,))
                lease = True
            for sid in ids:
                with connect(self.path) as db:
                    state = self._source_state(db, sid)
                result = {'source_id': sid, 'pages_read': 0, 'detail_pages_read': 0, 'notices_seen': 0,
                          'scope': 'latest_list_page_only', 'max_list_pages': 1, 'max_detail_pages': MAX_DETAIL_ITEMS,
                          'list_url': SOURCES[sid]['list_url'], 'failures': []}
                run['sources'].append(result)
                if state.get('paused') or state.get('next_refresh_epoch', 0) > time.time():
                    result.update(status='paused' if state.get('paused') else 'cooldown', reason=state.get('reason', 'minimum_refresh_interval'))
                    continue
                # One source cannot consume the other source's entire time budget.
                source_deadline = min(deadline, time.monotonic() + MAX_SECONDS / len(ids))
                state.update(last_attempt_at=started, next_refresh_epoch=time.time() + MIN_REFRESH_SECONDS)
                last_fetch = [0.0]
                def fetch(url):
                    if run['requests'] >= MAX_REQUESTS:
                        raise FetchError('request_budget_exhausted')
                    if time.monotonic() >= source_deadline:
                        raise FetchError('time_budget_exhausted')
                    wait = max(0, 1.0 - (time.monotonic() - last_fetch[0]))
                    if time.monotonic() + wait >= source_deadline:
                        raise FetchError('time_budget_exhausted')
                    if wait:
                        time.sleep(wait)
                    run['requests'] += 1
                    last_fetch[0] = time.monotonic()
                    return self.transport.get(url, deadline=source_deadline)
                try:
                    robots = self._robots(sid, state, fetch)
                    if not robots.can_fetch('ApplyPilot', SOURCES[sid]['list_url']):
                        raise FetchError('robots_disallowed')
                    # A stated crawl delay/request rate governs this reader too.
                    delay = robots.crawl_delay('ApplyPilot') or robots.crawl_delay('*') or 0
                    rate = robots.request_rate('ApplyPilot') or robots.request_rate('*')
                    delay = max(delay, rate.seconds / rate.requests if rate and rate.requests else 0)
                    if delay > 0:
                        raise FetchError('robots_rate_requires_operator_review')
                    raw = fetch(SOURCES[sid]['list_url'])
                    items, found = parse_list(sid, raw, deadline=source_deadline)
                    result.update(pages_read=1, notices_seen=len(items), list_candidates=found,
                                  list_limit=SOURCES[sid]['page_size'], more_pages_not_read=True,
                                  university_date_range={'newest': items[0]['university_published_on'], 'oldest': items[-1]['university_published_on']})
                    details = {}
                    with connect(self.path) as db:
                        known = {r['id']: r['last_detail_seen_at'] for r in db.execute('SELECT id,last_detail_seen_at FROM un_notices WHERE source_id=?', (sid,))}
                    # Rotate detail work across visible notices, prioritizing never
                    # parsed entries. Do not imply every list item was read fully.
                    selected = sorted(items, key=lambda item: (known.get(item['id']) or '', -int(item['university_published_on'].replace('-', ''))))[:MAX_DETAIL_ITEMS] if SOURCES[sid]['detail_enabled'] else []
                    result['detail_support'] = 'html' if SOURCES[sid]['detail_enabled'] else 'list_only_pending_selector_review'
                    for item in selected:
                        if not robots.can_fetch('ApplyPilot', item['source_url']):
                            details[item['id']] = (None, 'robots_disallowed')
                            result['failures'].append({'notice_id': item['id'], 'code': 'robots_disallowed'})
                            continue
                        try:
                            details[item['id']] = (parse_detail(sid, fetch(item['source_url']), item['title'], deadline=source_deadline), None)
                            result['detail_pages_read'] += 1
                        except FetchError as error:
                            details[item['id']] = (None, error.code)
                            result['failures'].append({'notice_id': item['id'], 'code': error.code})
                            if error.code in ('http_401', 'http_403', 'http_429'):
                                self._pause(state, error)
                                break
                    with connect(self.path) as db:
                        for item in items:
                            detail, error = details.get(item['id'], (None, None))
                            self._observe(db, item, utc_now(), detail, error)
                    result['status'] = 'partial' if result['failures'] else 'ok'
                    state.update(status=result['status'], last_success_at=utc_now(), last_result=result)
                    if not state.get('paused') and not any(f['code'] == 'http_429' for f in result['failures']):
                        state.pop('reason', None)
                except (FetchError, UnicodeError) as error:
                    code = error.code if isinstance(error, FetchError) else 'robots_encoding_invalid'
                    result.update(status='failed', reason=code)
                    state.update(status='failed', reason=code, last_result=result)
                    self._pause(state, error)
                finally:
                    with connect(self.path) as db:
                        self._save_source(db, sid, state)
            run['finished_at'] = utc_now()
            statuses = [r['status'] for r in run['sources']]
            run['status'] = 'ok' if all(s == 'ok' for s in statuses) else 'partial' if any(s in ('ok', 'partial') for s in statuses) else 'unavailable'
            if run['requests']:
                with connect(self.path) as db:
                    db.execute('INSERT INTO un_runs(started_at,finished_at,data_json) VALUES(?,?,?)', (started, run['finished_at'], dump(run)))
            return {**self.state(), 'run': run}
        finally:
            try:
                if lease:
                    with connect(self.path) as db:
                        db.execute('UPDATE un_lease SET until_epoch=0 WHERE id=1 AND until_epoch=?', (start_epoch + MAX_SECONDS + 10,))
            finally:
                _REFRESH_LOCK.release()

    @staticmethod
    def _pause(state, error):
        code = getattr(error, 'code', '')
        if code in ('http_401', 'http_403', 'robots_disallowed', 'robots_rate_requires_operator_review'):
            state.update(paused=True, reason=code)
        elif code == 'http_429':
            until = time.time() + MIN_REFRESH_SECONDS
            raw = getattr(error, 'retry_after', None)
            if raw:
                try:
                    until = max(until, time.time() + int(raw) if str(raw).isdigit() else parsedate_to_datetime(raw).timestamp())
                except (ValueError, TypeError, OverflowError):
                    pass
            state.update(next_refresh_epoch=until, reason=code)
