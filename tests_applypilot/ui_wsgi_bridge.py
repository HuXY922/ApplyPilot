"""Socket-free UI test bridge: real WSGI/SQLite/rules, mocked discovery.

This is test-only code, not a server route and not real-network verification.
The model configuration switch tests consent without invoking a provider.
"""
import io,json,os,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from applypilot_core.server import ApplyPilotApp
from applypilot_core import runtime
from applypilot_core.store import demo_jobs,now
from copy import deepcopy
req=json.load(sys.stdin)
# Never let inherited deployment credentials turn this test into a provider call.
runtime.model.status=lambda: {'configured':False,'mode':'rules','status':'test_forced_rules'}
class TestRuntime:
 @staticmethod
 def model_status():return {'configured':bool(req.get('model_configured')),'mode':'model' if req.get('model_configured') else 'rules','provider':req.get('model_provider','openai'),'status':'test_configuration_only'}
 extract_profile=staticmethod(runtime.extract_profile)
 analyze=staticmethod(runtime.analyze)
 suggest_resume=staticmethod(runtime.suggest_resume)
 @staticmethod
 def discover(query='',**kwargs):
  if query=='offline':return {'jobs':[],'failures':[{'source':'fixture','error':'simulated_network_failure'}],'source_summary':[{'source':'fixture','status':'failed','requests':1,'verified':0,'returned':0,'scope':'Mocked fixture only'}]}
  if query=='empty':return {'jobs':[],'failures':[],'source_summary':[{'source':'fixture','status':'ok','requests':1,'verified':0,'returned':0,'scope':'Mocked fixture only'}]}
  job=deepcopy(demo_jobs()[0]);job.update(id='ignored',provider='fixture',external_id='safe-1',record_type='verified_job',canonical_url='https://careers.example.com/job/safe-1',availability='open',title='<img src=x onerror=alert(1)> AI 产品经理')
  job['verification']={'status':'verified','source':'mock fixture, not live network','verified_at':now(),'evidence':'Mock source evidence only'}
  return {'jobs':[job],'failures':[],'source_summary':[{'source':'fixture','status':'ok','requests':1,'verified':1,'returned':1,'scope':'Mocked fixture only'}]}
body=json.dumps(req.get('body'),ensure_ascii=False).encode() if 'body' in req else b''
app=ApplyPilotApp(os.environ.get('AP_UI_TEST_DATA','/tmp/applypilot-ui-simulation-data'),runtime=TestRuntime,searches_per_hour=1000,session_searches_per_hour=1000)
env={'REQUEST_METHOD':'POST' if 'body' in req else 'GET','PATH_INFO':req['path'],'HTTP_HOST':'127.0.0.1:8787','HTTP_ORIGIN':'http://127.0.0.1:8787','HTTP_COOKIE':req.get('cookie',''),'HTTP_X_CSRF_TOKEN':req.get('csrf',''),'CONTENT_TYPE':'application/json','CONTENT_LENGTH':str(len(body)),'wsgi.input':io.BytesIO(body),'REMOTE_ADDR':'ui-test'}
response={}
def start(status,headers):response.update(status=int(status.split()[0]),headers=dict(headers))
raw=b''.join(app(env,start));response['body']=json.loads(raw)
print(json.dumps(response,ensure_ascii=False))
