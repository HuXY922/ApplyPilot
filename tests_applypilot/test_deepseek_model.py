"""Offline adapter and real-runtime integration checks; every HTTP call is mocked."""
import copy
import io
import json
import os
import unittest
import urllib.error
from unittest.mock import Mock, patch

from applypilot_core import model, runtime
from tests_applypilot.test_runtime import fixture_job, fixture_profile


def envelope(value=None, *, content=None, finish_reason="stop", **message_fields):
    return {"id": "mock-completion", "object": "chat.completion", "model": "deepseek-flash",
            "choices": [{"index": 0, "finish_reason": finish_reason,
                         "message": {"role": "assistant", "content": content if content is not None else json.dumps(value), **message_fields}}]}


def opener_for(value=None, *, raw=None):
    opener = Mock()
    opener.open.return_value = io.BytesIO(raw if raw is not None else json.dumps(value).encode())
    return opener


class _DeepSeekTestCase(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"APPLYPILOT_MODEL_PROVIDER": "deepseek",
                                         "APPLYPILOT_DEEPSEEK_API_KEY": "mock-deepseek-secret"}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        # A missed mock fails immediately instead of reaching any external host.
        self.no_network = patch("socket.socket.connect", side_effect=AssertionError("live network forbidden"))
        self.no_network.start()
        self.addCleanup(self.no_network.stop)

    def call(self, opener, *, task="match_evidence", payload=None, schema=None):
        return model.request_json(task, payload or {}, schema or model.MATCH_SCHEMA, opener=opener)



class DeepSeekModelTests(_DeepSeekTestCase):
    def test_status_configured_but_not_live_verified(self):
        status = model.status()
        self.assertEqual(status["provider"], "deepseek")
        self.assertEqual(status["model"], "deepseek-flash")
        self.assertEqual(status["status"], "configured_not_live_verified")
        self.assertEqual(status["mode"], "model")
        self.assertNotIn("mock-deepseek-secret", json.dumps(status))

    def test_missing_key_explicit_rules_and_no_call(self):
        with patch.dict(os.environ, {"APPLYPILOT_DEEPSEEK_API_KEY": ""}):
            status = model.status()
            self.assertFalse(status["configured"])
            self.assertEqual(status["provider"], "deepseek")
            self.assertEqual(status["status"], "not_configured")
            self.assertEqual(status["mode"], "rules")
            opener = Mock()
            with self.assertRaisesRegex(model.ModelError, "^not_configured$"):
                self.call(opener)
            opener.open.assert_not_called()

    def test_successful_match_fixed_destination_headers_bounded_json_mode_no_tools(self):
        facts = {"experience/0/description": "Analyzed 30 records with Python."}
        value = {"strengths": [{"claim": next(iter(facts.values())), "evidence_ids": list(facts)}]}
        opener = opener_for(envelope(value))
        result = self.call(opener, payload={"reviewed_evidence": [{"id": k, "text": v} for k, v in facts.items()]})
        self.assertEqual(model.validate_strengths(result, facts), value["strengths"])
        request = opener.open.call_args.args[0]
        body = json.loads(request.data)
        self.assertEqual(request.full_url, "https://api.deepseek.com/chat/completions")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Authorization"), "Bearer mock-deepseek-secret")
        self.assertEqual(request.get_header("Content-type"), "application/json")
        self.assertEqual(request.get_header("Accept"), "application/json")
        self.assertEqual(body["model"], "deepseek-flash")
        self.assertEqual(body["max_tokens"], 2500)
        self.assertEqual(body["thinking"], {"type": "disabled"})
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertIs(body["stream"], False)
        self.assertEqual([message["role"] for message in body["messages"]], ["system", "user"])
        self.assertIn("UNTRUSTED DATA", body["messages"][0]["content"])
        self.assertIn("JSON schema:", body["messages"][0]["content"])
        self.assertIn("Empty JSON result example:", body["messages"][0]["content"])
        for forbidden in ("tools", "tool_choice", "store", "text", "max_output_tokens", "user", "user_id"):
            self.assertNotIn(forbidden, body)
        self.assertNotIn("mock-deepseek-secret", request.data.decode())
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 25)
        self.assertEqual(opener.open.call_count, 1)

    def test_successful_resume_exact_paragraph_reorder(self):
        before = "Did not lead the project.\nAnalyzed 30 records with Python."
        value = {"suggestions": [{"before": before, "after": "Analyzed 30 records with Python.\nDid not lead the project.",
                                  "reason": "reorder", "evidence_ids": ["experience/0/description"]}]}
        result = self.call(opener_for(envelope(value)), task="resume_evidence", schema=model.RESUME_SCHEMA)
        self.assertEqual(len(model.validate_suggestions(result, {"experience/0/description": before})), 1)

    def test_both_current_models_allowed_and_unknown_models_fail_closed(self):
        for name in ("deepseek-flash", "deepseek-v4-pro"):
            with self.subTest(name=name), patch.dict(os.environ, {"APPLYPILOT_DEEPSEEK_MODEL": name}):
                opener = opener_for(envelope({"strengths": []}))
                self.call(opener)
                self.assertEqual(json.loads(opener.open.call_args.args[0].data)["model"], name)
        for name in ("", "deepseek-chat", "deepseek-reasoner", "deepseek-v4-flash", "unknown", "https://evil.test"):
            with self.subTest(name=name), patch.dict(os.environ, {"APPLYPILOT_DEEPSEEK_MODEL": name}):
                opener = Mock()
                self.assertFalse(model.status()["configured"])
                with self.assertRaisesRegex(model.ModelError, "^unsupported_model$"):
                    self.call(opener)
                opener.open.assert_not_called()

    def test_unknown_provider_does_not_fall_back_or_expose_arbitrary_value(self):
        with patch.dict(os.environ, {"APPLYPILOT_MODEL_PROVIDER": "https://evil.test/private-token"}):
            self.assertEqual(model.status()["provider"], "unsupported")
            self.assertEqual(model.status()["status"], "unsupported_provider")
            self.assertNotIn("private-token", json.dumps(model.status()))
            opener = Mock()
            with self.assertRaisesRegex(model.ModelError, "^unsupported_provider$"):
                self.call(opener)
            opener.open.assert_not_called()

    def test_keys_are_never_cross_provider_fallbacks(self):
        for env in ({"APPLYPILOT_DEEPSEEK_API_KEY": "", "APPLYPILOT_OPENAI_API_KEY": "openai-private", "OPENAI_API_KEY": "other-private", "DEEPSEEK_API_KEY": "generic-private"},
                    {"APPLYPILOT_MODEL_PROVIDER": "openai", "APPLYPILOT_OPENAI_MODEL": "test-model"}):
            with self.subTest(env=list(env)), patch.dict(os.environ, env):
                opener = Mock()
                self.assertFalse(model.status()["configured"])
                with self.assertRaisesRegex(model.ModelError, "^not_configured$"):
                    self.call(opener)
                opener.open.assert_not_called()

    def test_both_keys_configured_only_selected_key_and_fixed_endpoint_used(self):
        common = {"APPLYPILOT_OPENAI_API_KEY": "mock-openai-private", "APPLYPILOT_OPENAI_MODEL": "test-model",
                  "APPLYPILOT_MODEL_BASE_URL": "https://evil.test", "APPLYPILOT_DEEPSEEK_BASE_URL": "https://evil.test",
                  "OPENAI_BASE_URL": "https://evil.test", "DEEPSEEK_BASE_URL": "https://evil.test"}
        with patch.dict(os.environ, common):
            opener = opener_for(envelope({"strengths": []}))
            self.call(opener)
            request = opener.open.call_args.args[0]
            self.assertEqual(request.full_url, model.DEEPSEEK_API_URL)
            self.assertEqual(request.get_header("Authorization"), "Bearer mock-deepseek-secret")
            self.assertNotIn("mock-openai-private", repr(request.header_items()) + request.data.decode())
            with patch.dict(os.environ, {"APPLYPILOT_MODEL_PROVIDER": "openai"}):
                value = {"status": "completed", "output": [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": '{"strengths":[]}'}]}]}
                opener = opener_for(value)
                self.assertEqual(self.call(opener), {"strengths": []})
                request = opener.open.call_args.args[0]
                self.assertEqual(request.full_url, model.API_URL)
                self.assertEqual(request.get_header("Authorization"), "Bearer mock-openai-private")
                self.assertNotIn("mock-deepseek-secret", repr(request.header_items()) + request.data.decode())
                body = json.loads(request.data)
                self.assertFalse(body["store"])
                self.assertTrue(body["text"]["format"]["strict"])
                self.assertNotIn("response_format", body)

    def test_no_provider_preserves_optional_openai_compatibility(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "mock-openai-generic", "OPENAI_MODEL": "test-model"}):
            os.environ.pop("APPLYPILOT_MODEL_PROVIDER")
            self.assertEqual(model.status()["provider"], "openai")
            self.assertTrue(model.status()["configured"])

    def test_malformed_empty_refusal_unknown_fields_and_non_json_rejected(self):
        variants = (["not-object"], {}, {"error": {"message": "private-provider-details"}},
                    {"object": "chat.completion", "choices": []}, envelope({"strengths": []}, role="user"),
                    envelope({"strengths": []}, refusal="no"), envelope({"strengths": []}, tool_calls=[{"id": "tool"}]),
                    envelope({"strengths": []}, function_call={"name": "send_email"}),
                    envelope(content=""), envelope(content="   "), envelope(content="I cannot do that"),
                    envelope(content='```json\n{"strengths": []}\n```'), envelope(content='{"strengths":'),
                    envelope({"strengths": "yes"}), envelope({"strengths": [], "eligibility": "pass"}),
                    envelope({"strengths": [{"claim": "a", "evidence_ids": [], "extra": "b"}]}),
                    envelope(content='{"strengths":[],"strengths":[]}'), envelope(content='{"strengths":NaN}'))
        for value in variants:
            with self.subTest(value=value), self.assertRaises(model.ModelError):
                self.call(opener_for(value))
        for raw in (b"invalid", b"\xff", b'{"object":"chat.completion","object":"chat.completion","choices":[]}'):
            with self.subTest(raw=raw), self.assertRaises(model.ModelError):
                self.call(opener_for(raw=raw))

    def test_truncation_filter_tool_and_provider_abort_rejected(self):
        for reason in ("length", "content_filter", "tool_calls", "insufficient_system_resource", "aborted", "unknown", None):
            with self.subTest(reason=reason), self.assertRaisesRegex(model.ModelError, "^incomplete_response$"):
                self.call(opener_for(envelope({"strengths": []}, finish_reason=reason)))

    def test_non_single_choice_or_incorrect_index_rejected(self):
        value = envelope({"strengths": []})
        value["choices"].append(copy.deepcopy(value["choices"][0]))
        with self.assertRaisesRegex(model.ModelError, "^invalid_response$"):
            self.call(opener_for(value))
        for index in (None, 1, False, "0"):
            value = envelope({"strengths": []})
            value["choices"][0]["index"] = index
            with self.subTest(index=index), self.assertRaisesRegex(model.ModelError, "^invalid_response$"):
                self.call(opener_for(value))

    def test_reasoning_is_not_used_as_output(self):
        value = envelope(content=" ", reasoning_content='{"strengths":[]}')
        with self.assertRaises(model.ModelError):
            self.call(opener_for(value))
        value = envelope({"strengths": []}, reasoning_content="private reasoning must not persist")
        self.assertEqual(self.call(opener_for(value)), {"strengths": []})

    def test_http_network_error_categories_are_safe_and_not_retried(self):
        cases = [(urllib.error.HTTPError(model.DEEPSEEK_API_URL, code, "private mock-deepseek-secret", {}, io.BytesIO(b"private")), "http_" + str(code))
                 for code in (301, 302, 307, 308, 401, 403, 429, 500, 503)]
        cases += [(urllib.error.URLError("private mock-deepseek-secret"), "network_failure"),
                  (TimeoutError("private mock-deepseek-secret"), "network_failure")]
        for error, expected in cases:
            opener = Mock()
            opener.open.side_effect = error
            with self.subTest(expected=expected), self.assertRaisesRegex(model.ModelError, "^" + expected + "$"):
                self.call(opener)
            self.assertEqual(opener.open.call_count, 1)

    def test_default_transport_disables_redirects(self):
        opener = opener_for(envelope({"strengths": []}))
        with patch("applypilot_core.model.urllib.request.build_opener", return_value=opener) as build:
            model.request_json("match_evidence", {}, model.MATCH_SCHEMA)
        self.assertEqual(len(build.call_args.args), 1)
        handler = build.call_args.args[0]
        self.assertIsInstance(handler, model.NoRedirect)
        for code in (301, 302, 303, 307, 308):
            self.assertIsNone(handler.redirect_request(None, None, code, None, None, "https://evil.test"))

    def test_request_response_and_keepalive_time_are_bounded(self):
        opener = Mock()
        with self.assertRaisesRegex(model.ModelError, "^request_too_large$"):
            self.call(opener, payload={"large": "x" * model.MAX_REQUEST})
        opener.open.assert_not_called()
        with self.assertRaisesRegex(model.ModelError, "^response_too_large$"):
            self.call(opener_for(raw=b" " * (model.MAX_RESPONSE + 1)))
        opener = opener_for(raw=b"\n" * 20000)
        with patch("applypilot_core.model.time.monotonic", side_effect=(0, 0, 26)):
            with self.assertRaisesRegex(model.ModelError, "^request_timeout$"):
                self.call(opener)
        self.assertEqual(opener.open.call_count, 1)

    def test_unknown_task_and_unserializable_input_make_no_call(self):
        opener = Mock()
        with self.assertRaisesRegex(model.ModelError, "^task_not_allowed$"):
            self.call(opener, task="apply_to_job")
        with self.assertRaisesRegex(model.ModelError, "^invalid_request$"):
            self.call(opener, payload={"bad": float("nan")})
        opener.open.assert_not_called()


class DeepSeekRuntimeTests(_DeepSeekTestCase):
    def test_runtime_analyze_with_real_adapter_and_minimal_redacted_request(self):
        profile, job = fixture_profile(), fixture_job()
        profile["experience"][0]["description"] += "\n虚构测试者联系电话13800138000，邮箱demo@example.com。\n密钥sk-testsecret123456789"
        facts = runtime.profile_facts(profile)
        value = {"strengths": [{"claim": facts["experience/0/description"], "evidence_ids": ["experience/0/description"]}]}
        opener = opener_for(envelope(value))
        with patch("applypilot_core.model.urllib.request.build_opener", return_value=opener):
            result = runtime.analyze(profile, job)
        self.assertEqual(result["mode"], "model")
        self.assertEqual(result["model_status"], "used_grounded_model_output")
        self.assertEqual(result["model_provider"], "deepseek")
        self.assertEqual(result["model_name"], "deepseek-flash")
        body = json.loads(opener.open.call_args.args[0].data)
        data = json.loads(body["messages"][1]["content"])["data"]
        self.assertEqual(set(data), {"reviewed_evidence", "untrusted_job"})
        self.assertEqual(set(data["untrusted_job"]), {"title", "description", "requirements"})
        serialized = json.dumps(data, ensure_ascii=False)
        for private in ("虚构测试者", "13800138000", "demo@example.com", "sk-testsecret", "虚构企业", "虚构大学", "resume_text"):
            self.assertNotIn(private, serialized)
        self.assertIn("30 条产品反馈", serialized)

    def test_runtime_resume_uses_deepseek_and_retains_every_paragraph(self):
        profile, job = fixture_profile(), fixture_job()
        before = profile["experience"][0]["description"]
        after = "\n".join(reversed(before.splitlines()))
        value = {"suggestions": [{"before": before, "after": after, "reason": "reorder", "evidence_ids": ["experience/0/description"]}]}
        opener = opener_for(envelope(value))
        with patch("applypilot_core.model.urllib.request.build_opener", return_value=opener):
            result = runtime.suggest_resume(profile, job)
        self.assertEqual(result["mode"], "model")
        self.assertEqual(result["suggestions"][0]["after"], after)
        self.assertEqual(result["model_provider"], "deepseek")
        self.assertEqual(result["model_name"], "deepseek-flash")
        self.assertEqual(json.loads(opener.open.call_args.args[0].data)["model"], "deepseek-flash")

    def test_runtime_invalid_provider_output_degrades_without_invented_facts(self):
        profile, job = fixture_profile(), fixture_job()
        invented = {"strengths": [{"claim": "Led 50 engineers at Google", "evidence_ids": ["experience/0/description"]}]}
        cases = ((envelope(invented), "ungrounded_output"),
                 (envelope({"strengths": [], "eligibility": "pass"}), "invalid_schema"),
                 (envelope(content="refused"), "invalid_response"),
                 (envelope(content="", refusal="no"), "refused_or_invalid_response"),
                 (envelope({"strengths": []}, finish_reason="length"), "incomplete_response"))
        for value, reason in cases:
            with self.subTest(reason=reason), patch("applypilot_core.model.urllib.request.build_opener", return_value=opener_for(value)):
                result = runtime.analyze(profile, job)
            self.assertEqual(result["mode"], "rules")
            self.assertIsNone(result["model_provider"])
            self.assertIsNone(result["model_name"])
            self.assertEqual(result["model_status"], "fallback_rules:" + reason)
            self.assertNotIn("Google", json.dumps(result))
        opener = Mock()
        opener.open.side_effect = urllib.error.URLError("private")
        with patch("applypilot_core.model.urllib.request.build_opener", return_value=opener):
            result = runtime.analyze(profile, job)
        self.assertEqual(result["model_status"], "fallback_rules:network_failure")

    def test_runtime_resume_invention_or_dropped_qualifier_degrades(self):
        profile, job = fixture_profile(), fixture_job()
        profile["experience"][0]["description"] = "Did not lead the project.\nUsed Python to analyze 30 records."
        before = profile["experience"][0]["description"]
        for after in ("Led a 50-person team.", "Used Python to analyze 30 records."):
            value = {"suggestions": [{"before": before, "after": after, "reason": "select", "evidence_ids": ["experience/0/description"]}]}
            with self.subTest(after=after), patch("applypilot_core.model.urllib.request.build_opener", return_value=opener_for(envelope(value))):
                result = runtime.suggest_resume(profile, job)
            self.assertEqual(result["mode"], "rules")
            self.assertIsNone(result["model_provider"])
            self.assertIsNone(result["model_name"])
            self.assertEqual(result["model_status"], "fallback_rules:ungrounded_output")
            self.assertNotIn("50-person", json.dumps(result))
            self.assertIn("Did not lead", json.dumps(result))

    def test_provenance_uses_exact_call_snapshot_despite_later_config_change(self):
        profile, job = fixture_profile(), fixture_job()
        opener = opener_for(envelope({"strengths": []}))
        response = opener.open.return_value
        def change_provider_during_call(*args, **kwargs):
            os.environ["APPLYPILOT_MODEL_PROVIDER"] = "openai"
            os.environ["APPLYPILOT_OPENAI_API_KEY"] = "mock-openai-secret"
            os.environ["APPLYPILOT_OPENAI_MODEL"] = "different-model"
            return response
        opener.open.side_effect = change_provider_during_call
        with patch("applypilot_core.model.urllib.request.build_opener", return_value=opener):
            result = runtime.analyze(profile, job)
        self.assertEqual(model.status()["provider"], "openai")
        self.assertEqual(result["model_provider"], "deepseek")
        self.assertEqual(result["model_name"], "deepseek-flash")
        self.assertEqual(opener.open.call_args.args[0].full_url, model.DEEPSEEK_API_URL)

    def test_successful_deepseek_cannot_override_eligibility_fail_or_unknown(self):
        for scenario in ("fail", "unknown"):
            profile, job = fixture_profile(), fixture_job()
            if scenario == "fail":
                profile["education"][0]["degree"] = "专科"
            else:
                job["requirements"] += "专业不限，必须有3年工作经验。"
            with self.subTest(scenario=scenario), patch("applypilot_core.model.urllib.request.build_opener", return_value=opener_for(envelope({"strengths": []}))):
                result = runtime.analyze(profile, job)
            self.assertEqual(result["mode"], "model")
            self.assertEqual(result["eligibility"], scenario)


if __name__ == "__main__":
    unittest.main()
