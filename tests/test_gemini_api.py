"""Gemini connection diagnostics: no network and no real credentials."""
import json
import os
import threading
import unittest
from unittest.mock import Mock, patch

from google.genai.errors import ClientError

import main


class GeminiConnectionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.test_key = "fixture-private-key-do-not-return"
        self.client = Mock()
        self.client.models.generate_content.return_value = Mock(text=self.test_key)
        self.patches = [
            patch.object(main.settings, "GEMINI_API_KEY", self.test_key),
            patch.object(main.settings, "GEMINI_MODEL", "gemini-fixture"),
            patch("google.genai.Client", return_value=self.client),
        ]
        for item in self.patches:
            item.start()

    async def asyncTearDown(self):
        for item in reversed(self.patches):
            item.stop()

    async def test_success_returns_metadata_only_and_releases_client(self):
        result = await main.test_gemini_connection()
        self.assertEqual(set(result), {"ok", "model", "latency_ms"})
        self.assertTrue(result["ok"])
        self.assertEqual(result["model"], "gemini-fixture")
        self.assertGreaterEqual(result["latency_ms"], 0)
        self.assertNotIn(self.test_key, json.dumps(result))
        from google import genai
        genai.Client.assert_called_once_with(
            api_key=self.test_key,
            http_options={"timeout": 20000, "retry_options": {"attempts": 1}},
        )
        self.client.close.assert_called_once()

    async def test_sdk_request_runs_outside_event_loop_thread(self):
        event_loop_thread = threading.get_ident()
        request_threads = []

        def generate(**kwargs):
            request_threads.append(threading.get_ident())

        self.client.models.generate_content.side_effect = generate
        result = await main.test_gemini_connection()
        self.assertTrue(result["ok"])
        self.assertEqual(len(request_threads), 1)
        self.assertNotEqual(request_threads[0], event_loop_thread)

    async def test_missing_key_does_not_create_client(self):
        with patch.object(main.settings, "GEMINI_API_KEY", ""), \
                patch.dict(os.environ, {"GEMINI_API_KEY": ""}):
            result = await main.test_gemini_connection()
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "missing_key")
        from google import genai
        genai.Client.assert_not_called()

    async def test_api_errors_are_classified_without_echoing_private_data(self):
        cases = [
            (400, "API_KEY_INVALID", "authentication"),
            (401, "UNAUTHENTICATED", "authentication"),
            (403, "PERMISSION_DENIED", "authentication"),
            (429, "RESOURCE_EXHAUSTED", "quota"),
            (404, "NOT_FOUND", "model_unavailable"),
            (503, "UNAVAILABLE", "busy"),
            (400, "INVALID_ARGUMENT", "connection_failed"),
            (500, "INTERNAL", "connection_failed"),
        ]
        for code, reason, expected in cases:
            with self.subTest(code=code, reason=reason):
                self.client.reset_mock()
                self.client.models.generate_content.side_effect = ClientError(code, {
                    "error": {"message": self.test_key, "status": reason,
                              "details": [{"reason": reason}]},
                })
                result = await main.test_gemini_connection()
                self.assertFalse(result["ok"])
                self.assertEqual(result["error_code"], expected)
                self.assertNotIn(self.test_key, json.dumps(result))
                self.client.close.assert_called_once()

    async def test_network_and_constructor_errors_do_not_echo_private_data(self):
        self.client.models.generate_content.side_effect = TimeoutError(self.test_key)
        result = await main.test_gemini_connection()
        self.assertEqual(result["error_code"], "connection_failed")
        self.assertNotIn(self.test_key, json.dumps(result))
        self.client.close.assert_called_once()
        with patch("google.genai.Client", side_effect=ValueError(self.test_key)):
            result = await main.test_gemini_connection()
        self.assertEqual(result["error_code"], "connection_failed")
        self.assertNotIn(self.test_key, json.dumps(result))


if __name__ == "__main__":
    unittest.main()
