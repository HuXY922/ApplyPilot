"""Actual subprocess lifecycle tests using local synthetic workers; no network."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from applypilot_core import runtime
from test_official_sources import bootstrap, row, page

URL = 'https://cxmt.zhiye.com/campus/jobs'


class SourceDeadlineTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.env = patch('applypilot_core.runtime.urllib.request.getproxies', return_value={})
        self.env.start(); self.addCleanup(self.env.stop)
        self.cooldowns = patch.dict(runtime._SOURCE_COOLDOWNS, {}, clear=True)
        self.cooldowns.start(); self.addCleanup(self.cooldowns.stop)
        self.transport = runtime.Transport()

    def worker(self, body):
        script = self.root / 'synthetic_worker.py'
        script.write_text(body)
        return patch('applypilot_core.runtime._SOURCE_WORKER', str(script))

    def fetch(self, timeout=1, max_bytes=1024):
        return self.transport.get(URL, timeout=timeout, max_bytes=max_bytes, deadline=time.monotonic()+timeout)

    def assert_slot_released(self):
        self.assertTrue(runtime._SOURCE_FETCH_SLOT.acquire(blocking=False))
        runtime._SOURCE_FETCH_SLOT.release()

    def test_fixed_host_limits_match_parent_worker_and_direct_transport(self):
        byte_url=runtime.BYTE_ORIGIN+'/api/v1/job/posts/'+runtime.DEFAULT_SEEDS[0]+'?portal_type=3&with_recommend=true'
        cases=((URL,12), (byte_url,7), ('https://api.ashbyhq.com/posting-api/job-board/fictional',12))
        script="import sys,json,time\nr=json.load(sys.stdin);b=json.dumps({'timeout':r['timeout'],'remaining':r['deadline']-time.monotonic()}).encode();sys.stdout.buffer.write(json.dumps({'ok':True,'size':len(b)}).encode()+b'\\n'+b)\n"
        for url,expected in cases:
            with self.subTest(url=url),self.worker(script):
                response=json.loads(self.transport.get(url,timeout=99,deadline=time.monotonic()+60))
                self.assertEqual(response['timeout'],expected)
                self.assertGreater(response['remaining'],expected-1)
                self.assertLessEqual(response['remaining'],expected)
            direct=runtime._DirectTransport(proxy='')
            reply=Mock();reply.getcode.return_value=200;reply.read.side_effect=[b'{}',b'']
            reply.__enter__=Mock(return_value=reply);reply.__exit__=Mock(return_value=False)
            direct.opener=Mock();direct.opener.open.return_value=reply
            with patch('applypilot_core.runtime.socket.getaddrinfo',return_value=[(None,None,None,None,('93.184.216.34',443))]):
                self.assertEqual(direct.get(url,timeout=99),b'{}')
            self.assertEqual(direct.opener.open.call_args.kwargs['timeout'],expected)
        self.assert_slot_released()

    def test_source_deadline_still_shortens_larger_host_limit(self):
        script="import sys,json,time\nr=json.load(sys.stdin);b=json.dumps({'remaining':r['deadline']-time.monotonic()}).encode();sys.stdout.buffer.write(json.dumps({'ok':True,'size':len(b)}).encode()+b'\\n'+b)\n"
        with self.worker(script):
            response=json.loads(self.transport.get(URL,timeout=12,deadline=time.monotonic()+2))
        self.assertGreater(response['remaining'],1)
        self.assertLessEqual(response['remaining'],2)

    def test_actual_worker_accepts_twelve_seconds_but_not_thirteen(self):
        for timeout,expected in ((12,'destination_denied'),(13,'source_worker_failed')):
            request={'url':'offline://fixture.invalid/no-network','body':None,'max_bytes':1024,'timeout':timeout,'deadline':time.monotonic()+timeout,'proxy':''}
            process=subprocess.run([sys.executable,'-I','-S','-B',runtime._SOURCE_WORKER],input=json.dumps(request).encode(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,env={},close_fds=True,timeout=2)
            self.assertEqual(json.loads(process.stdout),{'ok':False,'error':expected})
            self.assertEqual(process.stderr,b'')

    def test_successful_worker_returns_bytes_and_is_reaped(self):
        observed=[]; original=subprocess.Popen
        def spawn(*args, **kwargs):
            process=original(*args, **kwargs); observed.append(process); return process
        with self.worker('import sys\nsys.stdin.buffer.read()\nsys.stdout.buffer.write(b\'{"ok":true,"size":2}\\n{}\')\n'), patch('applypilot_core.runtime.subprocess.Popen', side_effect=spawn):
            self.assertEqual(self.fetch(), b'{}')
        self.assertIsNotNone(observed[0].poll())
        self.assert_slot_released()

    def test_stuck_dns_connect_header_or_body_is_killed_reaped_and_cannot_continue(self):
        for phase in ('dns','multi_address_connect','headers','body'):
            with self.subTest(phase=phase):
                marker=self.root/(phase+'.continued')
                script=("import sys,time,pathlib\nsys.stdin.buffer.read()\n"
                        + ("sys.stdout.buffer.write(b'{\\\"ok\\\":true,\\\"size\\\":2}\\n{');sys.stdout.buffer.flush()\n" if phase=='body' else '')
                        + "time.sleep(5)\npathlib.Path("+repr(str(marker))+").write_text('continued')\n")
                observed=[]; original=subprocess.Popen
                def spawn(*args, **kwargs):
                    process=original(*args, **kwargs); observed.append(process); return process
                start=time.monotonic()
                with self.worker(script), patch('applypilot_core.runtime.subprocess.Popen', side_effect=spawn), self.assertRaisesRegex(runtime.SourceError,'network_timeout'):
                    self.fetch(timeout=.18)
                self.assertLess(time.monotonic()-start, 1)
                self.assertEqual(observed[0].returncode,-signal.SIGKILL)
                self.assertFalse(marker.exists())
                self.assert_slot_released()

    def test_drip_output_cannot_extend_wall_deadline(self):
        script="import sys,time\nsys.stdin.buffer.read()\nsys.stdout.buffer.write(b'{\"ok\":true,\"size\":100}\\n');sys.stdout.buffer.flush()\nfor i in range(100):\n sys.stdout.buffer.write(b'x');sys.stdout.buffer.flush();time.sleep(.04)\n"
        start=time.monotonic()
        with self.worker(script), self.assertRaisesRegex(runtime.SourceError,'network_timeout'):
            self.fetch(timeout=.2)
        self.assertLess(time.monotonic()-start,1)
        self.assert_slot_released()

    def test_output_cap_kills_worker_before_unbounded_allocation(self):
        observed=[]; original=subprocess.Popen
        def spawn(*args, **kwargs):
            process=original(*args, **kwargs); observed.append(process); return process
        script="import sys,time\nsys.stdin.buffer.read()\nsys.stdout.buffer.write(b'x'*10000);sys.stdout.buffer.flush();time.sleep(5)\n"
        with self.worker(script), patch('applypilot_core.runtime.subprocess.Popen',side_effect=spawn), self.assertRaisesRegex(runtime.SourceError,'response_too_large'):
            self.fetch(max_bytes=50)
        self.assertEqual(observed[0].returncode,-signal.SIGKILL)
        self.assert_slot_released()

    def test_secrets_environment_pythonpath_ca_and_fds_are_not_inherited(self):
        script="import sys,os,json\nsys.stdin.buffer.read()\nr=json.dumps({'env':dict(os.environ),'fds':len(os.listdir('/proc/self/fd')),'isolated':sys.flags.isolated,'no_site':sys.flags.no_site}).encode();sys.stdout.buffer.write(json.dumps({'ok':True,'size':len(r)}).encode()+b'\\n'+r)\n"
        observed=[]; original=subprocess.Popen
        def spawn(*args, **kwargs):
            observed.append((args,kwargs));return original(*args,**kwargs)
        with self.worker(script), patch.dict(os.environ,{'APPLYPILOT_DEEPSEEK_API_KEY':'SYNTHETIC_SECRET','PYTHONPATH':'/synthetic/untrusted','SSL_CERT_FILE':'/synthetic/ca'}), patch('applypilot_core.runtime.subprocess.Popen',side_effect=spawn):
            value=json.loads(self.fetch(max_bytes=4096))
        for key in ('APPLYPILOT_DEEPSEEK_API_KEY','PYTHONPATH','SSL_CERT_FILE'):
            self.assertNotIn(key,value['env'])
        self.assertNotIn('SYNTHETIC_SECRET',json.dumps(value))
        self.assertEqual(value['isolated'],1);self.assertEqual(value['no_site'],1)
        self.assertLessEqual(value['fds'],4)
        self.assertEqual(observed[0][1]['env'],{})
        self.assertTrue(observed[0][1]['close_fds']);self.assertFalse(observed[0][1]['shell'])
        self.assertNotIn('preexec_fn',observed[0][1])

    def test_parent_interruption_still_kills_reaps_and_releases_slot(self):
        observed=[];original=subprocess.Popen
        def spawn(*args,**kwargs):
            process=original(*args,**kwargs);observed.append(process);return process
        with self.worker('import time;time.sleep(5)'),patch('applypilot_core.runtime.subprocess.Popen',side_effect=spawn),patch.object(runtime.selectors.DefaultSelector,'select',side_effect=KeyboardInterrupt),self.assertRaises(KeyboardInterrupt):
            self.fetch()
        self.assertEqual(observed[0].returncode,-signal.SIGKILL)
        self.assert_slot_released()

    def test_slot_busy_has_no_queue_and_no_child(self):
        self.assertTrue(runtime._SOURCE_FETCH_SLOT.acquire(blocking=False))
        try:
            with patch('applypilot_core.runtime.subprocess.Popen') as spawn, self.assertRaisesRegex(runtime.SourceError,'source_fetch_busy'):
                self.fetch()
            spawn.assert_not_called()
        finally:
            runtime._SOURCE_FETCH_SLOT.release()

    def test_expired_deadline_and_bad_destination_spawn_nothing(self):
        with patch('applypilot_core.runtime.subprocess.Popen') as spawn:
            with self.assertRaisesRegex(runtime.SourceError,'time_budget_exhausted'):
                self.transport.get(URL,deadline=time.monotonic()-1)
            with self.assertRaisesRegex(runtime.SourceError,'destination_denied'):
                self.transport.get('https://127.0.0.1/private')
            spawn.assert_not_called()

    def test_invalid_protocol_failed_worker_and_alarm_all_reap(self):
        for script, expected in (("import sys;sys.stdin.buffer.read();print('not a protocol')",'source_worker_protocol'),
                                 ("import sys;sys.stdin.buffer.read();raise SystemExit(3)",'source_worker_failed'),
                                 ("import sys;sys.stdin.buffer.read();raise SystemExit(124)",'network_timeout')):
            with self.subTest(expected=expected),self.worker(script),self.assertRaisesRegex(runtime.SourceError,expected):
                self.fetch()
            self.assert_slot_released()

    def test_403_and_429_metadata_update_parent_shared_pause(self):
        for code in (403,429):
            runtime._SOURCE_COOLDOWNS.clear()
            control={'ok':False,'error':'http_'+str(code),'retry_after':'1800'}
            script='import sys\nsys.stdin.buffer.read()\nprint('+repr(json.dumps(control))+')\n'
            with self.worker(script),self.assertRaisesRegex(runtime.SourceError,'http_'+str(code)):
                self.fetch()
            until,reason=runtime._SOURCE_COOLDOWNS['cxmt.zhiye.com']
            self.assertGreater(until,time.time()+1790)
            with patch('applypilot_core.runtime.subprocess.Popen') as spawn,self.assertRaisesRegex(runtime.SourceError,'source_paused_http_'+str(code)):
                self.fetch()
            spawn.assert_not_called()

    def test_existing_credential_free_proxy_only_passed_as_explicit_parameter(self):
        script="import sys,json,os\np=json.load(sys.stdin);r=json.dumps({'proxy':p['proxy'],'environment':dict(os.environ)}).encode();sys.stdout.buffer.write(json.dumps({'ok':True,'size':len(r)}).encode()+b'\\n'+r)\n"
        self.transport.proxy='http://trusted-configured-proxy:8080'
        with self.worker(script),patch('applypilot_core.runtime.urllib.request.proxy_bypass',return_value=False):
            value=json.loads(self.fetch())
        self.assertEqual(value['proxy'],'http://trusted-configured-proxy:8080')
        self.assertNotIn('https_proxy',value['environment'])
        self.assertEqual(self.transport.transport_mode,'configured_proxy_dns')

    def test_actual_worker_bootstrap_installs_resource_limits_and_orphan_alarm(self):
        script=("import sys,runpy,json,resource,signal\nsys.stdin.buffer.read()\n"
                + "runpy.run_path("+repr(runtime._SOURCE_WORKER)+",run_name='caps_probe')\n"
                + "r=json.dumps({'as':resource.getrlimit(resource.RLIMIT_AS),'cpu':resource.getrlimit(resource.RLIMIT_CPU),'files':resource.getrlimit(resource.RLIMIT_NOFILE),'alarm':signal.getitimer(signal.ITIMER_REAL)[0]}).encode();sys.stdout.buffer.write(json.dumps({'ok':True,'size':len(r)}).encode()+b'\\n'+r)\n")
        with self.worker(script):
            value=json.loads(self.fetch())
        self.assertEqual(value['as'],[96*1024*1024]*2)
        self.assertEqual(value['cpu'],[8,8])
        self.assertEqual(value['files'],[32,32])
        self.assertGreater(value['alarm'],0)
        self.assertLessEqual(value['alarm'],13)

    def test_actual_fixed_worker_refuses_unsafe_endpoint_before_network(self):
        request={'url':'https://127.0.0.1/private','body':None,'max_bytes':1024,'timeout':1,'deadline':time.monotonic()+1,'proxy':''}
        process=subprocess.run([sys.executable,'-I','-S','-B',runtime._SOURCE_WORKER],input=json.dumps(request).encode(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,env={},close_fds=True,timeout=2)
        self.assertEqual(json.loads(process.stdout),{'ok':False,'error':'destination_denied'})
        self.assertEqual(process.stderr,b'')

    def test_real_child_hard_timeout_preserves_cxmt_under_global_budget(self):
        pages={index:json.dumps(page([row(i) for i in range(1+10*index,11+10*index)],45)).encode() for index in (0,1)}
        script=("import sys,json,time\nr=json.load(sys.stdin)\n"
                + "if 'jobs.bytedance.com' in r['url']: time.sleep(10)\n"
                + "pages="+repr(pages)+"\n"
                + "body="+repr(bootstrap())+" if r['body'] is None else pages[r['body']['PageIndex']]\n"
                + "sys.stdout.buffer.write(json.dumps({'ok':True,'size':len(body)}).encode()+b'\\n'+body)\n")
        for configured in (['cxmt'], ['bytedance','cxmt']):
            start=time.monotonic()
            with self.worker(script),patch('applypilot_core.runtime.MAX_DISCOVERY_SECONDS',3):
                result=runtime.discover('',source_config={'sources':configured},pause=lambda _:None)
            self.assertLess(time.monotonic()-start,3.5)
            self.assertEqual(len(result['jobs']),20)
            self.assertEqual(result['source_summary'][-1]['pages'],2)
            self.assertEqual(result['source_summary'][-1]['status'],'ok')
            if len(configured)==2:
                self.assertEqual(result['source_summary'][0]['requests'],1)
                self.assertEqual(result['source_summary'][0]['stop_reason'],'network_timeout')
            self.assert_slot_released()

    def test_bytedance_first_network_timeout_stops_and_preserves_later_source(self):
        http=Mock();http.get.side_effect=[runtime.SourceError('network_timeout'),bootstrap()]
        http.post.return_value=json.dumps(page([row()])).encode()
        result=runtime.discover('',http=http,source_config={'sources':['bytedance','cxmt']},pause=lambda _:None)
        self.assertEqual(result['source_summary'][0]['requests'],1)
        self.assertEqual(result['source_summary'][0]['stop_reason'],'network_timeout')
        self.assertEqual(result['source_summary'][1]['pages'],1)
        self.assertEqual(len(result['jobs']),1)


class DiscoveryBudgetTests(unittest.TestCase):
    def test_default_dual_source_fairness_and_later_source_thirty_second_cap(self):
        clock=[100.0];calls=[]
        def request(url,body=None,**kwargs):
            calls.append((url,body,kwargs.copy()))
            if 'jobs.bytedance.com' in url:
                clock[0]+=kwargs['timeout']
                raise runtime.SourceError('network_timeout')
            clock[0]+=10 if body is None else 8
            return bootstrap() if body is None else json.dumps(page([row(i) for i in range(1+10*body['PageIndex'],11+10*body['PageIndex'])],45)).encode()
        http=Mock();http.get.side_effect=request;http.post.side_effect=request
        with patch.dict(os.environ,{},clear=True),patch('applypilot_core.runtime.time.monotonic',side_effect=lambda:clock[0]):
            result=runtime.discover('',http=http,pause=lambda seconds:clock.__setitem__(0,clock[0]+seconds))
        self.assertEqual(runtime.MAX_DISCOVERY_SECONDS,45)
        self.assertEqual(runtime.MAX_SOURCE_SECONDS,30)
        self.assertEqual(runtime.MAX_DISCOVERY_REQUESTS,6)
        self.assertEqual(calls[0][2]['deadline'],122.5)
        self.assertEqual(calls[0][2]['timeout'],7)
        self.assertEqual([item[2]['deadline'] for item in calls[1:]],[137]*3)
        self.assertEqual([item[2]['timeout'] for item in calls[1:3]],[12]*2)
        self.assertAlmostEqual(calls[3][2]['timeout'],11.3)
        self.assertEqual(len(calls),4)  # first ByteDance timeout is never retried
        self.assertEqual(len(result['jobs']),20)
        self.assertEqual(result['source_summary'][-1]['pages'],2)
        self.assertTrue(result['source_summary'][-1]['truncated'])
        self.assertLess(clock[0],137)

    def test_single_source_thirty_second_budget_shortens_last_request_and_preserves_partial(self):
        clock=[100.0];calls=[]
        def request(url,body=None,**kwargs):
            calls.append(kwargs.copy());clock[0]+=kwargs['timeout']
            if body is None:return bootstrap()
            if body['PageIndex']==0:return json.dumps(page([row(i) for i in range(1,11)],45)).encode()
            raise runtime.SourceError('network_timeout')
        http=Mock();http.get.side_effect=request;http.post.side_effect=request
        with patch('applypilot_core.runtime.time.monotonic',side_effect=lambda:clock[0]):
            result=runtime.discover('',http=http,source_config={'sources':['cxmt']},pause=lambda seconds:clock.__setitem__(0,clock[0]+seconds))
        self.assertEqual([call['deadline'] for call in calls],[130]*3)
        self.assertEqual([call['timeout'] for call in calls[:2]],[12,12])
        self.assertAlmostEqual(calls[-1]['timeout'],5.3)
        self.assertEqual(clock[0],130)
        self.assertEqual(len(result['jobs']),10)
        self.assertEqual(result['source_summary'][0]['status'],'partial')
        self.assertEqual(result['source_summary'][0]['stop_reason'],'network_timeout')
        self.assertEqual(http.post.call_count,2)

    def test_fair_early_source_and_later_global_deadline_are_both_bounded(self):
        clock=[100.0];calls=[]
        def request(url,body=None,**kwargs):
            calls.append((url,kwargs.copy()));clock[0]+=kwargs['timeout']
            if url==URL:return bootstrap()
            raise runtime.SourceError('network_timeout')
        http=Mock();http.get.side_effect=request;http.post.side_effect=request
        with patch('applypilot_core.runtime.time.monotonic',side_effect=lambda:clock[0]):
            result=runtime.discover('',http=http,source_config={'sources':['cxmt','bytedance']},pause=lambda seconds:clock.__setitem__(0,clock[0]+seconds))
        self.assertEqual(calls[0][1]['deadline'],122.5)
        self.assertAlmostEqual(calls[1][1]['timeout'],10.15)
        self.assertEqual(calls[1][1]['deadline'],122.5)
        self.assertEqual(calls[2][1]['deadline'],145)
        self.assertEqual(calls[2][1]['timeout'],7)
        self.assertLessEqual(clock[0],145)
        self.assertEqual(len(calls),3)
        self.assertEqual(result['jobs'],[])
        self.assertTrue(all(item['status']=='failed' for item in result['source_summary']))


if __name__=='__main__':
    unittest.main()
