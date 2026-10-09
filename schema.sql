-- SQLite 3.38+ (JSON functions). All timestamps are UTC ISO 8601.
-- Every connection MUST enable foreign_keys; see db.connect().
BEGIN IMMEDIATE;
CREATE TABLE IF NOT EXISTS schema_migrations (
  version INTEGER PRIMARY KEY,
  applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE TABLE IF NOT EXISTS candidates (
  id TEXT PRIMARY KEY,
  profile_json TEXT NOT NULL CHECK(json_valid(profile_json)),
  revision INTEGER NOT NULL DEFAULT 1 CHECK(revision > 0),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  CHECK(json_extract(profile_json,'$.profile_id') IS NOT NULL
    AND json_extract(profile_json,'$.profile_id') = id)
);
CREATE TABLE IF NOT EXISTS campaigns (
  id TEXT PRIMARY KEY,
  candidate_id TEXT NOT NULL REFERENCES candidates(id),
  name TEXT NOT NULL,
  timezone TEXT NOT NULL DEFAULT 'Asia/Shanghai',
  radar_enabled INTEGER NOT NULL DEFAULT 0 CHECK(radar_enabled IN (0,1)),
  radar_local_time TEXT NOT NULL DEFAULT '08:00',
  UNIQUE(id,candidate_id)
);
CREATE TABLE IF NOT EXISTS companies (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  official_domain TEXT
);
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  company_id TEXT NOT NULL REFERENCES companies(id),
  title TEXT NOT NULL,
  city TEXT,
  canonical_url TEXT UNIQUE,
  description TEXT,
  deadline_at TEXT,
  availability TEXT NOT NULL DEFAULT 'unknown'
    CHECK(availability IN ('unknown','open','closed')),
  discovered_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  verified_at TEXT,
  UNIQUE(id,company_id)
);
-- Multiple sources can resolve to one job; do not merge by title alone.
CREATE TABLE IF NOT EXISTS job_sources (
  id TEXT PRIMARY KEY,
  job_id TEXT NOT NULL REFERENCES jobs(id),
  provider TEXT NOT NULL,
  external_id TEXT NOT NULL,
  url TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  UNIQUE(provider,external_id)
);
CREATE TABLE IF NOT EXISTS applications (
  id TEXT PRIMARY KEY,
  candidate_id TEXT NOT NULL REFERENCES candidates(id),
  campaign_id TEXT NOT NULL,
  job_id TEXT NOT NULL REFERENCES jobs(id),
  mode TEXT NOT NULL CHECK(mode IN ('live','simulation')),
  status TEXT NOT NULL DEFAULT 'pending_review' CHECK(status IN
    ('pending_review','approved','filling','needs_user_input','ready_to_submit',
     'submitting','submission_unknown','submitted','failed','skipped','withdrawn')),
  revision INTEGER NOT NULL DEFAULT 1 CHECK(revision > 0),
  approved_at TEXT,
  approved_by TEXT,
  profile_snapshot_json TEXT CHECK(profile_snapshot_json IS NULL OR json_valid(profile_snapshot_json)),
  resume_library_file_id TEXT,
  resume_sha256 TEXT,
  submission_evidence_json TEXT CHECK(submission_evidence_json IS NULL OR json_valid(submission_evidence_json)),
  submitted_at TEXT,
  last_error_code TEXT,
  last_error_message TEXT,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  FOREIGN KEY(campaign_id,candidate_id) REFERENCES campaigns(id,candidate_id),
  UNIQUE(candidate_id,job_id,mode),
  CHECK(status NOT IN ('approved','filling','ready_to_submit','submitting','submission_unknown','submitted')
    OR (approved_at IS NOT NULL AND approved_by IS NOT NULL)),
  CHECK(status NOT IN ('submitting','submission_unknown','submitted')
    OR (profile_snapshot_json IS NOT NULL AND resume_library_file_id IS NOT NULL AND resume_sha256 IS NOT NULL)),
  CHECK(status != 'submitted' OR (submitted_at IS NOT NULL AND submission_evidence_json IS NOT NULL
    AND coalesce(length(json_extract(submission_evidence_json,'$.reference')),0) > 0
    AND coalesce(json_extract(submission_evidence_json,'$.kind'),'') IN ('receipt','confirmation_page','simulation')
    AND (mode = 'simulation' OR json_extract(submission_evidence_json,'$.kind') != 'simulation')))
);
CREATE TABLE IF NOT EXISTS allowed_transitions (
  from_status TEXT NOT NULL,
  to_status TEXT NOT NULL,
  PRIMARY KEY(from_status,to_status)
);
INSERT OR IGNORE INTO allowed_transitions VALUES
 ('pending_review','approved'),('pending_review','skipped'),
 ('approved','filling'),('approved','withdrawn'),
 ('filling','needs_user_input'),('filling','ready_to_submit'),('filling','failed'),('filling','withdrawn'),
 ('needs_user_input','filling'),('needs_user_input','withdrawn'),
 ('ready_to_submit','submitting'),('ready_to_submit','needs_user_input'),('ready_to_submit','withdrawn'),
 ('submitting','submitted'),('submitting','submission_unknown'),('submitting','failed'),
 ('submission_unknown','submitted'),('submission_unknown','failed'),
 ('failed','filling'),('failed','withdrawn'),('submitted','withdrawn');
CREATE TABLE IF NOT EXISTS application_events (
  sequence INTEGER PRIMARY KEY AUTOINCREMENT,
  application_id TEXT NOT NULL REFERENCES applications(id),
  from_status TEXT,
  to_status TEXT NOT NULL,
  revision INTEGER NOT NULL,
  occurred_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  UNIQUE(application_id,revision)
);
CREATE TRIGGER IF NOT EXISTS application_initial_state BEFORE INSERT ON applications
WHEN NEW.status != 'pending_review' OR NEW.revision != 1
BEGIN SELECT RAISE(ABORT,'application must start at pending_review revision 1'); END;
CREATE TRIGGER IF NOT EXISTS application_transition_guard BEFORE UPDATE ON applications
BEGIN
  SELECT CASE WHEN NEW.id != OLD.id OR NEW.candidate_id != OLD.candidate_id
    OR NEW.job_id != OLD.job_id OR NEW.campaign_id != OLD.campaign_id OR NEW.mode != OLD.mode
    THEN RAISE(ABORT,'application identity is immutable') END;
  SELECT CASE WHEN NEW.revision != OLD.revision + 1
    THEN RAISE(ABORT,'application revision must increment by one') END;
  SELECT CASE WHEN NEW.status != OLD.status AND NOT EXISTS
    (SELECT 1 FROM allowed_transitions WHERE from_status=OLD.status AND to_status=NEW.status)
    THEN RAISE(ABORT,'invalid application transition') END;
END;
CREATE TRIGGER IF NOT EXISTS application_created AFTER INSERT ON applications
BEGIN INSERT INTO application_events(application_id,from_status,to_status,revision)
  VALUES(NEW.id,NULL,NEW.status,NEW.revision); END;
CREATE TRIGGER IF NOT EXISTS application_changed AFTER UPDATE ON applications
WHEN NEW.status != OLD.status
BEGIN INSERT INTO application_events(application_id,from_status,to_status,revision)
  VALUES(NEW.id,OLD.status,NEW.status,NEW.revision); END;
CREATE TABLE IF NOT EXISTS user_tasks (
  id TEXT PRIMARY KEY,
  application_id TEXT NOT NULL REFERENCES applications(id),
  kind TEXT NOT NULL CHECK(kind IN ('missing_information','captcha','login','manual_review','verify_submission')),
  status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','resolved','cancelled')),
  field_key TEXT,
  prompt TEXT NOT NULL,
  context_json TEXT CHECK(context_json IS NULL OR json_valid(context_json)),
  resolution_json TEXT CHECK(resolution_json IS NULL OR json_valid(resolution_json)),
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  resolved_at TEXT,
  CHECK(status != 'resolved' OR (resolution_json IS NOT NULL AND resolved_at IS NOT NULL))
);
CREATE TABLE IF NOT EXISTS candidate_answers (
  id TEXT PRIMARY KEY,
  candidate_id TEXT NOT NULL REFERENCES candidates(id),
  field_key TEXT NOT NULL,
  value_json TEXT NOT NULL CHECK(json_valid(value_json) AND value_json != 'null'),
  source_json TEXT NOT NULL CHECK(json_valid(source_json)),
  scope TEXT NOT NULL CHECK(scope IN ('global','campaign','company','job')),
  campaign_id TEXT,
  company_id TEXT REFERENCES companies(id),
  job_id TEXT REFERENCES jobs(id),
  confirmed_at TEXT NOT NULL,
  valid_until TEXT,
  revalidate_when TEXT,
  superseded_at TEXT,
  FOREIGN KEY(campaign_id,candidate_id) REFERENCES campaigns(id,candidate_id),
  CHECK((scope='global' AND campaign_id IS NULL AND company_id IS NULL AND job_id IS NULL)
     OR (scope='campaign' AND campaign_id IS NOT NULL AND company_id IS NULL AND job_id IS NULL)
     OR (scope='company' AND campaign_id IS NULL AND company_id IS NOT NULL AND job_id IS NULL)
     OR (scope='job' AND campaign_id IS NULL AND company_id IS NULL AND job_id IS NOT NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_answer ON candidate_answers
 (candidate_id,field_key,scope,coalesce(campaign_id,''),coalesce(company_id,''),coalesce(job_id,''))
 WHERE superseded_at IS NULL;
CREATE TABLE IF NOT EXISTS submission_attempts (
  id TEXT PRIMARY KEY,
  application_id TEXT NOT NULL REFERENCES applications(id),
  idempotency_key TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL CHECK(status IN ('running','succeeded','failed','unknown')),
  started_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  finished_at TEXT,
  evidence_json TEXT CHECK(evidence_json IS NULL OR json_valid(evidence_json))
);
CREATE UNIQUE INDEX IF NOT EXISTS one_unresolved_attempt ON submission_attempts(application_id)
 WHERE status IN ('running','unknown');
CREATE TABLE IF NOT EXISTS inbox_messages (
  id TEXT PRIMARY KEY,
  mailbox_key TEXT NOT NULL,
  provider_message_key TEXT NOT NULL,
  sender TEXT NOT NULL,
  subject TEXT NOT NULL,
  received_at TEXT NOT NULL,
  snippet TEXT,
  application_id TEXT REFERENCES applications(id),
  match_status TEXT NOT NULL DEFAULT 'unmatched' CHECK(match_status IN ('unmatched','suggested','confirmed')),
  category TEXT CHECK(category IN ('receipt','assessment','interview','offer','rejection','other')),
  action_due_at TEXT,
  UNIQUE(mailbox_key,provider_message_key),
  CHECK(match_status='unmatched' OR application_id IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS application_queue ON applications(mode,status,updated_at);
CREATE INDEX IF NOT EXISTS open_user_tasks ON user_tasks(status,application_id);
CREATE INDEX IF NOT EXISTS messages_by_application ON inbox_messages(application_id,received_at);
INSERT OR IGNORE INTO schema_migrations(version) VALUES(1);
PRAGMA user_version=1;
COMMIT;
