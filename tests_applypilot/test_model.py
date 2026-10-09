import io
import json
import os
import unittest
import urllib.error
from unittest.mock import Mock, patch

from applypilot_core import model


def envelope(content):
    return {"status": "completed", "output": [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": json.dumps(content)}]}]}


def opener_for(value):
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.read.return_value = json.dumps(value).encode()
    response.read1 = None
    opener = Mock()
    opener.open.return_value = response
    return opener


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"APPLYPILOT_MODEL_PROVIDER": "openai", "APPLYPILOT_OPENAI_API_KEY": "test-secret-do-not-log", "APPLYPILOT_OPENAI_MODEL": "test-model"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_no_key_or_no_model_no_call(self):
        for field in ("APPLYPILOT_OPENAI_API_KEY", "APPLYPILOT_OPENAI_MODEL"):
            with patch.dict(os.environ, {field: ""}):
                self.assertFalse(model.status()["configured"])
                opener = Mock()
                with self.assertRaisesRegex(model.ModelError, "not_configured"):
                    model.request_json("match_evidence", {}, model.MATCH_SCHEMA, opener=opener)
                opener.open.assert_not_called()

    def test_responses_structured_request_no_tools_no_storage(self):
        opener = opener_for(envelope({"strengths": []}))
        self.assertEqual(model.request_json("match_evidence", {"data": "untrusted"}, model.MATCH_SCHEMA, opener=opener), {"strengths": []})
        request = opener.open.call_args.args[0]
        body = json.loads(request.data)
        self.assertEqual(request.full_url, "https://api.openai.com/v1/responses")
        self.assertFalse(body["store"])
        self.assertNotIn("tools", body)
        self.assertTrue(body["text"]["format"]["strict"])
        self.assertIn("UNTRUSTED DATA", body["instructions"])
        self.assertNotIn("test-secret", json.dumps(model.status()))

    def test_reject_malformed_refusal_incomplete_tool_call_and_schema(self):
        values = ({"status": "incomplete", "output": []}, {"status": "completed", "output": [{"type": "function_call", "name": "send_email"}]},
                  {"status": "completed", "output": [{"type": "message", "role": "assistant", "content": [{"type": "refusal"}]}]},
                  envelope({"strengths": "yes"}), envelope({"strengths": [], "eligibility": "pass"}), ["bad"])
        for value in values:
            with self.subTest(value=value), self.assertRaises(model.ModelError):
                model.request_json("match_evidence", {}, model.MATCH_SCHEMA, opener=opener_for(value))

    def test_http_error_redacts_key_body_and_no_retry(self):
        opener = Mock()
        opener.open.side_effect = urllib.error.HTTPError(model.API_URL, 401, "test-secret-do-not-log", {}, io.BytesIO(b"private details"))
        with self.assertRaisesRegex(model.ModelError, "^http_401$"):
            model.request_json("match_evidence", {}, model.MATCH_SCHEMA, opener=opener)
        self.assertEqual(opener.open.call_count, 1)

    def test_strength_claim_requires_exact_evidence_and_real_id(self):
        facts = {"experience/0/description": "Assisted a 3-person team with Python tests."}
        for claim, ids in (("Led a 30-person team with Python tests.", list(facts)), (facts[next(iter(facts))], ["invented"]), ("Python expert", [])):
            with self.assertRaisesRegex(model.ModelError, "ungrounded_output"):
                model.validate_strengths({"strengths": [{"claim": claim, "evidence_ids": ids}]}, facts)
        claim = facts[next(iter(facts))]
        self.assertEqual(model.validate_strengths({"strengths": [{"claim": claim, "evidence_ids": list(facts)}]}, facts)[0]["claim"], claim)

    def test_resume_rejects_numbers_entities_ability_and_removed_negation(self):
        text = "Did not lead the project.\nUsed Python to analyze 30 feedback records."
        facts = {"experience/0/description": text}
        for after in ("Led the project.", "Used Python to analyze 300 feedback records.", "Led Google AI team.", "Certified in Python."):
            with self.assertRaisesRegex(model.ModelError, "ungrounded_output"):
                model.validate_suggestions({"suggestions": [{"before": text, "after": after, "reason": "select", "evidence_ids": list(facts)}]}, facts)
        with self.assertRaisesRegex(model.ModelError, "ungrounded_output"):
            model.validate_suggestions({"suggestions": [{"before": text, "after": "Used Python to analyze 30 feedback records.", "reason": "select", "evidence_ids": list(facts)}]}, facts)
        valid = {"suggestions": [{"before": text, "after": "Used Python to analyze 30 feedback records.\nDid not lead the project.", "reason": "reorder", "evidence_ids": list(facts)}]}
        self.assertEqual(len(model.validate_suggestions(valid, facts)), 1)

    def test_requests_and_responses_bounded(self):
        opener = Mock()
        with self.assertRaisesRegex(model.ModelError, "request_too_large"):
            model.request_json("match_evidence", {"huge": "x" * model.MAX_REQUEST}, model.MATCH_SCHEMA, opener=opener)
        opener.open.assert_not_called()
        opener = opener_for({})
        opener.open.return_value.read.return_value = b"x" * (model.MAX_RESPONSE + 1)
        with self.assertRaisesRegex(model.ModelError, "response_too_large"):
            model.request_json("match_evidence", {}, model.MATCH_SCHEMA, opener=opener)

    def test_redirect_and_unallowed_task_denied(self):
        self.assertIsNone(model.NoRedirect().redirect_request(None, None, 302, None, None, "https://evil.test"))
        with self.assertRaisesRegex(model.ModelError, "task_not_allowed"):
            model.request_json("apply_to_job", {}, model.MATCH_SCHEMA)


if __name__ == "__main__":
    unittest.main()
