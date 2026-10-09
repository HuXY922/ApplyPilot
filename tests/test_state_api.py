import io
import json
import tempfile
import unittest
from pathlib import Path
from wsgiref.util import setup_testing_defaults
from db import initialize, connect
from state_api import StateAPI


class APITests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)/'api.sqlite3'
        initialize(self.path)
        self.api=StateAPI(self.path,'u'*32,'w'*32)
        with connect(self.path) as db:
            self.candidate=db.execute('SELECT id FROM candidates').fetchone()[0]
            self.campaign=db.execute('SELECT id FROM campaigns').fetchone()[0]
        self.job=self.call('POST','/v1/jobs',{'company_id':'test-co','company_name':'测试公司','title':'测试岗位'})['id']
        self.app=self.create()

    def tearDown(self):
        self.temp.cleanup()

    def request(self,method,path,data=None,role='user',query=''):
        env={}; setup_testing_defaults(env)
        raw=json.dumps(data or {}).encode()
        env.update(REQUEST_METHOD=method,PATH_INFO=path,QUERY_STRING=query,CONTENT_TYPE='application/json',CONTENT_LENGTH=str(len(raw)),HTTP_AUTHORIZATION='Bearer '+('u'*32 if role=='user' else 'w'*32))
        env['wsgi.input']=io.BytesIO(raw)
        response=[]
        body=b''.join(self.api(env,lambda status,headers:response.append(status)))
        return int(response[0].split()[0]),json.loads(body)

    def call(self,method,path,data=None,role='user',query='',expected=200):
        status,body=self.request(method,path,data,role,query)
        self.assertEqual(status,expected,body)
        return body

    def create(self,mode='simulation'):
        return self.call('POST','/v1/applications',{'candidate_id':self.candidate,'campaign_id':self.campaign,'job_id':self.job,'mode':mode})

    def action(self,action,data=None,role='worker',expected=200):
        body={'expected_revision':self.app['revision'],**(data or {})}
        result=self.call('POST',f"/v1/applications/{self.app['id']}/{action}",body,role,expected=expected)
        if expected==200:
            self.app=result.get('application',result)
        return result

    def filling(self):
        self.action('approve',role='user')
        self.action('transition',{'status':'filling'})

    def task(self,field='salary',kind='missing_information'):
        result=self.action('tasks',{'kind':kind,'field_key':field,'prompt':'请补充测试信息'})
        return result['task']

    def resolve(self,task,**answer):
        result=self.call('POST',f"/v1/tasks/{task['id']}/resolve",{'expected_revision':self.app['revision'],**answer})
        self.app=result['application']
        return result

    def test_auth_and_worker_cannot_approve(self):
        self.action('approve',expected=403)
        env={}; setup_testing_defaults(env); env.update(PATH_INFO='/v1/health')
        response=[]
        self.api(env,lambda status,headers:response.append(status))
        self.assertTrue(response[0].startswith('401'))

    def test_duplicate_create_and_stale_update(self):
        self.assertEqual(self.create()['id'],self.app['id'])
        old=self.app['revision']
        self.action('approve',role='user')
        self.call('POST',f"/v1/applications/{self.app['id']}/approve",{'expected_revision':old},expected=409)
        detail=self.call('GET',f"/v1/applications/{self.app['id']}")
        self.assertEqual(len(detail['events']),2)

    def test_multiple_tasks_atomic_answer_and_resume(self):
        self.filling()
        first=self.task(); second=self.task('city')
        self.resolve(first,value=20000,scope='job')
        self.assertEqual(self.app['status'],'needs_user_input')
        self.resolve(second,value='广州',scope='global')
        self.assertEqual(self.app['status'],'filling')
        answer=self.call('GET',f"/v1/applications/{self.app['id']}/answer",query='field_key=salary')
        self.assertEqual(answer['value'],20000)
        exported=self.call('GET',f'/v1/candidates/{self.candidate}/export')
        self.assertEqual(len(exported['profile']['application_answers']),2)

    def test_bad_answer_rolls_back_task_and_state(self):
        self.filling(); task=self.task()
        self.call('POST',f"/v1/tasks/{task['id']}/resolve",{'expected_revision':self.app['revision'],'value':1,'scope':'company','valid_until':'2020-01-01T00:00:00Z'},expected=400)
        detail=self.call('GET',f"/v1/applications/{self.app['id']}")
        self.assertEqual(detail['tasks'][0]['status'],'open')
        self.assertEqual(detail['revision'],self.app['revision'])
        with connect(self.path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM candidate_answers').fetchone()[0],0)

    def test_company_answer_does_not_leak_and_stale_specific_does_not_fallback(self):
        self.filling(); task=self.task()
        self.resolve(task,value=100,scope='global')
        task=self.task(); self.resolve(task,value=200,scope='company',revalidate_when='每次投递前确认')
        answer=self.call('GET',f"/v1/applications/{self.app['id']}/answer",query='field_key=salary')
        self.assertEqual(answer['status'],'needs_reconfirmation'); self.assertIsNone(answer['value'])
        other=self.call('POST','/v1/jobs',{'company_id':'other','company_name':'另一公司','title':'另一岗位'})
        other_app=self.call('POST','/v1/applications',{'candidate_id':self.candidate,'campaign_id':self.campaign,'job_id':other['id']})
        answer=self.call('GET',f"/v1/applications/{other_app['id']}/answer",query='field_key=salary')
        self.assertEqual(answer['value'],100)

    def test_submission_unknown_blocks_retry_and_can_be_verified(self):
        self.filling(); self.action('transition',{'status':'ready_to_submit'})
        attempt=self.action('submit',{'idempotency_key':'attempt-1'})['attempt_id']
        self.action('submission-result',{'attempt_id':attempt,'outcome':'unknown'})
        self.action('submit',{'idempotency_key':'attempt-2'},expected=409)
        self.action('submission-result',{'attempt_id':attempt,'outcome':'failed'},expected=409)
        self.action('submission-result',{'attempt_id':attempt,'outcome':'succeeded'})
        self.assertEqual(self.app['status'],'submitted')
        summary=self.call('GET','/v1/summary',query='mode=simulation')
        self.assertEqual(summary['applications_by_status']['submitted'],1)
        self.assertEqual(self.call('GET','/v1/summary')['applications_by_status'],{})

    def test_live_submission_is_disabled(self):
        self.app=self.create('live'); self.filling()
        self.action('transition',{'status':'ready_to_submit'})
        self.action('submit',{'idempotency_key':'live-attempt'},expected=409)

    def test_login_resolution_does_not_save_credential(self):
        self.filling(); task=self.task(None,'login')
        self.resolve(task,completed=True)
        self.assertEqual(self.app['status'],'filling')
        with connect(self.path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM candidate_answers').fetchone()[0],0)


if __name__=='__main__':
    unittest.main()
