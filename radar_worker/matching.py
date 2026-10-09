"""Explainable lexical evidence rubric, not a probability of receiving an offer."""
import json
import re

# These are role taxonomy/scope, never facts about a candidate or company prestige.
TOPICS={
 'AI 视频':('视频','多模态'), 'Agent 交互':('agent','智能体'),
 '模型评测':('评测','评估'), '大模型':('大模型','llm','aigc'),
 '产品迭代':('迭代','原型','需求','交互'), '数据分析':('数据分析','sql','python'),
 '增长策略':('增长','策略'), '用户反馈':('用户反馈','用户调研')}
CITIES=('上海','北京','杭州','深圳','广州')


def match(job, profile):
    from radar_gate import hard_requirements
    from radar_privacy import matching_profile
    profile=matching_profile(profile)
    gate=hard_requirements(job,profile)
    if gate["status"] != "pass":
        raise ValueError(gate["status"]+":"+",".join(gate["reasons"]))
    profile_text=json.dumps({k:profile.get(k) for k in ('experience','projects','skills')},ensure_ascii=False).lower()
    text=(job['title']+' '+job['description']).lower()
    title=job['title'].lower()
    prefs=profile.get('job_preferences',{})
    targets=[prefs.get('resume_target',{}).get('value',''),*prefs.get('additional_roles',{}).get('value',[])]
    if not any(isinstance(v,str) and v for v in targets):
        raise ValueError('profile_role_preferences_missing')
    product='产品' in title
    ai=any(t in title for t in ('ai','aigc','agent','大模型','多模态','智能体'))
    score=35 if product else 25
    reasons=['岗位属于产品方向（35 分）' if product else '岗位属于有产品协作闭环的策略/增长运营（25 分）']
    if ai and any('ai' in t.lower() or '大模型' in t for t in targets):
        score+=5;reasons.append('与档案目标 AI 产品方向一致（5 分）')
    overlaps=[]
    for name,words in TOPICS.items():
        if any(w in text for w in words) and any(w in profile_text for w in words):overlaps.append(name)
    score+=min(40,8*len(overlaps))
    reasons.append('岗位与档案共有能力：'+('、'.join(overlaps) or '未找到明确关键词证据')+f'（{min(40,8*len(overlaps))} 分）')
    risks=[]
    from radar_policy import policy
    cities=policy()['preferred_cities']
    included=prefs.get('include_cities',{})
    included=included.get('value',[]) if isinstance(included,dict) else included
    if isinstance(included,list): cities=list(dict.fromkeys([*cities,*[c for c in included if isinstance(c,str)]]))
    if any(c in job['city'] for c in cities):
        score+=10;reasons.append('地点符合本轮优先城市，广州与深圳同权（10 分）')
    else:risks.append('城市不在本轮优先列表')
    graduates=[e.get('end_month','') for e in profile.get('education',[]) if e.get('end_status')=='expected']
    if any(g.startswith('2027-') for g in graduates):
        score+=10;reasons.append('档案预计毕业年份与 2027 届一致（10 分）')
    else:risks.append('档案未确认 2027 届毕业资格');score-=20
    majors=' '.join(str(e.get('major','')) for e in profile.get('education',[]))
    requirements=job['description'].split('任职要求：')[-1]
    if re.search('计算机|软件工程|人工智能|数据科学',requirements) and not re.search('计算机|软件|人工智能|数据科学',majors):
        score-=15;risks.append('岗位有技术专业要求或偏好；档案专业未直接对应，需用户核查（−15 分）')
    if re.search('内容运营|社区运营|活动运营|新媒体运营',title):
        score-=25;risks.append('偏内容/社区/活动运营，降低优先级（−25 分）')
    if re.search(r'[2-9]\s*年.*经验',requirements):
        score-=15;risks.append('存在多年经验要求，需核查校招条件（−15 分）')
    score=max(0,min(100,score))
    risks.append('规则匹配分，不代表录用概率；已通过确定性资格检查，语义不确定项仍需人工审核')
    return {**job,'match_score':score,'priority':'P0' if score>=80 else 'P1' if score>=60 else 'P2',
            'match_reasons':reasons,'risk_flags':risks,
            'recommendation':'建议人工审核岗位详情后决定是否批准；Radar 不执行审批。'}
