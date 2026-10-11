# Tencent Lighthouse deployment

## Scope and demo data retention

This repository can run as a standalone Python WSGI application with SQLite and static assets. It does not require the legacy application entry point or a managed database.

**虚构演示；匿名会话约24小时后过期，过期数据会清理，不用于真实长期求职记录。**

The public demo uses per-session SQLite databases. The default session lifetime is 86,400 seconds. Expired sessions cannot access their previous workspace; when a new session is created, expired session databases are cleaned up. Persistent disk protects data across application restarts within this lifetime. It is not indefinite retention, an account system, cross-device access, or a backup service.

Before deploying to an occupied server, inventory its operating system, available RAM/disk, existing users, services, listening ports, reverse proxy, and firewall rules. Preserve all unrelated services. Do not blindly reinstall, reboot, upgrade shared runtimes, or reuse another service's account/configuration.

## Verified P0 status — 2026-10-10 08:54 UTC

- Public HTTPS for the deployed IP was verified with normal certificate trust. The final fixes are deployed. Server-side verification passed 139 legacy tests in 5.873 seconds and 161 ApplyPilot tests in 5.913 seconds.
- Local verification passed 139 legacy tests, 161 ApplyPilot tests, 132 DOM/API checks and genuine loopback HTTP/SQLite smoke. The DOM/API suite uses a simulated DOM and mocked source/model responses; it is not browser E2E or a real provider test.
- Separate computer-use testing in the real browser passed rules-mode search, profile, analysis, tracker, persistence, evidence-display and unchanged-save flows. TXT, PDF and DOCX each passed selection, extraction, loading into the editor, editing, explicit review/confirmation and saving. Filenames survive navigation; a new source clears stale drafts; cancellation preserves existing edits; loading focuses the first field. Source fields remain conservatively extracted; PDF OCR is not supported.
- The owner securely configured the key. Four real DeepSeek calls used fictional data and separate consent for each call: one matching call produced six grounded strengths, the first two resume calls returned structurally valid empty lists, and the final resume call after the prompt fix produced one grounded whole-paragraph reordering suggestion at 08:54:15 UTC. Fictional/non-production/no-growth caveats and the `projects/0/description` citation were retained. The final output did not use rules fallback. No further model calls were made after acceptance.
- The requested model was `deepseek-flash`. The stored label is request metadata, not the provider-resolved model ID, which is not currently stored. The result does not independently establish a specific underlying model version.
- **Bounded demo acceptance passed.** The separate controlled simulated-application form still needs real-browser E2E verification (P1). No real recruitment application was submitted. Wider device/provider coverage and arbitrary resume quality are not established by this acceptance.
- Approximately 24-hour anonymous-session expiry and expired-data cleanup remain in effect. This is not long-term storage for real job-search records. See the [verification report](TEST_REPORT.md) for the current scope and historical checks.

## Subsequent source-expansion status

The hosted deployment now includes the CXMT provider and fixed per-request process deadlines. The hard-deadline deployment passed 139 legacy and 206 ApplyPilot tests. The smaller-page follow-up passed local and independent suites of 139 + 208 tests, plus 150 DOM checks / 95 real WSGI+SQLite requests, and was deployed at 11:03:11 UTC. Its server suites passed 139 tests in 5.711 seconds and 208 tests in 9.089 seconds, with ten matching live hashes and healthy HTTPS. The follow-up browser search still exposed CXMT bootstrap timeout. The final candidate uses a 45-second global deadline, at most 30 seconds per source and a 12-second CXMT request cap, retaining the one-slot/resource restrictions and no-retry policy. It passed 139 + 214 Python tests and 152 DOM checks / 95 WSGI requests. Deployed at 11:16:20 UTC, it passed server suites of 139 tests in 5.715 seconds and 214 in 9.338 seconds; all seven changed file hashes and trusted HTTPS health matched. The final browser query still failed at the upstream network stage; CXMT is experimental and its hosted acceptance remains blocked. Existing records and manual state were preserved. Stop repeated tuning/retries and disclose this limitation rather than calling the expansion complete.

Source failures have occurred in formal browser searches even when isolated official reads succeeded. Keep the original failure and partial results visible; never treat an isolated source smoke, a configured provider or a passing test suite as proof of general availability. The smaller-page limit is two pages of ten records, within six requests / 45 seconds globally. Network work runs in a fixed secret-free child with a hard per-request deadline, bounded output and kill/reap on timeout. No scheduled crawler was added. See [the expansion report](OFFICIAL_SOURCE_EXPANSION.md) for actual timing and acceptance evidence.

## Independent layout

Suggested paths:

- Code and isolated Python virtual environment: `/opt/applypilot` and `/opt/applypilot/.venv`
- Persistent app data: `/var/lib/applypilot`, owned by the dedicated non-login app user, mode `0700`
- Environment file: `/etc/applypilot.env`, `root:root`, mode `0600`
- Dedicated service: `applypilot.service`
- If no proxy exists: separate Caddy configuration, service account and state directory

Install the repository-pinned `requirements-lock.txt` into the isolated virtual environment. Do not upgrade shared system Python just to obtain `ensurepip`. If the host's venv component is unavailable without such an upgrade, Python's `venv --without-pip` plus the [official pip bootstrap](https://pip.pypa.io/en/stable/installation/) can be used inside the app venv after verifying its source and integrity. Never install application dependencies into the global interpreter.

Run applicable tests as the app user with a fresh temporary data directory and a clean environment. Do not load the production environment file or provider credentials for tests:

```sh
python -m unittest discover -s tests -v
python -m unittest discover -s tests_applypilot -v
node scripts/test-ui-dom.cjs
```

## Production entry point

Use one application instance/worker with SQLite. The tested Gunicorn entry point is:

```sh
/opt/applypilot/.venv/bin/gunicorn --config /dev/null \
  --bind 127.0.0.1:8090 --workers 1 --threads 4 --timeout 90 \
  --access-logfile - 'applypilot_core.server:create_app()'
```

`--config /dev/null` is intentional. Otherwise Gunicorn can automatically load the repository's legacy `gunicorn.conf.py`, which is for a different application and requires unrelated configuration.

Set the exact public origin and persistent data path:

```dotenv
APPLYPILOT_PUBLIC=1
PUBLIC_ORIGIN=https://YOUR_PUBLIC_IP
APPLYPILOT_DATA_DIR=/var/lib/applypilot
PORT=8090
APPLYPILOT_SOURCES=bytedance,cxmt
APPLYPILOT_MAX_SESSIONS=32
APPLYPILOT_SESSION_TTL=86400
```

Replace `YOUR_PUBLIC_IP` with the actual IP before use. Do not set Railway-specific volume environment variables on Tencent. Keep the app port on loopback; expose only the reverse proxy.

A systemd unit should use the dedicated non-login user, `WorkingDirectory=/opt/applypilot`, the root-only environment file, and restart-on-failure. Apply hardening and resource limits appropriate to the inspected host. The tested small-host limits were 256 MiB MemoryHigh / 384 MiB MemoryMax for the app, not a general sizing guarantee.

## Public HTTPS for an IP address

Let's Encrypt [supports public IP certificates](https://letsencrypt.org/2026/01/15/6day-and-ip-general-availability). They are short-lived and require reliable automated renewal. Obtain the owner's approval for the applicable [Subscriber Agreement](https://letsencrypt.org/repository/) and for firewall/account changes before issuance. Do not buy resources or change unrelated services implicitly.

If an existing proxy is present, integrate a reviewed route without disrupting its domains/services. If ports 80/443 are free, Caddy 2.11.7 was tested with an explicit public ACME issuer:

```caddyfile
{
    admin off
    default_sni YOUR_PUBLIC_IP
    persist_config off
    servers {
        protocols h1 h2
    }
}
https://YOUR_PUBLIC_IP {
    tls {
        issuer acme {
            dir https://acme-v02.api.letsencrypt.org/directory
            profile shortlived
        }
    }
    encode zstd gzip
    reverse_proxy 127.0.0.1:8090
}
```

Replace both `YOUR_PUBLIC_IP` placeholders. Do not assume bare-IP automatic HTTPS alone will select a public CA: an explicit issuer avoids Caddy's local-CA default for IPs. `default_sni` is required here because ordinary IP clients can omit SNI. See [Caddy TLS](https://caddyserver.com/docs/caddyfile/directives/tls) and [default_sni](https://caddyserver.com/docs/caddyfile/options#default-sni).

Keep Caddy's persistent certificate state private and available across restarts. Enable its systemd service at boot. Use `Wants=applypilot.service` with `After=applypilot.service`, rather than `Requires=`, if the proxy must remain running through an application restart. The latter dependency can stop the proxy when the app is restarted.

Verify public TCP 80/443 reachability before issuance. HTTP/1.1 and HTTP/2 do not need a UDP 443 rule. Preserve original firewall rules. Never bypass certificate warnings or replace public HTTPS with a self-signed certificate as a successful result.

## Provider key configuration

Provider/model configuration without a valid key can still use honest rules fallback. It is not proof of a real model call.

The owner must enter and submit their key through a private, non-echoing workflow. Do not put keys in chat, command arguments, shell history, uploaded files, source control, screenshots or shared logs. Keep the resulting environment file root-only. Restart only ApplyPilot after saving and verify health before a minimal fictional-profile model test. Real provider requests use the owner's API quota and transmit the necessary profile/job information to that provider.

Do not dump environment files or process environments to diagnose model configuration. Inspect only non-sensitive booleans, error categories and reported provider/model status.

## Acceptance and operations

Verify each item separately:

1. Both app and proxy are active and enabled at boot.
2. Normal HTTPS certificate verification succeeds from the server and an independent client.
3. `/healthz` reports `status=ok`, `storage=writable`, and `isolation=per-session-sqlite`.
4. HTTP redirects to the exact HTTPS origin; the app port remains loopback-only.
5. A confirmed fictional profile and tracker state survive an app-only restart in the same browser session.
6. Original services, listening ports and available resources remain healthy.
7. Real browser search, evidence, resume suggestions, tracker controls and honest model/fallback reporting work.
8. Certificate issuance is verified, automated renewal is configured, and future renewal is not falsely described as already tested.

Typical administrator checks:

```sh
systemctl is-active applypilot caddy-applypilot
systemctl is-enabled applypilot caddy-applypilot
curl --fail --show-error https://YOUR_PUBLIC_IP/healthz
```

Review updates against the deployed commit/patch, test in a clean staging directory, preserve original code, atomically replace only intended files, and restart only the application. A rollback must restore code and verify HTTPS recovery, including after interruption. Do not delete data or stop unrelated services as an assumed rollback.

No off-host backup is implied by this layout. Plan backup and account/retention design separately before using real long-term job-search records.

## University notice module and navigation update — 2026-10-11

The external-link fix permits only exact public static GET document navigation; do not disable Fetch Metadata, Origin, Host or CSRF checks globally. API reads/writes remain protected. The subsequent notice module uses the same application service, dependency environment and writable data directory; no new port, process supervisor or scheduled task is required.

The first use of the notice API creates `university-notices.sqlite3` under `APPLYPILOT_DATA_DIR`. It is a shared public cache, separate from private per-session workspaces. Existing private database schemas are not migrated. Code rollback should leave this new public database in place; never delete data as an assumed rollback. Back up and obtain appropriate approval before a future irreversible archive/cleanup.

The public cache has finite row and 32 MiB storage limits. It stops with a maintenance notice rather than deleting history automatically. Anonymous workspace expiry does not turn this shared public cache into personal account storage. Preserve durable source pauses, robots observations and cooldowns; never clear them or restart to bypass a source restriction.

The deployed module passed 139 legacy and 250 ApplyPilot server tests, with all 20 candidate hashes matching. A single formal browser refresh stored 20 Nankai notices with partial detail success; DUT Panjin timed out. Reload, local filter, history and original job/tracker preservation passed. Three rendered original-link URLs and new-window security attributes were checked; destination-click E2E was not performed. Only the app restarted. The HTTP navigation checks confirmed cross-site document GET succeeds while cross-site API reads/writes remain rejected; a real external-page click was not completed by the test tools. See [notice operations](UNIVERSITY_NOTICE_RADAR.md) and [verification](TEST_REPORT.md) for precise coverage and limits. No additional model call or actual application was made.
