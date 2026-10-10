"""Small, explicitly reviewed official-source registry and read-only providers.

No third-party recruiter code is executed or bundled. Beisen's public page
bootstrap establishes categories; complete JD list rows establish job facts.
This is bounded interactive discovery, never a bulk mirror or application API.
"""
from __future__ import annotations

import hashlib
import html
import json
import math
import re
from datetime import datetime, timezone, timedelta
from urllib.parse import parse_qs


class SourceError(Exception):
    pass


BEISEN_SOURCES = {
    "cxmt": {
        "company": "长鑫存储", "host": "cxmt.zhiye.com", "tenant": "cxmt",
        "ownership_url": "https://www.cxmt.com/join.html",
        "ownership_evidence": "企业官网加入长鑫页的校园招聘链接指向 cxmt.zhiye.com/campus/jobs。",
        "scope": "长鑫官方中国校园招聘及实习；按关键词、有界分页，不代表全部岗位。",
        "reviewed_at": "2026-10-10",
    },
}
BEISEN_HOSTS = frozenset(item["host"] for item in BEISEN_SOURCES.values())
LIST_PATH = "/api/Jobad/GetJobAdPageList"
DETAIL_PATH = "/api/JobAd/GetJobAdInfo"
DISPLAY_FIELDS = ["Category", "Kind", "LocId", "PostDate", "ClassificationOne"]
GUID = re.compile(r"[a-fA-F0-9]{8}-(?:[a-fA-F0-9]{4}-){3}[a-fA-F0-9]{12}")
PAGE_SIZE = 10
MAX_PAGES = 2
SOURCE_METHOD = "官方完整JD列表核验（北森公开网站接口，非授权搜索API）"


def clean(value, limit=20000):
    if not isinstance(value, str):
        return ""
    return html.unescape(re.sub(r"<[^>]{0,1000}>", " ", value)).replace("\x00", "").strip()[:limit]


def endpoint_allowed(parsed, method, body=None):
    """Only the reviewed read-only endpoints; never accept arbitrary API paths."""
    if parsed.hostname not in BEISEN_HOSTS:
        return False
    if method == "GET" and parsed.path == "/campus/jobs" and not parsed.query:
        return True
    if method == "GET" and parsed.path == DETAIL_PATH:
        query = parse_qs(parsed.query, keep_blank_values=True)
        return (set(query) == {"jobAdId", "category", "displayFields"}
                and all(len(value) == 1 for value in query.values())
                and bool(GUID.fullmatch(query["jobAdId"][0]))
                and bool(re.fullmatch(r"[0-9]{1,6}", query["category"][0]))
                and query["displayFields"][0] == json.dumps(DISPLAY_FIELDS, separators=(",", ":")))
    if method != "POST" or parsed.path != LIST_PATH or parsed.query or not isinstance(body, dict):
        return False
    return (set(body) == {"PageIndex", "PageSize", "Category", "KeyWords", "SpecialType", "PortalId", "DisplayFields"}
            and type(body["PageIndex"]) is int and 0 <= body["PageIndex"] < MAX_PAGES
            and type(body["PageSize"]) is int and 1 <= body["PageSize"] <= PAGE_SIZE
            and isinstance(body["Category"], list) and 1 <= len(body["Category"]) <= 8
            and all(isinstance(value, str) and re.fullmatch(r"[0-9]{1,6}", value) for value in body["Category"])
            and isinstance(body["KeyWords"], str) and len(body["KeyWords"]) <= 160
            and type(body["SpecialType"]) is int and body["SpecialType"] == 0
            and body["PortalId"] == "" and body["DisplayFields"] == DISPLAY_FIELDS)


def decode_json(raw):
    try:
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (ValueError, UnicodeError, TypeError, RecursionError):
        raise SourceError("invalid_json") from None


def bootstrap_categories(raw, source):
    """Parse a JSON literal, never evaluate scripts from the source page."""
    try:
        text = raw.decode("utf-8-sig")
        match = re.search(r"\b(?:var\s+)?BSGlobal\s*=\s*", text)
        if not match:
            raise ValueError()
        bootstrap, _ = json.JSONDecoder().raw_decode(text[match.end():])
        tenant = bootstrap.get("tenantInfo") or {}
        if tenant.get("Name") != source["tenant"]:
            raise SourceError("official_tenant_identity_mismatch")
        pages = bootstrap.get("Pages")
        if not isinstance(pages, list) or len(pages) > 100:
            raise ValueError()
        categories = {}
        definitions = {"CampusList": ("campus", "campus", r"校园|校招"),
                       "InternList": ("internship", "intern", r"实习"),
                       "SocialList": ("experienced", "social", r"社会|社招")}
        for page in pages:
            if not isinstance(page, dict) or page.get("Code") not in definitions or page.get("PageType") != 2:
                continue
            kind, route, name_pattern = definitions[page["Code"]]
            category = page.get("BusinessType")
            if not isinstance(category, str) or not re.fullmatch(r"[0-9]{1,6}", category) or not re.search(name_pattern, str(page.get("Name", ""))):
                raise ValueError()
            if any(isinstance(detail, dict) and detail.get("Code") == route.capitalize() + "Detail"
                   and detail.get("BusinessType") == category and detail.get("PageType") == 1 for detail in pages):
                if category in categories and categories[category]["type"] != kind:
                    raise ValueError()
                categories[category] = {"type": kind, "route": route, "label": clean(page["Name"], 100)}
        if not categories:
            raise ValueError()
        return categories, hashlib.sha256(raw).hexdigest()
    except SourceError:
        raise
    except (ValueError, UnicodeError, AttributeError, TypeError, RecursionError):
        raise SourceError("category_schema_changed") from None


def normalize_job(row, source_id, categories, digest, bootstrap_digest, now, page):
    source = BEISEN_SOURCES[source_id]
    if not isinstance(row, dict):
        raise SourceError("detail_schema_changed")
    ident = row.get("Id")
    if not isinstance(ident, str) or not GUID.fullmatch(ident):
        raise SourceError("detail_identity_not_confirmed")
    category = row.get("CategoryId")
    if not isinstance(category, str) or category not in categories:
        raise SourceError("category_identity_not_confirmed")
    category_info = categories[category]
    label = clean(row.get("Category"), 100)
    label_pattern = {"campus": r"校园|校招", "internship": r"实习", "experienced": r"社会|社招"}[category_info["type"]]
    if not re.search(label_pattern, label):
        raise SourceError("category_identity_not_confirmed")
    for key in ("JobAdName", "Duty", "Require"):
        if not isinstance(row.get(key), str) or not row[key].strip():
            raise SourceError("incomplete_detail")
        if len(row[key]) > 20000:
            raise SourceError("detail_text_too_large")
    title, description, requirements = (clean(row[key]) for key in ("JobAdName", "Duty", "Require"))
    if not title or not description or not requirements:
        raise SourceError("incomplete_detail")
    status = row.get("Status")
    availability = "open" if type(status) is int and status == 1 else "closed" if type(status) is int and status == 2 else "unknown"
    expiry = row.get("EndTimeInt")
    if expiry is not None:
        if isinstance(expiry, bool) or not isinstance(expiry, (int, float)) or not math.isfinite(expiry) or expiry < 0:
            raise SourceError("unknown_expiry")
        end_text = row.get("EndTime")
        if end_text not in (None, "", "0001-01-01T00:00:00"):
            if not expiry or not isinstance(end_text, str):
                raise SourceError("unknown_expiry")
            try:
                end = datetime.fromisoformat(end_text.replace("Z", "+00:00"))
                if end.tzinfo is None:
                    end = end.replace(tzinfo=timezone(timedelta(hours=8)))
                stamp = expiry / 1000 if expiry > 1e11 else expiry
                if abs(end.timestamp() - stamp) > 60:
                    raise ValueError()
            except (ValueError, OverflowError):
                raise SourceError("unknown_expiry") from None
        if expiry:
            stamp = expiry / 1000 if expiry > 1e11 else expiry
            if stamp <= datetime.now(timezone.utc).timestamp():
                availability = "expired"
    elif row.get("EndTime") not in (None, "", "0001-01-01T00:00:00"):
        # Do not silently ignore an expiry when the known timestamp is absent.
        raise SourceError("unknown_expiry")
    if row.get("StdIsDeleted") is True or re.search(r"已下线|停止招聘|招聘结束|岗位关闭", title + description):
        availability = "closed"
    raw_cities = row.get("LocNames")
    if not isinstance(raw_cities, list) or len(raw_cities) > 30 or any(not isinstance(city, str) for city in raw_cities):
        raise SourceError("detail_schema_changed")
    cities = list(dict.fromkeys(clean(city, 100) for city in raw_cities if clean(city, 100)))
    recruitment = "internship" if re.search(r"实习|\bintern(?:ship)?\b", title + " " + str(row.get("Kind", "")), re.I) else category_info["type"]
    origin = "https://" + source["host"]
    canonical = origin + "/" + category_info["route"] + "/detail?jobAdId=" + ident.lower()
    return {"id": source_id + ":" + ident.lower(), "provider": source_id, "external_id": ident.lower(),
            "title": title, "company": source["company"], "city": " / ".join(cities), "cities": cities,
            "canonical_url": canonical, "description": description, "requirements": requirements,
            "recruitment_type": recruitment, "availability": availability, "record_type": "verified_job", "tracker_status": "saved",
            "verification": {"status": "verified", "source": source["company"] + " · " + SOURCE_METHOD,
                             "method": "official_complete_jd_listing", "source_url": origin + LIST_PATH,
                             "official_ownership_url": source["ownership_url"], "verified_at": now,
                             "evidence_hash": digest, "category_evidence_hash": bootstrap_digest,
                             "evidence": [source["ownership_evidence"],
                                          "官方完整JD列表含稳定Id、职责Duty与要求Require；未逐条访问详情页。",
                                          "Official job Id=" + ident.lower() + "; PageIndex=" + str(page),
                                          "Status=" + str(status) + "; availability=" + availability,
                                          "EndTimeInt=" + str(expiry),
                                          "招聘类型原文: " + label + "; CategoryId=" + category,
                                          "城市原文: " + " / ".join(cities),
                                          "届别只取具体岗位要求；不从招聘首页或用户偏好补全。"]}}


def discover_beisen(source_id, query, recruitment_type, fetch, now):
    """Provider interface: bounded fetch callback in, observations/summary out."""
    source = BEISEN_SOURCES[source_id]
    origin = "https://" + source["host"]
    result = {"observed_jobs": [], "failures": [], "pages": 0, "candidates": 0,
              "duplicates": 0, "rejected": 0, "truncated": False, "stop_reason": "completed",
              "upstream_keyword": bool(query.strip()), "search_mode": "upstream_keyword_and_local_filter",
              "verification_mode": "official_complete_jd_listing", "max_pages": MAX_PAGES, "page_size": PAGE_SIZE}
    seen, page_signatures = set(), set()
    stage = "official_categories"
    try:
        categories, bootstrap_digest = bootstrap_categories(fetch(origin + "/campus/jobs"), source)
        selected = {ident: info for ident, info in categories.items() if recruitment_type == "all" or info["type"] == recruitment_type}
        if not selected:
            raise SourceError("recruitment_category_unavailable")
        stage = "official_search"
        for page in range(MAX_PAGES):
            body = {"PageIndex": page, "PageSize": PAGE_SIZE, "Category": list(selected), "KeyWords": query.strip(),
                    "SpecialType": 0, "PortalId": "", "DisplayFields": DISPLAY_FIELDS}
            raw = fetch(origin + LIST_PATH, body=body)
            response = decode_json(raw)
            if response.get("Code") in (401, 403, 429):
                raise SourceError("http_" + str(response["Code"]))
            rows, total = response.get("Data"), response.get("Count")
            if response.get("Code") != 200 or not isinstance(rows, list) or len(rows) > PAGE_SIZE or type(total) is not int or not 0 <= total <= 100000:
                raise SourceError("board_schema_changed_or_too_large")
            if (not rows and total > page * PAGE_SIZE) or (rows and total < page * PAGE_SIZE + len(rows)):
                raise SourceError("pagination_count_inconsistent")
            result["pages"] += 1
            result["total_available"] = total
            result["candidates"] += len(rows)
            signature = tuple(str(row.get("Id", "")) if isinstance(row, dict) else "" for row in rows)
            if signature and signature in page_signatures:
                result.update(truncated=True, stop_reason="repeated_page")
                break
            page_signatures.add(signature)
            digest = hashlib.sha256(raw).hexdigest()
            for row in rows:
                ident = row.get("Id", "").lower() if isinstance(row, dict) and isinstance(row.get("Id"), str) else ""
                if ident and ident in seen:
                    result["duplicates"] += 1
                    continue
                try:
                    job = normalize_job(row, source_id, selected, digest, bootstrap_digest, now, page)
                    seen.add(job["external_id"])
                    result["observed_jobs"].append(job)
                except SourceError as error:
                    result["rejected"] += 1
                    result["failures"].append({"source": source_id, "stage": "normalize", "reason": str(error)})
                    if str(error) != "detail_text_too_large":
                        raise SourceError("board_schema_changed_or_too_large") from None
            if not rows or (page + 1) * PAGE_SIZE >= total:
                result.update(truncated=False, stop_reason="completed")
                break
            result.update(truncated=True, stop_reason="page_limit_reached")
    except SourceError as error:
        reason = str(error)
        result.update(stop_reason=reason, truncated=bool(result["pages"]))
        if reason != "source_request_budget_exhausted" or not result["pages"]:
            result["failures"].append({"source": source_id, "stage": stage, "reason": reason})
    return result
