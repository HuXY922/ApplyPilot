BEGIN IMMEDIATE;
CREATE TABLE IF NOT EXISTS radar_decisions (
 id TEXT PRIMARY KEY,
 run_id TEXT REFERENCES radar_runs(id),
 campaign_id TEXT NOT NULL REFERENCES campaigns(id),
 canonical_url TEXT NOT NULL,
 outcome TEXT NOT NULL CHECK(outcome IN ('signal','defer','hard_fail','imported','duplicate')),
 reason_json TEXT NOT NULL CHECK(json_valid(reason_json)),
 evidence_json TEXT NOT NULL CHECK(json_valid(evidence_json)),
 profile_revision INTEGER,
 created_at TEXT NOT NULL,
 UNIQUE(run_id,canonical_url)
);
CREATE TABLE IF NOT EXISTS radar_sessions (
 session_hash TEXT PRIMARY KEY,
 expires_at INTEGER NOT NULL,
 created_at INTEGER NOT NULL
);
INSERT OR IGNORE INTO schema_migrations(version) VALUES(4);
PRAGMA user_version=4;
COMMIT;
