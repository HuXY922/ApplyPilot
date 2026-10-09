import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from db import initialize,connect,ROOT
from state_api import StateAPI
from dashboard_server import DashboardServer


class RadarTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.path=Path(self.temp.name)/'db.sqlite3';initialize(self.path)
        self.api=StateAPI(self.path,'u'*32,'w'*32,'r'*32,browser_origin='http://127.0.0.1:8765')
        self.batch=json.loads((ROOT/'radar-batch.json').read_text())
        # Fictional fixtures must be visibly separate from the live queue.
        self.assertEqual(self.batch['mode'],'simulation')
    def tearDown(self): self.temp.cleanup()
    def call(self,method,path,data=None,role='radar',query='',origin=None,expected=200):
        raw=json.dumps(data or {}).encode(); statuses=[]
        env={'REQUEST_METHOD':method,'PATH_INFO':path,'QUERY_STRING':query,'CONTENT_TYPE':'application/json','CONTENT_LENGTH':str(len(raw)),'HTTP_AUTHORIZATION':'Bearer '+{'radar':'r','user':'u','worker':'w'}[role]*32,'wsgi.input':io.BytesIO(raw)}
        if origin: env['HTTP_ORIGIN']=origin
        result=json.loads(b''.join(self.api(env,lambda s,h:statuses.append(s))))
        self.assertEqual(int(statuses[0].split()[0]),expected,result);return result
    def ingest(self): return self.call('POST','/v1/radar/batch',self.batch)['items'][0]['application']
    def dash(self): return self.call('GET','/v1/dashboard',role='user',query='date='+self.batch['radar_date']+'&mode=simulation')
    def test_import_approve_queue_and_audit(self):
        app=self.ingest();self.assertEqual(len(self.dash()['today']),2)
        self.call('POST',f"/v1/applications/{app['id']}/approve",{'expected_revision':1},'user',origin='http://127.0.0.1:8765')
        self.assertEqual(len(self.dash()['queue']),1)
        with connect(self.path) as db:
            self.assertEqual(db.execute('SELECT status FROM applications WHERE id=?',(app['id'],)).fetchone()[0],'approved')
            self.assertEqual([r[0] for r in db.execute('SELECT to_status FROM application_events WHERE application_id=? ORDER BY sequence',(app['id'],))],['pending_review','approved'])
    def test_idempotency_preserves_approval(self):
        app=self.ingest();self.call('POST',f"/v1/applications/{app['id']}/approve",{'expected_revision':1},'user')
        again=self.call('POST','/v1/radar/batch',self.batch);self.assertEqual(again['created_applications'],0)
        self.assertEqual(again['items'][0]['application']['status'],'approved')
        with connect(self.path) as db:
            for table,count in [('companies',1),('jobs',2),('applications',2),('job_recommendations',2)]: self.assertEqual(db.execute('SELECT count(*) FROM '+table).fetchone()[0],count)
    def test_permissions(self):
        app=self.ingest()
        for role in ('radar','worker'):
            self.call('POST',f"/v1/applications/{app['id']}/approve",{'expected_revision':1},role,expected=403)
            self.call('POST',f"/v1/applications/{app['id']}/transition",{'expected_revision':1,'status':'approved'},role,expected=403)
        self.call('POST','/v1/radar/batch',self.batch,'worker',expected=403)
        self.call('GET','/v1/candidates',role='radar',expected=403)
    def test_worker_queue_only_approved(self):
        app=self.ingest();self.assertEqual(self.call('GET','/v1/applications',role='worker',query='mode=simulation')['items'],[])
        self.call('POST',f"/v1/applications/{app['id']}/approve",{'expected_revision':1},'user')
        self.assertEqual(len(self.call('GET','/v1/applications',role='worker',query='mode=simulation')['items']),1)
    def test_atomic_invalid_batch(self):
        self.batch['jobs'][1]['priority']='P9';self.call('POST','/v1/radar/batch',self.batch,expected=400)
        with connect(self.path) as db:
            for table in ('companies','jobs','applications','job_recommendations'): self.assertEqual(db.execute('SELECT count(*) FROM '+table).fetchone()[0],0)
    def test_reject_approval_fields_and_invalid_types(self):
        for key,value in [('status','approved'),('match_score',True),('match_reasons','bad'),('canonical_url','javascript:alert(1)')]:
            b=copy.deepcopy(self.batch);b['jobs'][0][key]=value;self.call('POST','/v1/radar/batch',b,expected=400)
    def test_skip_history_stale_conflict(self):
        app=self.ingest();path=f"/v1/applications/{app['id']}/transition"
        self.call('POST',path,{'expected_revision':1,'status':'skipped'},'user')
        self.assertEqual(self.dash()['history'][0]['status'],'skipped')
        self.call('POST',f"/v1/applications/{app['id']}/approve",{'expected_revision':1},'user',expected=409)
    def test_bad_origin_and_host(self):
        self.call('GET','/v1/dashboard',role='user',origin='https://attacker.invalid',expected=403)
        response=[];DashboardServer(self.api,'127.0.0.1:8765')({'HTTP_HOST':'attacker.invalid'},lambda s,h:response.append(s));self.assertTrue(response[0].startswith('403'))
    def test_date_and_mode_filter(self):
        self.ingest();self.assertEqual(self.call('GET','/v1/dashboard',role='user',query='date=2000-01-01&mode=simulation')['today'],[])
        self.assertEqual(self.call('GET','/v1/dashboard',role='user',query='mode=live')['today'],[])
    def test_v1_migration_preserves_profile(self):
        with connect(self.path) as db:
            before=db.execute('SELECT profile_json FROM candidates').fetchone()[0]
            db.execute('DROP TABLE job_recommendations');db.execute('DELETE FROM schema_migrations WHERE version=2');db.execute('PRAGMA user_version=1')
        initialize(self.path);initialize(self.path)
        with connect(self.path) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],4)
            self.assertEqual(db.execute('SELECT profile_json FROM candidates').fetchone()[0],before)
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])
