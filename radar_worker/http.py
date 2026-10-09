"""Bounded HTTPS transport. No redirects, cookies, proxy auth or secret logging."""
import json
import re
import urllib.request
from urllib.parse import urlsplit

class FetchError(Exception):
    pass

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

class Transport:
    def __init__(self, allowed_hosts, token=None):
        self.hosts = set(allowed_hosts)
        self.token = token
        self.opener = urllib.request.build_opener(NoRedirect())

    def get(self, url, body=None, *, authenticated=False):
        u = urlsplit(url)
        if u.scheme != 'https' or u.hostname not in self.hosts or u.username or u.password or u.port not in (None,443):
            raise FetchError('destination_denied')
        headers = {'User-Agent':'CareerDeskRadar/1.0', 'Accept':'application/json, application/rss+xml, text/xml'}
        if authenticated:
            if not self.token:
                raise FetchError('authentication_not_configured')
            headers['Authorization'] = 'Bearer '+self.token
        raw = None
        if body is not None:
            raw = json.dumps(body,ensure_ascii=False,allow_nan=False).encode()
            if len(raw)>256*1024:
                raise FetchError('request_too_large')
            headers['Content-Type']='application/json'
        try:
            with self.opener.open(urllib.request.Request(url,data=raw,headers=headers),timeout=20) as r:
                data=r.read(2*1024*1024+1)
                if len(data)>2*1024*1024:
                    raise FetchError('response_too_large')
                return data
        except FetchError:
            raise
        except Exception as e:
            # Never include exception messages, request headers or response bodies.
            code=getattr(e,'code',None)
            raise FetchError('http_'+str(code) if isinstance(code,int) else 'network_failure') from None

    def json(self,url,body=None,*,authenticated=False):
        try:
            return json.loads(self.get(url,body,authenticated=authenticated))
        except (ValueError,UnicodeError):
            raise FetchError('invalid_json') from None
