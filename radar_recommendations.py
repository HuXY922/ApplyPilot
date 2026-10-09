"""Browser evidence contract and deterministic admission. No remote fetching or Agent scores."""
import hashlib
import ipaddress
import json
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit
from state_api import require, text_field, now, validate_time, encode
from radar_audit import record, profile as current_profile
from radar_gate import hard_requirements
from radar import canonical, import_batch
from radar_worker.matching import TOPICS
from radar_policy import policy


def safe_url(value):
    # Check the original transport/query before alias canonicalization can
    # upgrade HTTP or discard a secret-bearing tracking query.
    require(isinstance(value,str),'Public HTTPS URL required')
    try:
        u=urlsplit(value.strip())
        require(u.scheme=='https' and u.port in (None,443) and u.hostname and '.' in u.hostname,'Public HTTPS URL required')
    except ValueError:
        require(False,'Public HTTPS URL required')
    url=canonical(value)
    require(not u.query or not re.search(r'token|key|session|code|password|email',u.query,re.I),'Sensitive URL query forbidden')
    require(not u.hostname.endswith(('.local','.localhost','.internal')),'Private host forbidden')
    try:
        address=ipaddress.ip_address(u.hostname)
    except ValueError: address=None
    require(address is None,'IP literal forbidden')
    return url


def fixture(origin):
    url=origin+'/radar/test-job'
    return {'company':'Career Desk TEST ONLY','title':'TEST ONLY AI 产品经理（请勿投递）','city':'广州','canonical_url':url,
      'official_evidence':{'url':url,'excerpt':'Career Desk TEST ONLY — TEST ONLY AI 产品经理（请勿投递） — 广州；测试详情在招。','locator':'TEST ONLY fixture'},
      'campus_evidence':{'url':url,'excerpt':'正式校招。2027届本科及以上学历。专业不限。','locator':'TEST ONLY requirements'},
      'jd_responsibilities':'TEST ONLY 负责产品迭代、用户反馈、数据分析。',
      'jd_requirements':'TEST ONLY 正式校招。2027届本科及以上学历。专业不限。',
      'acquired_at':now(),'availability':'open','test_only':True,
      'candidate_evidence_references':[],'evidence_interpretation':'仅验证系统数据链路，不代表真实招聘。','uncertainty':['TEST ONLY，不可投递']}


def evidence_matches(job,profile):
    leaves=[]
    def walk(x,path):
        if isinstance(x,dict):
            for k,v in x.items():walk(v,path+'/'+k)
        elif isinstance(x,list):
            for i,v in enumerate(x):walk(v,path+'/'+str(i))
        elif isinstance(x,str):leaves.append((path,x.lower()))
    for k in ('experience','projects','skills'):walk(profile.get(k),'/'+k)
    jd=(job['jd_responsibilities']+' '+job['jd_requirements']).lower()
    return [{'topic':topic,'candidate_references':[path for path,value in leaves if any(w in value for w in words)],'jd_terms':[w for w in words if w in jd], 'strength':'lexical_evidence_only'}
            for topic,words in TOPICS.items() if any(w in jd for w in words) and any(any(w in value for w in words) for _,value in leaves)]


def validate(job,origin):
    required={'company','title','city','canonical_url','official_evidence','campus_evidence','jd_responsibilities','jd_requirements','acquired_at','availability'}
    optional={'test_only','evidence_interpretation','candidate_evidence_references','uncertainty'}
    require(isinstance(job,dict) and required<=set(job)<=required|optional,'Verified job fields invalid; client scores/priorities are forbidden')
    for key in ('company','title','city','jd_responsibilities','jd_requirements'): require(len(text_field(job,key))<=16000,'Text too long')
    url=safe_url(job['canonical_url']);job={**job,'canonical_url':url}
    stamp=validate_time(job['acquired_at']);age=(datetime.now(timezone.utc)-datetime.fromisoformat(stamp.replace('Z','+00:00'))).total_seconds()
    reasons=[]
    if not 0<=age<=policy()['evidence_max_age_seconds']: reasons.append('evidence_stale_or_future')
    if job['availability']!='open': reasons.append('not_confirmed_open')
    for key in ('official_evidence','campus_evidence'):
        e=job[key];require(isinstance(e,dict) and set(e)=={'url','excerpt','locator'},'Evidence requires URL/excerpt/locator')
        safe_url(e['url']);text_field(e,'excerpt');text_field(e,'locator')
        if canonical(e['url'])!=url: reasons.append('evidence_not_job_specific')
    if job.get('test_only') is not None: require(type(job['test_only']) is bool,'test_only must be boolean')
    if job.get('test_only'):
        expected=fixture(origin)
        require(policy()['test_fixture_enabled'] and all(job[k]==expected[k] for k in ('company','title','city','canonical_url','official_evidence','campus_evidence','jd_responsibilities','jd_requirements','availability')),'TEST ONLY fixture mismatch')
    else:
        if urlsplit(url).hostname not in policy()['official_job_hosts']: reasons.append('official_host_not_registered_owner_review_required')
        if urlsplit(url).path in ('','/'): reasons.append('job_detail_identity_missing')
        if not all(job[k] in job['official_evidence']['excerpt'] for k in ('company','title','city')): reasons.append('identity_not_supported_by_evidence')
        if not re.search(r'在招|招聘中|open|active',job['official_evidence']['excerpt'],re.I): reasons.append('availability_evidence_missing')
    if '2027' not in job['campus_evidence']['excerpt'] or job['campus_evidence']['excerpt'] not in job['jd_requirements']: reasons.append('campus_evidence_not_in_job_requirements')
    refs=job.get('candidate_evidence_references',[])
    require(isinstance(refs,list) and len(refs)<=40 and all(isinstance(r,str) for r in refs),'Invalid evidence references')
    uncertainty=job.get('uncertainty',[])
    require(isinstance(uncertainty,list) and all(isinstance(v,str) and len(v)<=500 for v in uncertainty),'Invalid uncertainty')
    require(isinstance(job.get('evidence_interpretation',''),str),'Invalid interpretation')
    return job,reasons,stamp


def submit(api,db,run,job,origin):
    job,reasons,stamp=validate(job,origin)
    profile,revision=current_profile(db,run['candidate_id'])
    for ref in job.get('candidate_evidence_references',[]):
        require(re.fullmatch(r'/(experience|projects|skills)/[A-Za-z0-9_/]+',ref) is not None,'Reference outside minimal evidence')
        try:
            node=profile
            for key in ref.strip('/').split('/'):node=node[int(key)] if isinstance(node,list) else node[key]
            require(isinstance(node,str) and bool(node),'Reference must resolve to a current fact')
        except (IndexError,KeyError,ValueError,TypeError): require(False,'Stale or invalid candidate reference')
    gate=hard_requirements(job,profile)
    if gate['status']=='hard_fail': outcome='hard_fail'
    elif reasons or gate['status']=='defer': outcome='defer'
    else: outcome='pass'
    audit={'source':job,'hard_requirements':gate,'evidence_matches':evidence_matches(job,profile),'policy_version':policy()['version'],'rubric':'step9-lexical-v2'}
    if outcome!='pass':
        record(db,run['campaign_id'],job['canonical_url'],outcome,reasons+gate['reasons'],audit,run['id'],revision)
        return {'outcome':outcome,'reasons':reasons+gate['reasons'],'hard_requirements':gate,'priority':None,'match_score':None}
    # No untrusted interpretation contributes numeric points. Refs must resolve to current profile.
    item={'company_id':'company-'+hashlib.sha256(job['company'].strip().encode()).hexdigest()[:20],
      'company_name':job['company'].strip(),'title':job['title'].strip(),'city':job['city'],
      'canonical_url':job['canonical_url'],'description':job['jd_responsibilities']+'\n任职要求：\n'+job['jd_requirements'],
      'verification':{'verified_at':stamp,'evidence_hash':hashlib.sha256(encode(job).encode()).hexdigest(),'source_evidence':job},
      'priority':'P2','match_score':0,'match_reasons':[],'risk_flags':[],'recommendation':'Server calculation required'}
    # Only this Console path can admit the exact explicitly marked TEST ONLY fixture.
    # The public legacy batch route never receives this server-side option.
    response=import_batch(api,db,{'candidate_id':run['candidate_id'],'campaign_id':run['campaign_id'],'radar_date':run['radar_date'],'run_id':run['id'],'mode':'live','jobs':[item]},allow_test_fixture=True)
    require(bool(response['items']),'Admission changed; defer and refresh',409)
    app=response['items'][0]['application'];outcome='imported' if response['created_applications'] else 'duplicate'
    # Refresh job facts with the score-producing JD; never touch application state/snapshot.
    db.execute('UPDATE jobs SET description=?,city=?,verified_at=?,availability=? WHERE id=?',(item['description'],job['city'],stamp,'open',app['job_id']))
    score=dict(db.execute('SELECT priority,match_score,match_reasons,risk_flags,recommendation FROM job_recommendations WHERE application_id=? AND radar_date=?',(app['id'],run['radar_date'])).fetchone())
    for k in ('match_reasons','risk_flags'):score[k]=json.loads(score[k])
    if job.get('test_only'):score['risk_flags'].append('TEST ONLY — 虚构验收岗位，禁止投递')
    if job.get('uncertainty'):score['risk_flags'].append('Agent 标记了不确定项；仅作审核提示，不作为候选人事实')
    db.execute('UPDATE job_recommendations SET risk_flags=? WHERE application_id=? AND radar_date=?',(encode(score['risk_flags']),app['id'],run['radar_date']))
    record(db,run['campaign_id'],job['canonical_url'],outcome,[],audit,run['id'],revision)
    return {'outcome':outcome,'application_id':app['id'],'application_status':app['status'],**score,'hard_requirements':gate,'evidence_matches':audit['evidence_matches']}
