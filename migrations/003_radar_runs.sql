BEGIN IMMEDIATE;
CREATE TABLE IF NOT EXISTS radar_runs (
 id TEXT PRIMARY KEY,
 candidate_id TEXT NOT NULL REFERENCES candidates(id),
 campaign_id TEXT NOT NULL REFERENCES campaigns(id),
 radar_date TEXT NOT NULL,
 started_at TEXT NOT NULL,
 finished_at TEXT,
 status TEXT NOT NULL DEFAULT 'running' CHECK(status IN ('running','completed','partial','failed')),
 discovered_count INTEGER NOT NULL DEFAULT 0 CHECK(discovered_count>=0),
 verified_count INTEGER NOT NULL DEFAULT 0 CHECK(verified_count>=0),
 imported_count INTEGER NOT NULL DEFAULT 0 CHECK(imported_count>=0),
 duplicate_count INTEGER NOT NULL DEFAULT 0 CHECK(duplicate_count>=0),
 rejected_count INTEGER NOT NULL DEFAULT 0 CHECK(rejected_count>=0),
 failure_count INTEGER NOT NULL DEFAULT 0 CHECK(failure_count>=0),
 UNIQUE(candidate_id,campaign_id,radar_date)
);
CREATE TABLE IF NOT EXISTS radar_observations (
 run_id TEXT NOT NULL REFERENCES radar_runs(id),
 application_id TEXT NOT NULL REFERENCES applications(id),
 canonical_url TEXT NOT NULL,
 verified_at TEXT NOT NULL,
 evidence_hash TEXT NOT NULL,
 created INTEGER NOT NULL CHECK(created IN (0,1)),
 PRIMARY KEY(run_id,canonical_url)
);
INSERT OR IGNORE INTO schema_migrations(version) VALUES(3);
PRAGMA user_version=3;
COMMIT;
