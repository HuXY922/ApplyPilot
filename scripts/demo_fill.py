#!/usr/bin/env python3
"""Fill and read back only the owned ApplyPilot loopback simulation.

Install the optional official tooling with:
  python -m pip install playwright
  python -m playwright install chromium
Run after starting ApplyPilot locally:
  python scripts/demo_fill.py
  python scripts/demo_fill.py --submit --confirm-local-simulation

Default mode stops before preparation/confirmation/submission. No stealth,
login, CAPTCHA, external recruiting site, arbitrary input file or real data.
API references: https://playwright.dev/python/docs/locators
               https://playwright.dev/python/docs/network
"""
import argparse
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from applypilot_core.form_demo import FICTIONAL_FIELDS


class SafetyError(ValueError):
    pass


def owned_url(value):
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise SafetyError('Invalid loopback demo URL.') from exc
    if (parsed.scheme != 'http' or parsed.hostname not in ('localhost', '127.0.0.1', '::1')
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in ('', '/', '/demo-form') or not port):
        raise SafetyError('Only http://localhost:PORT/demo-form or literal loopback equivalents are allowed.')
    origin = f'http://{parsed.netloc}'
    return origin, origin + '/demo-form'


def request_allowed(url, method, origin, allow_submit=False):
    try:
        parsed = urlsplit(url)
        actual_origin = f'{parsed.scheme}://{parsed.netloc}'
    except ValueError:
        return False
    if actual_origin != origin or parsed.query or parsed.fragment:
        return False
    gets = {'/demo-form', '/demo-form.js', '/demo-form.css', '/style.css', '/api/state', '/favicon.ico', '/api/demo/receipts'}
    posts = {'/api/demo/prepare', '/api/demo/cancel'} if allow_submit else set()
    if allow_submit:
        posts.add('/api/demo/submit')
    return (method == 'GET' and parsed.path in gets) or (method == 'POST' and parsed.path in posts)


def run(args):
    origin, url = owned_url(args.url)
    if args.submit and not args.confirm_local_simulation:
        raise SafetyError('--submit also requires --confirm-local-simulation as explicit operator approval.')
    if args.confirm_local_simulation and not args.submit:
        raise SafetyError('Use --confirm-local-simulation only together with --submit.')
    try:
        from playwright.sync_api import sync_playwright, expect
    except ImportError as exc:
        raise RuntimeError('Optional Playwright is missing. Install it from the official package and install Chromium.') from exc
    blocked = []
    with sync_playwright() as playwright:
        browser_options = {'headless': not args.headed}
        # Prefer Playwright's matched browser. Use an existing official system
        # Chromium if the optional bundled browser has not been installed.
        if not Path(playwright.chromium.executable_path).is_file():
            import shutil
            executable = shutil.which('chromium') or shutil.which('chromium-browser')
            if executable:
                browser_options['executable_path'] = executable
        browser = playwright.chromium.launch(**browser_options)
        context = browser.new_context(service_workers='block', accept_downloads=False,
                                      viewport={'width':1280,'height':1000})
        def route_request(route):
            request = route.request
            if request_allowed(request.url, request.method, origin, args.submit):
                route.continue_()
            else:
                blocked.append({'url': request.url, 'method': request.method})
                route.abort('blockedbyclient')
        context.route('**/*', route_request)
        if hasattr(context, 'route_web_socket'):
            context.route_web_socket('**/*', lambda socket: socket.close())
        page = context.new_page()
        try:
            response = page.goto(url, wait_until='domcontentloaded', timeout=20000)
            if not response or response.status != 200 or page.url != url:
                raise SafetyError('Owned form did not load directly; refusing redirected or unexpected destination.')
            expect(page.locator('#simulation-notice')).to_contain_text('受控模拟，非真实投递')
            expect(page.locator('#name')).to_be_enabled(timeout=20000)
            for key,value in FICTIONAL_FIELDS.items():
                locator = page.locator(f'[name="{key}"]')
                if key == 'role':
                    locator.select_option(label=value)
                else:
                    locator.fill(value)
                expect(locator).to_have_value(value)
            observed = {key:page.locator(f'[name="{key}"]').input_value() for key in FICTIONAL_FIELDS}
            if observed != FICTIONAL_FIELDS:
                raise SafetyError('Readback differs from the fictional fixture; stopped before confirmation.')
            result = {'simulation':True, 'mode':'dry_run', 'filled':True, 'readback_verified':True,
                      'fields':observed, 'destination':url, 'submitted':False,
                      'message':'Stopped before preparation, confirmation, and submission.'}
            if args.submit:
                page.locator('#prepare').click()
                expect(page.locator('#review')).to_be_visible(timeout=20000)
                expect(page.locator('#review-destination')).to_have_text(origin + '/api/demo/submit')
                for value in observed.values():
                    expect(page.locator('#review-fields')).to_contain_text(value)
                # Allowed only by the two explicit operator flags above. This
                # records a fictional receipt; it never represents real consent.
                page.locator('#confirm-simulation').check()
                page.locator('#submit-simulation').click()
                try:
                    expect(page.locator('#result')).to_be_visible(timeout=20000)
                except Exception:
                    result.update(mode='submission_unknown', message='No verified receipt. Do not automatically retry.',
                                  page_status=page.locator('#status').inner_text())
                    if args.screenshot:
                        page.screenshot(path=str(Path(args.screenshot).resolve()), full_page=True)
                    print(json.dumps(result, ensure_ascii=False, indent=2))
                    return 3
                receipt_id = page.locator('#result').get_attribute('data-receipt-id')
                if not receipt_id or not receipt_id.startswith('sim-'):
                    raise SafetyError('No valid simulation receipt; do not retry automatically.')
                result.update(mode='confirmed_simulation',submitted=True,receipt_id=receipt_id,
                              receipt=page.locator('#receipt').inner_text(),
                              message='Fictional local simulation recorded; no real application submitted.')
            if args.screenshot:
                page.screenshot(path=str(Path(args.screenshot).resolve()), full_page=True)
                result['screenshot'] = str(Path(args.screenshot).resolve())
            result['blocked_requests'] = blocked
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        finally:
            context.close()
            browser.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8787/demo-form')
    parser.add_argument('--submit', action='store_true', help='Record a fictional local receipt (requires second flag).')
    parser.add_argument('--confirm-local-simulation', action='store_true', help='Explicit operator approval for this simulation only.')
    parser.add_argument('--headed', action='store_true')
    parser.add_argument('--screenshot', help='Optional local PNG output path.')
    args = parser.parse_args()
    try:
        return run(args)
    except (SafetyError, RuntimeError) as exc:
        print(json.dumps({'error':str(exc),'submitted':False}, ensure_ascii=False), file=sys.stderr)
        return 2
    except Exception as exc:
        # A browser/process failure is not proof of failure after a write.
        print(json.dumps({'error':str(exc),'submitted':None if args.submit else False,
                          'status':'not_verified','automatic_retry':False}, ensure_ascii=False), file=sys.stderr)
        return 3


if __name__ == '__main__':
    raise SystemExit(main())
