"""Import a standard JSON batch through the real local State API."""
import argparse
import json
import os
from pathlib import Path
from fake_submitter import Client
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('file',type=Path);p.add_argument('--base-url',default='http://127.0.0.1:8765');a=p.parse_args()
    result=Client(a.base_url,os.environ.get('RECRUITMENT_RADAR_TOKEN')).post('/v1/radar/batch',json.loads(a.file.read_text(encoding='utf-8')))
    print(json.dumps(result,ensure_ascii=False,indent=2))
