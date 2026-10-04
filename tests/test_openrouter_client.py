"""Offline tests of free-only OpenRouter calls and saved credential reuse."""

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, mock_open, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests
from config import settings
from core.engines.translation.openrouter_client import (
    CHAT_COMPLETIONS_URL, DEFAULT_FREE_MODEL, OpenRouterClientError,
    OpenRouterFreeClient, resolve_openrouter_key,
)


MODULE = "core.engines.translation.openrouter_client"


class OpenRouterKeyTests(unittest.TestCase):
    def test_reuses_only_saved_openrouter_api_credentials(self):
        providers = {"opencode": {"type": "api", "key": "wrong-provider"},
                     "OpenRouter-Free": {"type": "api", "key": "free-provider-key"},
                     "openrouter": {"type": "api", "key": "standard-key"}}
        with patch.object(settings, "OPENROUTER_API_KEY", ""), patch.dict("os.environ", {"OPENROUTER_API_KEY": ""}), \
                patch("pathlib.Path.open", mock_open(read_data=json.dumps(providers))):
            self.assertEqual(resolve_openrouter_key(), "free-provider-key")
        del providers["OpenRouter-Free"]
        with patch.object(settings, "OPENROUTER_API_KEY", ""), patch.dict("os.environ", {"OPENROUTER_API_KEY": ""}), \
                patch("pathlib.Path.open", mock_open(read_data=json.dumps(providers))):
            self.assertEqual(resolve_openrouter_key(), "standard-key")

    def test_setting_and_environment_override_auth_file(self):
        with patch.object(settings, "OPENROUTER_API_KEY", "configured"), patch.dict("os.environ", {"OPENROUTER_API_KEY": "environment"}), \
                patch("pathlib.Path.open", side_effect=AssertionError("No auth read")):
            self.assertEqual(resolve_openrouter_key(), "configured")
        with patch.object(settings, "OPENROUTER_API_KEY", ""), patch.dict("os.environ", {"OPENROUTER_API_KEY": "environment"}), \
                patch("pathlib.Path.open", side_effect=AssertionError("No auth read")):
            self.assertEqual(resolve_openrouter_key(), "environment")

    def test_wrong_or_corrupt_auth_does_not_use_other_provider(self):
        for text in ('{"opencode":{"type":"api","key":"wrong"}}', "bad-json", '{"OpenRouter-Free":{"type":"oauth","key":"wrong"}}'):
            with self.subTest(text=text), patch.object(settings, "OPENROUTER_API_KEY", ""), \
                    patch.dict("os.environ", {"OPENROUTER_API_KEY": ""}), patch("pathlib.Path.open", mock_open(read_data=text)):
                self.assertIsNone(resolve_openrouter_key())


class OpenRouterRequestTests(unittest.TestCase):
    def setUp(self):
        self.key_patch = patch(MODULE + ".resolve_openrouter_key", return_value="private-test-key")
        self.key_patch.start()
        self.addCleanup(self.key_patch.stop)

    def response(self, data=None, status=200):
        response = Mock(status_code=status)
        response.json.return_value = data if data is not None else {"choices": [{"message": {"content": "Xin chào"}, "finish_reason": "stop"}]}
        return response

    def test_request_uses_official_endpoint_free_model_and_utf8(self):
        response = self.response()
        response.json.side_effect = lambda: self.assertEqual(response.encoding, "utf-8") or {"choices": [{"message": {"content": "Xin chào"}}]}
        with patch(MODULE + ".requests.post", return_value=response) as post:
            result = OpenRouterFreeClient().translate("你好", system="Dịch sang tiếng Việt")
        self.assertEqual(result, "Xin chào")
        args, kwargs = post.call_args
        self.assertEqual(args, (CHAT_COMPLETIONS_URL,))
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer private-test-key")
        self.assertEqual(kwargs["json"]["model"], DEFAULT_FREE_MODEL)
        self.assertEqual(kwargs["json"]["reasoning"], {"enabled": False})
        self.assertGreaterEqual(kwargs["json"]["max_tokens"], 1024)
        self.assertNotIn("response_format", kwargs["json"])
        self.assertFalse(kwargs["allow_redirects"])
        response.close.assert_called_once()

    def test_paid_or_malformed_models_never_request(self):
        with patch(MODULE + ".requests.post") as post:
            for model in ("openai/gpt-4o", "openrouter/auto", ":free", "a/b:free/paid", "a/b:free\nsecret"):
                with self.subTest(model=model), self.assertRaises(OpenRouterClientError):
                    OpenRouterFreeClient(model=model).translate("你好")
        post.assert_not_called()

    def test_status_errors_are_sanitized_and_never_retry_or_fallback(self):
        for status in (301, 400, 401, 402, 403, 404, 429, 500):
            response = self.response({"error": {"message": "private-test-key raw transcript"}}, status=status)
            with self.subTest(status=status), patch(MODULE + ".requests.post", return_value=response) as post:
                with self.assertRaises(OpenRouterClientError) as error:
                    OpenRouterFreeClient().translate("sensitive transcript")
                self.assertNotIn("private-test-key", str(error.exception))
                self.assertNotIn("transcript", str(error.exception))
                post.assert_called_once()
                response.close.assert_called_once()

    def test_network_errors_do_not_expose_request_details(self):
        for error in (requests.Timeout("private-test-key"), requests.ConnectionError("private-test-key")):
            with self.subTest(error=type(error)), patch(MODULE + ".requests.post", side_effect=error) as post:
                with self.assertRaises(OpenRouterClientError) as caught:
                    OpenRouterFreeClient().translate("你好")
                self.assertNotIn("private-test-key", str(caught.exception))
                self.assertIsNone(caught.exception.__cause__)
                post.assert_called_once()

    def test_malformed_empty_and_truncated_results_fail(self):
        responses = [self.response({}), self.response({"choices": []}),
                     self.response({"choices": [{"message": {"content": " "}}]}),
                     self.response({"choices": [{"message": {"content": "partial"}, "finish_reason": "length"}]})]
        malformed = self.response()
        malformed.json.side_effect = ValueError("private-test-key")
        responses.append(malformed)
        for response in responses:
            with self.subTest(response=response), patch(MODULE + ".requests.post", return_value=response):
                with self.assertRaises(OpenRouterClientError) as caught:
                    OpenRouterFreeClient().translate("你好")
                self.assertNotIn("private-test-key", str(caught.exception))
                response.close.assert_called_once()

    def test_missing_key_and_invalid_budget_or_timeout_never_request(self):
        with patch(MODULE + ".requests.post") as post:
            with patch(MODULE + ".resolve_openrouter_key", return_value=None), self.assertRaises(OpenRouterClientError):
                OpenRouterFreeClient().translate("你好")
            with self.assertRaises(OpenRouterClientError):
                OpenRouterFreeClient().translate("你好", max_tokens=32)
            for timeout in (0, -1, 301, float("nan"), "bad"):
                with self.subTest(timeout=timeout), self.assertRaises(OpenRouterClientError):
                    OpenRouterFreeClient(timeout=timeout)
        post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
