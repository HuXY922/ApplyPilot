# API and data model

## Trust boundary

The public ApplyPilot app is a separate WSGI entry point. It does not forward `/v1`, `/radar`, role credentials, or arbitrary outbound URLs. The original StateAPI remains in the repository for compatibility and tests. Same-origin cookies identify isolated demonstration workspaces. A visitor cannot select a database filename or another visitor's session identifier.

`GET /api/state` creates a session if needed and supplies a CSRF token. All JSON writes require the exact configured `Origin` and `X-CSRF-Token`. Cookie is HttpOnly, SameSite=Lax and Secure on HTTPS. A model call additionally requires current-provider consent in its request. Fields are validated; unexpected input is rejected.

## Routes

| Method | Route | Purpose |
|---|---|---|
| GET | `/healthz` | Verify local writable registry; no applicant data |
| GET | `/api/state` | Profile/version/review state, jobs, outputs, runs, metrics, model configuration and expiry |
| POST | `/api/profile/import` | `{text}` or `{filename,content_base64}` → unreviewed draft |
| POST | `/api/profile` | `{profile,confirmed:true,expected_version}` → new immutable version |
| POST | `/api/search` | `{query,recruitment_type,cities}` → bounded live-source jobs + run |
| POST | `/api/jobs/{id}/analyze` | `{}` in rules mode; `{model_consent:"deepseek"}` for selected DeepSeek, or `{model_consent:"openai"}` only when OpenAI is configured |
| POST | `/api/jobs/{id}/resume` | Versioned evidence-constrained suggestions; same consent rule |
| POST | `/api/jobs/{id}/track` | `{status,idempotency_key,note?,attestation?}` → updated job and events |
| GET | `/api/jobs/{id}/events` | Ordered manual career-tracker history |
| GET | `/demo-form` | Owned fictional form, not a recruitment site |
| POST | `/api/demo/prepare` | Prepare exact fictional contents and destination for confirmation |
| POST | `/api/demo/submit` | Confirm content-bound local simulation; idempotent receipt |
| POST | `/api/demo/cancel` | Invalidate a pending confirmation |
| GET | `/api/demo/receipts` | Read this session's simulated receipts for reconciliation |

Errors are non-2xx JSON with `error` and `code`; relevant failures include run details. Source failures cannot silently return fictional jobs. A useful rules fallback after a model failure is explicitly degraded and its run is `partial`.

## Model configuration, consent and result provenance

`GET /api/state` includes `model.configured`, `provider`, `mode`, `status`, `model` and a human-readable `message`. The deployment template selects DeepSeek with `APPLYPILOT_MODEL_PROVIDER=deepseek` and `APPLYPILOT_DEEPSEEK_MODEL=deepseek-flash`; its sole credential variable is `APPLYPILOT_DEEPSEEK_API_KEY`. No OpenAI or generic credential fallback is allowed for DeepSeek. The current DeepSeek model allowlist is `deepseek-flash` and `deepseek-v4-pro`.

OpenAI remains an optional, separately selected provider using `APPLYPILOT_OPENAI_MODEL` and `APPLYPILOT_OPENAI_API_KEY` (legacy `OPENAI_MODEL` / `OPENAI_API_KEY` fallback within that provider only). An unset provider variable defaults to OpenAI for backward compatibility. Unsupported providers, including Claude, or invalid/missing selected-provider configuration expose rules mode with a categorical status. Secrets never appear in the state response.

When `model.configured` is true, analysis and resume requests must supply exactly the current provider string as `model_consent`. Omitted, wrong-provider or stale-provider consent returns HTTP 409 `model_consent_required` before reserving a model call or starting a run. The browser obtains provider-specific confirmation for each action; changing providers requires new consent. Rules-only requests use `{}`. A consent value is not an API key and cannot choose a provider, model or endpoint.

The model receives reviewed experience/project description evidence (plus skills for analysis) and de-identified job title, description and requirements. It does not receive the full profile, original resume file or separate name/contact fields. Text de-identification is best effort; embedded personal information may survive, so the public demo remains fictional-data-only. Profile extraction and hard eligibility checks do not call the model.

DeepSeek uses a fixed HTTPS Chat Completions endpoint with non-thinking, non-streaming JSON output; OpenAI uses the fixed Responses endpoint. Provider JSON output still passes local schema and exact-evidence validation. Neither adapter has tools, redirect support or automatic retry, and neither falls back to another provider. See the [DeepSeek API reference](https://api-docs.deepseek.com/api/create-chat-completion/) and [deployment configuration](DEPLOYMENT.md#selected-model-deepseek).

- `configured_not_live_verified` is configuration state, not proof of a successful authenticated call. No live authenticated model verification has been completed as of 2026-10-09.
- Accepted model output has `mode:"model"`, `model_status:"used_grounded_model_output"`, `model_provider` and `model_name`. Provenance is captured from the actual request configuration, not taken from model-generated JSON or a later environment read.
- Failed requests produce `mode:"rules"`, `model_status:"fallback_rules:<category>"`, `degraded:true`, a warning and a `partial` run. Rules/no-evidence results do not claim model provenance. HTTP 200 alone does not prove model use.
- Limits remain global/per-session request counters, not currency budgets. Failed provider requests consume reserved calls; provider billing limits must be managed separately.

## Original authoritative tables retained

- `candidates`, `campaigns`: original profile identity and campaign scope.
- `companies`, `jobs`, `job_sources`: canonical job identity, independent availability, provider IDs and source URLs.
- `applications`, `allowed_transitions`, `application_events`: immutable identities, guarded execution transitions, revisions and evidence requirements.
- `candidate_answers`, `user_tasks`: scoped answers and human-assistance requirements.
- `submission_attempts`: unique idempotency key, unresolved-attempt guard.
- Existing Radar recommendation/run/audit tables remain compatible.

No production database was imported. Demonstration applications are simulation records. Marking a manual tracker stage does not advance the original execution state machine or cause a network submission.

## Incremental tables

`migrations/005_applypilot.sql` adds an `ap_schema` marker without changing the original v4 `PRAGMA user_version` contract.

| Table | Responsibility |
|---|---|
| `ap_profile_versions` | Immutable confirmed/unconfirmed profile snapshots |
| `ap_drafts` | Imported draft awaiting user review |
| `ap_job_details` | Normalized source detail plus observation revision |
| `ap_observations` | Time/hash/source snapshots, bounded recent history |
| `ap_tracker` | Manual career progress associated with original application |
| `ap_tracker_events` | Transactional events, stable idempotency key and request fingerprint |
| `ap_outputs` | Analysis/resume output tied to profile and observation versions |
| `ap_runs` | Start/finish/status/request summary and categorical result |
| `ap_demo_*` | Owned-form content confirmations and simulation receipts |

A separate session registry holds hashed session identifiers, expiry, rate counters and durable search/model budgets. It does not hold user credentials. Each visitor's domain records live in their own SQLite file.

## Invariants

1. Official provider ID and canonical URL decide identity; matching titles alone never merge jobs.
2. Rediscovery updates observations/availability but preserves tracker state.
3. State changes and idempotency fingerprints commit atomically. Reusing a key with different contents conflicts.
4. `submitted` requires explicit manual attestation. Its event is labeled `manual_attestation`, not automated receipt evidence.
5. `submission_unknown` never automatically retries. Reconciliation/user decision is required.
6. Profile or job changes make prior output stale; outputs retain input versions.
7. Verified source facts do not prove candidate eligibility. Hard gates remain pass/fail/unknown and cannot be overridden by a model.
8. Model output is data only. It cannot authorize a tool, change system policy, submit an application or overwrite reviewed facts.

## Demo retention and limits

Default sessions expire after 24 hours; at most 100 sessions, 8 MiB per session DB and 4 MiB registry (journals add temporary overhead). Each job retains the latest 20 observations. Other per-workspace record bounds fail explicitly. These limits make an anonymous demonstration practical; permanent production account retention is not implemented.

Default daily model budget is 20 global and 5 per session; hourly search budget 60 global and 10 per session. Requests, including failures, reserve budget atomically. Configure provider-side financial limits separately.
