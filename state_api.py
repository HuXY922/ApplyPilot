"""Local State API v1. Standard library only; no external submissions."""
import argparse
import hmac
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from urllib.parse import parse_qs
from wsgiref.simple_server import make_server
from db import connect, initialize, ROOT


def now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def uid():
    return str(uuid.uuid4())


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


class APIError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


def require(condition, message, status=400):
    if not condition:
        raise APIError(status, message)


def text_field(data, name):
    value = data.get(name)
    require(isinstance(value, str) and bool(value.strip()), f'{name} must be nonempty text')
    return value


def validate_time(value):
    require(isinstance(value, str), 'Timestamp must be a UTC string')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        require(parsed.tzinfo is not None and parsed.utcoffset().total_seconds() == 0, 'Timestamp must use UTC')
        return parsed.isoformat(timespec='milliseconds').replace('+00:00', 'Z')
    except ValueError:
        raise APIError(400, 'Invalid timestamp')


class StateAPI:
    def __init__(self, path, user_token, worker_token, radar_token=None, browser_origin=None):
        require(all(isinstance(x, str) and len(x) >= 32 for x in (user_token, worker_token)), 'Two tokens of at least 32 characters are required')
        require(user_token != worker_token, 'User and worker tokens must differ')
        self.path = path
        self.tokens = {'user': user_token, 'worker': worker_token}
        if radar_token is not None:
            require(isinstance(radar_token,str) and len(radar_token)>=32 and radar_token not in self.tokens.values(), 'Radar token must be distinct and at least 32 characters')
            self.tokens['radar'] = radar_token
        self.browser_origin = browser_origin

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

    def row(self, db, table, id):
        # table is always an internal constant, never supplied by a client.
        row = db.execute(f'SELECT * FROM {table} WHERE id=?', (id,)).fetchone()
        require(row is not None, 'Record not found', 404)
        return dict(row)

    def revision(self, app, data):
        require(type(data.get('expected_revision')) is int, 'expected_revision is required')
        require(app['revision'] == data['expected_revision'], 'Revision conflict: refresh before retrying', 409)

    def move(self, db, app, status, **fields):
        fields.update(status=status, revision=app['revision'] + 1, updated_at=now())
        sql = 'UPDATE applications SET ' + ','.join(f'{k}=?' for k in fields) + ' WHERE id=? AND revision=?'
        cur = db.execute(sql, [*fields.values(), app['id'], app['revision']])
        require(cur.rowcount == 1, 'Revision conflict', 409)
        return self.row(db, 'applications', app['id'])

    def save_answer(self, db, app, task, data):
        require(data.get('value') is not None, 'An explicit answer value is required')
        scope = data.get('scope')
        require(scope in ('global', 'campaign', 'company', 'job'), 'Explicit answer scope is required')
        field = task['field_key']
        require(bool(field), 'Task has no answer field')
        require(not any(x in field.lower() for x in ('password', 'token', 'secret', 'cookie', 'otp', '验证码', '密码', '授权码')), 'Credentials must not be stored as answers')
        expiry = validate_time(data['valid_until']) if data.get('valid_until') else None
        require(expiry is None or expiry > now(), 'Answer expiry must be in the future')
        rule = data.get('revalidate_when')
        require(rule is None or isinstance(rule, str), 'revalidate_when must be text or null')
        company = self.row(db, 'jobs', app['job_id'])['company_id']
        campaign_id = app['campaign_id'] if scope == 'campaign' else None
        company_id = company if scope == 'company' else None
        job_id = app['job_id'] if scope == 'job' else None
        stamp = now()
        db.execute('''UPDATE candidate_answers SET superseded_at=? WHERE candidate_id=? AND field_key=?
          AND scope=? AND campaign_id IS ? AND company_id IS ? AND job_id IS ? AND superseded_at IS NULL''',
          (stamp, app['candidate_id'], field, scope, campaign_id, company_id, job_id))
        id = uid()
        db.execute('''INSERT INTO candidate_answers
          (id,candidate_id,field_key,value_json,source_json,scope,campaign_id,company_id,job_id,confirmed_at,valid_until,revalidate_when)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
          (id, app['candidate_id'], field, encode(data['value']), encode({'type':'user_statement','task_id':task['id'],'actor':'user'}),
           scope, campaign_id, company_id, job_id, stamp, expiry, rule))
        return id

    def route(self, method, path, query, data, role):
        require(role in self.tokens, 'Unknown role',403)
        if role == 'radar':
            if method=='POST':
                serialized=encode(data)
                require(not any(secret in serialized for secret in self.tokens.values()),'Credentials must not be recorded')
            require((method=='POST' and path in ('/v1/radar/batch','/v1/radar/runs/start','/v1/radar/runs/finish')) or (method=='GET' and path in ('/v1/health','/v1/radar/profile')), 'Radar may only import pending jobs',403)
        parts = path.strip('/').split('/')
        require(parts[0] == 'v1', 'Route not found', 404)
        with self.transaction() as db:
            if path in ('/v1/radar/profile','/v1/radar/runs/start','/v1/radar/runs/finish'):
                require(role in ('user','radar'),'Radar/user endpoint',403)
                import radar_runs
                if method=='GET' and path=='/v1/radar/profile':
                    return radar_runs.profile_for_campaign(self,db,query)
                if method=='POST' and path.endswith('/start'):
                    return radar_runs.start(self,db,data)
                if method=='POST' and path.endswith('/finish'):
                    return radar_runs.finish(self,db,data)
                require(False,'Method denied',405)
            if method=='POST' and path=='/v1/radar/batch':
                require(role in ('radar','user'),'Worker cannot import',403)
                from radar import import_batch
                return import_batch(self,db,data)
            if method=='GET' and path=='/v1/dashboard':
                require(role=='user','User endpoint',403)
                from radar import dashboard
                return dashboard(db,query)
            if method == 'GET' and path == '/v1/health':
                return {'status':'ok','api_version':'1','live_submission_enabled':False}
            if method == 'GET' and path == '/v1/summary':
                mode = query.get('mode', ['live'])[0]
                require(mode in ('live','simulation'), 'Invalid mode')
                counts = {r[0]:r[1] for r in db.execute('SELECT status,count(*) FROM applications WHERE mode=? GROUP BY status',(mode,))}
                tasks = db.execute("SELECT count(*) FROM user_tasks t JOIN applications a ON a.id=t.application_id WHERE t.status='open' AND a.mode=?",(mode,)).fetchone()[0]
                return {'mode':mode,'applications_by_status':counts,'open_tasks':tasks}
            lists = {'candidates':'candidates','campaigns':'campaigns','jobs':'jobs','applications':'applications','tasks':'user_tasks','messages':'inbox_messages'}
            if method == 'GET' and len(parts) == 2 and parts[1] in lists:
                table = lists[parts[1]]
                try:
                    limit = int(query.get('limit',['50'])[0]); offset = int(query.get('offset',['0'])[0])
                except ValueError:
                    raise APIError(400, 'Invalid pagination')
                require(1 <= limit <= 200 and offset >= 0, 'Invalid pagination')
                where, values = '', []
                if table == 'applications':
                    mode = query.get('mode',['live'])[0]
                    require(mode in ('live','simulation'), 'Invalid mode')
                    where = ' WHERE mode=?'; values = [mode]
                    if role=='worker':
                        where += " AND status='approved'"
                rows = db.execute(f'SELECT * FROM {table}{where} ORDER BY id LIMIT ? OFFSET ?', [*values,limit+1,offset]).fetchall()
                return {'items':[dict(r) for r in rows[:limit]],'next_offset':offset+limit if len(rows)>limit else None}
            if method == 'GET' and len(parts) == 3 and parts[1] == 'applications':
                app = self.row(db,'applications',parts[2])
                app['events'] = [dict(r) for r in db.execute('SELECT * FROM application_events WHERE application_id=? ORDER BY sequence',(app['id'],))]
                app['tasks'] = [dict(r) for r in db.execute('SELECT * FROM user_tasks WHERE application_id=? ORDER BY created_at,id',(app['id'],))]
                app['attempts'] = [dict(r) for r in db.execute('SELECT * FROM submission_attempts WHERE application_id=? ORDER BY started_at,id',(app['id'],))]
                return app
            if method == 'GET' and len(parts) == 4 and parts[1] == 'candidates' and parts[3] == 'export':
                candidate = self.row(db,'candidates',parts[2])
                profile = json.loads(candidate['profile_json'])
                profile['application_answers'] = [dict(r) for r in db.execute('SELECT * FROM candidate_answers WHERE candidate_id=? ORDER BY confirmed_at,id',(candidate['id'],))]
                for answer in profile['application_answers']:
                    answer['value'] = json.loads(answer.pop('value_json'))
                    answer['source'] = json.loads(answer.pop('source_json'))
                return {'profile':profile,'answer_format':'state_api_v1','exported_at':now()}
            if method == 'GET' and len(parts) == 4 and parts[1] == 'applications' and parts[3] == 'answer':
                app = self.row(db,'applications',parts[2])
                company = self.row(db,'jobs',app['job_id'])['company_id']
                field = query.get('field_key',[''])[0]
                require(bool(field), 'field_key is required')
                rows = db.execute('''SELECT * FROM candidate_answers WHERE candidate_id=? AND field_key=? AND superseded_at IS NULL
                  AND (scope='global' OR (scope='campaign' AND campaign_id=?) OR (scope='company' AND company_id=?) OR (scope='job' AND job_id=?))
                  ORDER BY CASE scope WHEN 'job' THEN 0 WHEN 'company' THEN 1 WHEN 'campaign' THEN 2 ELSE 3 END''',
                  (app['candidate_id'],field,app['campaign_id'],company,app['job_id'])).fetchall()
                if not rows:
                    return {'status':'unknown','value':None}
                answer = dict(rows[0])
                stale = (answer['valid_until'] and answer['valid_until'] <= now()) or answer['revalidate_when']
                return {'status':'needs_reconfirmation' if stale else 'available','value':None if stale else json.loads(answer['value_json']), 'answer_id':answer['id'],'scope':answer['scope']}
            require(method == 'POST', 'Route not found', 404)
            if path in ('/v1/jobs','/v1/applications'):
                require(role in ('user','radar'),'Worker cannot create jobs or applications',403)
                require(not any(k in data for k in ('status','approved_by','approved_at')), 'Approval fields are not accepted')
            if path == '/v1/jobs':
                company_id = text_field(data,'company_id')
                name = text_field(data,'company_name')
                company = db.execute('SELECT name FROM companies WHERE id=?',(company_id,)).fetchone()
                require(company is None or company[0] == name, 'Company identity conflict', 409)
                db.execute('INSERT OR IGNORE INTO companies(id,name) VALUES(?,?)',(company_id,name))
                id = uid()
                db.execute('INSERT INTO jobs(id,company_id,title,city,canonical_url,description) VALUES(?,?,?,?,?,?)',
                           (id,company_id,text_field(data,'title'),data.get('city'),data.get('canonical_url'),data.get('description')))
                return self.row(db,'jobs',id)
            if path == '/v1/applications':
                mode = data.get('mode','simulation')
                require(mode in ('live','simulation'), 'Invalid mode')
                candidate = text_field(data,'candidate_id'); job = text_field(data,'job_id'); campaign = text_field(data,'campaign_id')
                existing = db.execute('SELECT * FROM applications WHERE candidate_id=? AND job_id=? AND mode=?',(candidate,job,mode)).fetchone()
                if existing:
                    require(existing['campaign_id'] == campaign,'Existing application belongs to another campaign',409)
                    return dict(existing)
                id = uid()
                db.execute('INSERT INTO applications(id,candidate_id,campaign_id,job_id,mode) VALUES(?,?,?,?,?)',(id,candidate,campaign,job,mode))
                return self.row(db,'applications',id)
            if len(parts) == 4 and parts[1] == 'applications':
                app = self.row(db,'applications',parts[2]); action = parts[3]
                self.revision(app,data)
                if action == 'approve':
                    require(role == 'user','Only the user can approve jobs',403)
                    require(app['status']=='pending_review','Application is not awaiting approval',409)
                    return self.move(db,app,'approved',approved_at=now(),approved_by='user')
                if action == 'transition':
                    status = text_field(data,'status')
                    allowed = {'user':{'skipped','withdrawn'},'worker':{'filling','ready_to_submit','failed'}}
                    require(status in allowed[role], 'Use a dedicated endpoint for this action',403)
                    require(app['status'] not in ('submitting','submission_unknown'), 'Use submission result verification',409)
                    require(not db.execute("SELECT 1 FROM user_tasks WHERE application_id=? AND status='open'",(app['id'],)).fetchone() or status=='withdrawn','Resolve open tasks first',409)
                    result = self.move(db,app,status)
                    if status=='withdrawn':
                        db.execute("UPDATE user_tasks SET status='cancelled' WHERE application_id=? AND status='open'",(app['id'],))
                    return result
                if action == 'tasks':
                    require(role=='worker','Worker endpoint',403)
                    require(app['status'] in ('filling','needs_user_input','ready_to_submit'),'Cannot pause this state',409)
                    kind = data.get('kind')
                    require(kind in ('missing_information','captcha','login','manual_review'),'Invalid task kind')
                    field = data.get('field_key')
                    if kind=='missing_information':
                        text_field(data,'field_key')
                    prompt = text_field(data,'prompt')
                    existing = db.execute("SELECT * FROM user_tasks WHERE application_id=? AND status='open' AND kind=? AND field_key IS ?",(app['id'],kind,field)).fetchone()
                    if existing:
                        return {'task':dict(existing),'application':app}
                    id = uid()
                    db.execute('INSERT INTO user_tasks(id,application_id,kind,field_key,prompt) VALUES(?,?,?,?,?)',(id,app['id'],kind,field,prompt))
                    result = self.move(db,app,'needs_user_input')
                    return {'task':self.row(db,'user_tasks',id),'application':result}
                if action == 'submit':
                    require(role=='worker','Worker endpoint',403)
                    require(app['mode']=='simulation','Live submission remains disabled until a real adapter is connected',409)
                    require(app['status']=='ready_to_submit','Application is not ready',409)
                    require(not db.execute("SELECT 1 FROM user_tasks WHERE application_id=? AND status='open'",(app['id'],)).fetchone(),'Resolve tasks first',409)
                    key = text_field(data,'idempotency_key')
                    candidate = self.row(db,'candidates',app['candidate_id'])
                    profile = json.loads(candidate['profile_json'])
                    profile['application_answers'] = [dict(r) for r in db.execute('SELECT * FROM candidate_answers WHERE candidate_id=? AND superseded_at IS NULL',(app['candidate_id'],))]
                    asset = next((a for a in profile.get('resume_assets',[]) if a.get('is_default')),None)
                    require(asset is not None,'No default resume',409)
                    source = next((s for s in profile['sources'] if s['id']==asset['source_id']),None)
                    require(source is not None and bool(source.get('sha256')),'Resume digest missing',409)
                    attempt = uid()
                    db.execute("INSERT INTO submission_attempts(id,application_id,idempotency_key,status) VALUES(?,?,?,'running')",(attempt,app['id'],key))
                    result = self.move(db,app,'submitting',profile_snapshot_json=encode(profile),resume_library_file_id=asset['library_file_id'],resume_sha256=source['sha256'])
                    return {'application':result,'attempt_id':attempt}
                if action == 'submission-result':
                    require(role=='worker','Worker endpoint',403)
                    require(app['mode']=='simulation','Live submission verification is not connected',409)
                    attempt = self.row(db,'submission_attempts',text_field(data,'attempt_id'))
                    require(attempt['application_id']==app['id'],'Attempt belongs to another application',409)
                    require(attempt['status'] in ('running','unknown'),'Attempt is already finalized',409)
                    outcome = data.get('outcome')
                    require(outcome in ('succeeded','failed','unknown'),'Invalid outcome')
                    require(app['status'] in ('submitting','submission_unknown'),'No active submission',409)
                    fields = {}; evidence = None
                    if outcome=='succeeded':
                        evidence = {'kind':'simulation','reference':'simulation-attempt:'+attempt['id']}
                        fields = {'submitted_at':now(),'submission_evidence_json':encode(evidence)}
                    if outcome=='failed':
                        require(data.get('verified_not_submitted') is True,'Confirm non-submission before allowing retry',409)
                    status = {'succeeded':'submitted','failed':'failed','unknown':'submission_unknown'}[outcome]
                    db.execute('UPDATE submission_attempts SET status=?,finished_at=?,evidence_json=? WHERE id=?',(outcome,None if outcome=='unknown' else now(),encode(evidence) if evidence else None,attempt['id']))
                    return self.move(db,app,status,**fields)
            if len(parts)==4 and parts[1]=='tasks' and parts[3]=='resolve':
                require(role=='user','Only user can resolve tasks',403)
                task = self.row(db,'user_tasks',parts[2]); app = self.row(db,'applications',task['application_id'])
                self.revision(app,data)
                require(task['status']=='open' and app['status']=='needs_user_input','Task is no longer pending',409)
                answer_id = None
                if task['kind']=='missing_information':
                    answer_id = self.save_answer(db,app,task,data)
                else:
                    require(data.get('completed') is True,'Confirm completion in the browser')
                db.execute("UPDATE user_tasks SET status='resolved',resolved_at=?,resolution_json=? WHERE id=?",(now(),encode({'completed':True,'answer_id':answer_id}),task['id']))
                remaining = db.execute("SELECT count(*) FROM user_tasks WHERE application_id=? AND status='open'",(app['id'],)).fetchone()[0]
                result = self.move(db,app,'needs_user_input' if remaining else 'filling')
                return {'application':result,'answer_id':answer_id,'remaining_tasks':remaining}
            raise APIError(404,'Route not found')

    def __call__(self, environ, start_response):
        status = 200
        try:
            # No CORS: browser clients must use an authenticated same-origin backend.
            require(not environ.get('HTTP_ORIGIN') or environ.get('HTTP_ORIGIN') == self.browser_origin, 'Origin denied',403)
            header = environ.get('HTTP_AUTHORIZATION','')
            role = next((role for role,token in self.tokens.items() if hmac.compare_digest(header.encode(),('Bearer '+token).encode())),None)
            require(role is not None,'Authentication required',401)
            method = environ['REQUEST_METHOD']
            data = {}
            if method=='POST':
                require(environ.get('CONTENT_TYPE','').split(';')[0]=='application/json','Use application/json',415)
                try:
                    length = int(environ.get('CONTENT_LENGTH') or '0')
                except ValueError:
                    raise APIError(400,'Invalid content length')
                require(0 < length <= 262144,'Invalid or oversized request body',413)
                try:
                    data = json.loads(environ['wsgi.input'].read(length),parse_constant=lambda s: (_ for _ in ()).throw(ValueError(s)))
                except (ValueError,UnicodeDecodeError):
                    raise APIError(400,'Invalid JSON')
                require(isinstance(data,dict),'JSON body must be an object')
            result = self.route(method,environ.get('PATH_INFO',''),parse_qs(environ.get('QUERY_STRING','')),data,role)
        except APIError as error:
            status=error.status; result={'error':error.message}
        except sqlite3.IntegrityError:
            status=409; result={'error':'Database constraint conflict: refresh state and verify the operation'}
        except sqlite3.OperationalError:
            status=503; result={'error':'Database unavailable; retry reads, verify state before retrying writes'}
        except Exception:
            status=500; result={'error':'Internal error'}
        payload=encode(result).encode('utf-8')
        from http import HTTPStatus
        start_response(f'{status} {HTTPStatus(status).phrase}', [('Content-Type','application/json; charset=utf-8'),('Content-Length',str(len(payload))),('Cache-Control','no-store'),('X-Content-Type-Options','nosniff')])
        return [payload]


if __name__=='__main__':
    from state_api import StateAPI
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',default=str(ROOT/'recruitment.sqlite3'))
    parser.add_argument('--port',type=int,default=8765)
    args=parser.parse_args()
    initialize(args.db)
    app=StateAPI(args.db,os.environ.get('RECRUITMENT_USER_TOKEN'),os.environ.get('RECRUITMENT_WORKER_TOKEN'),os.environ.get('RECRUITMENT_RADAR_TOKEN'))
    print(f'State API listening on http://127.0.0.1:{args.port}; live submission disabled',flush=True)
    with make_server('127.0.0.1',args.port,app) as server:
        server.serve_forever()
