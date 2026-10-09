# Independent deployment

This is a plan/configuration, not a claim that a public service has been created.

## Current delivery status (checked 2026-10-09)

- Railway `create_project` returned: "Your trial has expired. Please select a plan to continue using Railway." No new ApplyPilot project was created. A paid plan upgrade has not been authorized; do not retry by selecting a paid plan or using the old Career Desk project/volume.
- The public GitHub plugin is installed and enabled, but this execution session exposes no GitHub tools. Remote repository publication remains pending; installation alone does not establish a published repository.
- DeepSeek is the selected deployment provider. Its adapter and configuration are present, but no live authenticated model request has been verified. No public ApplyPilot URL, deployed outbound connectivity or persistent-volume redeploy test has been verified.
- Resume provisioning only after the account blocker is resolved within an approved spending scope. Publication and deployed acceptance checks below remain separate completion gates.

## Preconditions
- Use a new ApplyPilot project/service. Never attach, copy or reset old Career Desk production volumes.
- Confirm account billing, payment method and recurring spending limit before provisioning paid resources. Published rates are not a fixed quote.
- Supply only fictional demonstration data. Review secrets and source licenses before public upload.
- A connected GitHub account or an authorized deployment upload is required for remote publication.

## Railway
1. Create the authorized independent service using this repository.
2. Select `railway.applypilot.toml` as its configuration file (otherwise Railway may select the preserved legacy `railway.toml`). It builds `Dockerfile.applypilot`.
3. Attach a NEW persistent volume at `/data`; set `APPLYPILOT_DATA_DIR=/data`.
4. Set `APPLYPILOT_PUBLIC=1` and exact HTTPS `PUBLIC_ORIGIN` matching the generated domain. Configure remaining non-secret settings from `.env.example`.
5. Use the selected DeepSeek settings below. The owner supplies the provider key through the hosting platform's secure server-variable settings. Never enter credentials in chat, source files or the ApplyPilot browser UI. Missing configuration gives visibly labeled rules mode; a configured adapter is not live authenticated acceptance.
6. Deploy one instance. SQLite plus volume is not a multi-replica design. Verify `/healthz`, isolated visitors, writes, and persistence after redeploy.
7. Set appropriate account cost alerts/limits and edge rate limits. Railway hard limits may affect the entire workspace; do not change shared billing controls without explicit authorization.

Sessions are temporary demo workspaces with configurable expiry (default 24 hours), not production applicant accounts. Export/production account retention is a future design requirement. Session expiry must be visible in the interface and user guide.

## Selected model: DeepSeek

Set these non-secret variables on the independent service:

```dotenv
APPLYPILOT_MODEL_PROVIDER=deepseek
APPLYPILOT_DEEPSEEK_MODEL=deepseek-flash
# APPLYPILOT_DEEPSEEK_API_KEY=
```

The commented key is a placeholder only. `.env.example` is a deployment template, not a credential store; this application does not automatically load it. Configure actual values in the process environment. DeepSeek uses only `APPLYPILOT_DEEPSEEK_API_KEY`, never `OPENAI_API_KEY`, `APPLYPILOT_OPENAI_API_KEY` or a generic fallback. A failure returns labeled rules output, not a request to another provider.

The adapter sends a bounded, non-streaming request to the fixed `https://api.deepseek.com/chat/completions` endpoint with `thinking.type=disabled`, `response_format.type=json_object` and `max_tokens=2500`. It permits `deepseek-flash` (the selected default) and `deepseek-v4-pro`. These model names and API options were checked against [official DeepSeek Chat Completions documentation](https://api-docs.deepseek.com/api/create-chat-completion/) on 2026-10-09. JSON mode is not a substitute for ApplyPilot's local schema/evidence validation. Check [current model details and pricing](https://api-docs.deepseek.com/quick_start/pricing/) and the actual account before use; no model balance, account access or authenticated call has been verified.

A request requires `model_consent:"deepseek"` while DeepSeek is configured. Review the named recipient and minimized evidence/JD payload again after any provider change. Consent for OpenAI does not authorize DeepSeek. Only fictional data belongs in this public demo; text de-identification is best effort, not guaranteed anonymity.

Optional OpenAI compatibility remains available by explicitly setting `APPLYPILOT_MODEL_PROVIDER=openai`, an available `APPLYPILOT_OPENAI_MODEL` and `APPLYPILOT_OPENAI_API_KEY`. That path alone supports the legacy `OPENAI_MODEL` / `OPENAI_API_KEY` variables and uses the fixed OpenAI Responses endpoint. An environment with no provider variable defaults to OpenAI for backward compatibility, so set the provider explicitly for this deployment. Keys must match their provider. Claude is not supported.

### Model acceptance gate

After secure key configuration and deployment:
1. Inspect `/api/state`: selected provider/model should be correct. `configured_not_live_verified` only means configuration is present.
2. Using a fictional confirmed profile and the current provider-specific consent, run one analysis and one resume request within the approved account budget.
3. Verify each output is `mode:"model"`, `model_status:"used_grounded_model_output"`, and identifies `model_provider:"deepseek"` plus the selected `model_name`. Check saved evidence links and output versions. A successful HTTP response containing rules fallback is not model acceptance.
4. Check provider-side usage and deployed logs without exposing credentials or request contents. Record the observed result and time separately from mocked tests. If authentication, budget, source networking or schema/evidence validation fails, preserve the explicit failure/fallback and resolve it before marking the gate complete.

## Docker locally
`docker build -f Dockerfile.applypilot -t applypilot .`

For local testing, set `PUBLIC_ORIGIN=http://localhost:8787`, bind port 8787 and mount a local directory to `/data`. Keep `APPLYPILOT_PUBLIC` unset for local HTTP; public hosting requires HTTPS.

## Costs and limits
Check current provider documentation and account billing before deploying:
- [DeepSeek model pricing](https://api-docs.deepseek.com/quick_start/pricing/)
- https://docs.railway.com/pricing
- https://docs.railway.com/volumes/reference
- https://docs.railway.com/pricing/cost-control

No automated application submission is enabled. The optional owned-form demonstration is explicitly simulated.

## Minimal single-instance resource profile

For an initial 500 MB volume, configure at most 32 simultaneous retained demo sessions (8 MiB each plus registry/journals) rather than the application default of 100. This is a storage ceiling plan, not measured service memory. File parsing is separately resource-limited and concurrent import work is bounded. Confirm actual account plan, credit, egress and quota before creating a service. No PostgreSQL migration is currently justified by the tested single-instance workload.

## Trial and account checks (checked 2026-10-09)

Railway Full Trial has full networking; Limited Trial restricts outbound requests and ports without publishing an exact destination allowlist. Trial is at most 30 days or $5 credit; Free has different resources and must not be mistaken for an active Trial. Read https://docs.railway.com/pricing/free-trial and https://docs.railway.com/pricing/plans, then check the actual workspace plan/credit. Connecting GitHub does not guarantee automatic verification.

After minimal deployment, prove outbound access to the official job source and selected model from that deployed instance. A healthcheck or fictional-data smoke is not core-feature acceptance. If access is restricted, disclose it and request approval for any paid remedy; never silently substitute demo records. Trial stateful volumes also have an expiry-deletion policy, so a trial is not permanent storage.

Startup now fails before database creation if Railway's volume mount is missing, not /data, or the configured data directory escapes it. Model API keys remain owner-configured server variables.
