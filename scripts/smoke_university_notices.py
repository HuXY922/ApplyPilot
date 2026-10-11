"""One manual bounded live-source check. Never runs as part of offline tests.

Run once with --data-dir pointing to a dedicated disposable smoke directory.
Reuse that directory on a retry so persisted source cooldowns remain effective.
No credentials, model, profile, job or application operations are involved.
"""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from applypilot_core.university_notices import NoticeRadar

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--data-dir', required=True)
args = parser.parse_args()
root = Path(args.data_dir)
root.mkdir(parents=True, exist_ok=True)
result = NoticeRadar(root).refresh({})
report = {'classification': 'live public-source bounded smoke, not browser or production acceptance',
          'run': result['run'], 'total': result['total'],
          'source_states': result['sources'],
          'sample_metadata': [{k: n.get(k) for k in ('id', 'title', 'source_url', 'university_published_on',
                                                     'first_seen_at', 'last_seen_at', 'detail_status',
                                                     'deadline_status', 'deadline_on', 'explicit_cohorts')}
                              for n in result['notices'][:6]]}
print(json.dumps(report, ensure_ascii=False, indent=2))
