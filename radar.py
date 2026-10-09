"""Transactional Radar import and database-backed dashboard projections."""
from datetime import date, datetime, timezone
import re
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit, urlunsplit
from state_api import require, text_field, uid, encode, APIError


def canonical(value):
    require(isinstance(value,str), 'canonical_url is required')
    try:
        u=urlsplit(value.strip())
        require(u.scheme in ('http','https') and bool(u.hostname) and not u.username and not u.password, 'Invalid job URL')
        if u.hostname == 'jobs.bytedance.com' and (re.fullmatch(r'/campus/(?:m/)?position/detail/[0-9]+/?',u.path) or re.fullmatch(r'/campus/position/[0-9]+/detail/?',u.path)):
            return 'https://jobs.bytedance.com/campus/position/detail/'+next(part for part in u.path.split('/') if part.isdigit())
        return urlunsplit((u.scheme.lower(),u.netloc.lower(),u.path or '/',u.query,''))
    except ValueError:
        raise APIError(400,'Invalid job URL')


def import_batch(api,db,data,*,allow_test_fixture=False):
    require(set(data)<= {'candidate_id','campaign_id','mode','radar_date','jobs','run_id'},'Unknown batch fields')
    candidate=text_field(data,'candidate_id'); campaign=text_field(data,'campaign_id')
    c=api.row(db,'campaigns',campaign)
    require(c['candidate_id']==candidate,'Campaign/candidate mismatch',409)
    mode=data.get('mode','live'); require(mode in ('live','simulation'),'Invalid mode')
    day=text_field(data,'radar_date')
    try: require(date.fromisoformat(day).isoformat()==day,'Use YYYY-MM-DD')
    except ValueError: raise APIError(400,'Invalid radar_date')
    jobs=data.get('jobs'); require(isinstance(jobs,list) and 1<=len(jobs)<=100,'jobs requires 1–100 items')
    run = None
    if 'run_id' in data:
        run=api.row(db,'radar_runs',text_field(data,'run_id'))
        require(run['status']=='running','Run is finished',409)
        require((run['candidate_id'],run['campaign_id'],run['radar_date'],mode)==(candidate,campaign,day,'live'),'Run scope mismatch',409)
    require(mode=='simulation' or run is not None,'Live imports require an active scoped run and official source evidence')
    results=[]; rejections=[]
    from radar_gate import hard_requirements
    from radar_audit import record, profile as current_profile
    profile, revision=current_profile(db,candidate)
    for item in jobs:
        require(isinstance(item,dict),'Each job must be an object')
        require(set(item)<= {'company_id','company_name','title','city','canonical_url','description','priority','match_score','match_reasons','risk_flags','recommendation','verification'},'Unknown job fields; approval is not accepted')
        evidence=item.get('verification')
        source=None
        if run:
            require(isinstance(evidence,dict) and set(evidence)=={'verified_at','evidence_hash','source_evidence'},'Official source verification evidence required')
            from state_api import validate_time
            stamp=validate_time(evidence['verified_at'])
            age=(datetime.now(timezone.utc)-datetime.fromisoformat(stamp.replace('Z','+00:00'))).total_seconds()
            require(0<=age<=3600,'Verification must be fresh (one hour)')
            require(isinstance(evidence['evidence_hash'],str) and bool(re.fullmatch('[a-f0-9]{64}',evidence['evidence_hash'])),'Invalid evidence hash')
            from radar_recommendations import validate, safe_url
            source,reasons,source_stamp=validate(evidence['source_evidence'],api.browser_origin or '')
            require(not source.get('test_only') or allow_test_fixture,'Live batch imports cannot use a TEST ONLY source')
            require(not reasons,'Official source evidence rejected: '+', '.join(reasons))
            require(stamp==source_stamp,'Verification timestamp does not match source evidence')
        else:
            require(evidence is None,'Evidence requires run_id')
        co=text_field(item,'company_id'); name=text_field(item,'company_name').strip(); title=text_field(item,'title').strip()
        url=safe_url(item.get('canonical_url')) if source is not None else canonical(item.get('canonical_url'))
        if source is not None:
            require((name,title,item.get('city'),url,item.get('description'))==
                    (source['company'].strip(),source['title'].strip(),source['city'],source['canonical_url'],
                     source['jd_responsibilities']+'\n任职要求：\n'+source['jd_requirements']),
                    'Job facts do not match official source evidence')
        for key in ('city','description'):
            require(item.get(key) is None or isinstance(item[key],str),key+' must be text')
        require(item.get('priority') in ('P0','P1','P2'),'Invalid priority')
        score=item.get('match_score'); require(type(score) in (int,float) and 0<=score<=100,'match_score must be 0–100')
        for key in ('match_reasons','risk_flags'):
            require(isinstance(item.get(key),list) and all(isinstance(x,str) for x in item[key]),key+' must be a string array')
        recommendation=text_field(item,'recommendation')
        gate=hard_requirements(item,profile)
        if gate['status'] != 'pass':
            record(db,campaign,url,gate['status'],gate['checks'],evidence={'source':source} if source else None,run_id=run['id'] if run else None,revision=revision)
            rejections.append({'canonical_url':url,**gate})
            continue
        from radar_worker.matching import match
        item=match(item,profile)
        score=item['match_score'];recommendation=item['recommendation']

        existing=db.execute('SELECT name FROM companies WHERE id=?',(co,)).fetchone()
        require(existing is None or existing[0]==name,'Company identity conflict',409)
        same_name=db.execute('SELECT id FROM companies WHERE name=? ORDER BY id LIMIT 1',(name,)).fetchone()
        if existing is None and same_name: co=same_name['id']
        db.execute('INSERT OR IGNORE INTO companies(id,name) VALUES(?,?)',(co,name))
        job=db.execute('SELECT * FROM jobs WHERE canonical_url=?',(url,)).fetchone()
        if job is None and url.startswith('https://jobs.bytedance.com/campus/position/detail/'):
            # Reuse legacy tracking/mobile variants without changing existing user decisions.
            variants=[r for r in db.execute("SELECT * FROM jobs WHERE canonical_url LIKE 'https://jobs.bytedance.com/%'") if canonical(r['canonical_url'])==url]
            require(len(variants)<=1,'Existing canonical collision needs user review',409)
            job=variants[0] if variants else None
        if job:
            require(job['company_id']==co and job['title']==title,'Job identity conflict',409)
            jid=job['id']
        else:
            jid=uid()
            db.execute('INSERT INTO jobs(id,company_id,title,city,canonical_url,description) VALUES(?,?,?,?,?,?)',(jid,co,title,item.get('city'),url,item.get('description')))
        app=db.execute('SELECT * FROM applications WHERE candidate_id=? AND job_id=? AND mode=?',(candidate,jid,mode)).fetchone()
        created=app is None
        if app:
            require(app['campaign_id']==campaign,'Application belongs to another campaign',409)
            aid=app['id']
        else:
            aid=uid()
            db.execute('INSERT INTO applications(id,candidate_id,campaign_id,job_id,mode) VALUES(?,?,?,?,?)',(aid,candidate,campaign,jid,mode))
        # Same-day retry preserves the first observation; never resets an approval.
        db.execute('INSERT OR IGNORE INTO job_recommendations(id,application_id,radar_date,priority,match_score,match_reasons,risk_flags,recommendation) VALUES(?,?,?,?,?,?,?,?)',(uid(),aid,day,item['priority'],score,encode(item['match_reasons']),encode(item['risk_flags']),recommendation))
        if run:
            previous=db.execute('SELECT verified_at FROM radar_observations WHERE application_id=? ORDER BY verified_at DESC LIMIT 1',(aid,)).fetchone()
            if previous is None or stamp>=previous['verified_at']:
                db.execute('UPDATE job_recommendations SET priority=?,match_score=?,match_reasons=?,risk_flags=?,recommendation=? WHERE application_id=? AND radar_date=?',
                           (item['priority'],score,encode(item['match_reasons']),encode(item['risk_flags']),recommendation,aid,day))
            db.execute('INSERT OR IGNORE INTO radar_observations(run_id,application_id,canonical_url,verified_at,evidence_hash,created) VALUES(?,?,?,?,?,?) ON CONFLICT(run_id,canonical_url) DO UPDATE SET verified_at=excluded.verified_at,evidence_hash=excluded.evidence_hash WHERE excluded.verified_at>=radar_observations.verified_at',
                       (run['id'],aid,url,stamp,evidence['evidence_hash'],int(created)))
            record(db,campaign,url,'imported' if created else 'duplicate',[],{'source':source,'hard_requirements':gate},run['id'],revision)
        results.append({'application':api.row(db,'applications',aid),'created':created})
    return {'items':results,'created_applications':sum(r['created'] for r in results),'rejections':rejections,'rejected_count':len(rejections)}


def dashboard(db,query):
    campaigns=[dict(r) for r in db.execute('SELECT id,name,timezone FROM campaigns ORDER BY id')]
    require(bool(campaigns),'No campaign',409)
    campaign=query.get('campaign_id',[campaigns[0]['id']])[0]
    selected=next((c for c in campaigns if c['id']==campaign),None)
    require(selected is not None,'Campaign not found',404)
    mode=query.get('mode',['live'])[0]; require(mode in ('live','simulation'),'Invalid mode')
    day=query.get('date',[datetime.now(ZoneInfo(selected['timezone'])).date().isoformat()])[0]
    try: require(date.fromisoformat(day).isoformat()==day,'Use YYYY-MM-DD')
    except ValueError: raise APIError(400,'Invalid date')
    rows=[dict(r) for r in db.execute('''SELECT a.id,a.status,a.revision,a.updated_at,j.title,j.city,j.canonical_url,c.name AS company_name,
      r.radar_date,r.priority,r.match_score,r.match_reasons,r.risk_flags,r.recommendation
      FROM applications a JOIN jobs j ON a.job_id=j.id JOIN companies c ON j.company_id=c.id
      LEFT JOIN job_recommendations r ON r.id=(SELECT id FROM job_recommendations WHERE application_id=a.id ORDER BY (radar_date=?) DESC,radar_date DESC LIMIT 1)
      WHERE a.campaign_id=? AND a.mode=? ORDER BY coalesce(r.priority,'P2'),a.updated_at DESC,a.id''',(day,campaign,mode))]
    for row in rows:
        for key in ('match_reasons','risk_flags'):
            import json
            row[key]=json.loads(row[key]) if row[key] else []
        row['tasks']=[dict(t) for t in db.execute("SELECT id,kind,prompt FROM user_tasks WHERE application_id=? AND status='open' ORDER BY created_at,id",(row['id'],))]
    latest=db.execute('SELECT * FROM radar_runs WHERE campaign_id=? ORDER BY started_at DESC LIMIT 1',(campaign,)).fetchone() if mode=='live' else None
    return {'radar_run':dict(latest) if latest else None,'date':day,'mode':mode,'campaign_id':campaign,'campaigns':campaigns,'today':[r for r in rows if r['radar_date']==day],
      'queue':[r for r in rows if r['status'] in ('approved','filling','ready_to_submit','submitting')],
      'attention':[r for r in rows if r['tasks'] or r['status'] in ('needs_user_input','submission_unknown')],
      'history':[r for r in rows if r['status'] in ('submitted','failed','skipped','withdrawn')]}
