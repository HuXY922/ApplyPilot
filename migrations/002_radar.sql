BEGIN IMMEDIATE;
CREATE TABLE IF NOT EXISTS job_recommendations (
 id TEXT PRIMARY KEY,
 application_id TEXT NOT NULL REFERENCES applications(id),
 radar_date TEXT NOT NULL CHECK(length(radar_date)=10),
 priority TEXT NOT NULL CHECK(priority IN ('P0','P1','P2')),
 match_score REAL NOT NULL CHECK(match_score BETWEEN 0 AND 100),
 match_reasons TEXT NOT NULL CHECK(json_valid(match_reasons) AND json_type(match_reasons)='array'),
 risk_flags TEXT NOT NULL CHECK(json_valid(risk_flags) AND json_type(risk_flags)='array'),
 recommendation TEXT NOT NULL,
 UNIQUE(application_id,radar_date)
);
CREATE INDEX IF NOT EXISTS recommendations_date ON job_recommendations(radar_date,priority);
INSERT OR IGNORE INTO schema_migrations(version) VALUES(2);
PRAGMA user_version=2;
COMMIT;
