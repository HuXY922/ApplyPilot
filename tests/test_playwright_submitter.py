import json
import unittest
from pathlib import Path
from playwright_submitter import BrowserAdapter, PlaywrightSubmitter
from simulation_demo import DemoEnvironment


class Locator:
    def __init__(self,page,label): self.page,self.label=page,label
    async def count(self): return 1 if self.label in self.page.fields else 0
    async def is_visible(self): return True
    async def is_enabled(self): return True
    async def evaluate(self,source): return 'select' if self.label=='期望城市' else 'input'
    async def get_attribute(self,name): return 'text' if name=='type' else None
    async def fill(self,value): self.page.fields[self.label]=value; self.page.writes+=1
    async def select_option(self,*,value): self.page.fields[self.label]=value; self.page.writes+=1
    async def input_value(self): return self.page.fields[self.label]


class Message:
    def __init__(self,visible): self.visible=visible
    async def count(self): return int(self.visible)
    async def is_visible(self): return self.visible


class PageDouble:
    """Contract double, not a browser; no Playwright execution is claimed."""
    url='http://127.0.0.1:8877/form.html'
    def __init__(self): self.fields={'测试姓名':'','期望城市':''};self.writes=0;self.block=None
    def get_by_label(self,label,exact): return Locator(self,label)
    def get_by_text(self,text,exact): return Message(text==self.block)


def config():
    raw=json.loads((Path(__file__).resolve().parents[1]/'browser-fixtures/adapter.json').read_text())
    raw['fields']=[f for f in raw['fields'] if f['field_key']!='identity.email']
    return raw


class BrowserAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_origin_and_schema_guard(self):
        page=PageDouble(); adapter=BrowserAdapter(page,config())
        page.url='https://unexpected.invalid/'
        with self.assertRaises(ValueError): await adapter.fill({'identity.name_zh':'测试','preferred_city':'guangzhou'})
        self.assertEqual(page.writes,0)
        page.url='http://127.0.0.1:8877/form.html';del page.fields['测试姓名']
        self.assertEqual((await adapter.inspect())['kind'],'manual_review')

    async def test_blocker_stops_before_filling(self):
        page=PageDouble();page.block='请完成人工验证'
        result=await BrowserAdapter(page,config()).fill({'identity.name_zh':'虚构','preferred_city':'guangzhou'})
        self.assertEqual(result['kind'],'captcha');self.assertEqual(page.writes,0)

    async def test_authorization_missing_answer_resume(self):
        with DemoEnvironment() as env:
            id=env.create();env.approve(id);page=PageDouble()
            worker=PlaywrightSubmitter(env.worker,BrowserAdapter(page,config()))
            self.assertEqual((await worker.tick(id))['status'],'authorization_required')
            self.assertEqual(env.detail(id)['status'],'approved')
            result=await worker.tick(id,transmission_authorized=True)
            self.assertEqual(result['field_key'],'preferred_city');self.assertEqual(page.writes,0)
            env.resolve(id,value='guangzhou',scope='job')
            result=await worker.tick(id,transmission_authorized=True)
            self.assertEqual(result['status'],'ready_to_submit')
            self.assertEqual(page.fields['期望城市'],'guangzhou')
            self.assertEqual(env.detail(id)['attempts'],[])
            self.assertEqual((await worker.tick(id,transmission_authorized=True))['submit_enabled'],False)

    async def test_resolving_task_does_not_bypass_persistent_challenge(self):
        with DemoEnvironment() as env:
            id=env.create();env.approve(id);page=PageDouble();page.block='请完成人工验证'
            worker=PlaywrightSubmitter(env.worker,BrowserAdapter(page,config()))
            self.assertEqual((await worker.tick(id,transmission_authorized=True))['kind'],'captcha')
            env.resolve(id,completed=True)
            self.assertEqual((await worker.tick(id,transmission_authorized=True))['kind'],'captcha')
            self.assertEqual(page.writes,0)


if __name__=='__main__': unittest.main()
