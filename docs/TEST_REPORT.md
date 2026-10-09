# Verification report — 2026-10-09 UTC

## Current code verification

- `python -m unittest discover -s tests -v`: **139 passed**, zero skips; includes 119 preserved baseline tests + 20 audited-hardening regressions. Latest root run: 1.677 seconds.
- `python -m unittest discover -s tests_applypilot -v`: **145 passed**, zero skips; includes provider-specific consent, DeepSeek mocked transport/schema/grounding and exact-call provenance regressions. Latest root run: 0.991 seconds.
- `node scripts/test-ui-dom.cjs`: **60 interactive DOM/API checks passed** in a socket-free Node DOM simulation backed by real WSGI/SQLite. Discovery and configured-model responses are mocked. This is not a rendering/browser E2E test.
- `node --check applypilot_web/app.js` and `node --check applypilot_web/demo-form.js`: passed.
- Python compilation and isolated runtime `pip check`: passed.
- Known original profile identity values: no matches found in release text files. This is a targeted privacy scan, not a guarantee against all possible secrets.

## Genuine HTTP and persistence

`python scripts/smoke_flow.py` passed: real loopback HTTP, fictional profile review, rule analysis, versioned resume output, duplicate tracker idempotency, two-session isolation and data preservation when the application instance is recreated. See [record](http-smoke.json).

`python scripts/smoke_flow.py --live` passed its asserted core flow and persisted a genuine official ByteDance job at **2026-10-09 05:55:06Z**. The overall source run was **partial**, correctly retained as such. It is not exhaustive search/source acceptance. See [record](live-http-smoke.json).

Separately, the runtime fetched two official details at approximately 05:54 UTC and returned one matching job without failures in that narrower smoke. Applicant data and queries were not transmitted to sources.

## Original baseline audit

The original 9.1b archive first ran 118/119 due to missing declared Gunicorn. Installing Gunicorn25.1.0 in an isolated environment produced119/119, zero skipped. The suite covered deterministic/mocked cases and nine genuine loopback HTTP test methods, not external browser/model acceptance.

Four audit defects were reproduced and fixed: missing Radar image dependencies, weak legacy live-import provenance, implicit-major false-pass, and incomplete health-schema probes. Web/Radar Docker COPY-layout smoke checks pass; **Docker itself was not available**, so no actual image build is claimed.

## Controlled-form coverage

24 tests verify fictional-only fields, hash/destination/expiry-bound confirmation, cancellation or change invalidation, CSRF/Origin, cross-session isolation, idempotent simulation receipts, persistence and no mutation of real tracker records. Playwright script is supplied and defaults to readback only.

**Real browser execution remains blocked/unverified.** Sandbox Chromium cannot create its required sockets. Escalated execution fails during environment initialization with a mount error. Cloud browser cannot reach the local server. A visual-only data-URL attempt was explicitly rejected by browser URL policy, and was stopped. No screenshot or browser layout/E2E pass is claimed.

## Model acceptance

**No live model call occurred.** Without a safely configured server key, the application stays in rules mode. DeepSeek Chat Completions and the retained OpenAI Responses adapter are implemented. Provider API schemas, failure handling, provider-specific consent, output grounding, exact-call provider/model provenance and budgets are tested with mocks. DeepSeek tests also reject truncated, refused, malformed, invented and qualifier-dropping outputs. Configuring a credential is not a passing model test; verify a real call before presenting this as a live AI demo.

## Source limitations

- ByteDance: bounded official website detail/related-reference adapter. Schema/access changes can produce partial or failed runs.
- Ashby: public feed independently returned HTTP200 in initial research. Final adapter live acceptance is incomplete (timeout/oversized records). Further network validation was stopped after approval cancellation; default source remains ByteDance.
- No actual job application, login, challenge bypass, production database read/copy, or deployment occurred.

## Release checks still open

1. Real browser desktop/mobile layout and end-to-end flows, including imports, cancellation, back/forward and reload.
2. Live selected-model request with safely configured key and billing limits.
3. Authorized public hosting, real container build, health and restart/redeploy persistence from outside the development environment.
4. Public GitHub remote publication and exact commit verification.
5. Human assessment of job facts and résumé suggestions; wider provider/employer coverage.

The source bundle is an implementation checkpoint with explicit limits, not certification that every requested deliverable is complete.

## Preview safety patch

Three additional synthetic probes found defects in the first preview and were fixed before deployment: indirect transitions could hide unresolved submission status; Chinese-adjacent contact numbers could escape redaction; unrelated optional-major text could suppress a mandatory experience condition.

Regression tests now verify a durable history-derived uncertainty hold, serialized payload contact redaction, and requirement-specific optionality. Independent replay of all three original probes confirms the fixes.

Additional deployment safeguards require Railway's /data mount before creating any SQLite files, limit one import parser per process, and bound parser address space to 256 MiB. No legacy database schema was changed by these patches. These safeguards still require actual hosted resource/persistence tests; public deployment is pending.

## Deployment account checkpoint

On 2026-10-09 an authorized minimal Railway project-creation attempt returned: “Your trial has expired. Please select a plan to continue using Railway.” Re-listing projects confirmed no ApplyPilot project was created. No paid upgrade or resource change occurred. GitHub is installed/enabled, but this conversation exposes no GitHub operations; authenticated repository access and publication remain unverified.
