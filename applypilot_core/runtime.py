"""Bounded official discovery, transparent eligibility rules and grounded guidance.

Search fetches configured public boards without transmitting queries or applicant
facts. Source parsing establishes what the listing says, never who qualifies.
The optional model only selects reviewed evidence; deterministic gates win.
"""
from __future__ import annotations

import hashlib
import html
import ipaddress
import json
import math
import os
import re
import socket
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from urllib.parse import urlsplit

from . import model

BYTE_ORIGIN = "https://jobs.bytedance.com"
DEFAULT_SEEDS = ("7667889560824154373", "7667892775577733429", "7667837272777541893")
SOURCE_LABEL = "ByteDance official careers website endpoint (undocumented; not a licensed search API)"
ALLOWED_HOSTS = frozenset(("jobs.bytedance.com", "api.ashbyhq.com"))
MAX_DISCOVERY_SECONDS = 30
MAX_DISCOVERY_REQUESTS = 6


class SourceError(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _clean(value, limit=20000):
    if not isinstance(value, str):
        return ""
    value = re.sub(r"<[^>]{0,1000}>", " ", value)
    return html.unescape(value).replace("\x00", "").strip()[:limit]


def _list_strings(value, limit=20):
    return [_clean(x, 160) for x in value[:limit] if isinstance(x, str) and x.strip()] if isinstance(value, list) else []


class Transport:
    """Fixed public hosts, no redirects/auth, DNS guard, byte and time bounds."""
    def __init__(self):
        # Process deployment configuration only. HTTP inputs never choose a
        # proxy, host, URL or source. The proxy resolves the fixed public host.
        self.proxy = urllib.request.getproxies().get("https", "")
        if self.proxy:
            try:
                parsed = urlsplit(self.proxy)
                if (parsed.scheme not in ("http", "https") or not parsed.hostname
                        or parsed.username or parsed.password or parsed.query or parsed.fragment
                        or parsed.path not in ("", "/") or parsed.port == 0):
                    raise ValueError()
            except (ValueError, TypeError):
                raise SourceError("invalid_process_proxy") from None
        self.transport_mode = "configured_proxy_dns" if self.proxy else "direct_dns"
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"https": self.proxy} if self.proxy else {}), NoRedirect())

    def get(self, url, *, max_bytes=1024 * 1024, timeout=7, deadline=None):
        try:
            parsed = urlsplit(url)
            if (parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS
                    or parsed.netloc != parsed.hostname or parsed.query and parsed.hostname == "api.ashbyhq.com"
                    or parsed.fragment):
                raise SourceError("destination_denied")
            if parsed.hostname == "jobs.bytedance.com":
                if not re.fullmatch(r"/api/v1/job/posts/[0-9]{10,25}", parsed.path) or parsed.query != "portal_type=3&with_recommend=true":
                    raise SourceError("destination_denied")
            elif not re.fullmatch(r"/posting-api/job-board/[A-Za-z0-9_-]{1,80}", parsed.path):
                raise SourceError("destination_denied")
            via_proxy = bool(self.proxy) and not urllib.request.proxy_bypass(parsed.hostname)
            self.transport_mode = "configured_proxy_dns" if via_proxy else "direct_dns"
            if not via_proxy:
                addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
                if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
                    raise SourceError("private_network_denied")
            request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "ApplyPilot/0.1 (bounded public job discovery)"})
            source_timeout = 12 if parsed.hostname == "api.ashbyhq.com" else 7
            with self.opener.open(request, timeout=max(.1, min(timeout, source_timeout))) as response:
                if response.getcode() != 200:
                    raise SourceError("http_" + str(response.getcode()))
                chunks, size = [], 0
                while True:
                    if deadline is not None and time.monotonic() >= deadline:
                        raise SourceError("time_budget_exhausted")
                    chunk = response.read(min(65536, max_bytes + 1 - size))
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > max_bytes:
                        raise SourceError("response_too_large")
                    chunks.append(chunk)
                return b"".join(chunks)
        except SourceError:
            raise
        except urllib.error.HTTPError as exc:
            raise SourceError("http_" + str(exc.code)) from None
        except Exception:
            raise SourceError("network_failure") from None


def _json(http, url, deadline, max_bytes):
    if time.monotonic() >= deadline:
        raise SourceError("time_budget_exhausted")
    source_timeout = 12 if urlsplit(url).hostname == "api.ashbyhq.com" else 7
    raw = http.get(url, max_bytes=max_bytes, timeout=min(source_timeout, deadline - time.monotonic()), deadline=deadline)
    if not isinstance(raw, bytes) or len(raw) > max_bytes:
        raise SourceError("response_too_large_or_invalid")
    try:
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError()
        return value, hashlib.sha256(raw).hexdigest()
    except (ValueError, UnicodeError):
        raise SourceError("invalid_json") from None


def _byte_job(response, requested_id, digest, now):
    try:
        data = response["data"]
        row = data["job_post_detail"]
        if response.get("code") != 0 or not isinstance(row, dict) or str(row.get("id")) != requested_id:
            raise SourceError("detail_identity_not_confirmed")
        if type(row.get("channel_online_status")) is not int or row["channel_online_status"] not in (0, 1):
            raise SourceError("not_confirmed_online")
        availability = "open" if row["channel_online_status"] == 1 else "closed"
        if any(isinstance(row.get(k), str) and len(row[k]) > 20000 for k in ("title", "description", "requirement")):
            raise SourceError("detail_text_too_large")
        title, description, requirements = (_clean(row.get(k)) for k in ("title", "description", "requirement"))
        if not title or not description or not requirements:
            raise SourceError("incomplete_detail")
        if re.search(r"已下线|停止招聘|招聘结束|岗位关闭", title + description):
            availability = "closed"
        expiry = (row.get("job_post_info") or {}).get("expiry_time")
        if expiry:
            try:
                stamp = float(expiry)
                if not math.isfinite(stamp):
                    raise SourceError("unknown_expiry")
                if stamp > 1e11:
                    stamp /= 1000
                if stamp <= datetime.now(timezone.utc).timestamp():
                    availability = "expired"
            except (TypeError, ValueError):
                raise SourceError("unknown_expiry") from None
        recruit = row.get("recruit_type") or {}
        labels = " ".join((str(recruit.get("name", "")), str((recruit.get("parent") or {}).get("name", ""))))
        recruitment_type = "internship" if re.search(r"实习|intern", labels + " " + title, re.I) else "campus" if "校招" in labels else "experienced" if "社招" in labels else "unknown"
        cities = [_clean(c.get("name"), 100) for c in row.get("city_list", []) if isinstance(c, dict) and _clean(c.get("name"), 100)]
        url = BYTE_ORIGIN + "/campus/position/detail/" + requested_id
        return {"id": "bytedance:" + requested_id, "provider": "bytedance", "external_id": requested_id,
                "title": title, "company": "字节跳动", "city": " / ".join(dict.fromkeys(cities)), "cities": cities,
                "canonical_url": url, "description": description, "requirements": requirements,
                "recruitment_type": recruitment_type, "availability": availability, "record_type": "verified_job", "tracker_status": "saved",
                "verification": {"status": "verified", "source": SOURCE_LABEL, "verified_at": now,
                                 "source_url": BYTE_ORIGIN + "/api/v1/job/posts/" + requested_id + "?portal_type=3&with_recommend=true",
                                 "evidence_hash": digest, "evidence": ["Official detail ID matched: " + requested_id,
                                 "channel_online_status=" + str(row["channel_online_status"]), "availability=" + availability, "招聘类型原文: " + labels, "城市原文: " + " / ".join(cities),
                                 "Requirements come from this job detail, not a campaign banner."]}}
    except SourceError:
        raise
    except (KeyError, TypeError, AttributeError):
        raise SourceError("detail_schema_changed") from None


def _ashby_job(row, board, digest, now):
    if not isinstance(row, dict) or row.get("isListed") is not True:
        raise SourceError("not_confirmed_listed")
    url = row.get("jobUrl", "")
    try:
        parsed = urlsplit(url)
        match = re.fullmatch(r"/" + re.escape(board) + r"/([A-Za-z0-9_-]{8,80})/?", parsed.path, re.I)
        if parsed.scheme != "https" or parsed.netloc != "jobs.ashbyhq.com" or parsed.query or parsed.fragment or not match:
            raise SourceError("detail_identity_not_confirmed")
    except (ValueError, TypeError):
        raise SourceError("detail_identity_not_confirmed") from None
    external_id = match[1]
    if any(isinstance(row.get(k), str) and len(row[k]) > 20000 for k in ("title", "descriptionPlain")):
        raise SourceError("detail_text_too_large")
    title, description = _clean(row.get("title")), _clean(row.get("descriptionPlain"))
    if not title or not description:
        raise SourceError("incomplete_detail")
    city = _clean(row.get("location"), 200)
    employment = row.get("employmentType", "")
    recruitment_type = "internship" if employment == "Intern" or re.search(r"\bintern(ship)?\b", title, re.I) else "campus" if re.search(r"\bnew grad(uate)?\b|\buniversity graduate\b", title, re.I) else "unknown"
    return {"id": "ashby:" + board.lower() + ":" + external_id, "provider": "ashby:" + board.lower(), "external_id": external_id,
            "title": title, "company": board, "city": city, "cities": [city] if city else [], "canonical_url": url.rstrip("/"),
            "description": description, "requirements": description, "recruitment_type": recruitment_type,
            "availability": "open", "record_type": "verified_job", "tracker_status": "saved",
            "verification": {"status": "verified", "source": "Ashby documented public job-posting API; board " + board,
                             "source_url": "https://api.ashbyhq.com/posting-api/job-board/" + board,
                             "verified_at": now, "evidence_hash": digest,
                             "evidence": ["Listed in the configured official ATS board; isListed=true.",
                                          "Canonical job URL matches board and job identity.",
                                          "Global board; full-time does not establish campus eligibility."]}}


def _source_config():
    sources = [s.strip() for s in os.environ.get("APPLYPILOT_SOURCES", "bytedance").split(",") if s.strip()]
    boards = [s.strip() for s in os.environ.get("APPLYPILOT_ASHBY_BOARDS", "").split(",") if s.strip()]
    return {"sources": sources, "bytedance_seed_ids": list(DEFAULT_SEEDS), "ashby_boards": boards,
            "max_requests": MAX_DISCOVERY_REQUESTS}


def _city_key(value):
    aliases = {"shanghai": "上海", "beijing": "北京", "shenzhen": "深圳", "guangzhou": "广州", "hangzhou": "杭州"}
    return aliases.get(value.strip().casefold().removesuffix("市"), value.strip().casefold().removesuffix("市"))


def _matches(job, query, recruitment_type, cities):
    if job.get("availability") != "open":
        return False
    if recruitment_type != "all" and job["recruitment_type"] != recruitment_type:
        return False
    if cities and not any(_city_key(wanted) == _city_key(found) for wanted in cities for found in job["cities"]):
        return False
    haystack = " ".join(job[k] for k in ("title", "description", "requirements")).casefold()
    return all(term.casefold() in haystack for term in re.split(r"[\s,，]+", query.strip()) if term)


def discover(query="", recruitment_type="campus", cities=None, **optional):
    """Fetch bounded configured sources; query/city filtering happens locally.

    source_config/http/pause are server-side test seams, never HTTP input fields.
    A zero-result run does not broaden source geography or use cached fixtures.
    """
    if not isinstance(query, str) or len(query) > 160 or recruitment_type not in ("campus", "internship", "all"):
        raise ValueError("invalid_search")
    if cities is not None and (not isinstance(cities, list) or len(cities) > 20 or any(not isinstance(c, str) or len(c) > 100 for c in cities)):
        raise ValueError("invalid_cities")
    if set(optional) - {"http", "source_config", "pause"}:
        raise ValueError("unsupported_search_option")
    http = optional.get("http") or Transport()
    cfg = optional.get("source_config") or _source_config()
    pause = optional.get("pause", time.sleep)
    try:
        sources = cfg.get("sources", ["bytedance"])
        if not isinstance(sources, list) or not sources or any(s not in ("bytedance", "ashby") for s in sources):
            raise ValueError()
        seeds = cfg.get("bytedance_seed_ids", list(DEFAULT_SEEDS))
        boards = cfg.get("ashby_boards", [])
        if not isinstance(seeds, list) or len(seeds) > 10 or any(not isinstance(s, str) or not re.fullmatch(r"[0-9]{10,25}", s) for s in seeds):
            raise ValueError()
        if not isinstance(boards, list) or len(boards) > 4 or any(not isinstance(s, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", s) for s in boards):
            raise ValueError()
        budget = min(MAX_DISCOVERY_REQUESTS, max(1, int(cfg.get("max_requests", MAX_DISCOVERY_REQUESTS))))
    except (ValueError, TypeError, AttributeError):
        raise ValueError("invalid_source_config") from None
    jobs, observations, failures, summaries = [], [], [], []
    seen_jobs = set()
    deadline, requests = time.monotonic() + MAX_DISCOVERY_SECONDS, 0
    now = _now()
    def retain(job):
        observations.append(job)
        if job["id"] not in seen_jobs and _matches(job, query, recruitment_type, cities or []):
            seen_jobs.add(job["id"])
            jobs.append(job)
    for source in dict.fromkeys(sources):
        summary = {"source": source, "status": "ok", "requests": 0, "verified": 0, "returned": 0,
                   "scope": "China campus-related official detail graph; not exhaustive search" if source == "bytedance" else "Explicitly configured global ATS boards; not China-campus coverage",
                   "method": SOURCE_LABEL if source == "bytedance" else "Documented public job-posting API", "bounded": True}
        before = len(jobs)
        if source == "bytedance":
            queue, seen = list(seeds), set()
            while queue and requests < budget and time.monotonic() < deadline:
                ident = queue.pop(0)
                if ident in seen:
                    continue
                seen.add(ident)
                requests += 1
                summary["requests"] += 1
                url = BYTE_ORIGIN + "/api/v1/job/posts/" + ident + "?portal_type=3&with_recommend=true"
                try:
                    response, digest = _json(http, url, deadline, 1024 * 1024)
                    job = _byte_job(response, ident, digest, now)
                    summary["verified"] += 1
                    retain(job)
                    references = response.get("data", {}).get("recommend_job_post_List", [])
                    if not isinstance(references, list):
                        raise SourceError("recommendations_schema_changed")
                    for ref in references[:30]:
                        rid = str(ref.get("id", "")) if isinstance(ref, dict) else ""
                        if re.fullmatch(r"[0-9]{10,25}", rid) and rid not in seen and rid not in queue and len(queue) < 30:
                            queue.append(rid)
                except SourceError as exc:
                    reason = str(exc)
                    failures.append({"source": source, "stage": "official_detail", "external_id": ident, "reason": reason})
                    summary["status"] = "partial" if summary["verified"] else "failed"
                    if reason in ("http_401", "http_403", "http_429", "time_budget_exhausted", "invalid_json", "detail_schema_changed", "detail_identity_not_confirmed", "incomplete_detail", "detail_text_too_large", "recommendations_schema_changed", "board_schema_changed_or_too_large"):
                        break
                if queue and requests < budget:
                    pause(min(.35, max(0, deadline - time.monotonic())))
            summary["truncated"] = bool(queue)
        else:
            if not boards:
                failures.append({"source": source, "reason": "no_official_boards_configured"})
                summary["status"] = "failed"
            for board in boards:
                if requests >= budget or time.monotonic() >= deadline:
                    summary["truncated"] = True
                    break
                requests += 1
                summary["requests"] += 1
                try:
                    response, digest = _json(http, "https://api.ashbyhq.com/posting-api/job-board/" + board, deadline, 3 * 1024 * 1024)
                    rows = response.get("jobs")
                    if not isinstance(rows, list) or len(rows) > 1500:
                        raise SourceError("board_schema_changed_or_too_large")
                    for row in rows[:500]:
                        try:
                            job = _ashby_job(row, board, digest, now)
                            summary["verified"] += 1
                            retain(job)
                        except SourceError as exc:
                            if str(exc) == "not_confirmed_listed" and isinstance(row, dict) and row.get("isListed") is False:
                                continue
                            failures.append({"source": source, "board": board, "stage": "normalize", "reason": str(exc)})
                            summary["status"] = "partial" if summary["verified"] else "failed"
                            if str(exc) == "detail_text_too_large":
                                continue  # Oversized individual records are explicit rejections, not schema changes.
                            raise SourceError("board_schema_changed_or_too_large") from None
                    summary["truncated"] = len(rows) > 500
                except SourceError as exc:
                    reason = str(exc)
                    failures.append({"source": source, "board": board, "stage": "official_board", "reason": reason})
                    summary["status"] = "partial" if summary["verified"] else "failed"
                    if reason in ("http_401", "http_403", "http_429", "time_budget_exhausted", "invalid_json", "detail_schema_changed", "detail_identity_not_confirmed", "incomplete_detail", "detail_text_too_large", "recommendations_schema_changed", "board_schema_changed_or_too_large"):
                        break
        if summary["verified"] and summary["status"] == "failed":
            summary["status"] = "partial"
        summary["transport_mode"] = getattr(http, "transport_mode", "injected_test_transport") if isinstance(http, Transport) else "injected_test_transport"
        summary["returned"] = min(100, len(jobs)) - min(100, before)
        if not summary["requests"] and summary["status"] == "ok":
            summary["status"] = "not_run_budget_exhausted"
            failures.append({"source": source, "reason": "request_or_time_budget_exhausted"})
        summaries.append(summary)
    return {"jobs": jobs[:100], "observed_jobs": observations[:1500], "failures": failures[:100], "source_summary": summaries}

# Qualification/role vocabulary describes job statements, not a fixed applicant.
DEGREES = ((4, r"博士|\bph\.?d\.?\b|\bdoctorate\b"),
           (3, r"硕士|研究生|\bmaster(?:'s|s)?\b"),
           (2, r"本科|学士|\bbachelor(?:'s|s)?\b"),
           (1, r"大专|专科|\bassociate(?:'s|s)?\b"))
ROLE_FAMILIES = {
    "product": ("产品", "product manager", "product management"),
    "engineering": ("工程师", "开发", "software engineer", "developer"),
    "design": ("设计", "designer", "design"),
    "operations": ("运营", "operations"),
    "data": ("数据分析", "data analyst", "data scientist"),
    "sales": ("销售", "sales"),
    "research": ("研究员", "researcher", "research scientist"),
}
TOPICS = ("产品", "用户调研", "数据分析", "评测", "评估", "需求", "原型", "增长", "运营", "交互",
          "python", "sql", "javascript", "typescript", "react", "java", "设计", "实验", "测试",
          "agent", "智能体", "llm", "大模型", "多模态", "machine learning", "research", "sales")
CREDENTIAL = re.compile(r"(?:password|passwd|token|secret|api[_ -]?key|access[_ -]?key|密码|密钥|令牌)\s*[:：=是为]\s*\S+|"
                        r"(?:密码|密钥|令牌)\s*[:：=是为]?\s*[A-Za-z0-9_-]{4,}|(?<![A-Za-z0-9_])sk-[A-Za-z0-9_-]{10,}|(?<![A-Za-z0-9_])(?:ghp_|github_pat_|xox[baprs]-)[A-Za-z0-9_-]{10,}|"
                        r"-----BEGIN [A-Z ]*PRIVATE KEY|(?<![A-Za-z0-9_])Bearer\s+\S{8,}|(?<![A-Za-z0-9_])AKIA[A-Z0-9]{16}|"
                        r"(?<![A-Za-z0-9_])eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", re.I)
INJECTION = re.compile(r"ignore\s+(?:all\s+|previous\s+|the\s+)?instructions|system\s*prompt|developer\s*message|"
                       r"api[_ -]?key|reveal\s+(?:secret|credential)|忽略.*指令|系统提示|泄露.*密钥|执行.*命令", re.I)


def _degree_levels(text):
    return {level for level, pattern in DEGREES if re.search(pattern, text, re.I)}


def _requirement_clauses(text):
    # Conjunctions and punctuation separate the scope of waivers/preferences.
    return [part.strip() for part in re.split(
        r"[。；;,，\n!?！？]|(?<=[.])\s+|\b(?:but|however|while|whereas|and)\b|但是|但|并且|同时|且",
        text, flags=re.I) if part.strip()]


def _mandatory(clause):
    return bool(re.search(r"必须|至少|硬性|须具备|须有|要求具备|需要具备|\b(?:must|mandatory|minimum|at least)\b|(?<!not )\brequired\b", clause, re.I))


def _optional(clause):
    return bool(re.search(r"优先|加分|preferred|nice to have|bonus|optional|desirable", clause, re.I))


def _job_degree(requirements):
    hard, optional, waived = [], False, False
    for clause in _requirement_clauses(requirements):
        if re.search(r"学历不限|no\s+(?:degree|education)\s+requirement", clause, re.I):
            waived = True
            if not _degree_levels(clause):
                continue
        levels = _degree_levels(clause)
        if not levels:
            continue
        if _optional(clause) and not _mandatory(clause):
            optional = True
            continue
        if re.search(r"或同等|or equivalent|equivalent experience", clause, re.I):
            return None, "alternative_qualification"
        hard.append(min(levels))
    if hard:
        return max(hard), "explicit"
    if waived:
        return 0, "unrestricted"
    return (0, "preferred_only") if optional else (None, "unstated")


def _unresolved_requirements(requirements):
    # Waivers are dimension-specific, not a global escape hatch. In ambiguous
    # mixed clauses we retain unknown rather than infer candidate eligibility.
    dimensions = {
        "major": (r"专业|\bmajors?\b|field of study|degree in",
                  r"专业(?:不限|不作要求|无要求)|不限专业|no\s+(?:specific\s+)?major\s+(?:requirement|required)|any\s+major|major\s+(?:is\s+)?not\s+required"),
        "experience": (r"经验|\bexperience\b|\b(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s*\+?\s*years?\b",
                       r"(?:无需|无须|不需要|不要求|不限|无)(?:工作|相关|专业|任何|年限)?经验(?:要求)?|经验(?:不限|不作要求|无要求)|"
                       r"no\s+(?:(?:prior|work|professional|relevant)\s+)*experience\s+(?:is\s+)?(?:required|necessary|needed)|"
                       r"experience\s+(?:is\s+)?not\s+(?:required|necessary|needed)"),
        "authorization": (r"签证|工作许可|work authorization|citizenship|authorized to work|right to work|\bvisa\b",
                          r"(?:国籍|工作许可|签证)(?:不限|无要求)|(?:citizenship|work authorization|visa)\s+(?:is\s+)?not\s+required"),
    }
    unresolved = []
    for clause in _requirement_clauses(requirements):
        present = [name for name, (pattern, _) in dimensions.items() if re.search(pattern, clause, re.I)]
        for name in present:
            waiver = re.search(dimensions[name][1], clause, re.I)
            # A local "no experience required" waiver is valid; an unrelated
            # "no major requirement" never removes the experience constraint.
            if waiver:
                remainder = clause[:waiver.start()] + clause[waiver.end():]
                if not _mandatory(remainder) and not re.search(dimensions[name][0], remainder, re.I):
                    continue
            if not waiver and len(present) == 1 and _optional(clause) and not _mandatory(clause):
                continue
            unresolved.append(name + ": " + clause[:500])
    return list(dict.fromkeys(unresolved))


def _cohorts(requirements):
    years = set(re.findall(r"(?<!\d)(20\d{2})\s*届", requirements))
    years.update(re.findall(r"(?:class of|graduat(?:e|es|ing|ion)(?:\s+(?:in|year))?)\s*[:：]?\s*(20\d{2})", requirements, re.I))
    for first, last in re.findall(r"(?<!\d)(20\d{2})\s*[-–至到/]\s*(20\d{2})\s*届", requirements):
        if 0 <= int(last) - int(first) <= 5:
            years.update(str(y) for y in range(int(first), int(last) + 1))
    for first, last in re.findall(r"(?<!\d)(20\d{2})\s*(?:或|、|及|和|,|，|or|and|&)\s*(20\d{2})\s*(?:届|graduates?)", requirements, re.I):
        years.update((first, last))
    return years


def _families(text):
    return {name for name, aliases in ROLE_FAMILIES.items() if any(a in text.casefold() for a in aliases)}


def hard_requirements(profile, job, policy=None):
    """Tri-state evaluation. No missing job/applicant fact becomes a silent pass.

    Preferences configure city/role/recruitment constraints; no cohort or city is
    hardcoded. An explicit server policy may choose which checks are required.
    Verification and availability are always required.
    """
    prefs = profile.get("preferences", {}) if isinstance(profile.get("preferences"), dict) else {}
    requirements = _clean(job.get("requirements"))
    title = _clean(job.get("title"), 500)
    education = [e for e in profile.get("education", []) if isinstance(e, dict)]
    checks = []
    def add(name, status, reason, evidence=()):
        checks.append({"name": name, "status": status, "reason": reason, "evidence": list(evidence)})
    verification = job.get("verification") or {}
    verified = job.get("record_type") == "verified_job" and verification.get("status") == "verified"
    try:
        verified_at = datetime.fromisoformat(verification.get("verified_at", "").replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - verified_at).total_seconds()
        fresh = -300 <= age <= 24 * 60 * 60
    except (ValueError, TypeError, AttributeError):
        fresh = False
    verified = verified and fresh and bool(verification.get("evidence"))
    add("verification", "pass" if verified else "unknown", "有24小时内官方来源核验记录。" if verified else "演示、缺少证据或超过24小时的核验记录需要重新验证。",
        [job.get("canonical_url", ""), str((job.get("verification") or {}).get("verified_at", ""))])
    availability = job.get("availability")
    add("availability", "pass" if availability in ("open", "online") else "fail" if availability in ("closed", "expired") else "unknown",
        "岗位开放状态：" + str(availability or "未提供"))
    wanted_type = prefs.get("recruitment_type", "all")
    actual_type = job.get("recruitment_type", "unknown")
    add("recruitment_type", "pass" if wanted_type == "all" or wanted_type == actual_type else "unknown" if actual_type == "unknown" else "fail",
        "目标招聘类型：" + str(wanted_type) + "；岗位类型：" + str(actual_type))
    minimum, degree_basis = _job_degree(requirements)
    levels = set().union(*(_degree_levels(_clean(e.get("degree"), 100)) for e in education)) if education else set()
    if minimum == 0:
        degree_status, reason = "pass", "岗位学历不限或学历仅为明确偏好。"
    elif minimum is None:
        degree_status, reason = "unknown", "学历要求未明确，或接受替代资历，需核实。"
    elif not levels:
        degree_status, reason = "unknown", "档案缺少已确认学历，不能判断是否满足。"
    else:
        degree_status = "pass" if max(levels) >= minimum else "fail"
        reason = "按岗位明确最低学历与已确认档案比较。"
    add("degree", degree_status, reason, [requirements[:1000], *["education/" + str(i) + "/degree: " + _clean(e.get("degree"), 100) for i, e in enumerate(education)]])
    years = _cohorts(requirements)
    candidate_years = {m[0] for e in education for m in [re.findall(r"(?<!\d)20\d{2}(?!\d)", _clean(e.get("graduation"), 100))] if m}
    candidate_years = {max(candidate_years)} if candidate_years else set()
    # A preference for a cohort is not proof of the candidate's graduation date.
    if years:
        cohort_status = "unknown" if not candidate_years else "pass" if years & candidate_years else "fail"
        reason = "岗位届别：" + " / ".join(sorted(years)) + "；档案毕业年份：" + (" / ".join(sorted(candidate_years)) or "未确认")
    elif actual_type in ("campus", "internship") or wanted_type in ("campus", "internship"):
        cohort_status, reason = "unknown", "岗位没有明确毕业届别或在校时间窗口。"
    else:
        cohort_status, reason = "pass", "该非校招筛选未发现明确届别限制。"
    add("cohort", cohort_status, reason, [requirements[:1000], *["education/" + str(i) + "/graduation: " + _clean(e.get("graduation"), 100) for i, e in enumerate(education)]])
    cities = _list_strings(prefs.get("cities"))
    actual_cities = _list_strings(job.get("cities")) or [c.strip() for c in re.split(r"[/、,，]", _clean(job.get("city"), 500)) if c.strip()]
    city_status = "pass" if not cities or any(_city_key(w) == _city_key(a) for w in cities for a in actual_cities) else "unknown" if not actual_cities else "fail"
    add("cities", city_status, "目标城市：" + (" / ".join(cities) or "不限") + "；岗位城市：" + (" / ".join(actual_cities) or "未确认"))
    roles = _list_strings(prefs.get("roles"))
    excluded = _list_strings(prefs.get("excluded_roles"))
    role_families, target_families = _families(title), _families(" ".join(roles))
    if title and any(r.casefold() in title.casefold() for r in excluded):
        role_status, role_reason = "fail", "职位标题命中明确排除的岗位方向。"
    elif not roles:
        role_status, role_reason = "pass", "没有设置岗位方向限制。"
    elif not title:
        role_status, role_reason = "unknown", "职位标题缺失。"
    elif any(r.casefold() in title.casefold() for r in roles) or role_families & target_families:
        role_status, role_reason = "pass", "职位标题与目标方向或同一岗位类别相符。"
    elif role_families and target_families:
        role_status, role_reason = "fail", "职位与配置的目标岗位类别不一致。"
    else:
        role_status, role_reason = "unknown", "现有词汇不能可靠判定岗位方向，需要人工核对。"
    add("roles", role_status, role_reason, ["岗位: " + title, "目标: " + " / ".join(roles), "排除: " + " / ".join(excluded)])
    # Unparsed employer constraints must never disappear behind a green badge.
    unresolved = _unresolved_requirements(requirements)
    add("other_requirements", "unknown" if unresolved else "pass", "岗位还有专业、经验或工作许可等条件待人工核实。" if unresolved else "未检测到需额外硬性判断的专业、年限或许可条件；仍需阅读完整要求。", unresolved)
    if policy is not None:
        if not isinstance(policy, dict) or set(policy) != {"required_checks"} or not isinstance(policy["required_checks"], list):
            raise ValueError("invalid_eligibility_policy")
        allowed = {x["name"] for x in checks}
        if any(name not in allowed for name in policy["required_checks"]):
            raise ValueError("invalid_eligibility_policy")
        required = set(policy["required_checks"]) | {"verification", "availability"}
        for check in checks:
            check["required"] = check["name"] in required
    return checks


def _deidentify(text, profile):
    text = _clean(text, 6000)
    name = _clean(profile.get("name"), 200)
    if name:
        text = text.replace(name, "[identity removed]")
    # ASCII/number boundaries deliberately allow adjacent Chinese characters.
    # Unicode \w boundaries would miss e.g. 联系电话13800138000.
    text = "\n".join(line for line in text.splitlines() if not INJECTION.search(line) and not CREDENTIAL.search(line))
    text = re.sub(r"[^\s,，;；:：<>@]+@[\w.-]+\.[\w-]{2,}", "[contact removed]", text)
    text = re.sub(r"(?:https?://|www\.)[^\s，。；;、<>]+", "[link removed]", text, flags=re.I)
    text = re.sub(r"(?:身份证(?:号(?:码)?)?|护照(?:号(?:码)?)?|证件号|社保号|微信号|QQ号|"
                  r"passport(?: number)?|national id|social security(?: number)?|ssn)\s*[:：=]?\s*[A-Za-z\d][A-Za-z\d -]{3,}",
                  "[identity removed]", text, flags=re.I)
    text = re.sub(r"(?:联系电话|电话号码|手机号|电话|手机|phone|mobile|telephone|tel)\s*[:：=]?\s*[+＋(（]?\d[\d \t().（）\-–－]{4,}\d",
                  "[contact removed]", text, flags=re.I)
    def remove_personal_number(match):
        # Preserve short metrics (30 records, 3 years, 20.5%, 2027-06).
        return "[contact removed]" if sum(ch.isdecimal() for ch in match[0]) >= 8 else match[0]
    text = re.sub(r"(?<!\d)[+＋]?\d[\d \t().（）\-–－]{5,}\d(?:[Xx](?![A-Za-z0-9]))?(?!\d)",
                  remove_personal_number, text)
    return text.strip()



def profile_facts(profile, *, resume_only=False):
    facts = {}
    for section in ("experience", "projects"):
        rows = profile.get(section, [])
        if not isinstance(rows, list):
            continue
        for i, row in enumerate(rows[:30]):
            if isinstance(row, dict):
                value = _deidentify(row.get("description"), profile)
                if value:
                    facts[section + "/" + str(i) + "/description"] = value
    if not resume_only:
        for i, skill in enumerate(_list_strings(profile.get("skills"), 50)):
            value = _deidentify(skill, profile)
            if value:
                facts["skills/" + str(i)] = value
    return facts


def _topics(text):
    folded = text.casefold()
    return {topic for topic in TOPICS if (re.search(r"(?<![a-z])" + re.escape(topic) + r"(?![a-z])", folded) if re.fullmatch(r"[a-z]+", topic) else topic in folded)}


def _model_payload(facts, job):
    return {"reviewed_evidence": [{"id": k, "text": v} for k, v in facts.items()],
            "untrusted_job": {k: _deidentify(_clean(job.get(k), 12000), {}) for k in ("title", "description", "requirements")}}


def model_status():
    return model.status()


def analyze(profile, job, *, policy=None):
    checks = hard_requirements(profile, job, policy)
    statuses = {check["status"] for check in checks if check.get("required", True)}
    eligibility = "fail" if "fail" in statuses else "unknown" if "unknown" in statuses else "pass"
    facts = profile_facts(profile)
    job_topics = _topics(" ".join(_clean(job.get(k)) for k in ("title", "description", "requirements")))
    strengths = [{"claim": value, "evidence_ids": [key]} for key, value in facts.items() if _topics(value) & job_topics][:8]
    proven_topics = set().union(*(_topics(v) for v in facts.values())) if facts else set()
    gaps = ["岗位提及“" + topic + "”，已确认档案暂未提供对应证据；不等于没有这项能力。" for topic in sorted(job_topics - proven_topics)][:8]
    questions = [c["name"] + "：" + c["reason"] for c in checks if c["status"] == "unknown"]
    model_provider, model_name = None, None
    mode, status = "rules", "not_configured" if not model.status()["configured"] else "no_reviewed_evidence"
    if facts and model.status()["configured"]:
        try:
            result = model.request_json("match_evidence", _model_payload(facts, job), model.MATCH_SCHEMA)
            strengths = model.validate_strengths(result, facts)
            mode, status = "model", "used_grounded_model_output"
            # Provenance comes from the exact request config, never a later env read.
            if isinstance(result, model.ModelResult):
                model_provider, model_name = result.provider, result.model_name
        except model.ModelError as exc:
            status = "fallback_rules:" + str(exc)
    return {"eligibility": eligibility, "hard_requirements": checks, "strengths": strengths, "gaps": gaps,
            "questions": questions, "mode": mode, "model_status": status,
            "model_provider": model_provider, "model_name": model_name,
            "evidence_catalog": [{"id": key, "text": value} for key, value in facts.items()],
            "disclaimer": "资格结果是已列条件的检查，不代表雇主确认、录用概率或投递授权。"}


def suggest_resume(profile, job):
    facts = profile_facts(profile, resume_only=True)
    topics = _topics(" ".join(_clean(job.get(k)) for k in ("title", "description", "requirements")))
    ranked = sorted(facts.items(), key=lambda item: -len(_topics(item[1]) & topics))
    suggestions = []
    for key, value in ranked[:4]:
        overlap = sorted(_topics(value) & topics)
        if not overlap:
            continue
        paragraphs = [p.strip() for p in value.splitlines() if p.strip()]
        # Reorder whole original paragraphs, never invent a skill or metric.
        after = "\n".join(sorted(paragraphs, key=lambda p: -len(_topics(p) & topics)))
        suggestions.append({"before": value, "after": after,
                            "reason": "将已有“" + "、".join(overlap) + "”证据前置；仅调整展示顺序，未新增事实。",
                            "evidence_ids": [key]})
    model_provider, model_name = None, None
    mode, status = "rules", "not_configured" if not model.status()["configured"] else "no_reviewed_evidence"
    if facts and model.status()["configured"]:
        try:
            result = model.request_json("resume_evidence", _model_payload(facts, job), model.RESUME_SCHEMA)
            suggestions = model.validate_suggestions(result, facts)
            mode, status = "model", "used_grounded_model_output"
            # Provenance comes from the exact request config, never a later env read.
            if isinstance(result, model.ModelResult):
                model_provider, model_name = result.provider, result.model_name
        except model.ModelError as exc:
            status = "fallback_rules:" + str(exc)
    return {"suggestions": suggestions, "mode": mode, "model_status": status,
            "model_provider": model_provider, "model_name": model_name,
            "limitations": "建议仅选择或重排已确认原文。缺少量化结果时请补充真实证据，不自动生成能力或业绩。"}


def _graduation_value(value):
    parts = re.findall(r"\d+", value)
    if not parts:
        return ""
    if len(parts) > 1:
        return parts[0] + "-" + parts[1].zfill(2) if 1 <= int(parts[1]) <= 12 else ""
    return parts[0]


def extract_profile(text):
    """Local-only conservative parser. Return a draft; caller must require review.

    Supports explicit labels and common resume section headings. Unclassified text
    remains in resume_text, rather than being guessed into candidate facts.
    """
    if not isinstance(text, str) or not text.strip() or len(text) > 30000:
        raise ValueError("invalid_resume_text")
    draft = {"name": "", "education": [], "experience": [], "projects": [], "skills": [],
             "preferences": {"roles": [], "cities": [], "excluded_roles": [], "recruitment_type": "all", "graduation_year": ""},
             "resume_text": text}
    headings = {"教育经历": "education", "教育背景": "education", "教育": "education", "education": "education",
                "工作经历": "experience", "实习经历": "experience", "经历": "experience", "experience": "experience",
                "项目经历": "projects", "项目": "projects", "projects": "projects", "技能": "skills", "专业技能": "skills", "skills": "skills"}
    sections = {s: [] for s in ("education", "experience", "projects", "skills")}
    current = None
    for raw in text.splitlines():
        line = raw.strip().lstrip("# ").strip()
        if not line or INJECTION.search(line):
            continue
        label, sep, value = re.match(r"^([^：:]{1,30})([：:])(.*)$", line).groups() if re.match(r"^([^：:]{1,30})([：:])(.*)$", line) else (line, "", "")
        label = label.strip().casefold()
        value = value.strip()
        if label in ("姓名", "name") and sep:
            draft["name"] = value[:200]
            current = None
        elif label in ("目标岗位", "求职方向", "roles", "target roles") and sep:
            draft["preferences"]["roles"] = [v.strip()[:100] for v in re.split(r"[,，、/;；]", value) if v.strip()][:20]
            current = None
        elif label in ("目标城市", "意向城市", "cities", "target cities") and sep:
            draft["preferences"]["cities"] = [v.strip()[:100] for v in re.split(r"[,，、/;；]", value) if v.strip()][:20]
            current = None
        elif label in ("招聘类型", "recruitment type") and sep:
            draft["preferences"]["recruitment_type"] = "internship" if re.search(r"实习|intern", value, re.I) else "campus" if re.search(r"校招|campus", value, re.I) else "all"
            current = None
        elif label in headings:
            current = headings[label]
            if value:
                sections[current].append(value)
        elif current:
            sections[current].append(line.lstrip("-• "))
    for line in sections["education"][:12]:
        degree_match = next((re.search(pattern, line, re.I) for _, pattern in DEGREES if re.search(pattern, line, re.I)), None)
        institution = re.search(r"[\w\u4e00-\u9fff]+(?:大学|学院)|[A-Za-z ]+ University", line)
        graduation = re.search(r"(?:毕业|graduat\w*)\s*[：:]?\s*(20\d{2}(?:[-/.年]\d{1,2}月?)?)", line, re.I)
        if graduation is None:
            graduation = re.search(r"(20\d{2}(?:[-/.年]\d{1,2}月?)?)\s*(?:毕业|届|graduat\w*)", line, re.I)
        major = re.search(r"(?:专业|major)\s*[:：]\s*([^,，;；|]+)", line, re.I)
        draft["education"].append({"institution": institution[0].strip()[:200] if institution else "",
                                   "degree": degree_match[0] if degree_match else "", "major": major[1].strip()[:200] if major else "",
                                   "graduation": _graduation_value(graduation[1]) if graduation else ""})
    for section in ("experience", "projects"):
        if sections[section]:
            row = {"id": section + "-import-1", "description": "\n".join(sections[section])[:6000]}
            if section == "experience":
                row.update({"organization": "", "role": ""})
            else:
                row["name"] = ""
            draft[section].append(row)
    draft["skills"] = list(dict.fromkeys(v.strip()[:200] for line in sections["skills"] for v in re.split(r"[,，、;；|]", line) if v.strip()))[:50]
    return draft
