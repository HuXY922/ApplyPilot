# ApplyPilot backend audit: actual 9.1b source

This is the pre-implementation audit. Subsequent fixes and current verification are recorded in ../TEST_REPORT.md.

Audit date: 2026-10-09 UTC. Source archive: `career-desk-step9.1b-pro-work-radar.zip`. This report audits source 9.1b, not the connector-reported later 9.1c-1 production revision. No production requests, deployments, source changes, external recruiting requests, or real submissions were performed. Synthetic probes used temporary SQLite databases only. Candidate/profile/batch personal values are omitted.

## Result

The existing Python/SQLite backend is substantial and should be extended rather than replaced. With its exact declared runtime dependency installed in an isolated environment, **119/119 tests pass**, including local Gunicorn restart persistence. This establishes a local unit/integration baseline, not browser E2E, production availability, or real recruitment-site acceptance.

Two immediate blockers were reproduced despite the green suite: the standalone Radar Docker layout cannot import its new matching dependencies, and the legacy Radar batch endpoint admits unverified HTTP jobs from unregistered hosts.

## 1. Existing / Partial / Missing matrix

| Area | Status | Actual evidence and boundary |
|---|---|---|
| Authoritative backend/database | Existing | `state_api.py`, `db.py`, `schema.sql`; single SQLite owner, parameterized statements, foreign keys, busy timeout, `BEGIN IMMEDIATE`, optimistic revision checks. No replacement backend is needed. |
| Schema migrations | Existing | `migrations/002_radar.sql`, `003_radar_runs.sql`, `004_radar_console.sql`; schema v4, non-destructive additive migrations. Production startup locks initialization and creates a 0600 pre-v4 SQLite backup (`production_app.py:64-82`). Repeated initialization and v2/v3 upgrade tests pass. Whole upgrade is not one transaction; each script commits independently. |
| Applications/approval/state machine | Existing | `schema.sql` enforces initial pending_review, immutable identity, allowed transitions, incremented revision, approval/evidence constraints, and state-change events. Only USER approves; RADAR and WORKER cannot approve (`state_api.py:227-275`). |
| Deduplication/idempotency | Existing, bounded | Unique canonical URL; unique candidate/job/mode application; unique application/day recommendation; unique run/URL observation; unique submission idempotency key and one unresolved attempt. ByteDance mobile/tracking aliases normalized (`radar.py:9-16,71-105`). Other cross-site aliases require adapters. |
| Radar client | Existing | `radar_worker/run.py::CareerAPI` limits calls to profile/start/finish/batch; separate Radar token, HTTPS host allowlist, no redirects, bounded request/response sizes/timeouts (`radar_worker/http.py`). No approval route. |
| Discovery/verification | Partial | `radar_worker/sources.py` implements only ByteDance official related-jobs discovery and independently fetched detail verification; ID, open status, formal campus, 2027 cohort, city, expiry and role checks. Other sources/ATS adapters missing. External requests were mocked in all tests. |
| Hard eligibility and score | Partial | `radar_gate.py` enforces cohort, degree, graduation window, recognized mandatory majors, internship/social exclusion; reject/defer produces no scored application. `radar_worker/matching.py` recomputes an explainable lexical score server-side. Regex coverage is incomplete; implicit major restriction probe incorrectly passes. |
| Source evidence admission | Partial | `/radar` recommendation requires registered host, job-specific excerpts/locator, current timestamp, open availability; no client score (`radar_recommendations.py`). Backend does not independently fetch browser evidence. Legacy `/v1/radar/batch` does not enforce the same provenance contract. |
| Radar run/audit persistence | Existing, bounded | `radar_runs.py`, `radar_audit.py`, migrations 003/004; one run/day, server-counted imports/duplicates, persisted reject/defer decisions and source/profile/policy evidence. A completed partial/failed run cannot reopen same day. Decision rows are upserted per run/URL, so this is latest-decision evidence, not an append-only audit history. |
| Radar authentication/privacy | Existing, bounded | `radar_console.py`: Radar-only HMAC session, hashed DB session, 24h Secure/HttpOnly/SameSite=Strict cookie, CSRF/exact Origin, restrictive script-free CSP, logout/rotation. `radar_privacy.py`: recursive positive profile projection. Free-text screening remains heuristic; full WORKER profile export is still broad. |
| Pause/restart/answer reuse | Existing | `candidate_answers`, `user_tasks`; job/company/campaign/global specificity and stale-answer reconfirmation. `fake_submitter.py` refuses live mode, reloads state before actions, stops on unknown outcome. All six loopback simulation scenarios pass. |
| Browser adapter | Partial | `playwright_submitter.py` accepts a supplied async Page, exact configured labels/origins, explicit transmission-authorized flag, challenges, readback, and stops at ready_to_submit. Four PageDouble contract tests, two with real loopback API. No real Page execution in this suite. |
| Real submit/receipt verification | Missing by design | `/submit` and `/submission-result` reject live mode (`state_api.py:260-279`). No resume upload, real submit click, receipt acquisition, or live unknown-result reconciliation. Keep disabled. |
| Mail monitoring | Missing | `inbox_messages` schema and generic read exist; no mailbox connector, polling, ingest/matching pipeline, or user notification implementation. |
| Scheduling/execution host | Partial / not enabled | Worker CLI and `railway.radar.toml` exist; cron is commented out. No durable browser host, queue lease/claim, or scheduler enabled in source. Standalone Radar image currently broken. |
| Production diagnostics | Partial | Redacted error responses, disabled access logs, no token logging, `/healthz` read-only database probe. Health omits v4 decision/session table checks. No structured correlation/latency/error diagnostics, durable retry queue, or operational alerts. |
| CI/container/browser acceptance | Missing in supplied source | No CI configuration, lockfile/hashes, container smoke test, actual browser E2E, or live-source acceptance harness found. README claims historical acceptance boundaries explicitly. |

## 2. Directly reusable components

1. Keep the current SQLite volume, all existing IDs, `StateAPI`, transaction/revision helpers, migrations, database guards and per-role authorization.
2. Reuse Radar canonicalization/import, server scoring, minimal profile projection, run accounting and decision evidence. Consolidate admission rather than add a second discovery database.
3. Reuse approval/task/answer endpoints and simulation fault scenarios as the contract for a future worker.
4. Extend the current `BrowserAdapter` per approved site and preserve its final-submit-disabled boundary; replace test doubles with actual browser fixtures for acceptance.
5. Retain production config validation, initialization backup/lock, Gunicorn startup, role token separation and Console session protections.

## 3. Minimal additive architecture changes

- Make a small pure shared package for gate/privacy/scoring/policy data, with no import of API/database/Console modules. Install/copy it into both Web and Radar images. This resolves the current packaging fault and circular coupling without a new service or framework.
- Route every RADAR live import through one provenance + eligibility contract. Keep legacy compatibility only behind an explicit synthetic/test or migration path; do not let omission of run/evidence bypass production source registration.
- Add structured qualification extraction confidence and fail/defer on ambiguous major/degree requirements. Keep deterministic server gate/scoring; keep interpretation separate from candidate facts and numeric authority.
- Add a scoped worker execution/claim contract only when browser execution is authorized: application ID, approved job/profile versions, approved target origin/fields, resume reference and expiry. Reuse existing revision and submission-attempt machinery. Preserve human login/CAPTCHA/uncertain-outcome pauses.
- Before any real submission milestone, add version-bound approval snapshots, immutable TEST ONLY marker, explicit submit consent/evidence, and worker data minimization. Current application approval is a timestamp/actor, not a hash binding to a JD/profile version; Console can refresh JD after approval.
- Add tests alongside existing suite: isolated image layout/import, all admission paths, ambiguous qualifications, full health schema, worker minimum-data reads, retries after lost responses, real browser forms and navigation interruptions. Do not replace existing 119 regressions.

## 4. Dependency/environment and test results

Environment: Python **3.12.14**, SQLite **3.53.1**, Linux; `fcntl` available. Docker executable absent, so an actual container build/run was not performed. Isolated image-layout reproduction was performed instead.

Declared runtime: `requirements.txt` pins only **gunicorn==25.1.0**. All application/worker logic uses the standard library; Playwright adapter expects a host-supplied Page and does not import/install/launch Playwright. Existing ambient Playwright is not evidence of configured browser runtime.

- As-is command: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v`
- Result: **119 run / 118 passed / 1 failed / 0 skipped**; unittest **0.903s**, wall **1.064s**. Sole failure: `test_production_http_restart_preserves_approval`, because Gunicorn was not installed.
- Isolated dependency setup: `python3 -m venv /tmp/applypilot-audit-venv`; `/tmp/applypilot-audit-venv/bin/python -m pip install --index-url https://pypi.org/simple -r requirements.txt`.
- Install succeeded from official PyPI: **gunicorn 25.1.0**, transitive **packaging 26.3**; no global packages changed. `pip check`: **No broken requirements found**.
- Rerun command: `PYTHONDONTWRITEBYTECODE=1 /tmp/applypilot-audit-venv/bin/python -m unittest discover -s tests -v`
- Result: **119 run / 119 passed / 0 failed / 0 skipped**; unittest **1.700s**, wall **1.925s**.

Classification: **9 test methods use real loopback HTTP** (6 FakeSubmitter scenarios, 2 PageDouble/API scenarios, 1 Gunicorn restart test); **110 use in-process/unit/SQLite/mock transports**. **0 real browser E2E tests**, **0 external network-source tests**, **0 production tests**. All databases were temporary. Some original tests read the bundled candidate fixture; no fixture values were emitted in the report/logs.

Per-file totals: database 8; FakeSubmitter 6; Gunicorn 1; Playwright contract 4; production WSGI/config 13; Radar 10; Radar security 38; Radar worker 31; State API 8.

License metadata from installed wheels: Gunicorn **MIT**; packaging **Apache-2.0 OR BSD-2-Clause**, with license files present. No project LICENSE/NOTICE was found in supplied source. This is a metadata inventory, not legal clearance. No current advisory/CVE scan was performed. Reproducibility gaps: transitive packaging unpinned, no hashes/lockfile, `python:3.12-slim` image tag unpinned; Web image runs default root, unlike the Radar image's non-root user.

Artifacts: `baseline-tests.log`, `baseline-tests-summary.json`, `dependency-install.log`, `dependency-metadata.json`, `baseline-tests-with-deps.log`, `baseline-tests-with-deps-summary.json`, `probe_backend.py`, `backend-probes.json` in this audit directory.

## 5. Risk ranking and reproduced defects

**High / blocks standalone Radar enablement — incomplete Docker payload.** `Dockerfile.radar:5` copies only `radar_worker`. `radar_worker/matching.py:15-16,40` imports `radar_gate`, `radar_privacy`, then `radar_recommendations` (which in turn imports Web/DB modules). Reproducing exactly that directory layout in a temp folder and isolated Python produced `ModuleNotFoundError: No module named 'radar_gate'`. No Docker was run. Root-checkout tests cannot catch this. Add packaging/import smoke test before enabling a worker.

**High / source-integrity gap — legacy RADAR admission is weaker than Console.** `radar.py:9-16,31-54` allows HTTP/unregistered hosts and requires verification only when `run_id` is supplied; without a run, verification must be absent. A synthetic direct Radar import with no run/evidence and an HTTP `.invalid` host created one `pending_review` application. No remote request occurred, and approval was not bypassed. This disproves a universal “only verified official jobs can enter” claim; unify the contract before unattended discovery.

**High / eligibility false-pass — limited regex major parsing.** `radar_gate.py:61-70` only detects mandatory majors with selected modal words. Synthetic JD `正式校招。2027届计算机科学与技术专业，本科及以上学历。` and an unrelated major returned `pass` / `no_explicit_mandatory_major_detected`. Treat plausible ambiguous restrictions as defer; expand test corpus. Not evidence that every real JD is misclassified.

**Medium / health false-green.** `production_app.py:40-44` validates version 4 but queries only candidates/recommendations/runs. Dropping `radar_sessions` in a temporary v4 database still returned `200 {"status":"ok"}`. Check all required v4 tables, keeping health read-only.

**Medium now, high before broader worker access — full-profile privilege.** `state_api.py:157-201` allows WORKER candidate list/export and answer reads without an application-approval guard; synthetic unapproved export succeeded. This is compatible with the documented trusted single-user worker model but too broad for an external/multi-tenant execution host. Add scoped per-approved-application projection; do not remove recovery detail reads blindly.

**Medium now, high before live submit — approval/version/test boundaries.** `state_api.py:230-233` records approval actor/time only; snapshot is taken at simulated submit (`266-275`). `radar_recommendations.py::submit` refreshes job description without invalidating approval. TEST ONLY fixture is stored as live and identified via text/risk flags, not immutable schema. Real submit is currently disabled, containing immediate impact. Bind versioned consent and synthetic marker before enabling it.

**Medium / privacy and operations.** Web `Dockerfile` includes `candidate-profile.json`; personal seed data enters image/build artifacts and is read at each initialization even when DB records already exist. Preserve existing data but separate synthetic tests and controlled private bootstrap inputs in future packaging. Application errors are redacted but difficult to diagnose, all API reads take an immediate write transaction, and single-process rate limiting/SQLite serialization bound scale. There is no multi-user security model.

**Evidence gap / release blocker for production claims.** Supplied source is 9.1b; connector-reported 9.1c-1 may differ. Do not deploy over a later live revision or claim these defects are present there without obtaining/matching its exact source. No browser/production/live-source acceptance was established by this audit.

## 6. First-stage acceptance implications

Backend baseline is green after dependency setup. For an initial additive milestone, retain all 119 tests and require regressions for the three high risks above, an isolated image/import smoke test, a real browser fixture flow, and proof of preserved existing database/approval data. Real jobs/submission/scheduler remain separate acceptance gates. See the project Roadmap for the cross-layer acceptance schedule.
