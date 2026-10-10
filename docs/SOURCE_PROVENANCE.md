# Source and audit provenance

ApplyPilot reuses the user's existing Career Desk project. The audited full source is `career-desk-step9.1b-pro-work-radar.zip`, version 2, modified 2026-09-08, SHA-256 `412ebf49d3c224e21223dff246a88232e1cf1d892377963d6d722c8bad815245`.

This is the latest complete application archive located, not a claim that it is the final production source. A later connector archive records a 9.1c-1 deployment; that complete source was not located. The connector is not the application backend.

The original archive is preserved separately. Actual personal profile, historical job batch, reports and deployment credentials are not release assets. ApplyPilot uses independently authored fictional fixtures.

## Reproduced baseline
- Python 3.12.14 / SQLite 3.53.1.
- First run: 118 passed and one failure caused by absent Gunicorn.
- After installing pinned Gunicorn 25.1.0 in an isolated environment: 119 tests passed, zero skipped, 1.700 seconds unittest execution.
- Coverage comprises deterministic/mocked tests and some genuine loopback HTTP/restart checks. It is not live web discovery, browser E2E, model inference or production submission acceptance.

## Independently observed source availability (2026-10-09 UTC)
ByteDance official detail read: HTTP 200 at approximately 05:41, matching official job ID `7667889560824154373`, explicitly in-service campus job, 2027 cohort. Related references are discoveries, not independently verified jobs.

- Official page: https://jobs.bytedance.com/campus/position/detail/7667889560824154373
- Source endpoint is publicly reachable website infrastructure, not a documented developer API or blanket redistribution license.
- Availability is a time-bound observation. Refetch before relying on it.

Ashby public posting API: HTTP 200 at approximately 05:40, 68 postings in the Ashby employer board. This establishes global-source availability, not China-campus eligibility.

- Documentation: https://developers.ashbyhq.com/docs/public-job-posting-api
- Public feed: https://api.ashbyhq.com/posting-api/job-board/Ashby
- Documentation does not establish unrestricted redistribution rights. Wider aggregation should use permissioned partner feeds: https://developers.ashbyhq.com/docs/dedicated-partner-job-feeds

Store normalized factual fields, limited relevant excerpts, hashes, timestamps and source links; avoid redistributing full copyrighted descriptions as bulk datasets. Respect access restrictions and source changes.


## CXMT official China source (2026-10-10 UTC)

This addition is independently implemented in Python. It does not execute or bundle CareerSail, install a new dependency, use authenticated submission endpoints, or claim licensed-search-API access.

- Corporate ownership: https://www.cxmt.com/join.html links its campus recruitment entry to https://cxmt.zhiye.com/campus/jobs.
- Robots observed at https://cxmt.zhiye.com/robots.txt: all user agents allowed. Public readability and robots allowance are not blanket redistribution rights.
- The live campus page's JSON `BSGlobal` identifies tenant `cxmt`. Its `Pages` explicitly maps `CampusList` / `CampusDetail` to `BusinessType=2`; the adapter reads this mapping each run and never guesses a default campus category. It parses JSON, never executes downloaded scripts.
- Reviewed read-only list endpoint: `POST https://cxmt.zhiye.com/api/Jobad/GetJobAdPageList`. Payload has zero-based `PageIndex`, bounded `PageSize`, bootstrapped `Category`, user-entered `KeyWords`, `SpecialType=0`, empty `PortalId`, and the fixed display-field list. Successful responses provide `Code=200`, `Count`, and complete per-job `Id`, `Duty`, `Require`, `CategoryId`, `Status`, and location fields.
- Detail identity cross-check: `GET /api/JobAd/GetJobAdInfo` with the same GUID, category and fixed display fields matched `AI研究员(J20761)`, GUID `ac68414a-f5fb-47f7-a62e-ec710bf450fa`, complete requirements and `Status=1`. Public page: https://cxmt.zhiye.com/campus/detail?jobAdId=ac68414a-f5fb-47f7-a62e-ec710bf450fa.
- The official site's public detail component at https://tcdn.bstatics.com/ux/ux-recruitment-portal-2022/release/dist/6868-b3974eeb02939eeadf32.chk.js calls that info endpoint and sends `Status=2` records to `/job/empty`. Runtime accepts only integer 1 as open, integer 2 as closed; other values stay unknown. An unset deadline (`EndTimeInt=0`, `EndTime=0001-01-01T00:00:00`) is not an expiry; conflicting/malformed dates are rejected.
- Canonical job URLs use the official category detail route and stable GUID, never a title job number such as J20761. Runtime evidence explicitly identifies complete-JD-list verification and does not claim every detail page was opened.

### Separate live checks, not unit-test evidence

At 09:39:45–09:39:59 UTC, using the previous PageSize=20, an actual keyword `AI` search read page indices 0 and 1, returned 40 distinct complete open campus JD records, and reported 45 upstream matches. It stopped at the two-page limit; the remaining 5 were not checked. Three requests took 13.77 seconds.

At 09:41:57 and 09:42:06 UTC, using the previous PageSize=20, low-frequency control reads recorded actual POST bodies and unfiltered response identities: `KeyWords=AI` returned an upstream total of 45 and 20 first-page records whose JD text all contained AI; `KeyWords=财务` returned 8 records, all with 财务 in the JD text, led by three 财务专员 city variants. The sets differed. This establishes keyword participation upstream rather than counting a locally filtered fixed feed. It does not establish ranking quality or exhaustive coverage.

Real employer response bodies, downloaded JavaScript, full descriptions and raw capture logs are retained only as local review evidence, not release assets. Tests use clearly fictional synthetic fixtures. No graduation cohort is inferred from recruitment homepage banners.
