"""Discovery only returns references; verification always fetches official detail anew."""
import hashlib
import json
import re
import time
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlsplit
from .http import FetchError

ORIGIN='https://jobs.bytedance.com'


def normalize_url(url):
    try:
        u=urlsplit(url)
        if u.scheme!='https' or u.netloc!='jobs.bytedance.com':
            raise ValueError()
        m=re.fullmatch(r'/campus/(?:m/)?position/detail/([0-9]{10,25})/?',u.path) or re.fullmatch(r'/campus/position/([0-9]{10,25})/detail/?',u.path)
        if not m: raise ValueError()
        return ORIGIN+'/campus/position/detail/'+m[1]
    except (ValueError,TypeError):
        raise FetchError('not_official_job_detail') from None


def discover(http, *, pause=time.sleep, source_config=None):
    """Bounded public related-jobs graph. Never authenticate to recruiting sites."""
    cfg=source_config or json.loads(Path(__file__).with_name('source_config.json').read_text())
    queue=[normalize_url(u) for u in cfg['seed_urls']]
    seen=set();refs={};failures=[];aliases=0
    budget=min(12,max(1,cfg['max_discovery_requests']))
    limit=min(100,max(1,cfg['max_verified_references']))
    for url in queue:
        if len(seen)>=budget:break
        if url in seen:continue
        seen.add(url)
        try:
            response=http.json(ORIGIN+'/api/v1/job/posts/'+url.rsplit('/',1)[-1]+'?portal_type=3&with_recommend=true')
            data=response.get('data',{})
            if response.get('code')!=0 or not isinstance(data.get('recommend_job_post_List'),list):
                raise FetchError('discovery_schema_changed')
            rows=[data.get('job_post_detail'),*data['recommend_job_post_List']]
            for row in rows:
                if not isinstance(row,dict):continue
                id=str(row.get('id',''))
                if not re.fullmatch('[0-9]{10,25}',id):continue
                target=ORIGIN+'/campus/position/detail/'+id
                if target in refs:aliases+=1
                elif len(refs)<limit:
                    refs[target]={'url':target,'source':'bytedance_official_related_jobs','discovered_from':url}
                    queue.append(target)
        except (FetchError,AttributeError,TypeError,ValueError):
            failures.append({'stage':'discovery','source':'bytedance_official_related_jobs','reason':'source_unavailable_or_schema_changed'})
        finally:
            pause(1)
    return list(refs.values()),failures,aliases


def verify(ref,http, *, now=None):
    now=now or datetime.now(timezone.utc)
    url=normalize_url(ref['url'])
    id=url.rsplit('/',1)[-1]
    api_url=ORIGIN+'/api/v1/job/posts/'+id+'?portal_type=3&with_recommend=false'
    raw=http.get(api_url)
    try:
        response=json.loads(raw);job=response['data']['job_post_detail']
        if response.get('code')!=0 or not isinstance(job,dict) or str(job.get('id'))!=id:
            raise FetchError('detail_identity_not_confirmed')
        if type(job.get('channel_online_status')) is not int or job['channel_online_status']!=1:
            raise FetchError('not_confirmed_online')
        rt=job.get('recruit_type',{})
        if rt.get('name')!='正式' or rt.get('parent',{}).get('name')!='校招':
            raise FetchError('not_full_time_campus')
        title=job.get('title','');description=job.get('description','');requirements=job.get('requirement','')
        if not all(isinstance(v,str) and v.strip() for v in (title,description,requirements)):
            raise FetchError('incomplete_detail')
        # Job-specific requirement only, never the site-wide campaign banner.
        if not re.search(r'(?<![0-9])2027\s*届',requirements):
            raise FetchError('2027_cohort_not_confirmed')
        if re.search(r'实习生|intern',title,re.I):
            raise FetchError('internship_title')
        if re.search(r'已下线|停止招聘|招聘结束|岗位关闭',title+description):
            raise FetchError('closed')
        expiry=job.get('job_post_info',{}).get('expiry_time')
        if expiry:
            try:
                expiry=float(expiry);expiry=expiry/1000 if expiry>1e11 else expiry
                if expiry<=now.timestamp():raise FetchError('expired')
            except (TypeError,ValueError):raise FetchError('unknown_expiry') from None
        if not re.search(r'产品|策略运营|增长运营',title):
            raise FetchError('outside_role_scope')
        if not ('产品' in title or re.search(r'产品迭代|数据分析|用户反馈|产品协作',description)):
            raise FetchError('operations_without_product_loop')
        cities=[c['name'] for c in job.get('city_list',[]) if isinstance(c,dict) and isinstance(c.get('name'),str)]
        if not cities:raise FetchError('city_not_confirmed')
        title=' '.join(title.split());city=' '.join(' / '.join(cities).split())
        stamp=now.isoformat(timespec='milliseconds').replace('+00:00','Z')
        # Preserve the official fields used for admission. A bare hash/timestamp
        # cannot establish job identity, campus scope or currently open status.
        source={'company':'字节跳动','title':title,'city':city,'canonical_url':url,
                'jd_responsibilities':description,'jd_requirements':'招聘类型：正式校招\n'+requirements,
                'official_evidence':{'url':url,'excerpt':'字节跳动 — '+title+' — '+city+'；在招。',
                    'locator':'data.job_post_detail: id, title, city_list, channel_online_status=1'},
                'campus_evidence':{'url':url,'excerpt':requirements,'locator':'data.job_post_detail: recruit_type, requirement'},
                'acquired_at':stamp,'availability':'open'}
        return {'company_id':'bytedance','company_name':source['company'],'title':title,'city':city,
                'canonical_url':url,'description':source['jd_responsibilities']+'\n任职要求：\n'+source['jd_requirements'],
                'verification':{'verified_at':stamp,'evidence_hash':hashlib.sha256(raw).hexdigest(),'source_evidence':source}}
    except (KeyError,ValueError,TypeError,AttributeError):
        raise FetchError('detail_schema_changed') from None
