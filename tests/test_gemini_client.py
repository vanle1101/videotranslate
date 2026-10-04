"""Gemini JSON client contracts using mocked SDK calls only."""

import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from google.genai.errors import ClientError
from google.genai.types import FinishReason

from config import settings
from core.engines.translation.gemini_client import GeminiClient, GeminiError


class GeminiClientTests(unittest.TestCase):
    def setUp(self):
        self.test_key = "fixture-secret-not-for-output"
        self.client = Mock()
        self.client.models.generate_content.return_value = SimpleNamespace(
            text='  {"translation": "Xin chào"}  ',
            candidates=[SimpleNamespace(finish_reason=FinishReason.STOP)],
        )
        self.patches = [
            patch.object(settings, "GEMINI_API_KEY", self.test_key),
            patch.object(settings, "GEMINI_MODEL", "gemini-fixture"),
            patch("google.genai.Client", return_value=self.client),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()

    def test_json_request_uses_saved_model_system_bounded_timeout_and_closes(self):
        result = GeminiClient(timeout=12.5).translate("Translate hello", system="Return JSON")
        self.assertEqual(result, '{"translation": "Xin chào"}')
        from google import genai
        genai.Client.assert_called_once_with(
            api_key=self.test_key,
            http_options={"timeout": 12500, "retry_options": {"attempts": 1}},
        )
        self.client.models.generate_content.assert_called_once_with(
            model="gemini-fixture", contents="Translate hello",
            config={"temperature": 0.1, "response_mime_type": "application/json",
                    "max_output_tokens": 8192, "system_instruction": "Return JSON"},
        )
        self.client.close.assert_called_once()

    def test_rejects_incomplete_blocked_missing_or_empty_responses(self):
        for reason in ("MAX_TOKENS", "SAFETY", "RECITATION", None):
            with self.subTest(reason=reason):
                self.client.reset_mock()
                self.client.models.generate_content.return_value = SimpleNamespace(
                    text='{"translation":"partial"}', candidates=[SimpleNamespace(finish_reason=reason)],
                )
                with self.assertRaises(GeminiError):
                    GeminiClient().translate("Translate")
                self.client.close.assert_called_once()
        for text, candidates in (("", [SimpleNamespace(finish_reason="STOP")]), (None, [])):
            with self.subTest(text=text):
                self.client.models.generate_content.return_value = SimpleNamespace(text=text, candidates=candidates)
                with self.assertRaises(GeminiError):
                    GeminiClient().translate("Translate")

    def test_http_errors_are_actionable_and_do_not_echo_provider_details(self):
        for code, reason, message in (
            (400, "API_KEY_INVALID", "API key"),
            (401, "UNAUTHENTICATED", "API key"),
            (403, "PERMISSION_DENIED", "API key"),
            (429, "RESOURCE_EXHAUSTED", "quota"),
            (404, "NOT_FOUND", "Model"),
            (503, "UNAVAILABLE", "quá tải"),
            (400, "INVALID_ARGUMENT", "yêu cầu"),
            (500, "INTERNAL", "Kiểm tra mạng"),
        ):
            with self.subTest(code=code, reason=reason):
                self.client.reset_mock()
                self.client.models.generate_content.side_effect = ClientError(code, {
                    "error": {"message": self.test_key, "status": reason, "details": [{"reason": reason}]},
                })
                with self.assertRaises(GeminiError) as caught:
                    GeminiClient().translate("Translate")
                self.assertIn(message, str(caught.exception))
                self.assertNotIn(self.test_key, str(caught.exception))
                self.assertTrue(caught.exception.__suppress_context__)
                self.client.close.assert_called_once()

    def test_configuration_errors_do_not_send_a_request(self):
        with patch.object(settings, "GEMINI_API_KEY", ""), patch.dict(os.environ, {"GEMINI_API_KEY": ""}):
            with self.assertRaises(GeminiError):
                GeminiClient().translate("Translate")
        for timeout in (0, -1, 301, float("nan"), float("inf"), "invalid", True):
            with self.subTest(timeout=timeout):
                with self.assertRaises(GeminiError):
                    GeminiClient(timeout=timeout)
        with self.assertRaises(GeminiError):
            GeminiClient(model=" ")
        with self.assertRaises(GeminiError):
            GeminiClient().translate(" ")
        from google import genai
        genai.Client.assert_not_called()

    def test_network_constructor_and_close_failures_cannot_expose_secrets(self):
        self.client.models.generate_content.side_effect = TimeoutError(self.test_key)
        self.client.close.side_effect = RuntimeError(self.test_key)
        with self.assertRaises(GeminiError) as caught:
            GeminiClient().translate("Translate")
        self.assertNotIn(self.test_key, str(caught.exception))
        self.client.close.assert_called_once()
        with patch("google.genai.Client", side_effect=ValueError(self.test_key)):
            with self.assertRaises(GeminiError) as caught:
                GeminiClient().translate("Translate")
        self.assertNotIn(self.test_key, str(caught.exception))


if __name__ == "__main__":
    unittest.main()
