# Minimal observed HTML fixtures

Captured from public Nankai and DUT Panjin pages on 2026-10-11 using ordinary TLS GETs. These are deliberately tiny actual row/paragraph fragments, with reconstructed outer wrappers explicitly noted in each file. They are not full-page snapshots, licensed datasets, or proof of production availability.

- `nankai-list.html`: actual dates and titles for 102975 (single-role title, 2026-10-11) and 119691 (2027 cohort recruitment, 2026-10-10). School代发 is a publisher, not an employer.
- `nankai-detail.html`: Huya 119582; dated 2026-10-09, explicit graduation window and yearless October 31 deadline. The title's observed javascript href is intentionally retained to test treating it as text, never a navigation target.
- `dlut-list.html`: actual a-row structures for 48042 and 48012; both source dates 2026-10-10. Rows are not li/tr.
- `dlut-detail.html`: Chongqing Bank 48012; 2027 cohort and explicit 2026-11-01 18:00 deadline. No host timezone is inferred.

No full response, scripts, fetched assets or personal contacts are included. Test fake transports may intentionally reuse a detail fixture for multiple test IDs; those synthetic combinations exercise contracts and are not factual source records. Live evidence is separately recorded in docs/evidence/.
