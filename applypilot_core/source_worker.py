"""Fixed secret-free public-source network worker. Never invoked with user code."""
import os
import resource
import signal

# Isolated interpreter (-I -S) skips user paths/sitecustomize. Install the
# resource limits before importing the app or reading any request bytes.
resource.setrlimit(resource.RLIMIT_AS, (96 * 1024 * 1024, 96 * 1024 * 1024))
resource.setrlimit(resource.RLIMIT_CPU, (8, 8))
resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))
signal.signal(signal.SIGALRM, lambda *_: os._exit(124))
signal.setitimer(signal.ITIMER_REAL, 13)

import json
import math
import sys
import time
from pathlib import Path


def main():
    raw = sys.stdin.buffer.read(4097)
    if len(raw) > 4096:
        raise ValueError("request_too_large")
    request = json.loads(raw)
    if not isinstance(request, dict) or set(request) != {"url", "body", "max_bytes", "timeout", "deadline", "proxy"}:
        raise ValueError("invalid_worker_request")
    if (not isinstance(request["url"], str) or not isinstance(request["proxy"], str)
            or type(request["max_bytes"]) is not int or not 1 <= request["max_bytes"] <= 3 * 1024 * 1024
            or type(request["timeout"]) not in (int, float) or not 0 < request["timeout"] <= 12
            or type(request["deadline"]) not in (int, float) or not math.isfinite(request["deadline"])):
        raise ValueError("invalid_worker_request")
    # Self-expire even if the parent service exits before it can kill/reap us.
    signal.setitimer(signal.ITIMER_REAL, max(.001, min(13, request["deadline"] - time.monotonic())))
    # -I excludes arbitrary current-directory/PYTHONPATH imports. Insert only the
    # directory of this fixed installed worker, never a path from the input.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from applypilot_core.runtime import _DirectTransport, SourceError
    try:
        if time.monotonic() >= request["deadline"]:
            raise SourceError("time_budget_exhausted")
        transport = _DirectTransport(proxy=request["proxy"])
        kwargs = {key: request[key] for key in ("max_bytes", "timeout", "deadline")}
        result = (transport.get(request["url"], **kwargs) if request["body"] is None
                  else transport.post(request["url"], body=request["body"], **kwargs))
        sys.stdout.buffer.write(json.dumps({"ok": True, "size": len(result)}).encode() + b"\n" + result)
    except SourceError as error:
        control = {"ok": False, "error": str(error)}
        retry_after = getattr(error, "retry_after", None)
        if retry_after is not None:
            control["retry_after"] = str(retry_after)[:256]
        sys.stdout.buffer.write(json.dumps(control).encode() + b"\n")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Do not echo exceptions, network bodies, process environment or input.
        sys.stdout.buffer.write(b'{"ok":false,"error":"source_worker_failed"}\n')
