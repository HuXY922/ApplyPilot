"""Local same-origin UI + unchanged StateAPI execution; no proxy credentials."""
import argparse
import os
from wsgiref.simple_server import make_server
from state_api import StateAPI
from db import initialize, ROOT


class DashboardServer:
    def __init__(self,api,host): self.api,self.host=api,host
    def __call__(self,env,start):
        if env.get('HTTP_HOST')!=self.host:
            start('403 Forbidden',[('Content-Type','text/plain')]); return [b'Host denied']
        paths={'/':'index.html','/app.js':'app.js','/style.css':'style.css'}
        path=env.get('PATH_INFO','')
        if env['REQUEST_METHOD']=='GET' and path in paths:
            file=ROOT/'dashboard'/paths[path]
            types={'.html':'text/html','.js':'text/javascript','.css':'text/css'}
            body=file.read_bytes()
            start('200 OK',[('Content-Type',types[file.suffix]+'; charset=utf-8'),('Content-Length',str(len(body))),('Cache-Control','no-store'),('X-Content-Type-Options','nosniff'),('Content-Security-Policy',"default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")])
            return [body]
        return self.api(env,start)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--db',default=str(ROOT/'recruitment.sqlite3'))
    parser.add_argument('--port',type=int,default=8765)
    args=parser.parse_args(); initialize(args.db)
    host=f'127.0.0.1:{args.port}'
    api=StateAPI(args.db,os.environ.get('RECRUITMENT_USER_TOKEN'),os.environ.get('RECRUITMENT_WORKER_TOKEN'),os.environ.get('RECRUITMENT_RADAR_TOKEN'),browser_origin='http://'+host)
    if 'radar' not in api.tokens: raise ValueError('RECRUITMENT_RADAR_TOKEN required')
    print('CAREER DESK: http://'+host,flush=True)
    with make_server('127.0.0.1',args.port,DashboardServer(api,host)) as server: server.serve_forever()
