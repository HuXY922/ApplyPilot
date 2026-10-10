"""Synthetic provider contracts. Live-source smoke is a separate explicit script."""
import copy
import json
import os
import socket
import tempfile
import time
import unittest
import urllib.error
from datetime import datetime, timezone
from email.message import Message
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import urlsplit

from applypilot_core import official_sources as sources, runtime
from applypilot_core.store import Store, DomainError
from applypilot_core.service import Service


ID = "00000000-0000-4000-8000-000000000001"
BYTE_ID = runtime.DEFAULT_SEEDS[0]


def bootstrap(category="9", tenant="cxmt", include_intern=True):
    pages = [{"Code": "CampusList", "Name": "校园招聘列表页", "PageType": 2, "BusinessType": category},
             {"Code": "CampusDetail", "Name": "校园招聘详情页", "PageType": 1, "BusinessType": category}]
    if include_intern:
        pages += [{"Code": "InternList", "Name": "实习生招聘列表页", "PageType": 2, "BusinessType": "7"},
                  {"Code": "InternDetail", "Name": "实习生招聘详情页", "PageType": 1, "BusinessType": "7"}]
    return ('<html><script>var BSGlobal = ' + json.dumps({"tenantInfo": {"Name": tenant}, "Pages": pages}, ensure_ascii=False) + ';</script></html>').encode()


def row(index=1, **changes):
    return {"Id": f"00000000-0000-4000-8000-{index:012d}", "JobAdName": "虚构测试产品工程师",
            "CategoryId": "9", "Category": "校园招聘", "Duty": "虚构测试：开发产品与用户研究。",
            "Require": "本科及以上学历。", "Status": 1, "EndTimeInt": 0, "EndTime": "0001-01-01T00:00:00",
            "LocNames": ["安徽省·合肥市", "北京市"], "Kind": "全职", **changes}


def page(rows, total=None):
    return {"Code": 200, "Data": rows, "Count": len(rows) if total is None else total}


def client(responses=(), html=None):
    http = Mock()
    http.get.return_value = bootstrap() if html is None else html
    http.post.side_effect = [json.dumps(item, ensure_ascii=False).encode() if isinstance(item, dict) else item for item in responses]
    return http


def discover(http, query="", **kwargs):
    return runtime.discover(query, http=http, source_config={"sources": ["cxmt"], **kwargs.pop("config", {})}, pause=lambda _: None, **kwargs)


class OfficialProviderTests(unittest.TestCase):
    def test_success_has_complete_job_identity_status_and_provenance(self):
        http = client([page([row()])])
        result = discover(http, "产品", cities=["合肥"])
        self.assertEqual(len(result["jobs"]), 1)
        job = result["jobs"][0]
        self.assertEqual(job["canonical_url"], "https://cxmt.zhiye.com/campus/detail?jobAdId=" + ID)
        self.assertEqual(job["provider"], "cxmt")
        self.assertEqual(job["availability"], "open")
        self.assertEqual(job["verification"]["method"], "official_complete_jd_listing")
        self.assertIn("未逐条访问详情页", str(job["verification"]["evidence"]))
        self.assertEqual(http.post.call_args.kwargs["body"]["KeyWords"], "产品")
        self.assertEqual(http.post.call_args.kwargs["body"]["Category"], ["9"])
        self.assertEqual(result["source_summary"][0]["pages"], 1)

    def test_ten_record_pages_preserve_total_and_twenty_record_cap(self):
        http = client([page([row(i) for i in range(1, 11)], 45),
                       page([row(i) for i in range(11, 21)], 45)])
        result = discover(http, "产品")
        self.assertEqual([call.kwargs["body"]["PageSize"] for call in http.post.call_args_list], [10, 10])
        self.assertEqual([call.kwargs["body"]["PageIndex"] for call in http.post.call_args_list], [0, 1])
        self.assertEqual(len(result["jobs"]), 20)
        self.assertEqual(len({job["external_id"] for job in result["jobs"]}), 20)
        summary = result["source_summary"][0]
        self.assertEqual((summary["page_size"], summary["max_pages"], summary["pages"]), (10, 2, 2))
        self.assertEqual(summary["total_available"], 45)
        self.assertEqual(summary["requests"], 3)
        self.assertTrue(summary["truncated"])
        self.assertEqual(summary["stop_reason"], "page_limit_reached")
        self.assertEqual(result["failures"], [])

    def test_response_cannot_exceed_requested_ten_record_page(self):
        result = discover(client([page([row(i) for i in range(1, 12)], 45)]))
        self.assertEqual(result["source_summary"][0]["status"], "failed")
        self.assertEqual(result["jobs"], [])
        self.assertEqual(result["failures"][0]["reason"], "board_schema_changed_or_too_large")

    def test_empty_success_does_not_generate_job_or_close_anything(self):
        result = discover(client([page([])]))
        self.assertEqual(result["jobs"], [])
        self.assertEqual(result["observed_jobs"], [])
        self.assertEqual(result["failures"], [])
        self.assertEqual(result["source_summary"][0]["status"], "ok")

    def test_unknown_cohort_is_not_copied_from_campaign_or_preferences(self):
        result = discover(client([page([row()])]))
        job = result["jobs"][0]
        profile = {"education": [{"degree": "本科", "graduation": "2027-06"}], "preferences": {"recruitment_type": "campus"}}
        check = next(item for item in runtime.hard_requirements(profile, job) if item["name"] == "cohort")
        self.assertEqual(check["status"], "unknown")
        self.assertNotIn("2027", job["requirements"])

    def test_internship_is_mapped_from_its_own_official_category(self):
        http = client([page([row(CategoryId="7", Category="实习生招聘", Kind="实习")])])
        result = discover(http, recruitment_type="internship")
        self.assertEqual(result["jobs"][0]["recruitment_type"], "internship")
        self.assertIn("/intern/detail?", result["jobs"][0]["canonical_url"])
        self.assertEqual(http.post.call_args.kwargs["body"]["Category"], ["7"])

    def test_absent_category_never_falls_back_to_two(self):
        http = client([], html=bootstrap(include_intern=False))
        result = discover(http, recruitment_type="internship")
        self.assertEqual(result["jobs"], [])
        self.assertEqual(result["failures"][0]["reason"], "recruitment_category_unavailable")
        http.post.assert_not_called()

    def test_wrong_tenant_stops_before_list(self):
        http = client([], html=bootstrap(tenant="someoneelse"))
        result = discover(http)
        self.assertEqual(result["failures"][0]["reason"], "official_tenant_identity_mismatch")
        http.post.assert_not_called()

    def test_blocked_bootstrap_or_login_html_stops_source(self):
        for html in (b"<html>Please login</html>", b"<html>captcha</html>", b"var BSGlobal = {}"):
            http = client([], html=html)
            result = discover(http)
            self.assertTrue(result["failures"])
            http.post.assert_not_called()

    def test_403_429_timeout_and_malformed_list_stop_immediately(self):
        for error in (runtime.SourceError("http_403"), runtime.SourceError("http_429"), runtime.SourceError("network_timeout"),
                      b"not json", {"Code": 403}, {"Code": 429}, {"Code": 200, "Data": {}}):
            with self.subTest(error=error):
                http = client([error, page([row()])])
                result = discover(http)
                self.assertEqual(http.post.call_count, 1)
                self.assertEqual(result["jobs"], [])
                self.assertEqual(result["observed_jobs"], [])
                self.assertEqual(result["source_summary"][0]["status"], "failed")

    def test_second_page_failure_preserves_first_and_reports_partial(self):
        http = client([page([row(i) for i in range(1, 11)], 100), runtime.SourceError("http_403")])
        result = discover(http)
        self.assertEqual(len(result["jobs"]), 10)
        self.assertEqual(result["source_summary"][0]["status"], "partial")
        self.assertEqual(http.post.call_count, 2)

    def test_bounded_pagination_and_cross_page_deduplication(self):
        http = client([page([row(i) for i in range(1, 11)], 100), page([row(10), row(11)], 100)])
        result = discover(http)
        self.assertEqual(len(result["jobs"]), 11)
        self.assertEqual(len(result["observed_jobs"]), 11)
        self.assertEqual([call.kwargs["body"]["PageIndex"] for call in http.post.call_args_list], [0, 1])
        summary = result["source_summary"][0]
        self.assertEqual(summary["duplicates"], 1)
        self.assertEqual(summary["pages"], 2)
        self.assertTrue(summary["truncated"])
        self.assertEqual(summary["stop_reason"], "page_limit_reached")

    def test_finished_second_page_is_not_marked_truncated(self):
        http = client([page([row(i) for i in range(1, 11)], 11), page([row(11)], 11)])
        result = discover(http)
        self.assertFalse(result["source_summary"][0]["truncated"])
        self.assertEqual(result["source_summary"][0]["stop_reason"], "completed")

    def test_repeated_page_stops_and_is_not_double_counted(self):
        response = page([row(i) for i in range(1, 11)], 100)
        result = discover(client([response, response]))
        self.assertEqual(len(result["jobs"]), 10)
        self.assertEqual(result["source_summary"][0]["stop_reason"], "repeated_page")
        self.assertTrue(result["source_summary"][0]["truncated"])

    def test_fair_default_budget_reaches_second_page_after_bytedance(self):
        http = client([page([row(i) for i in range(1, 11)], 11), page([row(11)], 11)])
        def get(url, **kwargs):
            if urlsplit(url).hostname == "cxmt.zhiye.com":
                return bootstrap()
            ident = urlsplit(url).path.rsplit("/", 1)[1]
            return json.dumps({"code": 0, "data": {"job_post_detail": {"id": ident, "title": "虚构字节测试",
                "description": "虚构职责", "requirement": "虚构要求", "channel_online_status": 1,
                "recruit_type": {"name": "校招"}, "city_list": []}, "recommend_job_post_List": []}}).encode()
        http.get.side_effect = get
        result = discover(http, config={"sources": ["bytedance", "cxmt"], "max_requests": 6})
        self.assertEqual(sum(item["requests"] for item in result["source_summary"]), 6)
        self.assertEqual(result["source_summary"][1]["pages"], 2)
        self.assertEqual(result["source_summary"][0]["verified"], 3)

    def test_budget_only_permits_bootstrap_is_explicitly_incomplete(self):
        http = client([])
        result = discover(http, config={"max_requests": 1})
        self.assertEqual(result["jobs"], [])
        self.assertEqual(result["source_summary"][0]["stop_reason"], "source_request_budget_exhausted")
        self.assertEqual(result["source_summary"][0]["status"], "failed")
        self.assertTrue(result["failures"])
        http.post.assert_not_called()

    def test_closed_expired_unknown_boolean_and_deleted_statuses(self):
        for changes, expected in (({"Status": 2}, "closed"), ({"Status": 0}, "unknown"), ({"Status": True}, "unknown"),
                                  ({"Status": None}, "unknown"), ({"EndTimeInt": 1}, "expired"), ({"StdIsDeleted": True}, "closed")):
            with self.subTest(changes=changes):
                result = discover(client([page([row(**changes)])]))
                self.assertEqual(result["jobs"], [])
                self.assertEqual(result["observed_jobs"][0]["availability"], expected)

    def test_malformed_expiry_is_never_open(self):
        for value in ("never", -1, True, float("nan")):
            result = discover(client([page([row(EndTimeInt=value)])]))
            self.assertFalse(result["jobs"])
            self.assertFalse(result["observed_jobs"])
            self.assertTrue(result["failures"])

    def test_zero_expiry_cannot_hide_date_and_conflicting_expiry_is_rejected(self):
        for changes in ({"EndTimeInt": 0, "EndTime": "2020-01-01T00:00:00"},
                        {"EndTimeInt": 0, "EndTime": "garbage"},
                        {"EndTimeInt": 9999999999, "EndTime": "2020-01-01T00:00:00"}):
            result = discover(client([page([row(**changes)])]))
            self.assertFalse(result["jobs"])
            self.assertFalse(result["observed_jobs"])
            self.assertTrue(result["failures"])

    def test_international_title_is_not_internship(self):
        result = discover(client([page([row(JobAdName="International Sales Engineer")])]))
        self.assertEqual(result["jobs"][0]["recruitment_type"], "campus")

    def test_inconsistent_page_count_is_failed_not_empty_success(self):
        for response in (page([], 20), page([row()], 0)):
            result = discover(client([response]))
            self.assertEqual(result["source_summary"][0]["status"], "failed")
            self.assertEqual(result["failures"][0]["reason"], "pagination_count_inconsistent")
            self.assertEqual(result["jobs"], [])

    def test_identity_or_required_fields_missing_are_not_verified(self):
        for changes in ({"Id": "J12345"}, {"Id": "https://localhost"}, {"Duty": ""}, {"Require": None},
                        {"CategoryId": "2"}, {"Category": "社会招聘"}, {"LocNames": "北京"}):
            with self.subTest(changes=changes):
                result = discover(client([page([row(**changes)])]))
                self.assertEqual(result["jobs"], [])
                self.assertEqual(result["observed_jobs"], [])
                self.assertTrue(result["failures"])

    def test_personal_contacts_and_credentials_never_reach_source(self):
        for query in ("a@example.com", "https://example.com", "13800138000", "token: secret-test", "密钥sk-longsecret12345"):
            http = client([])
            result = discover(http, query)
            http.get.assert_not_called()
            http.post.assert_not_called()
            self.assertEqual(result["failures"][0]["reason"], "unsafe_search_keywords")

    def test_formatted_contacts_never_enter_upstream_search(self):
        queries = ("+1 415 555 1212", "+86 138 0013 8000", "138 0013 8000", "138-0013-8000",
                   "电话：010-8888-6666", "１３８ ００１３ ８０００", "电话(010)88886666",
                   "www.private.example.com/resume", "微信号abc_test123", "邮箱用户demo@example.com",
                   "token: secret-test", "密钥sk-longsecret12345", "https://private.example.com")
        for query in queries:
            with self.subTest(query=query):
                http = client([])
                result = discover(http, query)
                http.get.assert_not_called()
                http.post.assert_not_called()
                self.assertEqual(result["failures"][0]["reason"], "unsafe_search_keywords")

    def test_ordinary_job_keywords_versions_and_dates_remain_allowed(self):
        for query in ("AI", "财务", "AI 产品经理 2027", "C++ 17", "AI 2027-06"):
            with self.subTest(query=query):
                http = client([page([])])
                result = discover(http, query)
                self.assertEqual(result["failures"], [])
                self.assertEqual(http.post.call_count, 1)
                self.assertEqual(http.post.call_args.kwargs["body"]["KeyWords"], query)

    def test_ingest_repeat_refresh_and_failure_preserve_manual_history(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "session-provider.sqlite", initialize_new=True)
            initial = discover(client([page([row()])]))
            fake_runtime = Mock()
            fake_runtime.discover.return_value = initial
            service = Service(store, runtime=fake_runtime)
            first = service.search({"query": "产品"})
            self.assertEqual(first["run"]["result"]["created"], 1)
            jid = first["jobs"][0]["id"]
            store.track(jid, "skipped", False, "保留用户决定", "skip-source-fixture")
            again = service.search({"query": "产品"})
            self.assertEqual(again["run"]["result"]["created"], 0)
            self.assertEqual(again["jobs"][0]["tracker_status"], "skipped")
            fake_runtime.discover.return_value = discover(client([runtime.SourceError("http_403")]))
            with self.assertRaises(DomainError):
                service.search({"query": "产品"})
            self.assertEqual(store.job(jid)["availability"], "open")
            self.assertEqual(store.job(jid)["tracker_status"], "skipped")
            fake_runtime.discover.return_value = discover(client([page([])]))
            self.assertEqual(service.search({"query": "产品"})["run"]["status"], "completed")
            self.assertEqual(store.job(jid)["availability"], "open")
            fake_runtime.discover.return_value = discover(client([page([row(Status=2)])]))
            service.search({"query": "产品"})
            self.assertEqual(store.job(jid)["availability"], "closed")
            self.assertEqual(store.job(jid)["tracker_status"], "skipped")
            self.assertEqual(sum(event["to_status"] == "skipped" for event in store.events(jid)), 1)

    def test_no_listing_budget_is_failed_service_run(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "session-provider.sqlite", initialize_new=True)
            fake_runtime = Mock()
            fake_runtime.discover.return_value = discover(client([]), config={"max_requests": 1})
            with self.assertRaises(DomainError) as raised:
                Service(store, runtime=fake_runtime).search({"query": "产品"})
            self.assertEqual(raised.exception.details["run"]["status"], "failed")

    def test_fair_budget_keeps_later_source_running_after_first_failure(self):
        http = client([page([row()])])
        def get(url, **kwargs):
            if urlsplit(url).hostname == "cxmt.zhiye.com":
                return bootstrap()
            raise runtime.SourceError("http_403")
        http.get.side_effect = get
        result = discover(http, config={"sources": ["bytedance", "cxmt"]})
        self.assertEqual(result["source_summary"][0]["status"], "failed")
        self.assertEqual(result["source_summary"][0]["stop_reason"], "http_403")
        self.assertEqual(result["source_summary"][1]["status"], "ok")
        self.assertEqual(len(result["jobs"]), 1)

    def test_endpoint_allowlist_rejects_writes_extra_fields_and_arbitrary_urls(self):
        good = {"PageIndex": 0, "PageSize": 10, "Category": ["9"], "KeyWords": "产品", "SpecialType": 0, "PortalId": "", "DisplayFields": sources.DISPLAY_FIELDS}
        self.assertTrue(sources.endpoint_allowed(urlsplit("https://cxmt.zhiye.com" + sources.LIST_PATH), "POST", good))
        for path in ("/api/Jobad/Apply", sources.LIST_PATH + "?next=http://127.0.0.1", "/api/Jobad/../Submit", "/login"):
            self.assertFalse(sources.endpoint_allowed(urlsplit("https://cxmt.zhiye.com" + path), "POST", good))
        for change in ({"PageIndex": 2}, {"PageSize": 11}, {"PageSize": 20}, {"PageSize": 100}, {"Category": ["../internal"]}, {"profile": "private"}, {"PortalId": "user-controlled"}):
            self.assertFalse(sources.endpoint_allowed(urlsplit("https://cxmt.zhiye.com" + sources.LIST_PATH), "POST", {**good, **change}))

    def test_new_source_transport_rejects_private_dns_redirects_and_post_to_legacy(self):
        with patch("applypilot_core.runtime.urllib.request.getproxies", return_value={}):
            transport = runtime._DirectTransport()
        transport.opener = Mock()
        with patch("applypilot_core.runtime.socket.getaddrinfo", return_value=[(None, None, None, None, ("127.0.0.1", 443))]):
            with self.assertRaisesRegex(runtime.SourceError, "private_network_denied"):
                transport.get("https://cxmt.zhiye.com/campus/jobs")
        for url in ("https://cxmt.zhiye.com:443/campus/jobs", "https://cxmt.zhiye.com.evil.test/campus/jobs", "https://user@cxmt.zhiye.com/campus/jobs"):
            with self.assertRaisesRegex(runtime.SourceError, "destination_denied"):
                transport.get(url)
        with self.assertRaisesRegex(runtime.SourceError, "destination_denied"):
            transport.post("https://jobs.bytedance.com/api/v1/job/posts/" + BYTE_ID + "?portal_type=3&with_recommend=true", body={})
        transport.opener.open.assert_not_called()
        self.assertIsNone(runtime.NoRedirect().redirect_request(None, None, 302, None, None, "https://cxmt.zhiye.com/login"))

    def test_source_rate_limit_respects_full_retry_after_across_sessions(self):
        with patch.dict(runtime._SOURCE_COOLDOWNS, {}, clear=True), patch("applypilot_core.runtime.time.time", return_value=1000):
            runtime._pause_source("cxmt.zhiye.com", 429, "3600")
            self.assertEqual(runtime._SOURCE_COOLDOWNS["cxmt.zhiye.com"][0], 4600)
            with self.assertRaisesRegex(runtime.SourceError, "source_paused_http_429"):
                runtime._check_source_pause("cxmt.zhiye.com")
            runtime._pause_source("cxmt.zhiye.com", 429, "60")
            self.assertEqual(runtime._SOURCE_COOLDOWNS["cxmt.zhiye.com"][0], 4600)
        with patch.dict(runtime._SOURCE_COOLDOWNS, {}, clear=True):
            runtime._pause_source("cxmt.zhiye.com", 403)
            with self.assertRaisesRegex(runtime.SourceError, "source_paused_http_403"):
                runtime._check_source_pause("cxmt.zhiye.com")

    def test_http_429_headers_pause_transport_without_retry(self):
        with patch.dict(runtime._SOURCE_COOLDOWNS, {}, clear=True), patch("applypilot_core.runtime.urllib.request.getproxies", return_value={}):
            transport = runtime._DirectTransport()
            transport.opener = Mock()
            headers = Message(); headers["Retry-After"] = "1800"
            transport.opener.open.side_effect = urllib.error.HTTPError("https://cxmt.zhiye.com/campus/jobs", 429, "limited", headers, None)
            with patch("applypilot_core.runtime.socket.getaddrinfo", return_value=[(None, None, None, None, ("8.8.8.8", 443))]):
                with self.assertRaisesRegex(runtime.SourceError, "http_429"):
                    transport.get("https://cxmt.zhiye.com/campus/jobs")
                with self.assertRaisesRegex(runtime.SourceError, "source_paused_http_429"):
                    transport.get("https://cxmt.zhiye.com/campus/jobs")
            self.assertEqual(transport.opener.open.call_count, 1)


if __name__ == "__main__":
    unittest.main()
