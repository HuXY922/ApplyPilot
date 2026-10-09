import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from db import connect, initialize


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'test.sqlite3'
        initialize(self.path)
        self.db = connect(self.path)
        self.candidate = self.db.execute('SELECT id FROM candidates').fetchone()[0]
        self.campaign = self.db.execute('SELECT id FROM campaigns').fetchone()[0]
        self.db.execute("INSERT INTO companies(id,name) VALUES('co','测试公司')")
        self.db.execute("INSERT INTO jobs(id,company_id,title) VALUES('job','co','测试岗位')")
        self.add_application('app', 'live')

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def add_application(self, id, mode):
        self.db.execute('INSERT INTO applications(id,candidate_id,campaign_id,job_id,mode) VALUES(?,?,?,?,?)',
                        (id, self.candidate, self.campaign, 'job', mode))

    def move(self, status, **fields):
        assignments = ['status=?', 'revision=revision+1'] + [f'{key}=?' for key in fields]
        self.db.execute('UPDATE applications SET ' + ','.join(assignments) + " WHERE id='app'",
                        [status, *fields.values()])

    def prepare_submission(self):
        self.move('approved', approved_at='2026-09-06T00:00:00Z', approved_by='user')
        self.move('filling')
        self.move('ready_to_submit')
        self.move('submitting', profile_snapshot_json='{}', resume_library_file_id='test-resume', resume_sha256='test-sha')

    def test_duplicate_live_application_and_simulation_isolation(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.add_application('duplicate', 'live')
        self.add_application('fake', 'simulation')

    def test_initial_state_and_approval_required(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.move('approved')
        with self.assertRaises(sqlite3.IntegrityError):
            self.move('submitted')
        self.assertEqual(self.db.execute('SELECT count(*) FROM application_events').fetchone()[0], 1)

    def test_pause_resume_and_audit_history(self):
        self.move('approved', approved_at='2026-09-06T00:00:00Z', approved_by='user')
        self.move('filling')
        self.move('needs_user_input')
        self.move('filling')
        statuses = [row[0] for row in self.db.execute('SELECT to_status FROM application_events ORDER BY sequence')]
        self.assertEqual(statuses, ['pending_review','approved','filling','needs_user_input','filling'])

    def test_unknown_submission_cannot_blindly_retry(self):
        self.prepare_submission()
        self.move('submission_unknown')
        with self.assertRaises(sqlite3.IntegrityError):
            self.move('submitting')
        with self.assertRaises(sqlite3.IntegrityError):
            self.move('submitted', submitted_at='2026-09-06T00:00:00Z',
                      submission_evidence_json='{"kind":"simulation","reference":"fake"}')
        self.move('submitted', submitted_at='2026-09-06T00:00:00Z',
                  submission_evidence_json='{"kind":"receipt","reference":"receipt-test"}')

    def test_stale_revision_and_immutable_mode(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE applications SET mode='simulation',revision=revision+1 WHERE id='app'")
        self.move('approved', approved_at='2026-09-06T00:00:00Z', approved_by='user')
        cur = self.db.execute("UPDATE applications SET status='filling',revision=revision+1 WHERE id='app' AND revision=1")
        self.assertEqual(cur.rowcount, 0)

    def test_answer_scope_and_active_answer_uniqueness(self):
        sql = '''INSERT INTO candidate_answers
          (id,candidate_id,field_key,value_json,source_json,scope,company_id,confirmed_at)
          VALUES(?,?,'salary','100','{}',?,?, '2026-09-06T00:00:00Z')'''
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(sql, ('bad', self.candidate, 'company', None))
        self.db.execute(sql, ('answer', self.candidate, 'company', 'co'))
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(sql, ('duplicate', self.candidate, 'company', 'co'))

    def test_unresolved_attempts_block_duplicates(self):
        self.db.execute("INSERT INTO submission_attempts(id,application_id,idempotency_key,status) VALUES('a','app','key','unknown')")
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("INSERT INTO submission_attempts(id,application_id,idempotency_key,status) VALUES('b','app','key2','running')")

    def test_initializer_preserves_existing_profile(self):
        self.db.commit()
        self.db.execute("UPDATE candidates SET revision=2")
        self.db.commit()
        initialize(self.path)
        self.assertEqual(self.db.execute('SELECT revision FROM candidates').fetchone()[0], 2)
        self.assertEqual(self.db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        self.assertEqual(self.db.execute('PRAGMA foreign_key_check').fetchall(), [])


if __name__ == '__main__':
    unittest.main()
