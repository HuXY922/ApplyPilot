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
