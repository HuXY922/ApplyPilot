"""Initialize the recruitment database without overwriting existing profiles."""
import argparse
import json
from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parent


def connect(path):
    con = sqlite3.connect(path, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    con.execute('PRAGMA busy_timeout=10000')
    return con


def initialize(path, profile_path=ROOT / 'candidate-profile.json'):
    profile = json.loads(Path(profile_path).read_text(encoding='utf-8'))
    candidate_id = profile['profile_id']
    if not isinstance(candidate_id, str) or not candidate_id:
        raise ValueError('profile_id must be a nonempty string')
    with connect(path) as con:
        version = con.execute('PRAGMA user_version').fetchone()[0]
        if version not in (0, 1, 2, 3, 4):
            raise ValueError(f'Unsupported database version: {version}')
        if version < 1:
            con.executescript((ROOT / 'schema.sql').read_text(encoding='utf-8'))
        if version < 2:
            con.executescript((ROOT / 'migrations/002_radar.sql').read_text(encoding='utf-8'))
        if version < 3:
            con.executescript((ROOT / 'migrations/003_radar_runs.sql').read_text(encoding='utf-8'))
        if version < 4:
            con.executescript((ROOT / 'migrations/004_radar_console.sql').read_text(encoding='utf-8'))
        con.execute('INSERT OR IGNORE INTO candidates(id,profile_json) VALUES(?,?)',
                    (candidate_id, json.dumps(profile, ensure_ascii=False)))
        con.execute('INSERT OR IGNORE INTO campaigns(id,candidate_id,name) VALUES(?,?,?)',
                    ('autumn-2026-' + candidate_id, candidate_id, '2026 秋招'))
        if con.execute('PRAGMA foreign_key_check').fetchall():
            raise RuntimeError('Foreign key check failed')
    con.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=ROOT / 'recruitment.sqlite3')
    parser.add_argument('--profile', type=Path, default=ROOT / 'candidate-profile.json')
    args = parser.parse_args()
    initialize(args.db, args.profile)
    print(f'Initialized: {args.db}')
