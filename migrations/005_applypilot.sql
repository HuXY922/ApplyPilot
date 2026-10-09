-- Additive ApplyPilot tables; intentionally do not change legacy PRAGMA user_version.
BEGIN IMMEDIATE;
CREATE TABLE IF NOT EXISTS ap_schema (version INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS ap_profile_versions (
 version INTEGER PRIMARY KEY, profile_json TEXT NOT NULL CHECK(json_valid(profile_json)),
 confirmed INTEGER NOT NULL CHECK(confirmed IN (0,1)), created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ap_drafts (
 id INTEGER PRIMARY KEY AUTOINCREMENT, draft_json TEXT NOT NULL CHECK(json_valid(draft_json)),
 created_at TEXT NOT NULL, reviewed_at TEXT
);
CREATE TABLE IF NOT EXISTS ap_runs (
 id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL,
 started_at TEXT NOT NULL, finished_at TEXT, request_json TEXT NOT NULL CHECK(json_valid(request_json)),
 result_json TEXT CHECK(result_json IS NULL OR json_valid(result_json))
);
CREATE TABLE IF NOT EXISTS ap_job_details (
 job_id TEXT PRIMARY KEY REFERENCES jobs(id), provider TEXT NOT NULL, external_id TEXT NOT NULL,
 details_json TEXT NOT NULL CHECK(json_valid(details_json)), observation_revision INTEGER NOT NULL DEFAULT 1,
 updated_at TEXT NOT NULL, UNIQUE(provider,external_id)
);
CREATE TABLE IF NOT EXISTS ap_observations (
 id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL REFERENCES jobs(id),
 run_id TEXT REFERENCES ap_runs(id), observed_at TEXT NOT NULL,
 evidence_hash TEXT NOT NULL, details_json TEXT NOT NULL CHECK(json_valid(details_json))
);
CREATE TABLE IF NOT EXISTS ap_tracker (
 application_id TEXT PRIMARY KEY REFERENCES applications(id), job_id TEXT NOT NULL UNIQUE REFERENCES jobs(id),
 status TEXT NOT NULL CHECK(status IN ('saved','skipped','preparing','filling','waiting_user','submission_unknown','submitted','assessment','interview','offer','rejected')),
 revision INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ap_tracker_events (
 sequence INTEGER PRIMARY KEY AUTOINCREMENT, application_id TEXT NOT NULL REFERENCES applications(id),
 from_status TEXT, to_status TEXT NOT NULL, kind TEXT NOT NULL,
 note TEXT NOT NULL DEFAULT '', idempotency_key TEXT NOT NULL UNIQUE,
 request_hash TEXT NOT NULL, occurred_at TEXT NOT NULL,
 CHECK(to_status != 'submitted' OR kind='manual_attestation')
);
CREATE TABLE IF NOT EXISTS ap_outputs (
 id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id),
 kind TEXT NOT NULL CHECK(kind IN ('analysis','resume')),
 profile_version INTEGER NOT NULL REFERENCES ap_profile_versions(version),
 observation_revision INTEGER NOT NULL,
 output_json TEXT NOT NULL CHECK(json_valid(output_json)), created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ap_outputs_job ON ap_outputs(job_id,kind,created_at);
CREATE INDEX IF NOT EXISTS ap_observations_job ON ap_observations(job_id,observed_at);
CREATE INDEX IF NOT EXISTS ap_events_application ON ap_tracker_events(application_id,sequence);
INSERT OR IGNORE INTO ap_schema(version) VALUES(1);
COMMIT;
