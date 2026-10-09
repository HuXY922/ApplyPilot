"""Persistent isolated workspaces; original Career Desk tables remain authoritative.

The ap_* tables add reviewed profile versions, source observations and a manual
career tracker. They never change an application's guarded execution state.
"""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import secrets
import sqlite3
import time
from urllib.parse import urlsplit, urlunsplit
import uuid

from db import ROOT, connect as legacy_connect, initialize

SESSION_DATABASE_LIMIT = 8*1024*1024


def connect(path):
    db = legacy_connect(path)
    page_size = db.execute("PRAGMA page_size").fetchone()[0]
    limit = SESSION_DATABASE_LIMIT if Path(path).name.startswith("session-") else 4*1024*1024
    db.execute("PRAGMA max_page_count="+str(limit//page_size))
    return db


class DomainError(Exception):
    def __init__(self, status, code, message, **details):
        self.status, self.code, self.message, self.details = status, code, message, details
        super().__init__(message)


def require(ok, code, message, status=400):
    if not ok:
        raise DomainError(status, code, message)


def now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def uid():
    return uuid.uuid4().hex


DEMO_PROFILE = {
    'name': '林小禾（虚构示例）',
    'education': [{'institution': '示例大学（虚构）', 'degree': '硕士', 'major': '信息管理', 'graduation': '2027-07'}],
    'experience': [{'id': 'exp-demo', 'organization': '示例实验室（虚构）', 'role': '产品研究实习生',
                    'description': '维护团队任务清单与会议记录。\n设计 Agent 交互原型，整理用户反馈。\n使用 SQL 和 Python 分析产品迭代数据，整理模型评测清单。\n以上均为虚构教学案例。'}],
    'projects': [{'id': 'proj-demo', 'name': '校园学习助手（虚构）',
                  'description': '设计大模型 Agent 交互原型和评测清单，开展用户调研。虚构教学案例。'}],
    'skills': ['SQL', 'Python', '原型设计', '用户研究'],
    'preferences': {'roles': ['AI 产品经理', '产品经理'], 'cities': ['上海', '深圳', '广州'],
                    'excluded_roles': [], 'recruitment_type': 'campus', 'graduation_year': '2027'},
    'resume_text': '虚构教学档案，用于体验 ApplyPilot。请勿上传真实个人信息。',
}


def demo_jobs():
    specs = [
        ('product', 'AI 产品经理', '星屿科技（虚构）', '上海', 'campus',
         '设计 AI 助手的交互原型，整理用户反馈并参与产品评测。', '正式校招。2027届本科及以上学历。专业不限。熟悉 SQL 或 Python，具备用户研究和原型设计经验。'),
        ('operations', '产品运营实习生', '青空实验室（虚构）', '深圳', 'internship',
         '参与用户调研，整理产品需求，协助分析体验反馈。', '在校本科或硕士。产品运营实习，每周至少三天，连续三个月。'),
        ('research', '机器学习研究员', '远山智能（虚构）', '北京', 'campus',
         '研究模型评测方法与训练算法。', '正式校招。2027届。必须博士学历，仅限计算机科学与技术专业。'),
    ]
    return [dict(id='demo-'+key, provider='applypilot-demo', external_id=key, title=title,
                 company=company, city=city, canonical_url='https://example.invalid/applypilot/'+key,
                 description=description, requirements=requirements, recruitment_type=recruitment,
                 availability='unknown', record_type='demo',
                 verification={'status': 'demo', 'source': 'fictional_fixture', 'verified_at': None,
                               'evidence': '虚构体验岗位，不是真实招聘，不可投递。'})
            for key, title, company, city, recruitment, description, requirements in specs]


class SessionManager:
    """Opaque cookies, hashed identifiers on disk, bounded creation and expiry.

    Forwarded addresses are deliberately not trusted for limits. Deploy behind a
    trusted proxy and add edge limits for robust public abuse resistance.
    """
    cookie_name = 'applypilot_session'

    def __init__(self, data_root, max_sessions=100, ttl_seconds=86400, new_per_minute=12):
        self.root = Path(data_root).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.max_sessions = max(1, min(int(max_sessions), 1000))
        self.ttl = max(60, min(int(ttl_seconds), 7*86400))
        self.new_per_minute = max(1, min(int(new_per_minute), 100))
        self.registry = self.root / 'applypilot-sessions.sqlite3'
        with connect(self.registry) as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS sessions (
              session_hash TEXT PRIMARY KEY, csrf TEXT NOT NULL,
              created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL, last_seen INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS creation_limits (
              client_hash TEXT NOT NULL, bucket INTEGER NOT NULL, count INTEGER NOT NULL,
              PRIMARY KEY(client_hash,bucket));
            CREATE TABLE IF NOT EXISTS request_limits (
              session_hash TEXT NOT NULL, bucket INTEGER NOT NULL, count INTEGER NOT NULL,
              PRIMARY KEY(session_hash,bucket));
            CREATE TABLE IF NOT EXISTS operation_budgets (
              kind TEXT NOT NULL, scope TEXT NOT NULL, bucket INTEGER NOT NULL, count INTEGER NOT NULL,
              PRIMARY KEY(kind,scope,bucket));
            ''')
        self.registry.chmod(0o600)

    def path(self, session_hash):
        require(bool(re.fullmatch('[a-f0-9]{64}', session_hash)), 'invalid_session', 'Invalid session', 401)
        return self.root / ('session-'+session_hash+'.sqlite3')

    def get(self, token):
        if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', token):
            return None
        stamp = int(time.time())
        with connect(self.registry) as db:
            row = db.execute('SELECT * FROM sessions WHERE session_hash=? AND expires_at>?', (digest(token), stamp)).fetchone()
            if row:
                db.execute('UPDATE sessions SET last_seen=? WHERE session_hash=?', (stamp, row['session_hash']))
                return dict(row)
        return None

    def create(self, client='unknown'):
        stamp = int(time.time())
        db = connect(self.registry)
        session_hash = None
        try:
            db.execute('BEGIN IMMEDIATE')
            expired = [r[0] for r in db.execute('SELECT session_hash FROM sessions WHERE expires_at<=?', (stamp,))]
            for identifier in expired:
                for suffix in ('', '-wal', '-shm', '-journal'):
                    self.path(identifier).with_name(self.path(identifier).name+suffix).unlink(missing_ok=True)
            db.execute('DELETE FROM sessions WHERE expires_at<=?', (stamp,))
            db.execute('DELETE FROM creation_limits WHERE bucket<?', (stamp//60-2,))
            db.execute('DELETE FROM request_limits WHERE bucket<?', (stamp//60-2,))
            require(db.execute('SELECT count(*) FROM sessions').fetchone()[0] < self.max_sessions,
                    'session_capacity', 'Demo capacity is full. Please try later.', 503)
            # Per-address and global budgets prevent unbounded anonymous disk allocation.
            for key, cap in ((digest(str(client)), self.new_per_minute), ('global', self.new_per_minute*4)):
                count = db.execute('SELECT count FROM creation_limits WHERE client_hash=? AND bucket=?', (key, stamp//60)).fetchone()
                require(not count or count[0] < cap, 'session_rate_limit', 'Too many new sessions. Please wait a minute.', 429)
                db.execute('INSERT INTO creation_limits VALUES(?,?,1) ON CONFLICT(client_hash,bucket) DO UPDATE SET count=count+1', (key, stamp//60))
            token = secrets.token_urlsafe(32)
            session_hash = digest(token)
            csrf = secrets.token_urlsafe(32)
            Store(self.path(session_hash), initialize_new=True)
            db.execute('INSERT INTO sessions VALUES(?,?,?,?,?)', (session_hash, csrf, stamp, stamp+self.ttl, stamp))
            db.commit()
            return token, {'session_hash': session_hash, 'csrf': csrf, 'created_at': stamp,
                           'expires_at': stamp+self.ttl, 'last_seen': stamp}
        except Exception:
            db.rollback()
            if session_hash:
                for suffix in ('', '-wal', '-shm', '-journal'):
                    self.path(session_hash).with_name(self.path(session_hash).name+suffix).unlink(missing_ok=True)
            raise
        finally:
            db.close()

    def limit_write(self, session_hash, limit=60):
        bucket = int(time.time())//60
        with connect(self.registry) as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT count FROM request_limits WHERE session_hash=? AND bucket=?', (session_hash,bucket)).fetchone()
            require(not row or row[0] < limit, 'write_rate_limit', 'Too many changes. Please wait a minute.', 429)
            db.execute('INSERT INTO request_limits VALUES(?,?,1) ON CONFLICT(session_hash,bucket) DO UPDATE SET count=count+1', (session_hash,bucket))
            db.execute('DELETE FROM request_limits WHERE bucket<?', (bucket-2,))

    def reserve_operation(self, session_hash, kind, global_limit, session_limit):
        """Durable atomic reservations count failed attempts, across all sessions."""
        period = 86400 if kind == 'model' else 3600
        bucket = int(time.time())//period
        with connect(self.registry) as db:
            db.execute('BEGIN IMMEDIATE')
            for scope,limit in (('global',global_limit),(session_hash,session_limit)):
                row = db.execute('SELECT count FROM operation_budgets WHERE kind=? AND scope=? AND bucket=?',
                                 (kind,scope,bucket)).fetchone()
                require(limit > 0 and (not row or row[0] < limit), kind+'_budget_exhausted',
                        'The demo '+kind+' request budget is exhausted. Please try later.',429)
                db.execute('INSERT INTO operation_budgets VALUES(?,?,?,1) ON CONFLICT(kind,scope,bucket) DO UPDATE SET count=count+1',
                           (kind,scope,bucket))
            db.execute('DELETE FROM operation_budgets WHERE kind=? AND bucket<?',(kind,bucket-2))

    def health(self):
        with connect(self.registry) as db:
            require(db.execute('PRAGMA quick_check').fetchone()[0] == 'ok', 'database_unhealthy', 'Workspace database check failed', 503)
            db.execute('BEGIN IMMEDIATE')
            db.execute('CREATE TABLE IF NOT EXISTS health_probe (id INTEGER PRIMARY KEY)')
            db.execute('INSERT OR REPLACE INTO health_probe VALUES(1)')
            db.rollback()
        return {'status': 'ok', 'storage': 'writable', 'isolation': 'per-session-sqlite'}


class Store:
    def __init__(self, path, initialize_new=False):
        self.path = Path(path)
        if initialize_new:
            initialize(self.path)
            with connect(self.path) as db:
                db.executescript((ROOT/'migrations/005_applypilot.sql').read_text())
                if not db.execute('SELECT 1 FROM ap_profile_versions').fetchone():
                    db.execute('INSERT INTO ap_profile_versions VALUES(?,?,?,?)', (1, encode(DEMO_PROFILE), 0, now()))
            self.path.chmod(0o600)
            with self.transaction() as db:
                if not db.execute('SELECT 1 FROM ap_job_details').fetchone():
                    for job in demo_jobs():
                        self._ingest_job(db, job, None)

    @contextmanager
    def transaction(self):
        db = connect(self.path)
        try:
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def profile(self, db=None):
        if db is None:
            with connect(self.path) as con:
                return self.profile(con)
        row = db.execute('SELECT * FROM ap_profile_versions ORDER BY version DESC LIMIT 1').fetchone()
        return {'profile': json.loads(row['profile_json']), 'profile_version': row['version'], 'profile_confirmed': bool(row['confirmed'])}

    def state(self):
        with connect(self.path) as db:
            result = self.profile(db)
            draft = db.execute('SELECT draft_json FROM ap_drafts WHERE reviewed_at IS NULL ORDER BY id DESC LIMIT 1').fetchone()
            result['draft'] = json.loads(draft[0]) if draft else None
            result['jobs'] = self.jobs(db)
            result['runs'] = self.runs(db)
            result['metrics'] = {
                'total_jobs': len(result['jobs']),
                'live_jobs': sum(j['record_type'] != 'demo' for j in result['jobs']),
                'demo_jobs': sum(j['record_type'] == 'demo' for j in result['jobs']),
                'verified_jobs': sum(j['record_type'] == 'verified_job' for j in result['jobs']),
                'submitted': sum(j['tracker_status'] == 'submitted' for j in result['jobs']),
                'needs_attention': sum(j['tracker_status'] in ('waiting_user','submission_unknown') for j in result['jobs']),
                'by_status': {status:sum(j['tracker_status'] == status for j in result['jobs'])
                              for status in sorted({j['tracker_status'] for j in result['jobs']})},
            }
            return result

    def save_draft(self, draft):
        with self.transaction() as db:
            # Keep a bounded import audit; confirmed versions are stored separately.
            db.execute('DELETE FROM ap_drafts WHERE id NOT IN (SELECT id FROM ap_drafts ORDER BY id DESC LIMIT 19)')
            db.execute('INSERT INTO ap_drafts(draft_json,created_at) VALUES(?,?)', (encode(draft),now()))
        return draft

    def save_profile(self, profile, expected_version):
        with self.transaction() as db:
            current = self.profile(db)
            require(current['profile_version'] == expected_version, 'version_conflict', 'Profile changed. Refresh before saving.', 409)
            require(expected_version < 100, 'profile_capacity', 'This demo workspace reached its profile version limit.', 409)
            version = expected_version+1
            stamp = now()
            db.execute('INSERT INTO ap_profile_versions VALUES(?,?,1,?)', (version,encode(profile),stamp))
            db.execute('UPDATE ap_drafts SET reviewed_at=? WHERE reviewed_at IS NULL', (stamp,))
            # Maintain a compatible original candidate representation atomically.
            row = db.execute('SELECT id,profile_json FROM candidates ORDER BY id LIMIT 1').fetchone()
            legacy = json.loads(row['profile_json'])
            legacy['identity'] = {'name_zh': profile['name']}
            legacy['education'] = [{'institution':e['institution'],'major':e['major'],'degree_level':e['degree'],
                                    'end_month':e['graduation'],'end_status':'expected'} for e in profile['education']]
            legacy['experience'] = [{**e,'workstreams':[{'description':e['description']}]} for e in profile['experience']]
            legacy['projects'] = profile['projects']
            legacy['skills'] = {'data_tools':profile['skills']}
            prefs = profile['preferences']
            legacy['job_preferences'] = {'resume_target':{'value':next(iter(prefs['roles']), '')},
                                        'additional_roles':{'value':prefs['roles'][1:]},
                                        'include_cities':{'value':prefs['cities']},
                                        'excluded_roles':prefs['excluded_roles']}
            db.execute('UPDATE candidates SET profile_json=?,revision=revision+1,updated_at=? WHERE id=?', (encode(legacy),stamp,row['id']))
            return self.profile(db)

    def _submission_unresolved(self, db, application_id):
        """Derive the safety latch from durable, untrimmed tracking history.

        Display-state changes (including old versions' skip bypass) cannot erase
        an unknown attempt. Only a later explicit manual submission attestation
        reconciles it. This also protects existing workspaces without rewriting
        their audit events or depending on a new migration.
        """
        row = db.execute("""SELECT
            coalesce(max(CASE WHEN to_status='submission_unknown' THEN sequence END),0) AS unknown_at,
            coalesce(max(CASE WHEN to_status='submitted' AND kind='manual_attestation' THEN sequence END),0) AS attested_at
            FROM ap_tracker_events WHERE application_id=?""", (application_id,)).fetchone()
        return row['unknown_at'] > row['attested_at']

    def _job(self, db, job_id):
        row = db.execute('''SELECT d.details_json,d.observation_revision,t.status,t.revision,t.application_id
                            FROM ap_job_details d JOIN ap_tracker t ON t.job_id=d.job_id WHERE d.job_id=?''', (job_id,)).fetchone()
        require(row is not None, 'job_not_found', 'Job not found in this workspace.', 404)
        job = json.loads(row['details_json'])
        unresolved = row['status'] == 'submission_unknown' or self._submission_unresolved(db,row['application_id'])
        job.update(id=job_id, tracker_status='submission_unknown' if unresolved else row['status'],
                   tracker_recorded_status=row['status'], submission_unresolved=unresolved,
                   tracker_revision=row['revision'], application_id=row['application_id'],
                   observation_revision=row['observation_revision'])
        current = self.profile(db)['profile_version']
        for kind in ('analysis','resume'):
            output = db.execute('SELECT * FROM ap_outputs WHERE job_id=? AND kind=? ORDER BY created_at DESC,rowid DESC LIMIT 1', (job_id,kind)).fetchone()
            if output:
                result = json.loads(output['output_json'])
                result.update(id=output['id'],profile_version=output['profile_version'],created_at=output['created_at'],
                              stale=output['profile_version'] != current or output['observation_revision'] != row['observation_revision'])
                job[kind] = result
        return job

    def job(self, job_id):
        with connect(self.path) as db:
            return self._job(db,job_id)

    def jobs(self, db):
        return [self._job(db, r[0]) for r in db.execute('SELECT job_id FROM ap_job_details ORDER BY updated_at DESC,job_id')]

    def _ingest_job(self, db, item, run_id):
        require(isinstance(item,dict), 'source_schema', 'Source returned an invalid job.', 502)
        job = deepcopy(item)
        for field in ('provider','external_id','title','company','canonical_url'):
            require(isinstance(job.get(field),str) and 0 < len(job[field]) <= 2000, 'source_schema', 'Source job identity is incomplete.', 502)
        require(len(encode(job)) <= 100000, 'source_schema', 'Source job exceeds storage limit.', 502)
        require(job.get('record_type') in ('verified_job','lead','announcement','demo'), 'source_schema', 'Source classification missing.', 502)
        try:
            url = urlsplit(job['canonical_url'])
            port = url.port
        except ValueError:
            raise DomainError(502,'unsafe_job_url','Source supplied an unsafe job link.') from None
        require(url.scheme == 'https' and url.hostname and not url.username and not url.password and port in (None,443),
                'unsafe_job_url', 'Source supplied an unsafe job link.', 502)
        try:
            ipaddress.ip_address(url.hostname)
            address_literal = True
        except ValueError:
            address_literal = False
        require(not address_literal and '.' in url.hostname and not url.hostname.endswith(('.local','.localhost','.internal')),
                'unsafe_job_url','Source supplied a non-public job link.',502)
        canonical = urlunsplit((url.scheme,url.netloc.lower(),url.path or '/',url.query,''))
        job['canonical_url'] = canonical
        verification = job.get('verification')
        require(isinstance(verification,dict), 'source_schema', 'Source verification metadata missing.', 502)
        if job['record_type'] == 'verified_job':
            require(bool(verification.get('verified_at')) and bool(verification.get('evidence')) and verification.get('status') in ('verified','verified_open'),
                    'source_evidence', 'Verified jobs require detail evidence and a timestamp.', 502)
            try:
                verified = datetime.fromisoformat(str(verification['verified_at']).replace('Z','+00:00'))
                age = (datetime.now(timezone.utc)-verified).total_seconds()
                require(-60 <= age <= 3600, 'source_evidence','Source verification is stale or in the future.',502)
            except (ValueError,TypeError):
                raise DomainError(502,'source_evidence','Source verification timestamp is invalid.') from None
        existing = db.execute('SELECT * FROM ap_job_details WHERE provider=? AND external_id=?', (job['provider'],job['external_id'])).fetchone()
        by_url = db.execute('SELECT * FROM jobs WHERE canonical_url=?', (canonical,)).fetchone()
        if existing:
            jid = existing['job_id']
            require(not by_url or by_url['id'] == jid, 'source_identity_conflict', 'Conflicting source job identities.', 409)
        elif by_url:
            jid = by_url['id']
            other = db.execute('SELECT provider,external_id FROM ap_job_details WHERE job_id=?', (jid,)).fetchone()
            require(other is None or other['provider'] != job['provider'], 'source_identity_conflict', 'One provider URL refers to conflicting job IDs.', 409)
        else:
            require(db.execute('SELECT count(*) FROM jobs').fetchone()[0] < 1000, 'workspace_capacity', 'Demo workspace job limit reached.', 409)
            jid = 'job-'+digest(job['provider']+':'+job['external_id'])[:24]
        company_id = 'company-'+digest(job['company'])[:24]
        db.execute('INSERT OR IGNORE INTO companies(id,name,official_domain) VALUES(?,?,?)', (company_id,job['company'],url.hostname))
        stamp = now()
        availability = job.get('availability','unknown')
        if availability not in ('open','closed','expired','unknown'):
            availability = 'unknown'
        job.update(id=jid,availability=availability)
        db.execute('''INSERT INTO jobs(id,company_id,title,city,canonical_url,description,availability,verified_at)
                      VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                      company_id=excluded.company_id,title=excluded.title,city=excluded.city,canonical_url=excluded.canonical_url,
                      description=excluded.description,availability=excluded.availability,verified_at=excluded.verified_at''',
                   (jid,company_id,job['title'],job.get('city',''),canonical,job.get('description',''),'closed' if availability == 'expired' else availability,verification.get('verified_at')))
        encoded = encode(job)
        changed = not existing or existing['details_json'] != encoded
        db.execute('''INSERT INTO ap_job_details(job_id,provider,external_id,details_json,updated_at) VALUES(?,?,?,?,?)
                      ON CONFLICT(job_id) DO UPDATE SET details_json=excluded.details_json,updated_at=excluded.updated_at,
                      observation_revision=ap_job_details.observation_revision+?''',
                   (jid,job['provider'],job['external_id'],encoded,stamp,int(changed)))
        db.execute('''INSERT INTO job_sources(id,job_id,provider,external_id,url,observed_at) VALUES(?,?,?,?,?,?)
                      ON CONFLICT(provider,external_id) DO UPDATE SET url=excluded.url,observed_at=excluded.observed_at''',
                   (uid(),jid,job['provider'],job['external_id'],canonical,stamp))
        db.execute('INSERT INTO ap_observations(job_id,run_id,observed_at,evidence_hash,details_json) VALUES(?,?,?,?,?)',
                   (jid,run_id,stamp,digest(encoded),encoded))
        # Retain the most recent 20 observations per job to bound anonymous storage.
        db.execute('DELETE FROM ap_observations WHERE job_id=? AND id NOT IN (SELECT id FROM ap_observations WHERE job_id=? ORDER BY id DESC LIMIT 20)', (jid,jid))
        tracker = db.execute('SELECT 1 FROM ap_tracker WHERE job_id=?', (jid,)).fetchone()
        if not tracker:
            candidate = db.execute('SELECT id FROM candidates ORDER BY id LIMIT 1').fetchone()[0]
            campaign = db.execute('SELECT id FROM campaigns WHERE candidate_id=? ORDER BY id LIMIT 1', (candidate,)).fetchone()[0]
            aid = 'app-'+uid()
            db.execute("INSERT INTO applications(id,candidate_id,campaign_id,job_id,mode) VALUES(?,?,?,?,'simulation')", (aid,candidate,campaign,jid))
            db.execute("INSERT INTO ap_tracker VALUES(?,?,'saved',1,?)", (aid,jid,stamp))
            db.execute("INSERT INTO ap_tracker_events(application_id,from_status,to_status,kind,idempotency_key,request_hash,occurred_at) VALUES(?,NULL,'saved','discovered',?,?,?)", (aid,'initial:'+jid,digest(encoded),stamp))
        return jid, existing is None and by_url is None

    def ingest(self, jobs, run_id):
        with self.transaction() as db:
            result = [self._ingest_job(db,job,run_id) for job in jobs]
            ids = list(dict.fromkeys(jid for jid,_ in result))
            return [self._job(db,jid) for jid in ids], sum(int(created) for _,created in result)

    def refresh_observed(self, observations, run_id):
        """Refresh known jobs, including explicit closure; never infer absence = closed."""
        refreshed = 0
        with self.transaction() as db:
            for job in observations:
                require(isinstance(job,dict), 'source_schema','Invalid source observation.',502)
                existing = db.execute('SELECT 1 FROM ap_job_details WHERE provider=? AND external_id=?',
                                      (job.get('provider'),job.get('external_id'))).fetchone()
                if existing:
                    self._ingest_job(db,job,run_id)
                    refreshed += 1
        return refreshed

    def events(self, job_id, db=None):
        if db is None:
            with connect(self.path) as con:
                return self.events(job_id,con)
        job = self._job(db,job_id)
        return [dict(r) for r in db.execute('SELECT * FROM ap_tracker_events WHERE application_id=? ORDER BY sequence', (job['application_id'],))]

    def track(self, job_id, status, attestation, note, key):
        fingerprint = digest(encode({'job_id':job_id,'status':status,'attestation':attestation,'note':note}))
        with self.transaction() as db:
            job = self._job(db,job_id)
            previous = db.execute('SELECT * FROM ap_tracker_events WHERE idempotency_key=?', (key,)).fetchone()
            if previous:
                require(previous['request_hash'] == fingerprint, 'idempotency_conflict', 'This action key was already used for a different change.', 409)
                return {'job':job,'events':self.events(job_id,db),'replayed':True}
            require(status != 'submitted' or attestation is True, 'attestation_required', 'Confirm that you submitted this application yourself.', 409)
            if job['submission_unresolved']:
                require(status in ('submission_unknown','submitted'), 'submission_unresolved',
                        'Submission remains unresolved. Only an explicit manual submission attestation can release this safety hold.',409)
            require(db.execute('SELECT count(*) FROM ap_tracker_events').fetchone()[0] < 10000,
                    'workspace_capacity', 'Demo tracker history limit reached.', 409)
            stamp = now()
            kind = 'manual_attestation' if status == 'submitted' else 'manual_tracking'
            db.execute('UPDATE ap_tracker SET status=?,revision=revision+1,updated_at=? WHERE job_id=?', (status,stamp,job_id))
            db.execute('INSERT INTO ap_tracker_events(application_id,from_status,to_status,kind,note,idempotency_key,request_hash,occurred_at) VALUES(?,?,?,?,?,?,?,?)',
                       (job['application_id'],job['tracker_recorded_status'],status,kind,note,key,fingerprint,stamp))
            return {'job':self._job(db,job_id),'events':self.events(job_id,db),'replayed':False}

    def begin_run(self, kind, request):
        with self.transaction() as db:
            # A process crash leaves an honest failed run instead of an eternal spinner.
            cutoff = datetime.fromtimestamp(time.time()-180,timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
            db.execute("UPDATE ap_runs SET status='failed',finished_at=?,result_json=? WHERE status='running' AND started_at<?",
                       (now(),encode({'code':'interrupted','message':'Operation was interrupted; refresh before retrying.'}),cutoff))
            require(not db.execute("SELECT 1 FROM ap_runs WHERE status='running'").fetchone(),
                    'operation_running', 'Another operation is still running in this workspace.', 409)
            require(db.execute('SELECT count(*) FROM ap_runs').fetchone()[0] < 1000, 'workspace_capacity', 'Demo run history limit reached.', 409)
            rid = uid()
            db.execute("INSERT INTO ap_runs(id,kind,status,started_at,request_json) VALUES(?,?,'running',?,?)", (rid,kind,now(),encode(request)))
            return rid

    def finish_run(self, rid, status, result):
        with self.transaction() as db:
            db.execute('UPDATE ap_runs SET status=?,finished_at=?,result_json=? WHERE id=?', (status,now(),encode(result),rid))
            return self.run(db,rid)

    def run(self, db, rid):
        row = dict(db.execute('SELECT * FROM ap_runs WHERE id=?', (rid,)).fetchone())
        row['request'] = json.loads(row.pop('request_json'))
        row['result'] = json.loads(row.pop('result_json') or '{}')
        return row

    def runs(self, db):
        return [self.run(db,r[0]) for r in db.execute('SELECT id FROM ap_runs ORDER BY started_at DESC,rowid DESC LIMIT 30')]

    def save_output(self, job_id, kind, profile_version, observation_revision, output):
        with self.transaction() as db:
            require(self.profile(db)['profile_version'] == profile_version, 'version_conflict', 'Profile changed during analysis. Run it again.', 409)
            job = self._job(db,job_id)
            require(job['observation_revision'] == observation_revision, 'job_changed', 'Job evidence changed during analysis. Run it again.', 409)
            require(db.execute('SELECT count(*) FROM ap_outputs').fetchone()[0] < 2000, 'workspace_capacity', 'Demo analysis history limit reached.', 409)
            oid = uid()
            stamp = now()
            db.execute('INSERT INTO ap_outputs VALUES(?,?,?,?,?,?,?)', (oid,job_id,kind,profile_version,observation_revision,encode(output),stamp))
            return {**output,'id':oid,'profile_version':profile_version,'created_at':stamp,'stale':False}
