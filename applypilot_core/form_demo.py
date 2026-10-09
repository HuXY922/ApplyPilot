"""Owned-form simulation only. No networking and no real applicant data.

Routes must use the normal session, CSRF and Origin guards. Pass a fresh SQLite
connection for that anonymous session, not Store.transaction(): these functions
own the transaction so a rejected, changed confirmation remains invalidated.
GET /api/demo/receipts is read-only after additive schema initialization.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import secrets
import sqlite3
import time
import uuid

DESTINATION = '/api/demo/submit'
CONFIRMATION_TTL = 600
MAX_RECEIPTS = 100
FICTIONAL_FIELDS = {
    'name': '林小禾（虚构示例）',
    'email': 'lin.xiaohe@example.invalid',
    'role': 'AI 产品经理（虚构岗位）',
    'education': '示例大学（虚构） · 信息管理硕士 · 2027届',
    'experience': '示例实验室（虚构）：产品研究实习，原型设计、SQL 分析、用户反馈整理。',
}
ALLOWED_ROLES = (FICTIONAL_FIELDS['role'], '产品研究实习生（虚构岗位）')


class DemoError(Exception):
    def __init__(self, message, code='invalid_demo_request', status=400):
        self.message, self.code, self.status = message, code, status
        super().__init__(message)


def _require(condition, message, code='invalid_demo_request', status=400):
    if not condition:
        raise DemoError(message, code, status)


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def _digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def content_hash(fields, destination=DESTINATION):
    return _digest(_json({'schema': 'applypilot-controlled-form-v1',
                         'destination': destination, 'fields': fields}))


def ensure_schema(conn):
    """Additive and independent of legacy applications/tracker tables."""
    conn.execute('''CREATE TABLE IF NOT EXISTS ap_demo_confirmations (
        token_hash TEXT PRIMARY KEY, content_hash TEXT NOT NULL,
        destination TEXT NOT NULL, fields_json TEXT NOT NULL,
        idempotency_key TEXT NOT NULL UNIQUE, state TEXT NOT NULL,
        expires_at INTEGER NOT NULL, created_at INTEGER NOT NULL)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS ap_demo_receipts (
        id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE,
        token_hash TEXT NOT NULL UNIQUE, content_hash TEXT NOT NULL,
        destination TEXT NOT NULL, receipt_json TEXT NOT NULL,
        created_at INTEGER NOT NULL)''')


@contextmanager
def _transaction(conn):
    _require(not conn.in_transaction, 'Demo service requires a fresh session connection.',
             'demo_transaction_configuration', 500)
    ensure_schema(conn)
    conn.execute('BEGIN IMMEDIATE')
    try:
        yield
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _fields(payload):
    _require(isinstance(payload, dict), 'Expected an object.')
    _require(payload.get('destination') == DESTINATION,
             'Only the owned simulation endpoint is allowed.', 'demo_destination_forbidden')
    fields = payload.get('fields')
    _require(isinstance(fields, dict) and set(fields) == set(FICTIONAL_FIELDS),
             'All five fictional fields are required.', 'demo_fields_invalid')
    for key, expected in FICTIONAL_FIELDS.items():
        allowed = ALLOWED_ROLES if key == 'role' else (expected,)
        _require(isinstance(fields[key], str) and fields[key] in allowed,
                 'Only the supplied fictional candidate and demo roles are accepted.',
                 'fictional_data_only')
    return dict(fields)


def _token(payload):
    _require(isinstance(payload, dict), 'Expected an object.')
    token = payload.get('confirmation_token')
    _require(isinstance(token, str) and 32 <= len(token) <= 100,
             'A fresh review confirmation is required.', 'demo_confirmation_required', 409)
    return _digest(token)


def prepare(conn, payload):
    """Snapshot fictional fields; no submission. New review cancels older reviews."""
    fields = _fields(payload)
    stamp = int(time.time())
    token = secrets.token_urlsafe(32)
    key = str(uuid.uuid4())
    digest = content_hash(fields)
    with _transaction(conn):
        _require(conn.execute('SELECT count(*) FROM ap_demo_receipts').fetchone()[0] < MAX_RECEIPTS,
                 'This session has reached the simulation receipt limit.', 'demo_capacity', 409)
        conn.execute("UPDATE ap_demo_confirmations SET state='cancelled' WHERE state='pending'")
        conn.execute('DELETE FROM ap_demo_confirmations WHERE token_hash NOT IN '
                     '(SELECT token_hash FROM ap_demo_receipts)')
        conn.execute('INSERT INTO ap_demo_confirmations VALUES(?,?,?,?,?,?,?,?)',
                     (_digest(token), digest, DESTINATION, _json(fields), key,
                      'pending', stamp + CONFIRMATION_TTL, stamp))
    return {'status': 'waiting_user', 'simulation': True, 'fields': fields,
            'destination': DESTINATION, 'content_hash': digest,
            'confirmation_token': token, 'idempotency_key': key,
            'expires_at': stamp + CONFIRMATION_TTL,
            'message': 'Review these fictional values and explicitly confirm the local simulation.'}


def cancel(conn, payload):
    token_hash = _token(payload)
    with _transaction(conn):
        conn.execute("UPDATE ap_demo_confirmations SET state='cancelled' "
                     "WHERE token_hash=? AND state='pending'", (token_hash,))
    return {'status': 'cancelled', 'simulation': True}


def submit(conn, payload):
    """Atomic local receipt, bound to exact reviewed fields/path/token/key.

    Retrying the identical confirmed operation returns its original receipt.
    Clients must never automatically retry after a network/unknown response;
    they can use receipts() to reconcile the outcome without writing again.
    """
    token_hash = _token(payload)
    failure = None
    result = None
    with _transaction(conn):
        conn.row_factory = sqlite3.Row
        row = conn.execute('SELECT * FROM ap_demo_confirmations WHERE token_hash=?',
                           (token_hash,)).fetchone()
        _require(row is not None, 'Review confirmation was not found in this session.',
                 'demo_confirmation_required', 409)
        try:
            fields = _fields(payload)
            digest = content_hash(fields)
            _require(payload.get('confirm_simulation') is True,
                     'Explicit human confirmation is required.', 'demo_confirmation_required', 409)
            _require(payload.get('content_hash') == row['content_hash'] == digest
                     and payload.get('destination') == row['destination']
                     and _json(fields) == row['fields_json']
                     and payload.get('idempotency_key') == row['idempotency_key'],
                     'The content, destination or operation changed. Review again.',
                     'demo_confirmation_changed', 409)
        except DemoError as exc:
            # Commit this invalidation before reporting an error to the route.
            conn.execute("UPDATE ap_demo_confirmations SET state='cancelled' "
                         "WHERE token_hash=? AND state='pending'", (token_hash,))
            failure = exc
        if failure is None:
            prior = conn.execute('SELECT receipt_json FROM ap_demo_receipts WHERE idempotency_key=?',
                                 (row['idempotency_key'],)).fetchone()
            if prior:
                result = json.loads(prior[0])
                result['replayed'] = True
            elif row['state'] != 'pending' or row['expires_at'] <= int(time.time()):
                conn.execute("UPDATE ap_demo_confirmations SET state='cancelled' WHERE token_hash=?", (token_hash,))
                failure = DemoError('Confirmation was cancelled or expired. Review again.',
                                    'demo_confirmation_expired', 409)
            else:
                stamp = int(time.time())
                result = {
                    'id': 'sim-' + uuid.uuid4().hex,
                    'status': 'simulation_recorded', 'simulation': True, 'replayed': False,
                    'label': '受控模拟回执 · 非真实投递',
                    'destination': DESTINATION, 'content_hash': row['content_hash'],
                    'idempotency_key': row['idempotency_key'],
                    'created_at': datetime.fromtimestamp(stamp, timezone.utc).isoformat(),
                    'fields': fields,
                    'message': 'Recorded in this demo session only. No recruiter or external site was contacted.',
                }
                conn.execute('INSERT INTO ap_demo_receipts VALUES(?,?,?,?,?,?,?)',
                             (result['id'], row['idempotency_key'], token_hash,
                              row['content_hash'], DESTINATION, _json(result), stamp))
                conn.execute("UPDATE ap_demo_confirmations SET state='used' WHERE token_hash=?", (token_hash,))
    if failure:
        raise failure
    return result


def receipts(conn):
    ensure_schema(conn)
    values = [json.loads(row[0]) for row in conn.execute(
        'SELECT receipt_json FROM ap_demo_receipts ORDER BY created_at DESC,rowid DESC LIMIT ?',
        (MAX_RECEIPTS,))]
    return {'simulation': True, 'receipts': values}
