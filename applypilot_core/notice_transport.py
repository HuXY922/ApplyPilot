"""Secret-free, bounded public university HTML transport. No user supplied URLs."""
from __future__ import annotations
import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

MAX_BYTES = 768 * 1024
HOSTS = frozenset(('career.nankai.edu.cn', 'pjcareer.dlut.edu.cn'))
WORKER = str(Path(__file__).with_name('notice_worker.py').resolve())


class FetchError(Exception):
    def __init__(self, code, retry_after=None):
        self.code, self.retry_after = code, retry_after
        super().__init__(code)


def validate_url(url):
    try:
        p = urlsplit(url)
        if (p.scheme != 'https' or p.hostname not in HOSTS or p.netloc != p.hostname
                or p.query or p.fragment or len(url) > 400):
            raise ValueError()
        allowed = p.path == '/robots.txt'
        if p.hostname == 'career.nankai.edu.cn':
            allowed |= bool(re.fullmatch(r'/correcruit/(?:index|content/id/[0-9]+)\.html', p.path))
        else:
            allowed |= p.path == '/xsfw/qyzp.htm' or bool(re.fullmatch(r'/info/[0-9]+/[0-9]+\.htm', p.path))
        if not allowed:
            raise ValueError()
        return p
    except (ValueError, TypeError):
        raise FetchError('destination_denied') from None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Connect to one validated public IP; still verify TLS for the original host."""
    def connect(self):
        addresses = socket.getaddrinfo(self.host, self.port, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
            raise FetchError('private_network_denied')
        self.sock = socket.create_connection((addresses[0][4][0], self.port), self.timeout)
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host)


class PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(PinnedHTTPSConnection, req, context=ssl.create_default_context())


def direct_fetch(url, timeout, proxy=''):
    p = validate_url(url)
    if proxy:
        q = urlsplit(proxy)
        if (q.scheme not in ('http', 'https') or not q.hostname or q.username or q.password
                or q.query or q.fragment or q.path not in ('', '/')):
            raise FetchError('invalid_process_proxy')
    handlers = [urllib.request.ProxyHandler({'https': proxy} if proxy else {}), NoRedirect()]
    if not proxy:
        handlers.append(PinnedHTTPSHandler())
    opener = urllib.request.build_opener(*handlers)
    req = urllib.request.Request(url, headers={'User-Agent': 'ApplyPilot/0.1 (bounded public university notice reader)',
                                               'Accept': 'text/html,text/plain;q=0.8', 'Accept-Encoding': 'identity'})
    try:
        with opener.open(req, timeout=timeout) as response:
            if response.getcode() != 200:
                raise FetchError('http_' + str(response.getcode()))
            mime = response.headers.get_content_type()
            if mime not in ('text/html', 'text/plain', 'application/xhtml+xml'):
                raise FetchError('unsupported_content_type')
            raw = response.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise FetchError('response_too_large')
            return raw
    except urllib.error.HTTPError as exc:
        raise FetchError('http_' + str(exc.code), exc.headers.get('Retry-After') if exc.headers else None) from None
    except FetchError:
        raise
    except (TimeoutError, socket.timeout):
        raise FetchError('network_timeout') from None
    except (urllib.error.URLError, OSError):
        raise FetchError('network_failure') from None


class Transport:
    """Hard parent timeout also covers DNS and TLS; killed child is always reaped."""
    def __init__(self, proxy=None):
        self.proxy = urllib.request.getproxies().get('https', '') if proxy is None else proxy

    def get(self, url, *, deadline):
        p = validate_url(url)
        remaining = min(7.0, deadline - time.monotonic())
        if remaining <= 0:
            raise FetchError('time_budget_exhausted')
        # Only an operator's process proxy may be used, never request input.
        proxy = self.proxy if self.proxy and not urllib.request.proxy_bypass(p.hostname) else ''
        payload = json.dumps({'url': url, 'timeout': remaining, 'proxy': proxy}).encode()
        process = None
        try:
            process = subprocess.Popen([sys.executable, '-I', '-S', '-B', WORKER], stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env={}, close_fds=True)
            output, _ = process.communicate(payload, timeout=remaining)
            if process.returncode != 0 or len(output) > MAX_BYTES + 1024:
                raise FetchError('worker_failed')
            header, sep, raw = output.partition(b'\n')
            if not sep or len(header) > 1024:
                raise FetchError('worker_protocol')
            control = json.loads(header)
            if control == {'ok': True, 'size': len(raw)}:
                return raw
            reason = control.get('error', '')
            if raw or not re.fullmatch(r'[a-z][a-z0-9_]{0,70}', reason):
                raise FetchError('worker_protocol')
            raise FetchError(reason, control.get('retry_after'))
        except subprocess.TimeoutExpired:
            raise FetchError('network_timeout') from None
        except (OSError, ValueError, TypeError):
            raise FetchError('worker_failed') from None
        finally:
            if process:
                if process.poll() is None:
                    process.kill()
                process.wait()
                for stream in (process.stdin, process.stdout):
                    if stream and not stream.closed:
                        stream.close()
