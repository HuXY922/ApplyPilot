"""Same-origin ApplyPilot WSGI server. Deliberately excludes all legacy API routes."""
import argparse
from http import HTTPStatus
from http.cookies import SimpleCookie, CookieError
import importlib
import io
import json
import logging
import os
from pathlib import Path
import re
import secrets
import sqlite3
from urllib.parse import urlsplit
from wsgiref.simple_server import make_server, WSGIRequestHandler

from db import ROOT
from .service import Service
from .store import DomainError, SessionManager, Store, connect, encode, require

MAX_BODY = 3*1024*1024
LOG = logging.getLogger('applypilot')


def _unique_object(pairs):
    result = {}
    for key,value in pairs:
        if key in result:
            raise ValueError('duplicate key')
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError('non-finite JSON number')


class ApplyPilotApp:
    def __init__(self, data_root, origin='http://127.0.0.1:8787', public=False,
                 max_sessions=100, ttl_seconds=86400, new_per_minute=12, runtime=None, web_root=None,
                 model_calls_per_day=20, session_model_calls_per_day=5,
                 searches_per_hour=60, session_searches_per_hour=10, railway_healthcheck=False):
        parsed = urlsplit(origin)
        if (parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password
                or parsed.path not in ('','/') or parsed.query or parsed.fragment):
            raise ValueError('PUBLIC_ORIGIN must be one exact HTTP(S) origin')
        if public and parsed.scheme != 'https':
            raise ValueError('Public demos require an HTTPS PUBLIC_ORIGIN')
        if not public and parsed.scheme == 'http' and parsed.hostname not in ('127.0.0.1','localhost','::1'):
            raise ValueError('Non-loopback access requires HTTPS and APPLYPILOT_PUBLIC=1')
        self.origin = origin.rstrip('/')
        self.host = parsed.netloc.lower()
        self.secure = parsed.scheme == 'https'
        self.public = public
        self.railway_healthcheck = bool(railway_healthcheck)
        self.sessions = SessionManager(data_root,max_sessions,ttl_seconds,new_per_minute)
        self.runtime = runtime
        self.budgets = {'model':(max(0,int(model_calls_per_day)),max(0,int(session_model_calls_per_day))),
                        'search':(max(0,int(searches_per_hour)),max(0,int(session_searches_per_hour)))}
        self.web_root = Path(web_root or ROOT/'applypilot_web').resolve()

    def _session(self, environ, create=False):
        cookie = SimpleCookie()
        raw_cookie = environ.get('HTTP_COOKIE','')
        require(len(raw_cookie) <= 8192, 'invalid_cookie','Cookie header is too large.',400)
        try:
            cookie.load(raw_cookie)
        except CookieError:
            cookie = SimpleCookie()
        token = cookie[self.sessions.cookie_name].value if self.sessions.cookie_name in cookie else None
        session = self.sessions.get(token)
        header = None
        if session is None and create:
            token, session = self.sessions.create(environ.get('REMOTE_ADDR','unknown'))
            header = f'{self.sessions.cookie_name}={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={self.sessions.ttl}'
            if self.secure:
                header += '; Secure'
        require(session is not None, 'session_required', 'Open this demo workspace again before continuing.',401)
        return session,header

    def _read_json(self, environ):
        require(environ.get('CONTENT_TYPE','').split(';',1)[0].strip().lower() == 'application/json',
                'content_type','Use application/json.',415)
        try:
            length = int(environ.get('CONTENT_LENGTH',''))
        except (ValueError,TypeError):
            raise DomainError(411,'content_length','Content-Length is required.') from None
        require(0 <= length <= MAX_BODY, 'body_too_large','Request is too large.',413)
        require(not environ.get('HTTP_TRANSFER_ENCODING'), 'invalid_request','Transfer-Encoding is unsupported.',400)
        raw = environ.get('wsgi.input',io.BytesIO()).read(length)
        require(len(raw) == length, 'invalid_json','Request body is incomplete.')
        try:
            data = json.loads(raw.decode('utf-8'),object_pairs_hook=_unique_object,parse_constant=_invalid_constant)
        except (UnicodeDecodeError,ValueError,RecursionError):
            raise DomainError(400,'invalid_json','Expected a valid JSON object without duplicate keys.') from None
        require(isinstance(data,dict), 'invalid_json','Expected a JSON object.')
        return data

    def _guard(self, environ, write=False):
        host = environ.get('HTTP_HOST','').lower()
        require(host == self.host, 'invalid_host','This host is not permitted.',403)
        origin = environ.get('HTTP_ORIGIN')
        if origin is not None or write:
            require(origin == self.origin, 'invalid_origin','Same-origin requests are required.',403)
        require(environ.get('HTTP_SEC_FETCH_SITE','') not in ('cross-site',), 'cross_site','Cross-site requests are not permitted.',403)

    def _dispatch(self, environ):
        method = environ.get('REQUEST_METHOD','GET').upper()
        path = environ.get('PATH_INFO','/')
        require(method in ('GET','POST','HEAD'), 'method_not_allowed','Method is not supported.',405)
        # Railway probes use their documented dedicated Host, never the app host.
        # This exception serves one read-only health route only in public Railway mode.
        if (self.public and self.railway_healthcheck and method == 'GET' and path == '/healthz'
                and environ.get('HTTP_HOST','').lower() == 'healthcheck.railway.app'
                and not environ.get('HTTP_ORIGIN') and environ.get('HTTP_SEC_FETCH_SITE','') != 'cross-site'):
            return 200,self.sessions.health(),None,None
        self._guard(environ,method == 'POST')
        if method in ('GET','HEAD') and path in ('/healthz','/api/health'):
            return 200,self.sessions.health(),None,None
        assets = {'/':'index.html','/index.html':'index.html','/app.js':'app.js','/style.css':'style.css',
                  '/demo-form':'demo-form.html','/demo-form.html':'demo-form.html','/demo-form.js':'demo-form.js','/demo-form.css':'demo-form.css'}
        if method in ('GET','HEAD') and path in assets:
            target = self.web_root/assets[path]
            require(target.is_file(),'not_found','Page not found.',404)
            mime = 'text/html; charset=utf-8' if target.suffix == '.html' else 'application/javascript; charset=utf-8' if target.suffix == '.js' else 'text/css; charset=utf-8'
            return 200,target.read_bytes(),None,mime
        # Nothing below forwards to StateAPI, Radar or any arbitrary external URL.
        require(path.startswith('/api/'), 'not_found','Route not found.',404)
        session,cookie = self._session(environ,create=method == 'GET' and path == '/api/state')
        store = Store(self.sessions.path(session['session_hash']))
        def reserve(kind):
            self.sessions.reserve_operation(session['session_hash'],kind,*self.budgets[kind])
        service = Service(store,runtime=self.runtime,reserve=reserve)
        if method == 'GET' and path == '/api/state':
            state = service.state()
            state.update(csrf=session['csrf'],session_expires_at=session['expires_at'])
            return 200,state,cookie,None
        if method == 'POST':
            csrf = environ.get('HTTP_X_CSRF_TOKEN','')
            require(isinstance(csrf,str) and len(csrf) <= 100 and csrf.isascii() and secrets.compare_digest(csrf,session['csrf']), 'csrf','Refresh the page before making changes.',403)
            self.sessions.limit_write(session['session_hash'])
            data = self._read_json(environ)
            if path == '/api/profile/import':
                return 200,service.import_profile(data),cookie,None
            if path == '/api/profile':
                return 200,service.save_profile(data),cookie,None
            if path == '/api/search':
                return 200,service.search(data),cookie,None
        else:
            data = {}
        match = re.fullmatch(r'/api/jobs/([A-Za-z0-9_-]{1,100})/(analyze|resume|track|events)',path)
        if match:
            jid, action = match.groups()
            if method == 'GET' and action == 'events':
                return 200,{'events':store.events(jid)},cookie,None
            if method == 'POST' and action == 'track':
                return 200,service.track(jid,data),cookie,None
            if method == 'POST' and action in ('analyze','resume'):
                return 200,service.generate(jid,'analysis' if action == 'analyze' else 'resume',data),cookie,None
        demo_routes = {'/api/demo/prepare':'prepare','/api/demo/submit':'submit','/api/demo/cancel':'cancel','/api/demo/receipts':'receipts'}
        if path in demo_routes and ((path.endswith('/receipts') and method == 'GET') or (not path.endswith('/receipts') and method == 'POST')):
            module = importlib.import_module('applypilot_core.form_demo')
            db = connect(store.path)
            try:
                module.ensure_schema(db)
                action = getattr(module,demo_routes[path])
                result = action(db) if path.endswith('/receipts') else action(db,data)
            except module.DemoError as error:
                raise DomainError(error.status,error.code,error.message) from None
            finally:
                db.close()
            return 200,result,cookie,None
        raise DomainError(404,'not_found','Route not found.')

    def __call__(self, environ, start_response):
        cookie = None
        mime = None
        try:
            status,body,cookie,mime = self._dispatch(environ)
        except DomainError as error:
            status = error.status
            body = {'error':error.message,'code':error.code,**error.details}
        except sqlite3.Error:
            status,body = 503,{'error':'Workspace storage is temporarily unavailable.','code':'storage_unavailable'}
            LOG.error('Workspace database operation failed')
        except Exception:
            status,body = 500,{'error':'The request could not be completed.','code':'internal_error'}
            LOG.error('Unhandled ApplyPilot request failure')
        if not isinstance(body,bytes):
            body = encode(body).encode('utf-8')
        headers = [('Content-Type',mime or 'application/json; charset=utf-8'),('Content-Length',str(len(body))),
                   ('Cache-Control','no-store'),('X-Content-Type-Options','nosniff'),('X-Frame-Options','DENY'),
                   ('Referrer-Policy','no-referrer'),('Permissions-Policy','camera=(), microphone=(), geolocation=()'),
                   ('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")]
        if self.secure:
            headers.append(('Strict-Transport-Security','max-age=31536000'))
        if cookie:
            headers.append(('Set-Cookie',cookie))
        if status == 429:
            headers.append(('Retry-After','60'))
        start_response(f'{status} {HTTPStatus(status).phrase}',headers)
        return [] if environ.get('REQUEST_METHOD') == 'HEAD' else [body]


def _validate_railway_storage(env, data_root):
    """Never silently run Railway workspaces on an ephemeral filesystem."""
    if not (env.get('RAILWAY_PROJECT_ID') or env.get('RAILWAY_ENVIRONMENT_ID')):
        return
    if env.get('RAILWAY_VOLUME_MOUNT_PATH') != '/data':
        raise ValueError('Railway requires a persistent volume mounted at /data')
    if not Path('/data').is_dir():
        raise ValueError('Railway persistent volume directory /data is missing')
    try:
        resolved = Path(data_root).resolve()
    except (OSError,RuntimeError,TypeError):
        raise ValueError('APPLYPILOT_DATA_DIR must resolve within the /data volume') from None
    if not resolved.is_relative_to(Path('/data')):
        raise ValueError('APPLYPILOT_DATA_DIR must resolve within the /data volume')


def create_app(environ=None, **overrides):
    env = os.environ if environ is None else environ
    public = str(env.get('APPLYPILOT_PUBLIC','0')).lower() in ('1','true','yes')
    origin = env.get('PUBLIC_ORIGIN','')
    if public and not origin:
        raise ValueError('PUBLIC_ORIGIN is required for a public demo')
    kwargs = {'data_root':env.get('APPLYPILOT_DATA_DIR','/tmp/applypilot-demo'),
              'origin':origin or 'http://127.0.0.1:8787','public':public,
              'max_sessions':int(env.get('APPLYPILOT_MAX_SESSIONS','100')),
              'ttl_seconds':int(env.get('APPLYPILOT_SESSION_TTL','86400')),
              'new_per_minute':int(env.get('APPLYPILOT_NEW_SESSIONS_PER_MINUTE','12')),
              'model_calls_per_day':int(env.get('APPLYPILOT_MODEL_CALLS_PER_DAY','20')),
              'session_model_calls_per_day':int(env.get('APPLYPILOT_SESSION_MODEL_CALLS_PER_DAY','5')),
              'searches_per_hour':int(env.get('APPLYPILOT_SEARCHES_PER_HOUR','60')),
              'session_searches_per_hour':int(env.get('APPLYPILOT_SESSION_SEARCHES_PER_HOUR','10')),
              'railway_healthcheck':bool(env.get('RAILWAY_PROJECT_ID') or env.get('RAILWAY_ENVIRONMENT_ID'))}
    kwargs.update(overrides)
    _validate_railway_storage(env,kwargs['data_root'])
    return ApplyPilotApp(**kwargs)


create_application = create_app


class QuietHandler(WSGIRequestHandler):
    # No request URL/query/profile text in demo access logs.
    def log_message(self, format, *args):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host',default='127.0.0.1')
    parser.add_argument('--port',type=int,default=8787)
    parser.add_argument('--data-dir',default=os.environ.get('APPLYPILOT_DATA_DIR','/tmp/applypilot-demo'))
    parser.add_argument('--origin',default=None)
    args = parser.parse_args()
    origin = args.origin or os.environ.get('PUBLIC_ORIGIN') or f'http://{args.host}:{args.port}'
    app = create_app(data_root=args.data_dir,origin=origin)
    print(f'ApplyPilot demo listening at {origin}. Fictional data only; no external applications.',flush=True)
    with make_server(args.host,args.port,app,handler_class=QuietHandler) as httpd:
        httpd.serve_forever()


if __name__ == '__main__':
    main()
