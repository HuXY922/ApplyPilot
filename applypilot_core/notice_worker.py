"""Fixed resource-capped HTML reader, with no inherited credentials/environment."""
import os
import resource
import signal
resource.setrlimit(resource.RLIMIT_AS, (96 * 1024 * 1024, 96 * 1024 * 1024))
resource.setrlimit(resource.RLIMIT_CPU, (7, 7))
resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))
signal.signal(signal.SIGALRM, lambda *_: os._exit(124))
signal.setitimer(signal.ITIMER_REAL, 8)
import json
from pathlib import Path
import sys


def main():
    raw = sys.stdin.buffer.read(2049)
    if len(raw) > 2048:
        raise ValueError()
    request = json.loads(raw)
    if (not isinstance(request, dict) or set(request) != {'url', 'timeout', 'proxy'}
            or not isinstance(request['url'], str) or not isinstance(request['proxy'], str)
            or type(request['timeout']) not in (int, float) or not 0 < request['timeout'] <= 7):
        raise ValueError()
    signal.setitimer(signal.ITIMER_REAL, request['timeout'])
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from applypilot_core.notice_transport import direct_fetch, FetchError
    try:
        body = direct_fetch(**request)
        sys.stdout.buffer.write(json.dumps({'ok': True, 'size': len(body)}).encode() + b'\n' + body)
    except FetchError as error:
        sys.stdout.buffer.write(json.dumps({'ok': False, 'error': error.code,
                                          'retry_after': str(error.retry_after)[:200] if error.retry_after else None}).encode() + b'\n')


if __name__ == '__main__':
    try:
        main()
    except Exception:
        sys.stdout.buffer.write(b'{"ok":false,"error":"worker_failed"}\n')
