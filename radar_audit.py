"""Committed eligibility outcomes never masquerade as applications."""
import json
from state_api import uid, now, encode

def record(db,campaign,url,outcome,reasons,evidence=None,run_id=None,revision=None):
    db.execute('''INSERT INTO radar_decisions(id,run_id,campaign_id,canonical_url,outcome,reason_json,evidence_json,profile_revision,created_at)
      VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(run_id,canonical_url) DO UPDATE SET outcome=excluded.outcome,
      reason_json=excluded.reason_json,evidence_json=excluded.evidence_json,profile_revision=excluded.profile_revision,created_at=excluded.created_at''',
      (uid(),run_id,campaign,url,outcome,encode(reasons),encode(evidence or {}),revision,now()))

def profile(db,candidate):
    row=db.execute('SELECT profile_json,revision FROM candidates WHERE id=?',(candidate,)).fetchone()
    from radar_privacy import matching_profile
    return matching_profile(json.loads(row[0])),row[1]
