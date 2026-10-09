"""Simulation-only worker. Reads/writes state exclusively through HTTP."""
import argparse
import json
import os
import socket
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler


class ClientError(Exception):
    def __init__(self, status, message):
        self.status = status
        super().__init__(message)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class Client:
    def __init__(self, base_url, token):
        parts = urlsplit(base_url)
        if parts.scheme != 'http' or parts.hostname not in ('127.0.0.1', 'localhost', '::1') or parts.username or parts.password or parts.query or parts.fragment or parts.path not in ('','/'):
            raise ValueError('This local simulator requires a loopback HTTP URL')
        if not token or len(token) < 32:
            raise ValueError('A worker token of at least 32 characters is required')
        self.base_url = base_url.rstrip('/')
        self.token = token
        self.opener = build_opener(NoRedirect())

    def request(self, method, path, data=None):
        raw = json.dumps(data, ensure_ascii=False, allow_nan=False).encode() if data is not None else None
        request = Request(self.base_url + path, data=raw, method=method,
                          headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/json'})
        try:
            with self.opener.open(request, timeout=5) as response:
                return json.load(response)
        except HTTPError as error:
            # No automatic retries for writes, including errors after server commit.
            raise ClientError(error.code, 'State API rejected the request') from None
        except (URLError, TimeoutError, socket.timeout, ConnectionError):
            raise ClientError(0, 'Connection lost; read state before taking another action') from None

    def get(self, path):
        return self.request('GET', path)

    def post(self, path, data):
        return self.request('POST', path, data)


class FakeSubmitter:
    def __init__(self, client, required_fields=(), challenge=None, outcome='succeeded'):
        if challenge not in (None,'captcha','login') or outcome not in ('succeeded','failed','unknown'):
            raise ValueError('Invalid simulation scenario')
        self.client = client
        self.fields = tuple(required_fields)
        self.challenge = challenge
        self.outcome = outcome

    def tick(self, application_id):
        app = self.client.get('/v1/applications/'+application_id)
        if app['mode'] != 'simulation':
            raise ValueError('FakeSubmitter refuses live applications')
        status = app['status']
        base = '/v1/applications/'+application_id
        def post(action, **data):
            self.client.post(base+'/'+action, {'expected_revision':app['revision'], **data})
        if status == 'pending_review':
            return 'waiting_for_approval'
        if status in ('submitted','skipped','withdrawn','failed'):
            return status
        if status == 'needs_user_input':
            return 'waiting_for_user'
        if status in ('submitting','submission_unknown'):
            # A restarted worker cannot know what happened externally; never submit again.
            return 'verification_required'
        if status == 'approved':
            post('transition', status='filling')
            return 'progressed'
        if status == 'filling':
            for field in self.fields:
                answer = self.client.get(base+'/answer?'+urlencode({'field_key':field}))
                if answer['status'] != 'available':
                    post('tasks', kind='missing_information', field_key=field,
                         prompt='模拟表单需要补充或重新确认：'+field)
                    return 'waiting_for_user'
            if self.challenge and not any(t['kind']==self.challenge and t['status']=='resolved' for t in app['tasks']):
                post('tasks', kind=self.challenge, prompt='模拟页面需要手工处理：'+self.challenge)
                return 'waiting_for_user'
            post('transition', status='ready_to_submit')
            return 'progressed'
        if status == 'ready_to_submit':
            # Recheck dynamic answers at the submission boundary, not only while filling.
            for field in self.fields:
                answer = self.client.get(base+'/answer?'+urlencode({'field_key':field}))
                if answer['status'] != 'available':
                    post('tasks', kind='missing_information', field_key=field,
                         prompt='模拟提交前需要重新确认：'+field)
                    return 'waiting_for_user'
            attempt = self.client.post(base+'/submit', {'expected_revision':app['revision'],
                                      'idempotency_key':f"simulation:{application_id}:revision:{app['revision']}"})
            # This is a fake outcome, not a browser action or a real receipt.
            body = {'expected_revision':attempt['application']['revision'],
                    'attempt_id':attempt['attempt_id'],'outcome':self.outcome}
            if self.outcome == 'failed':
                body['verified_not_submitted'] = True
            self.client.post(base+'/submission-result',body)
            return {'succeeded':'submitted','failed':'failed','unknown':'verification_required'}[self.outcome]
        raise ValueError('Unsupported application status: '+status)

    def run(self, application_id, max_steps=12):
        trace = []
        for _ in range(max_steps):
            try:
                result = self.tick(application_id)
            except ClientError as error:
                # Caller can run again; each tick always begins with a fresh GET.
                return {'result':'refresh_required','http_status':error.status,'trace':trace}
            trace.append(result)
            if result != 'progressed':
                return {'result':result,'trace':trace}
        return {'result':'step_limit','trace':trace}

    def verify(self, application_id, outcome):
        """Explicit simulation operator action; never called by the automatic loop."""
        if outcome not in ('succeeded','failed'):
            raise ValueError('Verification must resolve to succeeded or failed')
        app = self.client.get('/v1/applications/'+application_id)
        if app['mode']!='simulation' or app['status'] not in ('submitting','submission_unknown'):
            raise ValueError('No uncertain simulation to verify')
        attempts = [a for a in app['attempts'] if a['status'] in ('running','unknown')]
        if len(attempts)!=1:
            raise ValueError('Expected exactly one unresolved attempt')
        return self.client.post('/v1/applications/'+application_id+'/submission-result',
            {'expected_revision':app['revision'],'attempt_id':attempts[0]['id'],'outcome':outcome,
             'verified_not_submitted':outcome=='failed'})


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url',default='http://127.0.0.1:8765')
    parser.add_argument('--application-id',required=True)
    parser.add_argument('--required-field',action='append',default=[])
    parser.add_argument('--challenge',choices=['captcha','login'])
    parser.add_argument('--outcome',choices=['succeeded','failed','unknown'],default='succeeded')
    parser.add_argument('--verify',choices=['succeeded','failed'])
    args=parser.parse_args()
    worker=FakeSubmitter(Client(args.base_url,os.environ.get('RECRUITMENT_WORKER_TOKEN')),
                         args.required_field,args.challenge,args.outcome)
    if args.verify:
        verified=worker.verify(args.application_id,args.verify)
        print(json.dumps({'result':verified['status'],'mode':'simulation'},ensure_ascii=False))
    else:
        print(json.dumps(worker.run(args.application_id),ensure_ascii=False,indent=2))
