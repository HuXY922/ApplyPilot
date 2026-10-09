"""Playwright form adapter: explicit mappings, pause/resume, dry run only.

Accepts an async Playwright Page. This module does not start a browser, log in,
solve CAPTCHAs, upload a resume, or click a final submit button.
"""
import asyncio
from urllib.parse import urlsplit, urlencode


class BrowserAdapter:
    def __init__(self, page, config):
        self.page, self.config = page, config
        if config.get('submit_enabled') is not False:
            raise ValueError('This milestone requires submit_enabled=false')
        if not config.get('allowed_origins'):
            raise ValueError('Explicit target origins are required')
        for spec in config.get('fields', []):
            if spec.get('kind') not in ('text', 'select'):
                raise ValueError('Only explicit text and select mappings are supported')
            if not spec.get('label') or not spec.get('field_key'):
                raise ValueError('Each field needs a label and profile key')
            if any(s in spec['field_key'].lower() for s in ('password', 'secret', 'cookie', 'token', 'otp')):
                raise ValueError('Authentication fields are not supported')

    def check_origin(self):
        url = urlsplit(self.page.url)
        origin = f'{url.scheme}://{url.netloc}'
        if url.scheme not in ('http', 'https') or origin not in self.config['allowed_origins']:
            raise ValueError('Unexpected destination; stop before entering any data')

    async def blocker(self):
        self.check_origin()
        for blocker in self.config.get('blockers', []):
            loc = self.page.get_by_text(blocker['text'], exact=True)
            if await loc.count() == 1 and await loc.is_visible():
                return {'kind':blocker['kind'], 'prompt':blocker['prompt']}
        return None

    async def inspect(self):
        self.check_origin()
        issue = await self.blocker()
        if issue:
            return {'status':'blocked', **issue}
        for spec in self.config['fields']:
            loc = self.page.get_by_label(spec['label'], exact=True)
            if await loc.count() != 1:
                return {'status':'blocked','kind':'manual_review','prompt':'表单结构改变或字段不唯一：'+spec['label']}
            if not await loc.is_visible() or not await loc.is_enabled():
                return {'status':'blocked','kind':'manual_review','prompt':'字段当前不可填写：'+spec['label']}
            tag = await loc.evaluate('(el) => el.tagName.toLowerCase()')
            input_type = await loc.get_attribute('type')
            if spec['kind']=='text' and (tag not in ('input','textarea') or input_type in ('password','file','hidden','checkbox','radio','submit')):
                return {'status':'blocked','kind':'manual_review','prompt':'字段类型与映射不一致：'+spec['label']}
            if spec['kind']=='select' and tag!='select':
                return {'status':'blocked','kind':'manual_review','prompt':'当前下拉控件需要专门适配：'+spec['label']}
        return {'status':'ready'}

    async def fill(self, values):
        state = await self.inspect()
        if state['status']!='ready':
            return state
        # Validate complete input first; do not half-fill before noticing missing data.
        for spec in self.config['fields']:
            value = values.get(spec['field_key'])
            if value is None or value=='':
                return {'status':'missing','field_key':spec['field_key'],'prompt':'请补充：'+spec['label']}
        for spec in self.config['fields']:
            self.check_origin()
            issue = await self.blocker()
            if issue:
                return {'status':'blocked', **issue}
            loc = self.page.get_by_label(spec['label'], exact=True)
            value = str(values[spec['field_key']])
            if spec['kind']=='select':
                await loc.select_option(value=value)
            else:
                await loc.fill(value)
            # Verification reads actual DOM values, never infers success from no exception.
            if await loc.input_value() != value:
                return {'status':'blocked','kind':'manual_review','prompt':'填写后读取的字段值不一致：'+spec['label']}
        self.check_origin()
        return {'status':'filled','fields':[s['field_key'] for s in self.config['fields']]}


class PlaywrightSubmitter:
    def __init__(self, state_client, adapter):
        self.state, self.adapter = state_client, adapter

    async def tick(self, application_id, *, transmission_authorized=False):
        """Authorization is granted by the operator for this exact destination/data.
        It is never inferred from page content or from the candidate's profile.
        """
        base = '/v1/applications/' + application_id
        app = await asyncio.to_thread(self.state.get, base)
        if app['status']=='pending_review': return {'status':'waiting_for_approval'}
        if app['status']=='needs_user_input': return {'status':'waiting_for_user'}
        if app['status'] in ('submitting','submission_unknown'): return {'status':'verification_required'}
        if app['status'] in ('submitted','failed','skipped','withdrawn'): return {'status':app['status']}
        if not transmission_authorized:
            return {'status':'authorization_required','destination':self.adapter.config['allowed_origins'],
                    'fields':[s['field_key'] for s in self.adapter.config['fields']]}
        async def post(action, **data):
            nonlocal app
            result = await asyncio.to_thread(self.state.post,base+'/'+action,{'expected_revision':app['revision'],**data})
            app = result.get('application', result)
            return result
        if app['status']=='approved':
            await post('transition',status='filling')
        if app['status']=='ready_to_submit':
            return {'status':'ready_to_submit','submit_enabled':False}
        if app['status']!='filling':
            return {'status':'unsupported_state'}
        try:
            observed = await self.adapter.inspect()
        except Exception:
            observed = {'status':'blocked','kind':'manual_review','prompt':'浏览器连接或目标页面异常，请检查后继续。'}
        if observed['status']=='blocked':
            await post('tasks',kind=observed['kind'],prompt=observed['prompt'])
            return {'status':'waiting_for_user','kind':observed['kind']}
        candidate = await asyncio.to_thread(self.state.get, '/v1/candidates/'+app['candidate_id']+'/export')
        profile = candidate['profile']
        values = {}
        for spec in self.adapter.config['fields']:
            key = spec['field_key']
            answer = await asyncio.to_thread(self.state.get,base+'/answer?'+urlencode({'field_key':key}))
            if answer['status']=='available':
                value = answer['value']
            elif answer['status']=='needs_reconfirmation':
                value = None
            elif spec.get('allow_profile_fallback') is True:
                value = profile
                for segment in key.split('.'):
                    value = value.get(segment) if isinstance(value,dict) else None
            else:
                value = None
            if value is None or value=='':
                await post('tasks',kind='missing_information',field_key=key,prompt='请补充或重新确认：'+spec['label'])
                return {'status':'waiting_for_user','field_key':key}
            values[key] = value
        try:
            result = await self.adapter.fill(values)
        except Exception:
            result = {'status':'blocked','kind':'manual_review','prompt':'填写未能确认完成；请检查当前页面，程序不会自动提交。'}
        if result['status']=='filled':
            await post('transition',status='ready_to_submit')
            return {'status':'ready_to_submit','submit_enabled':False,'verified_fields':result['fields']}
        await post('tasks',kind=result.get('kind','manual_review'),prompt=result.get('prompt','请检查表单'))
        return {'status':'waiting_for_user'}
