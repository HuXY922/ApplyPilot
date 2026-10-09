#!/usr/bin/env python3
"""Real loopback HTTP + SQLite smoke; --live adds bounded public job reads.

Uses fictional data, no model credentials, no external application submission.
"""
import argparse
import http.cookiejar
import json
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path
from wsgiref.simple_server import make_server
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from applypilot_core.server import create_app,QuietHandler


def main():
    p=argparse.ArgumentParser();p.add_argument('--live',action='store_true');p.add_argument('--output');args=p.parse_args()
    report={'kind':'real_loopback_http_and_sqlite','live_source_requested':args.live,'checks':[]}
    with tempfile.TemporaryDirectory(prefix='applypilot-http-') as directory:
        server=make_server('127.0.0.1',0,lambda e,s:[],handler_class=QuietHandler)
        origin='http://127.0.0.1:'+str(server.server_port)
        app=create_app(data_root=directory,origin=origin);server.set_app(app)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        client=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        csrf=''
        def call(path,data=None,expected=200,who=None):
            h={'Origin':origin,'Content-Type':'application/json','X-CSRF-Token':csrf}
            req=urllib.request.Request(origin+path,data=None if data is None else json.dumps(data).encode(),headers=h)
            try:r=(who or client).open(req,timeout=90)
            except urllib.error.HTTPError as e:r=e
            with r:
                value=json.load(r);assert r.code==expected,(path,r.code,value)
                return value
        try:
            state=call('/api/state');csrf=state['csrf'];assert len(state['jobs'])==3
            report['checks'].append('isolated_fictional_seed')
            saved=call('/api/profile',{'profile':state['profile'],'confirmed':True,'expected_version':state['profile_version']})
            report['checks'].append('review_and_confirm_profile')
            job=state['jobs'][0];jid=job['id']
            analysis=call('/api/jobs/'+jid+'/analyze',{})['analysis']
            resume=call('/api/jobs/'+jid+'/resume',{})['resume']
            report['analysis_mode']=analysis.get('mode');report['resume_mode']=resume.get('mode')
            report['checks'].append('analysis_and_versioned_resume_via_http')
            action={'status':'skipped','idempotency_key':'smoke-skip-0001','note':'Fictional smoke test'}
            call('/api/jobs/'+jid+'/track',action);call('/api/jobs/'+jid+'/track',action)
            events=call('/api/jobs/'+jid+'/events')['events']
            assert sum(x['to_status']=='skipped' for x in events)==1
            report['checks'].append('idempotent_tracker_event')
            second=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
            other=call('/api/state',who=second);assert all(x['tracker_status']=='saved' for x in other['jobs'])
            report['checks'].append('second_session_isolation')
            server.set_app(create_app(data_root=directory,origin=origin))
            restored=call('/api/state');assert restored['profile_confirmed'];assert next(x for x in restored['jobs'] if x['id']==jid)['tracker_status']=='skipped'
            report['checks'].append('app_recreation_persistence')
            if args.live:
                result=call('/api/search',{'query':'评测','recruitment_type':'campus','cities':['上海','深圳']})
                assert result['jobs'], 'No matching live verified jobs returned'
                report['live_jobs']=[{'id':j['id'],'title':j['title'],'url':j['canonical_url'],'verified_at':j['verification']['verified_at']} for j in result['jobs']]
                report['live_run_status']=result['run']['status']
                report['checks'].append('live_official_search_through_http_and_persistence')
            report['passed']=True
        finally:server.shutdown();server.server_close()
    text=json.dumps(report,ensure_ascii=False,indent=2);print(text)
    if args.output:Path(args.output).write_text(text+'\n')
if __name__=='__main__':main()
