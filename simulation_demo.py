"""Self-contained HTTP demonstrations with synthetic data and ephemeral credentials."""
import argparse
import json
import secrets
import tempfile
import threading
from pathlib import Path
from wsgiref.simple_server import make_server, WSGIRequestHandler
from db import initialize
from state_api import StateAPI
from fake_submitter import Client, ClientError, FakeSubmitter


class QuietHandler(WSGIRequestHandler):
    def log_message(self, *args):
        pass


class DemoEnvironment:
    def __enter__(self):
        self.temp=tempfile.TemporaryDirectory(prefix='recruitment-simulation-')
        root=Path(self.temp.name)
        profile={'profile_id':'synthetic-candidate','identity':{'name_zh':'虚构测试候选人'},
                 'sources':[{'id':'synthetic-resume','sha256':'0'*64}],
                 'resume_assets':[{'is_default':True,'source_id':'synthetic-resume','library_file_id':'synthetic-resume-only'}]}
        source=root/'synthetic-profile.json'; source.write_text(json.dumps(profile))
        self.db_path=root/'simulation.sqlite3'
        initialize(self.db_path,source)
        user_token=secrets.token_urlsafe(32); worker_token=secrets.token_urlsafe(32)
        self.server=make_server('127.0.0.1',0,StateAPI(self.db_path,user_token,worker_token),handler_class=QuietHandler)
        self.thread=threading.Thread(target=self.server.serve_forever,kwargs={'poll_interval':0.02},daemon=True)
        self.thread.start()
        base=f'http://127.0.0.1:{self.server.server_port}'
        # The demo driver represents a simulated user. FakeSubmitter gets worker only.
        self.user=Client(base,user_token); self.worker=Client(base,worker_token)
        self.candidate='synthetic-candidate'
        self.campaign=self.user.get('/v1/campaigns')['items'][0]['id']
        self.counter=0
        return self

    def __exit__(self,*args):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=2)
        self.temp.cleanup()

    def create(self,mode='simulation'):
        self.counter+=1
        job=self.user.post('/v1/jobs',{'company_id':'demo-company','company_name':'虚构演练公司','title':f'模拟岗位 {self.counter}'})
        return self.user.post('/v1/applications',{'candidate_id':self.candidate,'campaign_id':self.campaign,'job_id':job['id'],'mode':mode})['id']

    def detail(self,id):
        return self.user.get('/v1/applications/'+id)

    def approve(self,id):
        app=self.detail(id)
        self.user.post('/v1/applications/'+id+'/approve',{'expected_revision':app['revision']})

    def resolve(self,id,**answer):
        app=self.detail(id)
        task=next(t for t in app['tasks'] if t['status']=='open')
        self.user.post('/v1/tasks/'+task['id']+'/resolve',{'expected_revision':app['revision'],**answer})


def expect(value,expected):
    if value!=expected:
        raise AssertionError(f'Expected {expected!r}, got {value!r}')


def scenario_approval_and_repeat():
    with DemoEnvironment() as env:
        id=env.create(); worker=FakeSubmitter(env.worker)
        expect(worker.run(id)['result'],'waiting_for_approval')
        env.approve(id); expect(worker.run(id)['result'],'submitted')
        for _ in range(3): expect(FakeSubmitter(env.worker).run(id)['result'],'submitted')
        expect(len(env.detail(id)['attempts']),1)
        return '未批准时等待；批准后成功；重复运行三次仍只有一次提交尝试。'


def scenario_answer_reuse():
    with DemoEnvironment() as env:
        worker=FakeSubmitter(env.worker,required_fields=['demo.preferred_city'])
        first=env.create(); env.approve(first)
        expect(worker.run(first)['result'],'waiting_for_user')
        env.resolve(first,value='虚构测试城市',scope='global')
        expect(FakeSubmitter(env.worker,['demo.preferred_city']).run(first)['result'],'submitted')
        second=env.create(); env.approve(second)
        expect(worker.run(second)['result'],'submitted')
        expect(len(env.detail(second)['tasks']),0)
        return '第一份申请暂停补信息，第二份申请复用同一答案，没有再次提问。'


def scenario_challenge():
    with DemoEnvironment() as env:
        for kind in ('captcha','login'):
            id=env.create(); env.approve(id)
            worker=FakeSubmitter(env.worker,challenge=kind)
            expect(worker.run(id)['result'],'waiting_for_user')
            expect(FakeSubmitter(env.worker,challenge=kind).run(id)['result'],'waiting_for_user')
            expect(len(env.detail(id)['tasks']),1)
            env.resolve(id,completed=True)
            expect(FakeSubmitter(env.worker,challenge=kind).run(id)['result'],'submitted')
        return '验证码与登录分别暂停；重新启动仍保留待办；模拟手工完成后继续。'


def scenario_unknown():
    with DemoEnvironment() as env:
        id=env.create(); env.approve(id)
        worker=FakeSubmitter(env.worker,outcome='unknown')
        expect(worker.run(id)['result'],'verification_required')
        expect(FakeSubmitter(env.worker).run(id)['result'],'verification_required')
        expect(len(env.detail(id)['attempts']),1)
        worker.verify(id,'succeeded')
        expect(worker.run(id)['result'],'submitted')
        expect(len(env.detail(id)['attempts']),1)
        return '提交结果不明后停止；重启不重投；显式模拟核验后标为成功。'


class LoseCommittedResponse:
    def __init__(self,client): self.client=client
    def get(self,path): return self.client.get(path)
    def post(self,path,data):
        result=self.client.post(path,data)
        if path.endswith('/submit'):
            raise ClientError(0,'Injected response loss after the API committed')
        return result


def scenario_lost_response():
    with DemoEnvironment() as env:
        id=env.create(); env.approve(id)
        worker=FakeSubmitter(LoseCommittedResponse(env.worker))
        expect(worker.run(id)['result'],'refresh_required')
        expect(env.detail(id)['status'],'submitting')
        restarted=FakeSubmitter(env.worker)
        expect(restarted.run(id)['result'],'verification_required')
        restarted.verify(id,'failed')
        expect(restarted.run(id)['result'],'failed')
        expect(len(env.detail(id)['attempts']),1)
        return '模拟服务端已写入但响应丢失；重启识别未解决尝试，未自动再提交。'


def scenario_failed_and_live():
    with DemoEnvironment() as env:
        id=env.create(); env.approve(id)
        worker=FakeSubmitter(env.worker,outcome='failed')
        expect(worker.run(id)['result'],'failed')
        expect(worker.run(id)['result'],'failed')
        expect(len(env.detail(id)['attempts']),1)
        live=env.create('live')
        try:
            worker.run(live)
        except ValueError:
            pass
        else:
            raise AssertionError('FakeSubmitter did not refuse live mode')
        expect(env.detail(live)['status'],'pending_review')
        return '明确失败后停止，真实模式申请被拒绝执行。'


SCENARIOS=[('批准与重复运行',scenario_approval_and_repeat),('补充答案与跨申请复用',scenario_answer_reuse),
           ('验证码与登录暂停恢复',scenario_challenge),('提交结果待确认',scenario_unknown),
           ('提交响应丢失后恢复',scenario_lost_response),('失败与真实模式隔离',scenario_failed_and_live)]


def run_demo():
    return {'mode':'simulation','transport':'real_loopback_http','data':'synthetic_only',
            'scenarios':[{'name':name,'status':'passed','result':run()} for name,run in SCENARIOS],
            'limitations':['不连接真实招聘网站','不识别或绕过验证码','演示驱动程序模拟用户批准和回答','不能据此判断真实网申成功率']}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report',type=Path)
    args=parser.parse_args()
    report=run_demo()
    if args.report:
        args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False,indent=2))
