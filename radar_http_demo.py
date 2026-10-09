"""Real HTTP/SQLite demonstration; DOES NOT claim a browser click."""
import json
import secrets
import tempfile
import threading
from pathlib import Path
from wsgiref.simple_server import make_server
from db import initialize,connect,ROOT
from state_api import StateAPI
from dashboard_server import DashboardServer
from fake_submitter import Client
from simulation_demo import QuietHandler


def run():
    with tempfile.TemporaryDirectory() as tmp:
        path=Path(tmp)/'demo.sqlite3';initialize(path)
        tokens=[secrets.token_urlsafe(32) for _ in range(3)]
        api=StateAPI(path,*tokens)
        server=make_server('127.0.0.1',0,api,handler_class=QuietHandler)
        host=f'127.0.0.1:{server.server_port}';api.browser_origin='http://'+host
        server.set_app(DashboardServer(api,host))
        thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':0.02});thread.start()
        try:
            user=Client('http://'+host,tokens[0]);radar=Client('http://'+host,tokens[2]);worker=Client('http://'+host,tokens[1])
            batch=json.loads((ROOT/'radar-batch.json').read_text());first=radar.post('/v1/radar/batch',batch)
            before=user.get('/v1/dashboard?date='+batch['radar_date']);app=first['items'][0]['application']
            approved=user.post('/v1/applications/'+app['id']+'/approve',{'expected_revision':app['revision']})
            repeated=radar.post('/v1/radar/batch',batch)
            after=user.get('/v1/dashboard?date='+batch['radar_date'])
            with connect(path) as db:
                stored=dict(db.execute('SELECT id,status,revision,approved_by FROM applications WHERE id=?',(app['id'],)).fetchone())
                events=[r[0] for r in db.execute('SELECT to_status FROM application_events WHERE application_id=? ORDER BY sequence',(app['id'],))]
            assert len(before['today'])==2 and len(after['queue'])==1 and stored['status']=='approved' and repeated['created_applications']==0
            assert len(worker.get('/v1/applications')['items'])==1
            return {'transport':'real HTTP + SQLite','browser_click_verified':False,'sample':'radar-batch.json; explicitly fictional jobs','imported':first['created_applications'],'dashboard_today':len(before['today']),'dashboard_queue_after':len(after['queue']),'repeat_created':repeated['created_applications'],'database_row':stored,'events':events,'result':'passed'}
        finally: server.shutdown();server.server_close();thread.join()
if __name__=='__main__':
    print(json.dumps(run(),ensure_ascii=False,indent=2))
