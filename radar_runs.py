"""Radar-specific narrow reads and run accounting. No application transitions."""
import json
import re
from datetime import date
from state_api import require, text_field, now


from radar_privacy import matching_profile


def profile_for_campaign(api, db, query):
    campaign = api.row(db, 'campaigns', query.get('campaign_id', [''])[0])
    candidate = api.row(db, 'candidates', campaign['candidate_id'])
    return {'campaign_id': campaign['id'], 'candidate_id': candidate['id'],
            'profile': matching_profile(json.loads(candidate['profile_json']))}


def start(api, db, data):
    require(set(data) == {'id', 'candidate_id', 'campaign_id', 'radar_date'}, 'Invalid run fields')
    id = text_field(data, 'id')
    require(bool(re.fullmatch(r'[a-zA-Z0-9-]{1,100}', id)), 'Invalid run id')
    campaign = api.row(db, 'campaigns', data['campaign_id'])
    require(campaign['candidate_id'] == data['candidate_id'], 'Campaign mismatch', 409)
    try:
        require(date.fromisoformat(data['radar_date']).isoformat() == data['radar_date'], 'Invalid date')
    except (ValueError, TypeError):
        require(False, 'Invalid date')
    previous = db.execute('SELECT * FROM radar_runs WHERE candidate_id=? AND campaign_id=? AND radar_date=?',
                          (data['candidate_id'], data['campaign_id'], data['radar_date'])).fetchone()
    if previous:
        require(previous['id'] == id, 'Daily run already exists', 409)
        return dict(previous)
    require(db.execute('SELECT 1 FROM radar_runs WHERE id=?', (id,)).fetchone() is None, 'Run id conflict',409)
    db.execute('INSERT INTO radar_runs(id,candidate_id,campaign_id,radar_date,started_at) VALUES(?,?,?,?,?)',
               (id, data['candidate_id'], data['campaign_id'], data['radar_date'], now()))
    return api.row(db, 'radar_runs', id)


def finish(api, db, data):
    keys = ('discovered_count', 'verified_count', 'rejected_count', 'failure_count')
    require(set(data) == {'id', *keys}, 'Invalid finish fields')
    run = api.row(db, 'radar_runs', text_field(data, 'id'))
    if run['status'] != 'running':
        return run
    for k in keys:
        require(type(data[k]) is int and 0 <= data[k] <= 100000, 'Invalid run count')
    require(data['verified_count'] + data['rejected_count'] <= data['discovered_count'], 'Inconsistent counts')
    counts = db.execute('SELECT count(*),coalesce(sum(created),0) FROM radar_observations WHERE run_id=?', (run['id'],)).fetchone()
    require(counts[0] <= data['verified_count'], 'Verified count below imported observations')
    status = ('partial' if counts[0] else 'failed') if data['failure_count'] else 'completed'
    db.execute('''UPDATE radar_runs SET finished_at=?,status=?,discovered_count=?,verified_count=?,
               rejected_count=?,failure_count=?,imported_count=?,duplicate_count=? WHERE id=?''',
               (now(), status, *(data[k] for k in keys), counts[1], counts[0]-counts[1], run['id']))
    return api.row(db, 'radar_runs', run['id'])
