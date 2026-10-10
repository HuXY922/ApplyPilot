# Third-party dependency inventory

License fields below were checked from installed distribution metadata on 2026-10-09. Dependency source code is not copied into this repository. Install distributions with their bundled license files; preserve those notices if redistributing a packaged environment.

| Dependency | Version inspected | License metadata | Purpose |
|---|---|---|---|
| Gunicorn | 25.1.0 | MIT | WSGI serving |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | Gunicorn dependency |
| pypdf | 6.10.0 | BSD-3-Clause | PDF text extraction |
| python-docx | 1.2.0 | MIT | DOCX text extraction |
| lxml | 6.1.3 | BSD-3-Clause | DOCX dependency |
| typing_extensions | 4.16.0 | PSF-2.0 | DOCX dependency |
| Playwright | 1.62.0 | Apache-2.0 | Development browser testing |
| pyee | 13.0.1 | MIT | Playwright dependency |
| greenlet | 3.5.5 | MIT AND PSF-2.0 | Playwright dependency |

The Python and SQLite runtime licenses also apply. Browser binaries have their own distribution notices; do not assume the Playwright package license covers them.

The starting Career Desk code was supplied by the project owner. Its archive did not contain an explicit project-wide license. This audit does not infer a third party's permission or assign an open-source license on the owner's behalf. Select a project license before inviting external code reuse.

Job descriptions and employer branding are third-party content, not licensed by this application's code license. Public endpoints and robots rules do not imply unrestricted redistribution. Keep source attribution, limited supporting excerpts, timestamps and links; obtain permission for broader aggregation or bulk redistribution.


## Recruitment adapter design reference

CareerSail's Beisen adapter (https://github.com/genius916/CareerSail/blob/main/lib/recruiters/beisen.js) was read as an interface-design reference on 2026-10-10. Its repository carries an MIT license (https://github.com/genius916/CareerSail/blob/main/LICENSE). No CareerSail implementation code is copied, bundled or executed here; the Python provider is independently authored and validated against the employer's public website. In particular, fallback category IDs, pre-existing “verified” labels, browser impersonation and application-submission behavior are not adopted. The site's own scripts were read only to establish public API semantics, not copied into release assets.
