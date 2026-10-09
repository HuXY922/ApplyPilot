"""Real Gunicorn HTTP process restart, using temporary database and credentials."""
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

class GunicornTests(unittest.TestCase):
    def test_production_http_restart_preserves_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            with socket.socket() as sock:
                sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
            tokens={r:secrets.token_urlsafe(48) for r in ('USER','RADAR','WORKER')}
            env={**os.environ,'PORT':str(port),'DATABASE_PATH':tmp+'/career.sqlite3','PUBLIC_ORIGIN':'https://career.example.com',**{'RECRUITMENT_'+r+'_TOKEN':t for r,t in tokens.items()}}
            for key in list(env):
                if key.startswith('RAILWAY_'):env.pop(key)
            def request(path,role=None,data=None):
                headers={'Host':'career.example.com','Content-Type':'application/json'}
                if role:headers['Authorization']='Bearer '+tokens[role]
                raw=json.dumps(data).encode() if data is not None else None
                try:
                    with urlopen(Request(f'http://127.0.0.1:{port}'+path,data=raw,headers=headers),timeout=2) as r:return r.status,json.load(r)
                except HTTPError as e:return e.code,json.load(e)
            aid=None
            for turn in range(2):
                process=subprocess.Popen([sys.executable,'-m','gunicorn','--config','gunicorn.conf.py','production_wsgi:application'],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
                try:
                    deadline=time.monotonic()+8
                    while True:
                        try:
                            status,_=request('/healthz')
                            if status==200:break
                        except (URLError,TimeoutError,ConnectionError):pass
                        if process.poll() is not None or time.monotonic()>deadline:self.fail('Gunicorn failed to become ready')
                        time.sleep(.05)
                    self.assertEqual(request('/v1/dashboard')[0],401)
                    if turn==0:
                        batch=json.loads(Path('radar-batch.json').read_text())
                        status,body=request('/v1/radar/batch','RADAR',batch);self.assertEqual(status,200)
                        aid=body['items'][0]['application']['id']
                        for role in ('RADAR','WORKER'):
                            self.assertEqual(request('/v1/applications/'+aid+'/approve',role,{'expected_revision':1})[0],403)
                        self.assertEqual(request('/v1/applications/'+aid+'/approve','USER',{'expected_revision':1})[0],200)
                    else:
                        status,body=request('/v1/applications/'+aid,'USER');self.assertEqual(status,200)
                        self.assertEqual(body['status'],'approved');self.assertEqual(body['approved_by'],'user')
                        status,body=request('/v1/dashboard?mode=simulation','USER');self.assertEqual(len(body['queue']),1)
                finally:
                    process.terminate()
                    try:out,err=process.communicate(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill();out,err=process.communicate()
                    for t in tokens.values():self.assertNotIn(t.encode(),out+err)
