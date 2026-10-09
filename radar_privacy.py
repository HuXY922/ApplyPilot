"""Positive recursive projection. Unknown structures and sensitive text fail closed."""
import re

FIELDS = {
 'education': {'major','degree_level','end_month','end_status'},
 'experience': {'role','title','description','responsibilities','achievements','highlights','workstreams'},
 'projects': {'role','description','responsibilities','achievements','highlights'},
 'skills': {'data_tools','languages','technical','product_tools','proficiency'},
 'certifications': {'qualification','category','level','overall_score','expires_on'},
 'job_preferences': {'resume_target','additional_roles','include_cities','city_ranking','excluded_roles','exclusive_city_filter'},
}
NESTED = {'value','description','responsibilities','achievements','highlights','workstreams','role','title','level','proficiency','city','rank'}
FORBIDDEN = re.compile(r'name|gender|phone|email|identity|address|answers|resume.?assets|attachment|password|otp|cookie|session|token|api.?key|secret|姓名|性别|电话|邮箱|身份证|地址|密码|验证码', re.I)
SENSITIVE = re.compile(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)|(?<!\d)\d{17}[\dXx](?!\d)|https?://\S+|(?:姓名|性别|电话|邮箱|身份证|住址|地址|密码|验证码|password|otp|cookie|session|token|api[ _-]?key|e-?mail|phone|address)\s*[:=：]\s*\S+', re.I)

def matching_profile(profile):
    secrets = set()
    def collect(x):
        if isinstance(x,dict):
            for k,v in x.items():
                if FORBIDDEN.search(k):
                    def leaves(y):
                        if isinstance(y,str) and len(y.strip()) >= 2: secrets.add(y.strip())
                        elif isinstance(y,dict):
                            for z in y.values(): leaves(z)
                        elif isinstance(y,list):
                            for z in y: leaves(z)
                    leaves(v)
                else: collect(v)
        elif isinstance(x,list):
            for v in x: collect(v)
    collect(profile)
    def clean(x, allowed, depth=0):
        if depth > 10: return None
        if isinstance(x,dict):
            return {k:v for k,raw in x.items() if k in allowed and not FORBIDDEN.search(k)
                    if (v:=clean(raw,NESTED,depth+1)) is not None}
        if isinstance(x,list):
            return [v for raw in x[:100] if (v:=clean(raw,allowed,depth+1)) is not None]
        if isinstance(x,str):
            x=x[:6000]
            if SENSITIVE.search(x) or any(s in x for s in secrets): return None
            return x
        if x is None or type(x) in (int,float,bool): return x
        return None
    result={k:clean(profile.get(k,[] if k in ('education','experience','projects','certifications') else {}),v) for k,v in FIELDS.items()}
    # Opaque linkage is not taken from arbitrary profile nested values.
    ident=profile.get('profile_id','')
    if isinstance(ident,str) and re.fullmatch(r'[a-zA-Z0-9-]{1,100}',ident): result['profile_id']=ident
    return result
