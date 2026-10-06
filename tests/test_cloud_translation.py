"""Gemini and Muse share strict translation validation without implicit fallback."""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from core.engines.translation.gemini_client import GeminiError
from core.engines.translation.semantic_translator import SemanticTranslator
from core.services.muse_service import MuseError


class CloudTranslationTests(unittest.TestCase):
    PROVIDERS = ("gemini", "muse")

    def setUp(self):
        self.translation = {"literal_vi": "Xin chào", "natural_vi": "Chào bạn", "final_vi": "Chào bạn"}
        self.context = {"theme": "Lời chào", "terms": [], "pronouns": "mình - bạn"}
        self.segment = {"id": "2", "start": 1.0, "end": 3.0, "text": "你好", "speaker": "A"}

    def test_single_dispatch_sends_only_to_selected_provider(self):
        for provider in self.PROVIDERS:
            translator = SemanticTranslator(provider)
            with self.subTest(provider=provider), \
                    patch("core.engines.translation.gemini_client.GeminiClient") as gemini, \
                    patch("core.services.muse_service.muse_service.translate") as muse, \
                    patch("core.engines.translation.opencode_client.OpenCodeZenClient") as opencode, \
                    patch("core.engines.translation.openrouter_client.OpenRouterFreeClient") as openrouter, \
                    patch.object(translator, "_api_keys", side_effect=AssertionError("Legacy keys forbidden")), \
                    patch.object(translator, "_fallback_translate", side_effect=AssertionError("Fallback forbidden")):
                selected = gemini.return_value.translate if provider == "gemini" else muse
                selected.return_value = json.dumps(self.translation)
                result = translator.translate_single_segment("你好", 2.0, [{"zh": "之前", "vi": "Trước đó"}], "anh - em")
                self.assertEqual(result, self.translation)
                selected.assert_called_once()
                self.assertEqual(selected.call_args.args[0], "Dịch câu: 你好")
                self.assertIn("anh - em", selected.call_args.kwargs["system"])
                self.assertIn("Trước đó", selected.call_args.kwargs["system"])
                if provider == "gemini":
                    muse.assert_not_called()
                else:
                    gemini.assert_not_called()
                opencode.assert_not_called()
                openrouter.assert_not_called()

    def test_batch_keeps_ids_order_metadata_and_original_segments(self):
        segments = [self.segment, {**self.segment, "id": 4, "speaker": "B", "start": 4.0, "end": 6.0}]
        for provider in self.PROVIDERS:
            translator = SemanticTranslator(provider)
            responses = [json.dumps(self.context), json.dumps({"results": [
                {"id": 4, **self.translation}, {"id": "2", **self.translation},
            ]})]
            with self.subTest(provider=provider), \
                    patch.object(translator, "_opencode_request", side_effect=responses) as request, \
                    patch.object(translator, "_fallback_translate", side_effect=AssertionError("Fallback forbidden")):
                result = translator.translate(segments)
            self.assertEqual([item["id"] for item in result], ["2", 4])
            self.assertEqual([item["speaker"] for item in result], ["A", "B"])
            self.assertEqual(result[0]["start"], 1.0)
            self.assertEqual(result[0]["text_zh"], "你好")
            self.assertEqual(result[0]["vi_text"], "Chào bạn")
            self.assertNotIn("vi_text", self.segment)
            self.assertEqual(request.call_count, 2)
            self.assertIn("mình - bạn", request.call_args.args[0])

    def test_provider_failures_stop_without_google_fallback(self):
        cases = (("gemini", "core.engines.translation.gemini_client.GeminiClient", GeminiError("Gemini hết quota")),
                 ("muse", "core.services.muse_service.muse_service.translate", MuseError("Muse cần đăng nhập")))
        for provider, target, safe_error in cases:
            for error in (safe_error, ValueError("private-provider-response-do-not-echo")):
                translator = SemanticTranslator(provider)
                with self.subTest(provider=provider, error_type=type(error).__name__), \
                        patch(target) as client, \
                        patch.object(translator, "_fallback_translate", side_effect=AssertionError("Fallback forbidden")):
                    call = client.return_value.translate if provider == "gemini" else client
                    call.side_effect = error
                    with self.assertRaises(RuntimeError) as caught:
                        translator.translate_single_segment("你好", 2)
                message = str(caught.exception)
                self.assertIn("Hãy thử lại", message)
                self.assertNotIn("private-provider-response", message)
                if error is safe_error:
                    self.assertIn(str(safe_error), message)
                self.assertTrue(caught.exception.__suppress_context__)

    def test_invalid_batch_results_never_become_source_text(self):
        invalid = [
            {"results": []},
            {"results": [{"id": 2, **self.translation, "final_vi": "你好"}]},
            {"results": [{"id": 2, **self.translation, "natural_vi": ""}]},
            {"results": [{"id": 3, **self.translation}]},
            {"results": [{"id": 2, **self.translation}, {"id": 2, **self.translation}]},
            {"results": [{"id": True, **self.translation}]},
        ]
        for provider in self.PROVIDERS:
            translator = SemanticTranslator(provider)
            for data in invalid:
                with self.subTest(provider=provider, data=data), \
                        patch.object(translator, "_opencode_request", side_effect=[json.dumps(self.context), json.dumps(data)]), \
                        patch.object(translator, "_fallback_translate", side_effect=AssertionError("Fallback forbidden")):
                    with self.assertRaisesRegex(RuntimeError, "Hãy thử lại"):
                        translator.translate([self.segment])

    def test_bad_context_or_single_json_is_rejected_without_echoing_response(self):
        for provider in self.PROVIDERS:
            translator = SemanticTranslator(provider)
            with self.subTest(provider=provider), \
                    patch.object(translator, "_opencode_request", return_value='{"theme": ""}') as request:
                with self.assertRaises(RuntimeError):
                    translator.translate([self.segment])
                request.assert_called_once()
            with patch.object(translator, "_opencode_request", return_value="private-response-that-is-not-json"):
                with self.assertRaises(RuntimeError) as caught:
                    translator.translate_single_segment("你好", 2)
                self.assertNotIn("private-response", str(caught.exception))
                self.assertIn("JSON không hợp lệ", str(caught.exception))

    def test_muse_info_does_not_start_or_probe_browser(self):
        with patch("core.services.muse_service.muse_service.status", side_effect=AssertionError("No probe")), \
                patch("core.services.muse_service.muse_service._ensure_started", side_effect=AssertionError("No browser")):
            info = SemanticTranslator("muse").get_info()
        self.assertEqual(info["provider"], "muse")
        self.assertTrue(info["has_llm_key"])

    def test_legacy_provider_invalid_single_response_uses_valid_fallback_not_source(self):
        for provider in ("deepseek", "openai"):
            translator = SemanticTranslator(provider)
            for content in ({}, {**self.translation, "final_vi": "你好。"},
                            {**self.translation, "natural_vi": None}):
                with self.subTest(provider=provider, content=content), \
                        patch.object(translator, "_api_keys", return_value=(None, "test" if provider == "deepseek" else None, "test" if provider == "openai" else None)), \
                        patch("openai.OpenAI") as client, \
                        patch.object(translator, "_fallback_translate", return_value="Chào bạn") as fallback:
                    client.return_value.chat.completions.create.return_value = SimpleNamespace(
                        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(content)))])
                    result = translator.translate_single_segment("你好", 2)
                assert result["final_vi"] == "Chào bạn"
                fallback.assert_called_once_with("你好")

    def test_legacy_batch_rejects_invalid_tiers_and_preserves_numeric_string_ids(self):
        translator = SemanticTranslator("deepseek")
        for result_row in ({"id": 2, **self.translation},
                           {"id": 2, **self.translation, "literal_vi": "你好。"}):
            with self.subTest(result_row=result_row), \
                    patch.object(translator, "_api_keys", return_value=(None, "test", None)), \
                    patch("openai.OpenAI") as client, \
                    patch.object(translator, "_fallback_translate", return_value="Chào bạn") as fallback:
                client.return_value.chat.completions.create.return_value = SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({"results": [result_row]})))])
                result = translator.translate([self.segment])[0]
            assert result["final_vi"] == "Chào bạn"
            assert result["id"] == "2" and result["speaker"] == "A"
            assert "你" not in result["literal_vi"]
            assert fallback.call_count == (1 if "你" in result_row["literal_vi"] else 0)


if __name__ == "__main__":
    unittest.main()
