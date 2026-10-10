# Verification report — updated 2026-10-10 UTC

## Official-source expansion — 2026-10-10 UTC

The expansion adds one company, CXMT / 长鑫存储, to ByteDance. The current bounded-budget candidate passed **139 legacy + 214 ApplyPilot tests**, **152 DOM checks / 95 WSGI requests**, and genuine loopback HTTP/SQLite smoke. The DOM suite is simulated, with mocked source/model responses.

Expansion and hard-deadline code are deployed. Initial server verification passed 139 + 191 tests; the hard-deadline deployment passed 139 + 206. Genuine standalone CXMT reads succeeded, but formal browser runs also exposed source timeouts: 43.189 seconds before the fix, then 16.229 and 23.852 seconds after the hard bound. The third run was partial, returning one existing ByteDance record without duplication while preserving manual history. A successful two-request timing probe is diagnostic evidence, not a passing app-search acceptance result.

The PageSize=10 and obsolete-toast correction was deployed at 11:03:11 UTC. Server verification passed **139 tests in 5.711 seconds and 208 tests in 9.089 seconds**; all ten changed hashes and HTTPS health matched. Its formal browser run took 20.614 seconds and remained partial, with CXMT timing out at bootstrap. The final bounded 45-second global / 12-second CXMT request adjustment was deployed at 11:16:20 UTC. Server tests passed **139 in 5.715 seconds and 214 in 9.338 seconds**, all seven changed hashes matched, and trusted HTTPS health passed. The final formal browser run again failed both sources, including CXMT category bootstrap, with zero returned records. Existing jobs/manual state were retained and errors shown without fake fallback. **CXMT hosted usability is experimental and blocked; expansion acceptance is incomplete.** Concurrency and resource limits remain bounded. Full observations and coverage limits are in [OFFICIAL_SOURCE_EXPANSION.md](OFFICIAL_SOURCE_EXPANSION.md). The original P0 acceptance below is separate. No extra model calls or real applications were made by the source-expansion checks.

## Deployed P0 acceptance — 08:54 UTC

**The bounded public-demo acceptance passed as of 2026-10-10 08:54:15 UTC.** This covers the deployed fixes and the specific flows and fictional model examples below. It is not blanket acceptance of every application, device, provider or resume. Historical results are retained in a separately labeled section; their earlier pending statements are not the current status.

### Automated verification of the final fixes

- **Deployed server:** 139 legacy tests passed in 5.873 seconds; 161 ApplyPilot tests passed in 5.913 seconds. These are the suites rerun after the final fixes were deployed.
- **Local final candidate:** 139 legacy tests passed in 3.340 seconds; 161 ApplyPilot tests passed in 3.982 seconds; 132 DOM/API checks passed. The two Python suites total 300 passing tests.
- **DOM/API classification:** a Node DOM simulation exercises real WSGI, SQLite and rule-runtime behavior with mocked discovery/model responses. It does not render in a real browser and is not browser E2E, live-source or live-model acceptance.
- **Local HTTP smoke:** genuine loopback HTTP/SQLite passed fictional seed isolation, profile review/confirmation, rule analysis and versioned resume output, tracker idempotency, second-session isolation and application-recreation persistence. No live-source or provider request was made by this smoke run.

### Real hosted-browser and persistence verification

These checks were performed separately through computer use in a real browser against the hosted application; they are not inferred from the DOM simulation.

- Public HTTPS for the deployed IP passed normal certificate verification.
- Rules-mode search, profile review, analysis, tracker controls and same-session persistence passed, including preservation through the verified application restart.
- The three evidence rows and repeated unchanged-stage saves passed regression testing; unchanged saves did not add redundant history.
- TXT, PDF and DOCX each passed the complete select → extract → load into editor → edit → review/confirm → save flow using fictional fixtures.
- File selection survived navigation with its filename preserved. Selecting a new source cleared stale extraction data. Canceling draft replacement preserved existing edits. Loading a draft focused the first field.
- Parsed source fields remain conservative rather than inventing candidate facts. PDF extraction remains text-only; no OCR support was added.

### Four genuine DeepSeek calls, with bounded output acceptance

The owner securely configured the provider key. Each of the four calls used fictional data and a separate explicit transmission consent. The total is **one matching call plus three resume calls**; no further calls were made after the final acceptance.

1. The matching call returned **six evidence-grounded strengths**, without rules fallback.
2. The first two resume calls returned **structurally valid empty suggestion lists**. They demonstrated real authenticated responses, but did not establish useful resume output.
3. After the prompt fix was deployed, the final resume call at **2026-10-10 08:54:15 UTC** used confirmed fictional profile version 12, a six-paragraph project, and the same official job record `7667889560824154373`. It returned **one grounded suggestion**: an exact whole-paragraph reordering, with citation `projects/0/description`. The fictional, non-production and no-growth caveats were retained. The result was nonempty, meaningfully prioritized the supplied material, passed grounding validation, and did not use rules fallback. This is conservative source-preserving reordering, not proof of unrestricted rewriting quality.

The request model parameter was **`deepseek-flash`**. Stored provider/model labels describe the request. The vendor-resolved model ID is not currently stored, so these labels do not independently verify a particular underlying model version. Successful calls do not establish future account balance, availability or billing guarantees.

### Remaining limits and P1 work

- **P1:** the independent controlled simulated-application form still needs real-browser E2E verification. Main-workspace browser acceptance does not substitute for this separate Playwright/readback/confirmation flow.
- No real recruitment application was submitted. No claim is made for recruitment-site login, CAPTCHA handling or external submission.
- Broad cross-browser/mobile coverage, actual Docker image build and wider employer/provider acceptance are not established by the checks above. Additional job-fact and resume-quality evaluation remains useful beyond the tested fixture.
- Anonymous demo sessions expire after approximately 24 hours; expired SQLite data is cleaned when a new session is created. Refresh/restart persistence within this period is not indefinite retention. Only fictional data should be used; PDF OCR remains unsupported.
- This report records candidate and deployed-app verification. It does not assert that the latest candidate is already published to GitHub; the historical repository verification below applies only to its named snapshot.

See [Tencent deployment](tencent-deployment.md) for the generic operating procedure. No actual server address, account identifier, credentials, private configuration, or live data is included here.

## Historical verification record — 2026-10-09 UTC

The following preserves the 2026-10-09 checkpoint. Counts, environment blockers and pending items in this section describe that date; use the current summary above for subsequent acceptance.

### Historical code verification

The two Python suites total **284 passing tests**. The **60 DOM/API checks** below remain mocked, socket-free UI verification rather than real browser E2E acceptance.

- `python -m unittest discover -s tests -v`: **139 passed**, zero skips; includes 119 preserved baseline tests + 20 audited-hardening regressions. Latest root run: 1.677 seconds.
- `python -m unittest discover -s tests_applypilot -v`: **145 passed**, zero skips; includes provider-specific consent, DeepSeek mocked transport/schema/grounding and exact-call provenance regressions. Latest root run: 0.991 seconds.
- `node scripts/test-ui-dom.cjs`: **60 interactive DOM/API checks passed** in a socket-free Node DOM simulation backed by real WSGI/SQLite. Discovery and configured-model responses are mocked. This is not a rendering/browser E2E test.
- `node --check applypilot_web/app.js` and `node --check applypilot_web/demo-form.js`: passed.
- Python compilation and isolated runtime `pip check`: passed.
- Known original profile identity values: no matches found in release text files. This is a targeted privacy scan, not a guarantee against all possible secrets.

### Genuine HTTP and persistence

`python scripts/smoke_flow.py` passed: real loopback HTTP, fictional profile review, rule analysis, versioned resume output, duplicate tracker idempotency, two-session isolation and data preservation when the application instance is recreated. See [record](http-smoke.json).

`python scripts/smoke_flow.py --live` passed its asserted core flow and persisted a genuine official ByteDance job at **2026-10-09 05:55:06Z**. The overall source run was **partial**, correctly retained as such. It is not exhaustive search/source acceptance. See [record](live-http-smoke.json).

Separately, the runtime fetched two official details at approximately 05:54 UTC and returned one matching job without failures in that narrower smoke. Applicant data and queries were not transmitted to sources.

### Original baseline audit

The original 9.1b archive first ran 118/119 due to missing declared Gunicorn. Installing Gunicorn25.1.0 in an isolated environment produced119/119, zero skipped. The suite covered deterministic/mocked cases and nine genuine loopback HTTP test methods, not external browser/model acceptance.

Four audit defects were reproduced and fixed: missing Radar image dependencies, weak legacy live-import provenance, implicit-major false-pass, and incomplete health-schema probes. Web/Radar Docker COPY-layout smoke checks pass; **Docker itself was not available**, so no actual image build is claimed.

### Controlled-form coverage

24 tests verify fictional-only fields, hash/destination/expiry-bound confirmation, cancellation or change invalidation, CSRF/Origin, cross-session isolation, idempotent simulation receipts, persistence and no mutation of real tracker records. Playwright script is supplied and defaults to readback only.

**At this historical checkpoint, real browser execution was blocked/unverified.** Sandbox Chromium cannot create its required sockets. Escalated execution fails during environment initialization with a mount error. Cloud browser cannot reach the local server. A visual-only data-URL attempt was explicitly rejected by browser URL policy, and was stopped. No screenshot or browser layout/E2E pass is claimed.

### Model acceptance

**At this historical checkpoint, no live model call had occurred.** Without a safely configured server key, the application stays in rules mode. DeepSeek Chat Completions and the retained OpenAI Responses adapter are implemented. Provider API schemas, failure handling, provider-specific consent, output grounding, exact-call provider/model provenance and budgets are tested with mocks. DeepSeek tests also reject truncated, refused, malformed, invented and qualifier-dropping outputs. Configuring a credential is not a passing model test; verify a real call before presenting this as a live AI demo.

### Source limitations

- ByteDance: bounded official website detail/related-reference adapter. Schema/access changes can produce partial or failed runs.
- Ashby: public feed independently returned HTTP200 in initial research. Final adapter live acceptance is incomplete (timeout/oversized records). Further network validation was stopped after approval cancellation; default source remains ByteDance.
- At this historical checkpoint, no actual job application, recruitment-site login, challenge bypass, production database read/copy, or deployment had occurred.

### Public repository verification

On 2026-10-09, [HuXY922/ApplyPilot](https://github.com/HuXY922/ApplyPilot) was published as a public repository. The initial remote commit is [`df51cc9499de1a6bf4092c0e65f280c857625087`](https://github.com/HuXY922/ApplyPilot/commit/df51cc9499de1a6bf4092c0e65f280c857625087).

An unauthenticated Git clone succeeded. All **99 file paths and SHA-256 hashes** exactly matched the privacy-reviewed local source snapshot `9685da3b4af72452d0bb53269cc7165c3f91fbaf`: zero missing files, zero extra files and zero content mismatches. Nested directories and `.env.example` were preserved. The candidate profile is explicitly fictional demonstration data. No project license was added.

Publication used the authenticated GitHub website. It did not create a personal access token or authorize the separate CLI. This source-publication check does not establish a running public Web Demo, browser E2E success, or a live model call.

### Release checks open at the historical checkpoint

1. Real browser desktop/mobile layout and end-to-end flows, including imports, cancellation, back/forward and reload.
2. Live selected-model request with safely configured key and billing limits.
3. Authorized public hosting, real container build, health and restart/redeploy persistence from outside the development environment.
4. Human assessment of job facts and résumé suggestions; wider provider/employer coverage.

The source bundle is an implementation checkpoint with explicit limits, not certification that every requested deliverable is complete.

### Preview safety patch

Three additional synthetic probes found defects in the first preview and were fixed before deployment: indirect transitions could hide unresolved submission status; Chinese-adjacent contact numbers could escape redaction; unrelated optional-major text could suppress a mandatory experience condition.

Regression tests now verify a durable history-derived uncertainty hold, serialized payload contact redaction, and requirement-specific optionality. Independent replay of all three original probes confirms the fixes.

Additional deployment safeguards require Railway's /data mount before creating any SQLite files, limit one import parser per process, and bound parser address space to 256 MiB. No legacy database schema was changed by these patches. At this historical checkpoint, hosted resource/persistence tests and public deployment were pending; subsequent Tencent verification is recorded above.

### Deployment account checkpoint

On 2026-10-09 an authorized minimal Railway project-creation attempt returned: “Your trial has expired. Please select a plan to continue using Railway.” Re-listing projects confirmed no ApplyPilot project was created. No paid upgrade or resource change occurred. At that earlier checkpoint, the GitHub plugin was installed/enabled but exposed no GitHub operations, so authenticated access and publication were still unverified. The later browser publication and independent public-clone verification are recorded above; Railway remained blocked at that checkpoint. Subsequent Tencent, real-model and main-workspace browser results are recorded above; they do not assert successful Railway deployment or controlled-form browser E2E.
