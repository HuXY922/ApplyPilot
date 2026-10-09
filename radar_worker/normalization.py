"""Normalize verified records for the existing batch contract, without user state fields."""
from .sources import normalize_url


def normalize(job):
    allowed=('company_id','company_name','title','city','canonical_url','description','verification')
    result={k:job[k] for k in allowed}
    result['canonical_url']=normalize_url(result['canonical_url'])
    for key in ('company_name','title','city'):
        result[key]=' '.join(result[key].split())
    return result
