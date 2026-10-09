"""Deterministic campus eligibility. Missing/ambiguous critical facts never become P2."""
import re
from datetime import date
from calendar import monthrange

DEGREES={'专科':1,'本科':2,'学士':2,'硕士':3,'博士':4,'bachelor':2,'master':3,'phd':4,'ph.d.':4,'doctorate':4}

def degree(value):
    value=str(value).lower()
    return max((n for word,n in DEGREES.items() if word in value),default=0)

def hard_requirements(job,profile):
    text=job.get('jd_requirements') or job.get('description') or ''
    title=job.get('title','')
    checks=[]
    def add(key,state,reason): checks.append({'requirement':key,'status':state,'reason':reason})
    # Recruitment classification uses explicit official job facts, not URL or campaign year.
    context=title+' '+text+' '+job.get('campus_evidence',{}).get('excerpt','')
    if re.search(r'实习(?:生|岗位|招聘)|日常实习|暑期实习|实习转正|社会招聘|社招|\bintern(?:ship)?\b|experienced hire', title+' '+text,re.I):
        add('recruitment_type','fail','not_formal_campus')
    elif re.search(r'正式校招|正式校园招聘|全职校招|校招正式|full.time campus',context,re.I): add('recruitment_type','pass','formal_campus_evidence')
    else: add('recruitment_type','unknown','formal_campus_not_confirmed')
    edu=profile.get('education',[])
    current=[e for e in edu if isinstance(e,dict) and e.get('end_status')=='expected']
    grads=[e.get('end_month','') for e in current if re.fullmatch(r'\d{4}-(?:0[1-9]|1[0-2])',str(e.get('end_month','')))]
    # An explicit date window has precedence over cohort shorthand; month-only candidate facts
    # cannot prove eligibility at a partial-month boundary.
    window=re.search(r'(20\d{2})[-年./](\d{1,2})(?:[-月./](\d{1,2})日?)?月?\s*(?:至|到|~|—|–)\s*(20\d{2})[-年./](\d{1,2})(?:[-月./](\d{1,2})日?)?月?',text)
    years=re.findall(r'(20\d{2})\s*届',text)
    if not grads: add('graduation_window','unknown','candidate_graduation_unknown')
    elif window:
        try:
            y,m,d,y2,m2,d2=window.groups(); lo=date(int(y),int(m),int(d or 1)); hi=date(int(y2),int(m2),int(d2 or 28))
            if lo>hi: raise ValueError()
            inside=[g for g in grads if lo.strftime('%Y-%m')<=g<=hi.strftime('%Y-%m')]
            partial=any((g==lo.strftime('%Y-%m') and d and int(d)>1) or (g==hi.strftime('%Y-%m') and d2 and int(d2)<monthrange(int(y2),int(m2))[1]) for g in inside)
            add('graduation_window','unknown' if partial else 'pass' if inside else 'fail','graduation_boundary_unknown' if partial else 'graduation_window_checked')
        except ValueError: add('graduation_window','unknown','invalid_graduation_window')
    elif len(set(years))==1:
        add('graduation_window','pass' if any(g.startswith(years[0]+'-') for g in grads) else 'fail','cohort_year_checked')
    else: add('graduation_window','unknown','job_graduation_window_unknown_or_conflicting')
    if years and '2027' not in years: add('campaign_scope','fail','not_2027_cohort')
    elif not years or len(set(years))>1: add('campaign_scope','unknown','2027_job_cohort_not_confirmed')
    # Only hard wording counts. Preferred degrees do not create false minimums.
    mandatory=[]
    for clause in re.split(r'[。；;\n]',text):
        if re.search(r'优先|preferred|加分',clause,re.I) and not re.search(r'必须|须具备|要求|至少|required|minimum',clause,re.I): continue
        if re.search(r'学历|学位|必须|至少|及以上|以上学历|minimum|required|degree',clause,re.I):
            levels=[n for word,n in DEGREES.items() if word in clause.lower()]
            if levels: mandatory.append(min(levels) if re.search(r'或|/|、|及以上|or\b',clause) else max(levels))
    explicit_min=re.findall(r'(?:必须(?:具备|具有|为)?|要求|至少|minimum|requires?)\s*(博士|硕士|本科|专科|phd|master|bachelor)',text,re.I)
    mandatory.extend(degree(v) for v in explicit_min)
    if mandatory:
        minimum=max(mandatory)
        # Expected degree in the eligible cohort counts at graduation, not as already earned.
        eligible=[e for e in edu if isinstance(e,dict) and (e.get('end_status')!='expected' or str(e.get('end_month','')).startswith('2027-'))]
        attained=max((degree(e.get('degree_level')) for e in eligible),default=0)
        add('degree_minimum','unknown' if not attained else 'pass' if attained>=minimum else 'fail','degree_minimum_checked')
    elif re.search(r'学历不限|不限学历|no degree requirement',text,re.I): add('degree_minimum','pass','no_degree_minimum')
    else: add('degree_minimum','unknown','degree_minimum_not_confirmed')
    major_clauses=[c for c in re.split(r'[。；;\n]',text) if re.search(r'专业|\bmajors?\b',c,re.I) and re.search(r'必须|仅限|限于|限定|须为|要求|required|must',c,re.I)]
    majors=' '.join(str(e.get('major','')) for e in edu if isinstance(e,dict))
    for clause in major_clauses:
        explicit=re.search(r'(?:专业(?:必须|要求)?\s*[:：]\s*|仅限)([\w\u4e00-\u9fff、/ ]+?)(?:专业|[，,]|$)',clause)
        if not explicit:
            explicit=re.search(r'(?:专业必须为|必须(?:是|为)?)([\w\u4e00-\u9fff、/ ]+?)(?:专业|[，,]|$)',clause)
        if not explicit or '相关' in explicit.group(1): add('must_have_major','unknown','mandatory_major_not_reliably_structured');continue
        choices=[v.strip() for v in re.split(r'[、/]|或',explicit.group(1)) if v.strip()]
        add('must_have_major','unknown' if not majors else 'pass' if any(v==e.get('major') for v in choices for e in edu if isinstance(e,dict)) else 'fail','mandatory_major_checked')
    # A named field of study without a modal (e.g. "2027届计算机专业") is
    # not evidence of unrestricted eligibility. Keep ambiguous requirements
    # unknown even if the candidate happens to name the same major.
    ambiguous_major=False
    for clause in re.split(r'[。；;，,\n]',text):
        if not re.search(r'专业|\bmajors?\b|\bdegree\s+in\b',clause,re.I): continue
        if any(clause in mandatory for mandatory in major_clauses): continue
        if re.search(r'专业(?:背景)?不限|不限专业|任何专业|any major|no (?:specific )?major requirement',clause,re.I): continue
        if re.search(r'优先|preferred|加分',clause,re.I): continue
        # These describe competence rather than a degree field.
        if not re.search(r'专业(?!能力|知识|技能|素养|精神|服务|人士)|\bmajors?\b|\bdegree\s+in\b',clause,re.I): continue
        ambiguous_major=True
    if ambiguous_major: add('must_have_major','unknown','implicit_major_requirement_not_reliably_structured')
    if not major_clauses and not ambiguous_major: add('must_have_major','pass','no_explicit_mandatory_major_detected')
    state='hard_fail' if any(c['status']=='fail' for c in checks) else 'defer' if any(c['status']=='unknown' for c in checks) else 'pass'
    return {'status':state,'checks':checks,'reasons':[c['reason'] for c in checks if c['status']!='pass']}
