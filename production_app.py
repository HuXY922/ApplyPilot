"""Production factory wrapping the frozen StateAPI and DashboardServer."""
import fcntl
import hmac
import sqlite3
import os
from datetime import datetime, timezone
from contextlib import closing
from db import initialize
from state_api import StateAPI
from dashboard_server import DashboardServer
from production_config import ProductionConfig

class ProductionApp:
    def __init__(self, config):
        self.config = config
        self.api = StateAPI(config.database_path,config.user_token,config.worker_token,config.radar_token,browser_origin=config.public_origin)
        from radar_console import RadarConsole
        self.radar = RadarConsole(self.api,config)
        self.dashboard = DashboardServer(self.api,config.host)

    @staticmethod
    def response(start, status, payload):
        start(status,[('Content-Type','application/json'),('Content-Length',str(len(payload))),('Cache-Control','no-store'),('X-Content-Type-Options','nosniff')])
        return [payload]

    def __call__(self, env, start):
        host=env.get('HTTP_HOST',''); origin=env.get('HTTP_ORIGIN')
        # Proxy headers never select an allowed Host or Origin.
        if origin is not None and origin != self.config.public_origin:
            return self.response(start,'403 Forbidden',b'{"error":"Origin denied"}')
        if env.get('PATH_INFO')=='/healthz':
            allowed={self.config.host,'healthcheck.railway.app',f'healthcheck.railway.app:{self.config.port}'}
            if host not in allowed:
                return self.response(start,'403 Forbidden',b'{"error":"Host denied"}')
            if env.get('REQUEST_METHOD')!='GET':
                return self.response(start,'405 Method Not Allowed',b'{"error":"Method denied"}')
            try:
                # Read-only: health probes must not recreate a disappeared volume/database.
                with closing(sqlite3.connect(self.config.database_path.as_uri()+'?mode=ro',uri=True,timeout=2)) as db:
                    version=db.execute('PRAGMA user_version').fetchone()[0]
                    # A schema version alone cannot prove all required tables
                    # survived. These are the complete v1-v4 runtime tables.
                    for table in ('schema_migrations','candidates','campaigns','companies','jobs','job_sources',
                                  'applications','allowed_transitions','application_events','user_tasks',
                                  'candidate_answers','submission_attempts','inbox_messages',
                                  'job_recommendations','radar_runs','radar_observations',
                                  'radar_decisions','radar_sessions'):
                        db.execute('SELECT * FROM '+table+' LIMIT 0').fetchall()
                    if version != 4: raise sqlite3.DatabaseError()
            except sqlite3.Error:
                return self.response(start,'503 Service Unavailable',b'{"status":"unavailable"}')
            return self.response(start,'200 OK',b'{"status":"ok"}')
        if host != self.config.host:
            return self.response(start,'403 Forbidden',b'{"error":"Host denied"}')
        if env.get('PATH_INFO','') == '/radar' or env.get('PATH_INFO','').startswith('/radar/'):
            return self.radar(env,start)
        # Browser requests may carry only the user role. Agent HTTP clients omit Origin.
        browser_request = origin is not None or bool(env.get('HTTP_SEC_FETCH_MODE'))
        header=env.get('HTTP_AUTHORIZATION','')
        if browser_request and env.get('PATH_INFO','').startswith('/v1/'):
            if not hmac.compare_digest(header.encode(),('Bearer '+self.config.user_token).encode()):
                return self.response(start,'401 Unauthorized',b'{"error":"User authentication required"}')
        def secured(status,headers,exc_info=None):
            headers += [('Strict-Transport-Security','max-age=31536000'),('Referrer-Policy','no-referrer')]
            return start(status,headers,exc_info) if exc_info else start(status,headers)
        return self.dashboard(env,secured)


def create_application(env=None):
    config=ProductionConfig.from_env(env)
    config.database_path.parent.mkdir(parents=True,exist_ok=True)
    # Serialize init across workers/redeploy overlap; no migration runs at image build.
    lock_path=config.database_path.with_suffix(config.database_path.suffix+'.initialize.lock')
    with open(lock_path,'a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        # Consistent SQLite backup, inside the existing volume, before any v1/v2/v3 upgrade.
        # Never overwrite an earlier backup. No credentials or profile data are logged.
        if config.database_path.is_file():
            with closing(sqlite3.connect(config.database_path.as_uri()+'?mode=ro',uri=True)) as source:
                version=source.execute('PRAGMA user_version').fetchone()[0]
                if 1 <= version < 4:
                    backup=config.database_path.with_name(config.database_path.name+'.pre-v4-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.bak')
                    fd=os.open(backup,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd)
                    with closing(sqlite3.connect(backup)) as target: source.backup(target)
        initialize(config.database_path)
        config.database_path.chmod(0o600)
    return ProductionApp(config)
