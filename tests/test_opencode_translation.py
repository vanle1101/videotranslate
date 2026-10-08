"""Offline checks for provider isolation and OpenCode translation contracts."""

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.engines.translation.semantic_translator import SemanticTranslator, PacingReviewRejected
from core.translator import VideoTranslator
from config import settings


class OpenCodeTranslationTests(unittest.TestCase):
    def setUp(self):
        self.translator = SemanticTranslator("opencode")
        self.segment = {"id": 2, "start": 1.0, "end": 3.0, "text": "你好", "speaker": "A"}
        self.translation = {"literal_vi": "Xin chào", "natural_vi": "Chào bạn", "final_vi": "Chào bạn"}
        self.context = {"theme": "Lời chào", "terms": [], "pronouns": "mình - bạn"}

    def test_batch_parses_fenced_json_and_preserves_metadata(self):
        responses = [json.dumps(self.context), "Đây là kết quả:\n```JSON\n" + json.dumps({"results": [{"id": "2", **self.translation}]}) + "\n```"]
        with patch.object(self.translator, "_opencode_request", side_effect=responses) as request, \
                patch.object(self.translator, "_fallback_translate", side_effect=AssertionError("Fallback forbidden")):
            result = self.translator.translate([self.segment])
        self.assertEqual(result[0]["vi_text"], "Chào bạn")
        self.assertEqual(result[0]["speaker"], "A")
        self.assertEqual(result[0]["text_zh"], "你好")
        self.assertNotIn("vi_text", self.segment)
        self.assertEqual(request.call_count, 2)
        self.assertIn("mình - bạn", request.call_args.args[0])

    def test_batch_translation_request_keeps_speaker_and_addressee_metadata(self):
        segment = {**self.segment, "speaker_id": "child", "addressee_id": "mother"}
        responses = [json.dumps(self.context), json.dumps({"results": [{"id": "2", **self.translation}]})]
        with patch.object(self.translator, "_opencode_request", side_effect=responses) as request:
            self.translator.translate([segment])
        request_payload = json.loads(request.call_args.args[1].split("Dịch từng câu tiếng Trung", 1)[-1]
                                     if "Dịch từng câu tiếng Trung" in request.call_args.args[1]
                                     else request.call_args.args[1].split("Danh sách các câu thoại cần chuyển ngữ:\n", 1)[1])
        self.assertEqual(request_payload[0]["speaker_id"], "child")
        self.assertEqual(request_payload[0]["addressee_id"], "mother")

    def test_single_keeps_short_dialogue_context_and_passes_budget(self):
        context = [{"zh": f"previous-{i}", "vi": f"bản dịch {i}"} for i in range(7)]
        with patch.object(self.translator, "_opencode_request", return_value=json.dumps(self.translation)) as request:
            result = self.translator.translate_single_segment("你好", 2.0, context, "anh - em")
        self.assertEqual(result, self.translation)
        instruction = request.call_args.args[0]
        self.assertIn("previous-1", instruction)
        self.assertIn("previous-2", instruction)
        self.assertIn("previous-6", instruction)
        self.assertIn("anh - em", instruction)
        self.assertIn("2.0 giây", instruction)
        self.assertIn("đo âm thanh thật", instruction)
        self.assertNotIn("tối đa 6 từ", instruction)

    def test_selected_provider_uses_client_without_other_keys(self):
        with patch("core.engines.translation.opencode_client.OpenCodeZenClient") as client, \
                patch.object(self.translator, "_api_keys", side_effect=AssertionError("Other providers forbidden")), \
                patch.object(self.translator, "_fallback_translate", side_effect=AssertionError("Fallback forbidden")):
            client.return_value.translate.return_value = json.dumps(self.translation)
            result = self.translator.translate_single_segment("你好", 3.0)
        self.assertEqual(result["final_vi"], "Chào bạn")
        client.return_value.translate.assert_called_once()
        self.assertEqual(client.call_args.kwargs["max_retries"], 1)
        self.assertEqual(client.call_args.kwargs["timeout"], settings.OPENCODE_TIMEOUT)

    def test_client_failure_is_actionable_and_does_not_leak_details(self):
        with patch("core.engines.translation.opencode_client.OpenCodeZenClient") as client, \
                patch.object(self.translator, "_fallback_translate", side_effect=AssertionError("Fallback forbidden")):
            client.return_value.translate.side_effect = RuntimeError("secret-token-should-not-appear")
            with self.assertRaisesRegex(RuntimeError, "Hãy thử lại") as caught:
                self.translator.translate_single_segment("你好", 3.0)
        self.assertNotIn("secret-token", str(caught.exception))
        self.assertIsNone(caught.exception.__cause__)

    def test_safe_client_failure_preserves_diagnosis(self):
        from core.engines.translation.opencode_client import OpenCodeConfigurationError
        with patch("core.engines.translation.opencode_client.OpenCodeZenClient") as client:
            client.return_value.translate.side_effect = OpenCodeConfigurationError("Không tìm thấy OpenCode CLI")
            with self.assertRaisesRegex(RuntimeError, "Không tìm thấy OpenCode CLI.*Hãy thử lại"):
                self.translator.translate_single_segment("你好", 3.0)

    def test_numeric_or_latin_source_can_legitimately_be_unchanged(self):
        for text in ("2026", "TikTok"):
            translated = {key: text for key in self.translation}
            with self.subTest(text=text), patch.object(self.translator, "_opencode_request", return_value=json.dumps(translated)):
                self.assertEqual(self.translator.translate_single_segment(text, 2), translated)

    def test_missing_or_invalid_translation_never_becomes_source_text(self):
        invalid = [
            {}, {"results": []}, {"results": [{"id": 2, "final_vi": ""}]},
            {"results": [{"id": 2, **self.translation, "final_vi": "你好"}]},
            {"results": [{"id": 2, **self.translation, "final_vi": "你好！"}]},
            {"results": [{"id": 2, **self.translation, "final_vi": "Xin chào 你好"}]},
            {"results": [{"id": 2, **self.translation, "natural_vi": 42}]},
            {"results": [{"id": 2.5, **self.translation}]},
            {"results": [{"id": True, **self.translation}]},
            {"results": [{"id": 3, **self.translation}]},
            {"results": [{"id": 2, **self.translation}, {"id": 2, **self.translation}]},
        ]
        payload = [{"id": 2, "text_zh": "你好"}]
        for data in invalid:
            with self.subTest(data=data), self.assertRaisesRegex(RuntimeError, "Hãy thử lại"):
                self.translator._parse_opencode_results(json.dumps(data), payload)
        with self.assertRaisesRegex(RuntimeError, "JSON không hợp lệ"):
            self.translator._parse_opencode_results("invalid JSON", payload)

    def test_bad_context_does_not_trigger_translation_or_fallback(self):
        with patch.object(self.translator, "_opencode_request", return_value='{"theme": ""}') as request, \
                patch.object(self.translator, "_fallback_translate", side_effect=AssertionError("Fallback forbidden")):
            with self.assertRaisesRegex(RuntimeError, "Hãy thử lại"):
                self.translator.translate([self.segment])
        self.assertEqual(request.call_count, 1)

    def test_empty_input_does_not_request(self):
        with patch.object(self.translator, "_opencode_request", side_effect=AssertionError("No request expected")):
            self.assertEqual(self.translator.translate([]), [])
            self.assertEqual(self.translator.translate_single_segment("  ", 2), {"literal_vi": "", "natural_vi": "", "final_vi": ""})

    def test_candidate_only_address_requires_independent_preservation_proof(self):
        draft, candidate = "Đi trước đi.", "Em đi trước nhé."
        proposed = {"literal_vi": candidate, "natural_vi": candidate, "final_vi": candidate,
                    "needs_review": False, "review_reason": ""}
        for address_preserved in (False, None):
            verdict = {"equivalent": True, "natural": True,
                       "reason": "Câu trôi chảy nhưng chưa xác định vai em."}
            if address_preserved is not None:
                verdict["address_preserved"] = address_preserved
            responses = [json.dumps(proposed), json.dumps(verdict)]
            with self.subTest(address_preserved=address_preserved), \
                    patch.object(self.translator, "_opencode_request", side_effect=responses) as request, \
                    self.assertRaises(PacingReviewRejected):
                self.translator.rewrite_for_pacing("你先走吧", draft, 1.0)
            self.assertEqual(request.call_count, 2)

    def test_legacy_facade_honors_selected_provider(self):
        with patch("core.translator.SemanticTranslator") as engine:
            engine.return_value.translate.return_value = [{**self.segment, "vi_text": "Xin chào"}]
            result = VideoTranslator("opencode").translate_segments([self.segment])
        engine.assert_called_once_with(provider="opencode")
        engine.return_value.translate.assert_called_once_with([self.segment])
        self.assertEqual(result[0]["vi_text"], "Xin chào")

    def test_openrouter_route_uses_only_openrouter_key_and_preserves_contract(self):
        translator = SemanticTranslator("openrouter-free")
        with patch("core.engines.translation.openrouter_client.OpenRouterFreeClient") as client, \
                patch("core.engines.translation.opencode_client.OpenCodeZenClient", side_effect=AssertionError("Zen forbidden")), \
                patch.object(translator, "_fallback_translate", side_effect=AssertionError("Fallback forbidden")):
            client.return_value.translate.return_value = json.dumps(self.translation)
            result = translator.translate_single_segment("你好", 3)
        self.assertEqual(result, self.translation)
        client.return_value.translate.assert_called_once()

    def test_openrouter_rate_limit_does_not_switch_to_another_provider(self):
        from core.engines.translation.openrouter_client import OpenRouterRequestError
        translator = SemanticTranslator("openrouter-free")
        with patch("core.engines.translation.openrouter_client.OpenRouterFreeClient") as client, \
                patch.object(translator, "_fallback_translate", side_effect=AssertionError("Fallback forbidden")):
            client.return_value.translate.side_effect = OpenRouterRequestError("OpenRouter rate limit")
            with self.assertRaisesRegex(RuntimeError, "OpenRouter rate limit"):
                translator.translate_single_segment("你好", 3)

    def test_info_recognizes_local_opencode_credentials_without_exposing_key(self):
        for key in (None, "local-key-never-exposed"):
            with self.subTest(configured=bool(key)), patch("core.engines.translation.opencode_client.resolve_api_key", return_value=key):
                info = self.translator.get_info()
            self.assertEqual(info["has_llm_key"], bool(key))
            self.assertNotIn("local-key-never-exposed", str(info))


if __name__ == "__main__":
    unittest.main()
