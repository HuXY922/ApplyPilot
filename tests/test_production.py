import copy
import io
import json
import secrets
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from production_config import ProductionConfig
from production_app import create_application
from db import connect, initialize

class ProductionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'volume/career.sqlite3'
        self.env={'PORT':'8080','DATABASE_PATH':str(self.path),'PUBLIC_ORIGIN':'https://career.example.com',**{'RECRUITMENT_'+r+'_TOKEN':secrets.token_urlsafe(48) for r in ('USER','WORKER','RADAR')}}
    def tearDown(self):self.temp.cleanup()
    def request(self,app,path='/v1/dashboard',role=None,host='career.example.com',origin=None,method='GET',data=None,extra=None):
        raw=json.dumps(data or {}).encode();env={'HTTP_HOST':host,'PATH_INFO':path,'REQUEST_METHOD':method,'QUERY_STRING':'','CONTENT_TYPE':'application/json','CONTENT_LENGTH':str(len(raw)),'wsgi.input':io.BytesIO(raw)}
        if role:env['HTTP_AUTHORIZATION']='Bearer '+self.env['RECRUITMENT_'+role+'_TOKEN']
        if origin is not None:env['HTTP_ORIGIN']=origin
        env.update(extra or {});response=[]
        body=b''.join(app(env,lambda s,h:response.append((int(s.split()[0]),h))))
        return response[0][0],body
    def test_missing_environment_rejected_before_db_creation(self):
        for key in self.env:
            env=self.env.copy();env.pop(key)
            with self.assertRaises(ValueError):create_application(env)
        self.assertFalse(self.path.exists())
    def test_tokens_distinct_and_secret_redaction(self):
        for role in ('USER','WORKER','RADAR'):
            bad=self.env.copy();bad['RECRUITMENT_'+role+'_TOKEN']='short'
            with self.assertRaises(ValueError):create_application(bad)
        bad=self.env.copy();bad['RECRUITMENT_RADAR_TOKEN']=bad['RECRUITMENT_USER_TOKEN']
        with self.assertRaises(ValueError):create_application(bad)
        cfg=ProductionConfig.from_env(self.env)
        for key,value in self.env.items():
            if 'TOKEN' in key:self.assertNotIn(value,repr(cfg))
    def test_port_validation(self):
        for value in ('0','65536','-1','abc','80\n'):
            with self.assertRaises(ValueError):ProductionConfig.from_env({**self.env,'PORT':value})
    def test_public_origin_validation(self):
        for origin in ('http://career.example.com','https://career.example.com/','https://career.example.com/path','https://user:pass@career.example.com','https://career.example.com?x=1','https://career.example.com#x','https://localhost','https://career.example.com:bad','https://*.example.com','https://career.example.com\n'):
            with self.subTest(origin=origin),self.assertRaises(ValueError):ProductionConfig.from_env({**self.env,'PUBLIC_ORIGIN':origin})
    def test_database_path_validation(self):
        for path in ('career.sqlite3',':memory:','/tmp/career.txt'):
            with self.assertRaises(ValueError):ProductionConfig.from_env({**self.env,'DATABASE_PATH':path})
    def test_railway_requires_volume(self):
        for more in ({},{'RAILWAY_VOLUME_MOUNT_PATH':'/wrong'},{'RAILWAY_VOLUME_MOUNT_PATH':'/data'}):
            with self.assertRaises(ValueError):ProductionConfig.from_env({**self.env,'RAILWAY_PROJECT_ID':'test',**more})
        with patch('production_config.Path.is_dir',return_value=True):
            cfg=ProductionConfig.from_env({**self.env,'RAILWAY_PROJECT_ID':'test','RAILWAY_VOLUME_MOUNT_PATH':'/data','DATABASE_PATH':'/data/career.sqlite3'})
            self.assertEqual(str(cfg.database_path),'/data/career.sqlite3')
    def test_first_start_empty_business_data(self):
        create_application(self.env);self.assertTrue(self.path.exists())
        with connect(self.path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM candidates').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT count(*) FROM applications').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM jobs').fetchone()[0],0)
    def test_host_origin_and_forwarded_spoofing(self):
        app=create_application(self.env)
        self.assertEqual(self.request(app,role='USER',origin=self.env['PUBLIC_ORIGIN'])[0],200)
        for host in ('evil.example','127.0.0.1','career.example.com:80','healthcheck.railway.app'):
            self.assertEqual(self.request(app,role='USER',host=host,extra={'HTTP_X_FORWARDED_HOST':'career.example.com'})[0],403)
        for origin in ('https://evil.example','http://career.example.com','null',''):
            self.assertEqual(self.request(app,role='USER',origin=origin)[0],403)
    def test_auth_and_browser_user_only(self):
        app=create_application(self.env)
        self.assertEqual(self.request(app)[0],401)
        for role in ('RADAR','WORKER'):
            self.assertEqual(self.request(app,role=role,origin=self.env['PUBLIC_ORIGIN'])[0],401)
            self.assertEqual(self.request(app,role=role,extra={'HTTP_SEC_FETCH_MODE':'cors'})[0],401)
        for path in ('/','/app.js','/style.css'):
            status,body=self.request(app,path)
            self.assertEqual(status,200)
            for key,value in self.env.items():
                if 'TOKEN' in key:self.assertNotIn(value.encode(),body)
    def test_health_public_minimal_and_original_health_private(self):
        app=create_application(self.env)
        for host in ('career.example.com','healthcheck.railway.app','healthcheck.railway.app:8080'):
            self.assertEqual(self.request(app,'/healthz',host=host),(200,b'{"status":"ok"}'))
        self.assertEqual(self.request(app,'/healthz',host='evil.example')[0],403)
        self.assertEqual(self.request(app,'/healthz',method='POST')[0],405)
        self.assertEqual(self.request(app,'/v1/health')[0],401)
        self.assertEqual(self.request(app,'/v1/health',role='RADAR')[0],200)
    def test_health_does_not_recreate_missing_database(self):
        app=create_application(self.env);self.path.unlink()
        self.assertEqual(self.request(app,'/healthz')[0],503);self.assertFalse(self.path.exists())
    def test_migration_and_reboot_preserve_existing_data(self):
        self.path.parent.mkdir();initialize(self.path)
        with connect(self.path) as db:
            db.execute('UPDATE candidates SET revision=7')
            db.execute("INSERT INTO companies(id,name) VALUES('keep','Keep')")
            db.execute('DROP TABLE job_recommendations');db.execute('DELETE FROM schema_migrations WHERE version=2');db.execute('PRAGMA user_version=1')
        create_application(self.env);create_application(self.env)
        with connect(self.path) as db:
            self.assertEqual(db.execute('SELECT revision FROM candidates').fetchone()[0],7)
            self.assertEqual(db.execute('SELECT name FROM companies').fetchone()[0],'Keep')
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],4)
    def test_permissions_after_restart(self):
        app=create_application(self.env)
        batch=json.loads(Path('radar-batch.json').read_text())
        status,body=self.request(app,'/v1/radar/batch',role='RADAR',method='POST',data=batch);self.assertEqual(status,200)
        aid=json.loads(body)['items'][0]['application']['id'];path='/v1/applications/'+aid+'/approve'
        for role in ('RADAR','WORKER'):
            self.assertEqual(self.request(app,path,role=role,method='POST',data={'expected_revision':1})[0],403)
        self.assertEqual(self.request(app,path,role='USER',method='POST',data={'expected_revision':1},origin=self.env['PUBLIC_ORIGIN'])[0],200)
        app=create_application(self.env)
        status,body=self.request(app,'/v1/applications/'+aid,role='USER');self.assertEqual(status,200)
        self.assertEqual(json.loads(body)['status'],'approved')
