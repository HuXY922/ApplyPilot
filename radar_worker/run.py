"""python -m radar_worker.run --profile-file candidate-profile.json --report /tmp/radar.json"""
import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from zoneinfo import ZoneInfo
from .http import Transport, FetchError
from .sources import discover, verify
from .matching import match
from .normalization import normalize


def config(env):
    origin=env.get('CAREER_DESK_ORIGIN',env.get('PUBLIC_ORIGIN',''))
    u=urlsplit(origin)
    if u.scheme!='https' or not u.hostname or u.netloc!=u.hostname or u.path or u.query or u.fragment:
        raise ValueError('CAREER_DESK_ORIGIN must be an exact HTTPS origin')
    campaign=env.get('RADAR_CAMPAIGN_ID','')
    if not campaign or len(campaign)>200:raise ValueError('RADAR_CAMPAIGN_ID required')
    live=env.get('RADAR_LIVE_IMPORT','false')
    if live not in ('true','false'):raise ValueError('RADAR_LIVE_IMPORT must be true or false')
    token=env.get('RECRUITMENT_RADAR_TOKEN','')
    if not re.fullmatch('[a-zA-Z0-9_-]{32,256}',token):raise ValueError('Radar secret missing or invalid')
    if env.get('RECRUITMENT_USER_TOKEN') or env.get('RECRUITMENT_WORKER_TOKEN'):
        raise ValueError('Dedicated Radar service must not receive other role secrets')
    return origin,campaign,live=='true',token


class CareerAPI:
    def __init__(self,origin,token):
        self.origin=origin
        self.http=Transport([urlsplit(origin).hostname],token)
    def call(self,path,body=None):
        if path.split('?')[0] not in ('/v1/radar/profile','/v1/radar/runs/start','/v1/radar/runs/finish','/v1/radar/batch'):
            raise FetchError('radar_route_denied')
        return self.http.json(self.origin+path,body,authenticated=True)


def execute(profile, campaign_id, http, *, api=None, live=False, pause=time.sleep, discover_fn=discover, clock=None):
    if live and api is None:raise ValueError('Live import requires API')
    now=(clock or (lambda:datetime.now(timezone.utc)))()
    day=now.astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat()
    run_id=hashlib.sha256((campaign_id+'|'+profile['profile_id']+'|'+day).encode()).hexdigest()
    report={'schema_version':1,'run_id':run_id,'radar_date':day,'started_at':now.isoformat(),
            'mode':'live' if live else 'dry_run','discovered_count':0,'verified_count':0,
            'imported_count':0,'duplicate_count':0,'rejected_count':0,'failure_count':0,
            'candidates':[],'rejections':[],'failures':[]}
    if live:
        run=api.call('/v1/radar/runs/start',{'id':run_id,'candidate_id':profile['profile_id'],'campaign_id':campaign_id,'radar_date':day})
        if run['status']!='running':
            return {**report,'already_finished':True,'server_run':run}
    refs,failures,aliases=discover_fn(http,pause=pause)
    report.update(discovered_count=len(refs),discovery_alias_count=aliases,failures=failures)
    for ref in refs:
        try:
            job=verify(ref,http,now=(clock or (lambda:datetime.now(timezone.utc)))())
            item=match(normalize(job),profile)
            report['verified_count']+=1
            # Reports include factual metadata and scoring, not profile contents or full copyrighted JD.
            report_item={k:v for k,v in item.items() if k not in ('description','verification')}
            report_item['verification']={k:item['verification'][k] for k in ('verified_at','evidence_hash')}
            report['candidates'].append(report_item)
            if live:
                try:
                    response=api.call('/v1/radar/batch',{'candidate_id':profile['profile_id'],'campaign_id':campaign_id,
                                      'mode':'live','radar_date':day,'run_id':run_id,'jobs':[item]})
                    created=response['created_applications']
                    report['imported_count']+=created
                    report['duplicate_count']+=len(response['items'])-created
                    for rejection in response.get('rejections',[]):
                        report['rejections'].append({'url':item['canonical_url'],'reason':rejection['status']})
                        report['verified_count']-=1
                except (FetchError,KeyError,TypeError):
                    report['failures'].append({'stage':'import','url':item['canonical_url'],'reason':'import_failed_or_response_unknown'})
        except FetchError as e:
            # Fixed internal reason codes only. Never serialize arbitrary remote errors.
            reason=str(e)
            report['rejections'].append({'url':ref['url'],'reason':reason})
            if reason.startswith(('http_','network_','invalid_json','detail_schema')):
                report['failures'].append({'stage':'verification','reason':reason})
        except ValueError as e:
            reason=str(e) if str(e).startswith(('hard_fail:','defer:')) else 'profile_or_detail_incomplete'
            report['rejections'].append({'url':ref['url'],'reason':reason})
        except (KeyError,TypeError,AttributeError):
            report['rejections'].append({'url':ref['url'],'reason':'profile_or_detail_incomplete'})
        finally:
            pause(1)
    report['rejected_count']=len(report['rejections'])
    report['failure_count']=len(report['failures'])
    if live:
        report['server_run']=api.call('/v1/radar/runs/finish',{'id':run_id,**{k:report[k] for k in ('discovered_count','verified_count','rejected_count','failure_count')}})
        for k in ('imported_count','duplicate_count'):report[k]=report['server_run'][k]
    report['finished_at']=(clock or (lambda:datetime.now(timezone.utc)))().isoformat()
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile-file',type=Path,help='Local dry-run only; never used for live imports')
    parser.add_argument('--report',type=Path,default=Path('/tmp/radar-run.json'))
    args=parser.parse_args()
    live=False
    try:
        if args.profile_file:
            if os.environ.get('RADAR_LIVE_IMPORT','false')!='false':
                raise ValueError('File profiles are prohibited for live import')
            profile=json.loads(args.profile_file.read_text(encoding='utf-8'))
            campaign='autumn-2026-'+profile['profile_id']
            api=None
            provenance='user_supplied_formal_profile_snapshot; current production freshness not confirmed'
        else:
            origin,campaign,live,token=config(os.environ)
            api=CareerAPI(origin,token)
            exported=api.call('/v1/radar/profile?'+urlencode({'campaign_id':campaign}))
            profile=exported['profile']
            if exported['candidate_id']!=profile['profile_id'] or exported['campaign_id']!=campaign:
                raise ValueError('Profile identity mismatch')
            provenance='current Career Desk database via narrow authenticated profile endpoint'
        report=execute(profile,campaign,Transport(['jobs.bytedance.com']),api=api,live=live)
        report['profile_source']=provenance
        report['coverage']='ByteDance official detail adapter only. Other companies are not yet verified adapters.'
        args.report.parent.mkdir(parents=True,exist_ok=True)
        args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        args.report.chmod(0o600)
        print(json.dumps({k:report[k] for k in ('mode','discovered_count','verified_count','imported_count','duplicate_count','rejected_count','failure_count')},ensure_ascii=False))
        return 1 if report['failure_count'] else 0
    except Exception:
        # Fail closed and redact all exception data including secret-bearing config.
        print('Radar failed: configuration, profile source, network or run accounting unavailable.',file=sys.stderr)
        return 1

if __name__=='__main__':
    raise SystemExit(main())
