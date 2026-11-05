import io
import json
import unittest
from unittest.mock import patch

from config import settings
from core.engines.translation.ollama_client import OllamaClient, OllamaClientError
from core.engines.translation.semantic_translator import SemanticTranslator, _compact_local_dialogue_context


class OllamaClientTests(unittest.TestCase):
    def test_posts_json_to_local_model_without_retry(self):
        response = io.BytesIO(json.dumps({"message": {"content": '{"final_vi":"Xin chào"}'},
            "load_duration": 100_000_000, "prompt_eval_count": 120,
            "prompt_eval_duration": 500_000_000, "eval_count": 12,
            "eval_duration": 1_000_000_000, "total_duration": 1_600_000_000}).encode())
        response.status = 200
        client = OllamaClient("qwen2.5:7b", "http://127.0.0.1:11434", timeout=10)
        with patch("core.engines.translation.ollama_client.urlopen", return_value=response) as open_url:
            result = client.translate("Dịch câu", system="Chỉ trả JSON", max_tokens=512)
        request = open_url.call_args.args[0]
        body = json.loads(request.data)
        self.assertEqual(result, '{"final_vi":"Xin chào"}')
        self.assertEqual(request.full_url, "http://127.0.0.1:11434/api/chat")
        self.assertEqual(body["model"], "qwen2.5:7b")
        self.assertEqual(body["messages"][0], {"role": "system", "content": "Chỉ trả JSON"})
        self.assertEqual(body["format"], "json")
        self.assertFalse(body["stream"])
        self.assertEqual(client.last_metrics, {"load_duration": 100_000_000,
            "prompt_eval_count": 120, "prompt_eval_duration": 500_000_000,
            "eval_count": 12, "eval_duration": 1_000_000_000,
            "total_duration": 1_600_000_000})
        open_url.assert_called_once()

    def test_incomplete_json_generation_is_reported_for_upstream_batch_split(self):
        response = io.BytesIO(json.dumps({"message": {"content": '{"segments":['},
            "done_reason": "length", "prompt_eval_count": 22, "eval_count": 64,
            "prompt_eval_duration": 800_000_000, "eval_duration": 3_000_000_000,
            "total_duration": 4_000_000_000}).encode())
        response.status = 200
        client = OllamaClient("qwen2.5:7b", "http://127.0.0.1:11434", timeout=10)
        with patch("core.engines.translation.ollama_client.urlopen", return_value=response):
            with self.assertRaises(OllamaClientError) as raised:
                client.translate("Sửa nguồn", max_tokens=512)
        self.assertEqual(raised.exception.code, "provider_incomplete")
        self.assertFalse(raised.exception.retryable)
        self.assertEqual(raised.exception.metrics["eval_count"], 64)
        self.assertEqual(raised.exception.metrics["prompt_eval_count"], 22)

    def test_singleton_batch_uses_validated_plain_text_after_two_truncated_json_attempts(self):
        translator = SemanticTranslator("ollama")
        incomplete = OllamaClientError("truncated", code="provider_incomplete")
        row = {"id": 17, "start": 0., "end": 1., "text_zh": "明天见"}
        with patch.object(translator, "_ollama_text_request", side_effect=[
                incomplete, incomplete, "Mai gặp lại nhé."]) as request:
            result = translator._execute_ollama_text_batch([row], {}, [])
        self.assertEqual(result[17]["final_vi"], "Mai gặp lại nhé.")
        self.assertEqual(request.call_count, 3)
        self.assertEqual(request.call_args.kwargs["response_format"], "text")
        self.assertIn("明天见", request.call_args.args[1])

    def test_singleton_plain_text_after_truncation_cannot_bypass_translation_validation(self):
        translator = SemanticTranslator("ollama")
        incomplete = OllamaClientError("truncated", code="provider_incomplete")
        row = {"id": 17, "start": 0., "end": 1., "text_zh": "明天见"}
        with patch.object(translator, "_ollama_text_request", side_effect=[
                incomplete, incomplete, "明天见"]):
            with self.assertRaises(Exception) as raised:
                translator._execute_ollama_text_batch([row], {}, [])
        self.assertEqual(getattr(raised.exception, "code", None), "untranslated_text")

    def test_structured_json_schema_and_qwen3_resource_options_are_passed_to_ollama(self):
        response = io.BytesIO(json.dumps({"message": {"content": '{"results":[]}'}}).encode())
        response.status = 200
        client = OllamaClient("qwen3:4b", "http://127.0.0.1:11434", timeout=10)
        schema = {"type": "object", "properties": {"results": {"type": "array"}}}
        with patch("core.engines.translation.ollama_client.urlopen", return_value=response) as open_url:
            client.translate("Dịch lô", system="JSON", max_tokens=128,
                             response_format=schema, temperature=0.0)
        body = json.loads(open_url.call_args.args[0].data)
        self.assertEqual(body["format"], schema)
        self.assertIs(body["think"], False)
        self.assertEqual(body["options"]["num_thread"], 2)
        self.assertEqual(body["options"]["num_ctx"], 2048)

    def test_rejects_non_loopback_endpoint(self):
        for url in ("https://127.0.0.1:11434", "http://example.com:11434", "http://127.0.0.1:5000"):
            with self.subTest(url=url), self.assertRaises(OllamaClientError):
                OllamaClient("qwen2.5:7b", url)

    def test_semantic_translation_uses_local_provider_without_remote_fallback(self):
        payload = {"literal_vi": "Xin chào", "natural_vi": "Chào bạn",
                   "final_vi": "Chào bạn", "needs_review": False, "review_reason": ""}
        translator = SemanticTranslator("ollama")
        with patch("core.engines.translation.ollama_client.OllamaClient.translate",
                   return_value=json.dumps({"final_vi": "Chào bạn"})) as local, \
             patch.object(translator, "_fallback_translate", side_effect=AssertionError("remote fallback forbidden")):
            result = translator.translate_single_segment("你好", 2.0)
        self.assertEqual(result["final_vi"], "Chào bạn")
        local.assert_called_once()
        self.assertEqual(local.call_args.kwargs["response_format"]["required"], ["final_vi"])

    def test_ollama_translation_batches_allow_twelve_targets_but_keep_other_providers_at_eight(self):
        rows = [{"id": index, "start": float(index), "end": index + 1.,
                 "text_zh": f"源句{index}"} for index in range(16)]
        ollama = SemanticTranslator("ollama")
        calls = []
        def translate(payload, _context, _neighbors):
            calls.append(len(payload))
            return {row["id"]: {"final_vi": f"Lời {row['id']}."} for row in payload}
        with patch.object(ollama, "_execute_translation_batch", side_effect=translate):
            result = ollama._execute_3tier_translation(rows, {}, source_context=rows)
        self.assertEqual(calls, [12, 4])
        self.assertEqual(set(result), set(range(16)))

        remote = SemanticTranslator("gemini")
        with patch.object(remote, "_execute_translation_batch", wraps=remote._execute_translation_batch):
            with self.assertRaises(ValueError):
                remote._execute_translation_batch(rows, {}, [])

    def test_local_translation_context_keeps_nearby_turns_without_audit_payload(self):
        rows = [{"id": index, "text_zh": f"源句{index}", "final_vi": f"参考译文{index}",
                 "speaker_evidence": {"large": "x" * 5000},
                 "verification": {"status": "manual", "large": "y" * 5000}}
                for index in range(20)]
        rows[10]["is_focus"] = True
        context = _compact_local_dialogue_context(rows)
        decoded = json.loads(context)
        self.assertEqual([row["id"] for row in decoded], [7, 8, 9])
        self.assertLessEqual(len(context), 900)
        self.assertTrue(all(set(row) <= {"id", "zh", "vi_draft"} for row in decoded))

    def test_local_single_prompt_is_compact_and_does_not_use_a_canned_translation_example(self):
        translator = SemanticTranslator("ollama")
        prompts = {}
        def request(system, prompt, *, max_tokens, response_format="text"):
            prompts.update(system=system, prompt=prompt, max_tokens=max_tokens,
                           response_format=response_format)
            return json.dumps({"final_vi": "Tiểu Nguyên lên chức anh rồi."}, ensure_ascii=False)
        context = [{"id": index, "text_zh": f"源句{index}", "final_vi": f"Lời Việt {index}",
                    "speaker_evidence": {"large": "x" * 5000},
                    "verification": {"status": "manual"}}
                   for index in range(20)]
        context[10]["is_focus"] = True
        with patch.object(translator, "_ollama_text_request", side_effect=request):
            result = translator.translate_single_segment("小元是哥哥了", 2.0, rolling_context=context)
        self.assertEqual(result["final_vi"], "Tiểu Nguyên lên chức anh rồi.")
        self.assertLess(len(prompts["system"]), 800)
        self.assertLess(len(prompts["prompt"]), 1100)
        self.assertIn("Không tự đoán quan hệ/xưng hô", prompts["system"])
        self.assertIn("không phải thay đổi tuổi tác", prompts["prompt"])
        self.assertNotIn("lên chức anh/đã có em", prompts["system"] + prompts["prompt"])
        self.assertNotIn("明天见", prompts["system"] + prompts["prompt"])

    def test_residual_chinese_gets_one_targeted_local_retry(self):
        translator = SemanticTranslator("ollama")
        row = {"id": 5, "text_zh": "沈安"}
        with patch.object(settings, "OLLAMA_MODEL", "qwen2.5:3b-viet"), \
             patch.object(translator, "_ollama_text_request",
                          side_effect=[json.dumps({"results": [{"id": 5, "final_vi": "沈安"}]}),
                                       json.dumps({"final_vi": "Thẩm An"})]) as local:
            result = translator._execute_ollama_text_batch([row], {}, [])
        self.assertEqual(result[5]["final_vi"], "Thẩm An")
        self.assertEqual(local.call_count, 2)
        self.assertEqual(local.call_args_list[1].kwargs["max_tokens"], 1024)
        self.assertIn("final_vi", local.call_args_list[1].kwargs["response_format"]["properties"])

    def test_failed_structured_repair_gets_one_plain_text_retry_for_only_that_row(self):
        translator = SemanticTranslator("ollama")
        row = {"id": 5, "text_zh": "知道了妈妈"}
        with patch.object(settings, "OLLAMA_MODEL", "qwen3:4b-no-think"), \
             patch.object(translator, "_ollama_text_request", side_effect=[
                 json.dumps({"results": [{"id": 5, "final_vi": "知道了妈妈"}]}),
                 json.dumps({"final_vi": "知道了妈妈"}),
                 "Biết rồi, mẹ ạ.",
             ]) as local:
            result = translator._execute_ollama_text_batch([row], {}, [])
        self.assertEqual(result[5]["final_vi"], "Biết rồi, mẹ ạ.")
        self.assertEqual(local.call_count, 3)
        self.assertEqual(local.call_args_list[2].kwargs["response_format"], "text")
        self.assertIn("知道了妈妈", local.call_args_list[2].args[1])

    def test_plain_text_retry_cannot_bypass_residual_chinese_validator(self):
        translator = SemanticTranslator("ollama")
        row = {"id": 5, "text_zh": "知道了妈妈"}
        with patch.object(settings, "OLLAMA_MODEL", "qwen3:4b-no-think"), \
             patch.object(translator, "_ollama_text_request", side_effect=[
                 json.dumps({"results": [{"id": 5, "final_vi": "知道了妈妈"}]}),
                 json.dumps({"final_vi": "知道了妈妈"}),
                 "知道了妈妈",
             ]) as local:
            result = translator._execute_ollama_text_batch([row], {}, [])
        self.assertNotIn(5, result)
        self.assertEqual(set(result.errors), {5})
        self.assertEqual(local.call_count, 3)

    def test_untranslated_batch_row_keeps_successful_sibling_without_replay(self):
        translator = SemanticTranslator("ollama")
        rows = [
            {"id": 1, "start": 0.0, "end": 1.0, "text_zh": "你好"},
            {"id": 2, "start": 1.0, "end": 2.0, "text_zh": "知道了妈妈"},
        ]
        prompts = []
        def request(_system, prompt, *, max_tokens, response_format="text"):
            prompts.append((prompt, max_tokens, response_format))
            if "results" in response_format.get("properties", {}):
                return json.dumps({"results": [
                    {"id": 1, "final_vi": "Chào bạn."},
                    {"id": 2, "final_vi": "知道了，妈妈。"},
                ]})
            return json.dumps({"final_vi": "Biết rồi, mẹ."})

        with patch.object(settings, "OLLAMA_MODEL", "qwen2.5:3b-viet"), \
             patch.object(translator, "_ollama_text_request", side_effect=request):
            result = translator._execute_3tier_translation(rows, {}, source_context=rows)

        self.assertEqual(set(result), {1, 2})
        self.assertEqual(result[1]["final_vi"], "Chào bạn.")
        self.assertEqual(result[2]["final_vi"], "Biết rồi, mẹ.")
        self.assertFalse(result.errors)
        self.assertEqual(len(prompts), 2)
        self.assertEqual(prompts[1][1], 1024)
        self.assertNotIn('"id": 1', prompts[1][0])

    def test_translategemma_uses_native_text_prompts_and_preserves_batch_identity(self):
        translator = SemanticTranslator("ollama")
        calls = []

        def request(system, prompt, *, max_tokens, response_format):
            calls.append((system, prompt, max_tokens, response_format))
            return ["Tiểu Nguyên đã trở thành anh trai.", "Bố ơi, em bé dễ thương quá!"][len(calls) - 1]

        payload = [{"id": 2, "text_zh": "小元是哥哥了"},
                   {"id": 3, "text_zh": "是啊爸爸他好可爱"}]
        context = {"theme": "Đối thoại gia đình về em bé.",
                   "pronouns": "Giữ đúng vai; X是哥哥了 nghĩa là X lên chức anh.",
                   "terms": [{"src": "小元", "tgt": "Tiểu Nguyên", "note": "Tên riêng."}]}
        with patch.object(settings, "OLLAMA_MODEL", "translategemma:4b"), \
             patch.object(translator, "_ollama_text_request", side_effect=request):
            result = translator._execute_ollama_text_batch(payload, context,
                [{"id": 1, "text_zh": "妹妹出生了"}])

        self.assertEqual([result[index]["final_vi"] for index in (2, 3)],
                         ["Tiểu Nguyên đã trở thành anh trai.", "Bố ơi, em bé dễ thương quá!"])
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(call[3] == "text" for call in calls))
        self.assertIn("小元是哥哥了", calls[0][1])
        self.assertIn("Tiểu Nguyên", calls[0][1])
        self.assertIn("妹妹出生了", calls[0][1])

    def test_local_batch_prompt_uses_json_ids_and_guards_natural_meaning(self):
        translator = SemanticTranslator("ollama")
        prompts = {}
        def request(system, prompt, *, max_tokens, response_format):
            prompts.update(system=system, prompt=prompt, max_tokens=max_tokens,
                           response_format=response_format)
            return json.dumps({"results": [{"id": 3, "final_vi": "Nhìn em gái đáng yêu chưa kìa!"}]})
        with patch.object(settings, "OLLAMA_MODEL", "qwen2.5:3b-viet"), \
             patch.object(translator, "_ollama_text_request", side_effect=request):
            result = translator._execute_ollama_text_batch([{
                "id": 3, "text_zh": "你看妹妹多可爱啊", "duration": 1.28,
            }], {}, [])
        self.assertEqual(result[3]["final_vi"], "Nhìn em gái đáng yêu chưa kìa!")
        self.assertNotIn("1.3 giây", prompts["prompt"])
        self.assertEqual(prompts["response_format"]["properties"]["results"]["minItems"], 1)
        self.assertEqual(prompts["response_format"]["properties"]["results"]["maxItems"], 1)
        self.assertIn("Không tự đoán xưng hô", prompts["system"])
        self.assertIn("không để sót chữ Hán", prompts["system"])
        self.assertNotIn("Nhìn em gái đáng yêu", prompts["system"] + prompts["prompt"])
        self.assertNotIn("明天见", prompts["system"] + prompts["prompt"])

    def test_local_prompt_does_not_leak_canned_translation_examples_across_ids(self):
        translator = SemanticTranslator("ollama")
        prompts = {}
        payload = [
            {"id": 1, "text_zh": "小元是哥哥了"},
            {"id": 3, "text_zh": "是啊,爸爸,他好可爱"},
            {"id": 7, "text_zh": "孩子还有多久才能出来"},
            {"id": 8, "text_zh": "孩子妈妈特别想她"},
        ]

        def request(system, prompt, *, max_tokens, response_format):
            prompts.update(system=system, prompt=prompt)
            return json.dumps({"results": [
                {"id": row["id"], "final_vi": ("Mẹ nhớ bé lắm" if row["id"] == 8
                                                else f"Bản dịch {row['id']}")} for row in payload
            ]})

        context_info = {
            "theme": "Gia đình đang trao đổi ở bệnh viện và chờ em bé chào đời.",
            "pronouns": "Trong ngữ cảnh sinh nở, 孩子 là em bé; 出来 nói em bé được sinh ra.",
            "terms": [],
        }
        with patch.object(settings, "OLLAMA_MODEL", "qwen3:4b"), \
             patch.object(translator, "_ollama_text_request", side_effect=request):
            result = translator._execute_ollama_text_batch(
                payload, context_info, [{"id": 6, "text_zh": "护士您好"}])

        self.assertEqual(set(result), {1, 3, 7, 8})
        combined_prompt = prompts["system"] + prompts["prompt"]
        self.assertLess(len(prompts["system"]), 900)
        self.assertIn("小元是哥哥了", combined_prompt)
        self.assertIn("孩子还有多久才能出来", combined_prompt)
        self.assertNotIn("Tiểu Nguyên lên chức anh trai rồi", combined_prompt)
        self.assertNotIn("Em bé còn bao lâu nữa mới chào đời", combined_prompt)
        self.assertNotIn("Bé nhà mình thích anh trai lắm nhỉ", combined_prompt)

    def test_local_batch_prompt_uses_name_and_future_day_context(self):
        translator = SemanticTranslator("ollama")
        prompts = {}
        payload = [
            {"id": 1, "text_zh": "我不会起"},
            {"id": 2, "text_zh": "再过三天"},
        ]

        def request(system, prompt, *, max_tokens, response_format):
            prompts["prompt"] = prompt
            return json.dumps({"results": [
                {"id": 1, "final_vi": "Con không biết đặt tên đâu."},
                {"id": 2, "final_vi": "Còn ba ngày nữa."},
            ]})

        with patch.object(settings, "OLLAMA_MODEL", "qwen3:4b"), \
             patch.object(translator, "_ollama_text_request", side_effect=request):
            result = translator._execute_ollama_text_batch(payload, {}, [
                {"id": 0, "text_zh": "你说妹妹叫什么名字好"},
            ])

        self.assertEqual(result[1]["final_vi"], "Con không biết đặt tên đâu.")
        self.assertEqual(result[2]["final_vi"], "Còn ba ngày nữa.")
        hint_text = prompts["prompt"].split("Ghi chú nghĩa chỉ áp dụng cho đúng ID tương ứng; không chép ghi chú vào bản dịch:\n", 1)[1]
        hint_rows, _ = json.JSONDecoder().raw_decode(hint_text)
        hints = {row["id"]: row["meaning"] for row in hint_rows}
        self.assertIn("起名字", hints[1])
        self.assertIn("còn đúng số ngày", hints[2])

    def test_local_batch_adds_transcript_grounded_name_aliases_and_per_id_meaning_hints(self):
        translator = SemanticTranslator("ollama")
        source_context = json.dumps([
            {"id": 1, "text_zh": "小元是哥哥了"},
            {"id": 2, "text_zh": "小圆轻一点"},
            {"id": 3, "text_zh": "小冤"},
        ], ensure_ascii=False)
        context = translator._extract_context_and_glossary(source_context)
        terms = {row["src"]: row["tgt"] for row in context["terms"]}
        self.assertEqual(terms["小圆"], "Tiểu Nguyên")
        self.assertEqual(terms["小冤"], "Tiểu Nguyên")

        payload = [
            {"id": 10, "text_zh": "小冤 妈妈去一下洗手间"},
            {"id": 11, "text_zh": "為什麼你什麼都不做就可以讓父母喜歡"},
            {"id": 12, "text_zh": "你說妹妹叫什麼名字好"},
            {"id": 13, "text_zh": "抓住哥哥的手不放呢"},
            {"id": 14, "text_zh": "知道了妈妈"},
            {"id": 15, "text_zh": "再吵他又要哭了"},
            {"id": 16, "text_zh": "安安 看媽媽的嘴"},
        ]
        prompts = {}

        def request(system, prompt, *, max_tokens, response_format):
            prompts.update(system=system, prompt=prompt)
            return json.dumps({"results": [
                {"id": row["id"], "final_vi": (
                    "Tiểu Nguyên, mẹ đi vệ sinh một lát" if row["id"] == 10 else
                    "An An, nhìn miệng mẹ" if row["id"] == 16 else
                    f"Lời Việt {row['id']}")} for row in payload
            ]})

        with patch.object(settings, "OLLAMA_MODEL", "qwen3:4b"), \
             patch.object(translator, "_ollama_text_request", side_effect=request):
            result = translator._execute_ollama_text_batch(payload, context, [])

        self.assertEqual(set(result), {row["id"] for row in payload})
        self.assertIn("tự đối chiếu từng câu", prompts["system"])
        hint_text = prompts["prompt"].split(
            "Ghi chú nghĩa chỉ áp dụng cho đúng ID tương ứng; không chép ghi chú vào bản dịch:\n", 1)[1]
        hint_rows, _ = json.JSONDecoder().raw_decode(hint_text)
        hints = {row["id"]: row["meaning"] for row in hint_rows}
        self.assertIn("phủ định", hints[11])
        self.assertIn("câu hỏi xin ý kiến", hints[12])
        self.assertIn("nắm tay anh trai", hints[13])
        self.assertIn("người con", hints[14])
        self.assertIn("điều kiện-kết quả", hints[15])
        self.assertIn("miệng", hints[16])
        self.assertIn("người nói là mẹ", hints[10].lower())
        self.assertNotIn("明天见", prompts["system"] + prompts["prompt"])

    def test_local_batch_tracks_chinese_name_and_warm_family_comment(self):
        translator = SemanticTranslator("ollama")
        source_context = json.dumps([
            {"id": 85, "text_zh": "你看哥哥多疼妹妹"},
            {"id": 100, "text_zh": "倩佳你不能作弊啊"},
        ], ensure_ascii=False)
        context = translator._extract_context_and_glossary(source_context)
        assert {row["src"]: row["tgt"] for row in context["terms"]}["倩佳"] == "Thiến Giai"
        payload = [
            {"id": 85, "text_zh": "你看哥哥多疼妹妹"},
            {"id": 100, "text_zh": "倩佳你不能作弊啊"},
        ]
        prompts = {}

        def request(system, prompt, **_kwargs):
            prompts.update(system=system, prompt=prompt)
            return json.dumps({"results": [
                {"id": 85, "final_vi": "Nhìn anh trai thương em gái chưa kìa."},
                {"id": 100, "final_vi": "Thiến Giai, con không được gian lận đâu nhé."},
            ]}, ensure_ascii=False)

        with patch.object(settings, "OLLAMA_MODEL", "qwen3:4b-no-think"), \
             patch.object(translator, "_ollama_text_request", side_effect=request):
            result = translator._execute_ollama_text_batch(payload, context, [])

        assert set(result) == {85, 100}
        assert result[85]["final_vi"] == "Nhìn anh trai thương em gái chưa kìa."
        hint_text = prompts["prompt"].split(
            "Ghi chú nghĩa chỉ áp dụng cho đúng ID tương ứng; không chép ghi chú vào bản dịch:\n", 1)[1]
        hints, _ = json.JSONDecoder().raw_decode(hint_text)
        meanings = {row["id"]: row["meaning"] for row in hints}
        assert "mày/tao" in meanings[85]
        assert "Anh trai thương em gái biết bao" in meanings[85]
        assert "Anh trai thương em gái biết bao" not in meanings[100]
        assert "Thiến Giai" in meanings[100]

    def test_local_single_prompt_carries_verified_name_and_tone_hints(self):
        translator = SemanticTranslator("ollama")
        prompts = {}

        def request(_system, prompt, **_kwargs):
            prompts["prompt"] = prompt
            return json.dumps({"final_vi": "Thiến Giai, con không được gian lận đâu nhé."}, ensure_ascii=False)

        with patch.object(translator, "_ollama_text_request", side_effect=request):
            result = translator.translate_single_segment("倩佳你不能作弊啊", 1.3)
        assert result["final_vi"].startswith("Thiến Giai")
        assert "Thiến Giai" in prompts["prompt"]

    def test_local_single_translation_retries_only_when_generation_is_truncated(self):
        translator = SemanticTranslator("ollama")
        truncated = OllamaClientError("truncated", code="provider_incomplete")
        with patch.object(translator, "_ollama_text_request",
                          side_effect=[truncated, json.dumps({"final_vi": "Con không biết đặt tên đâu."})]) as request:
            result = translator.translate_single_segment("我不会起", 1.2)
        self.assertEqual(result["final_vi"], "Con không biết đặt tên đâu.")
        self.assertEqual(request.call_count, 2)
        first_tokens = request.call_args_list[0].kwargs["max_tokens"]
        second_tokens = request.call_args_list[1].kwargs["max_tokens"]
        self.assertEqual(second_tokens, first_tokens * 2)
        self.assertEqual(request.call_args_list[0].kwargs["response_format"]["required"], ["final_vi"])

    def test_local_single_batch_target_retries_a_truncated_answer_once_with_more_tokens(self):
        translator = SemanticTranslator("ollama")
        row = {"id": 5, "text_zh": "爸爸又不在我碰他干嘛"}
        complete = json.dumps({"final_vi": "Bố có ở đây đâu, chạm vào bố làm gì?"})
        with patch.object(settings, "OLLAMA_MODEL", "qwen3:4b"), \
             patch.object(translator, "_ollama_text_request", side_effect=[
                 OllamaClientError("truncated", code="provider_incomplete"), complete]) as request:
            result = translator._execute_ollama_text_batch([row], {}, [])
        self.assertEqual(result[5]["final_vi"], "Bố có ở đây đâu, chạm vào bố làm gì?")
        self.assertEqual(request.call_count, 2)
        self.assertEqual(request.call_args_list[1].kwargs["max_tokens"],
                         request.call_args_list[0].kwargs["max_tokens"] * 2)

    def test_local_batch_repairs_beauty_translation_that_changes_cute_to_beautiful(self):
        translator = SemanticTranslator("ollama")
        wrong = json.dumps({"results": [{"id": 5, "final_vi": "Em gái này đẹp quá."}]})
        repaired = json.dumps({"final_vi": "Em gái đáng yêu quá."})
        with patch.object(translator, "_ollama_text_request", side_effect=[wrong, repaired]) as request:
            result = translator._execute_ollama_text_batch(
                [{"id": 5, "text_zh": "你看妹妹多可爱啊"}], {}, [])
        self.assertEqual(result[5]["final_vi"], "Em gái đáng yêu quá.")
        self.assertEqual(request.call_count, 2)
        self.assertIn("可爱 nghĩa là đáng yêu", request.call_args_list[0].args[1])

    def test_local_batch_repairs_unspoken_family_vocative(self):
        translator = SemanticTranslator("ollama")
        wrong = json.dumps({"results": [{"id": 3, "final_vi": "Mẹ ơi, mày xem em gái này đáng yêu quá!"}]})
        repaired = json.dumps({"final_vi": "Nhìn em gái đáng yêu quá!"})
        with patch.object(translator, "_ollama_text_request", side_effect=[wrong, repaired]) as request:
            result = translator._execute_ollama_text_batch(
                [{"id": 3, "text_zh": "你看妹妹多可爱啊"}], {}, [])
        self.assertEqual(result[3]["final_vi"], "Nhìn em gái đáng yêu quá!")
        self.assertEqual(request.call_count, 2)
        self.assertIn("không tự thêm người nghe", request.call_args_list[0].args[1])

    def test_local_batch_repairs_older_brother_role_changed_to_younger_sibling(self):
        translator = SemanticTranslator("ollama")
        wrong = json.dumps({"results": [{"id": 1, "final_vi": "Tiểu Nguyên là em trai rồi."}]})
        repaired = json.dumps({"final_vi": "Tiểu Nguyên lên chức anh rồi."})
        with patch.object(translator, "_ollama_text_request", side_effect=[wrong, repaired]) as request:
            result = translator._execute_ollama_text_batch(
                [{"id": 1, "text_zh": "小元是哥哥了"}], {}, [])
        self.assertEqual(result[1]["final_vi"], "Tiểu Nguyên lên chức anh rồi.")
        self.assertEqual(request.call_count, 2)

    def test_local_batch_repairs_rhetorical_question_rendered_as_statement(self):
        translator = SemanticTranslator("ollama")
        wrong = json.dumps({"results": [{"id": 2, "final_vi": "Một gia đình có một con là đủ rồi."}]})
        repaired = json.dumps({"final_vi": "Một gia đình có một đứa con chẳng phải là đủ rồi sao?"})
        with patch.object(translator, "_ollama_text_request", side_effect=[wrong, repaired]) as request:
            result = translator._execute_ollama_text_batch(
                [{"id": 2, "text_zh": "一个家庭一个孩子不就够了吗"}], {}, [])
        self.assertEqual(result[2]["final_vi"], "Một gia đình có một đứa con chẳng phải là đủ rồi sao?")
        self.assertEqual(request.call_count, 2)

    def test_local_batch_repairs_rhetorical_question_changed_to_enough_yet(self):
        translator = SemanticTranslator("ollama")
        wrong = json.dumps({"results": [{"id": 2, "final_vi": "Đã đủ một gia đình có một đứa con chưa?"}]})
        repaired = json.dumps({"final_vi": "Một gia đình có một đứa con chẳng phải là đủ rồi sao?"})
        with patch.object(translator, "_ollama_text_request", side_effect=[wrong, repaired]) as request:
            result = translator._execute_ollama_text_batch(
                [{"id": 2, "text_zh": "一个家庭一个孩子不就够了吗"}], {}, [])
        self.assertEqual(result[2]["final_vi"], "Một gia đình có một đứa con chẳng phải là đủ rồi sao?")
        self.assertEqual(request.call_count, 2)

    def test_local_batch_repairs_omitted_glossary_name(self):
        translator = SemanticTranslator("ollama")
        wrong = json.dumps({"results": [{"id": 1, "final_vi": "Anh là anh trai rồi."}]})
        repaired = json.dumps({"final_vi": "Tiểu Nguyên lên chức anh rồi."})
        with patch.object(translator, "_ollama_text_request", side_effect=[wrong, repaired]) as request:
            result = translator._execute_ollama_text_batch(
                [{"id": 1, "text_zh": "小元是哥哥了"}],
                {"terms": [{"src": "小元", "tgt": "Tiểu Nguyên", "note": "Tên riêng"}]}, [])
        self.assertEqual(result[1]["final_vi"], "Tiểu Nguyên lên chức anh rồi.")
        self.assertEqual(request.call_count, 2)

    def test_local_batch_plain_repair_has_bounded_budget_above_truncation_floor(self):
        translator = SemanticTranslator("ollama")
        wrong_batch = json.dumps({"results": [{"id": 24, "final_vi": "Mày khóc cả buổi chiều à?"}]})
        wrong_repair = json.dumps({"final_vi": "Mày còn khóc cả buổi chiều nữa à?"})
        correct_plain = "Cậu còn khóc cả buổi chiều nữa à?"
        with patch.object(translator, "_ollama_text_request",
                          side_effect=[wrong_batch, wrong_repair, correct_plain]) as request:
            result = translator._execute_ollama_text_batch(
                [{"id": 24, "text_zh": "你还哭了一下午"}], {}, [])
        self.assertEqual(result[24]["final_vi"], correct_plain)
        self.assertEqual(request.call_count, 3)
        self.assertEqual(request.call_args.kwargs["response_format"], "text")
        self.assertEqual(request.call_args.kwargs["max_tokens"], 160)
        self.assertLess(len(request.call_args.args[1]), 260)

    def test_local_batch_gives_gender_neutral_mother_misses_child_guidance(self):
        translator = SemanticTranslator("ollama")
        response = json.dumps({"results": [{"id": 3, "final_vi": "Mẹ nhớ bé lắm."}]})
        with patch.object(translator, "_ollama_text_request", return_value=response) as request:
            translator._execute_ollama_text_batch(
                [{"id": 3, "text_zh": "孩子妈妈特别想他"}], {}, [])
        self.assertIn("Mẹ nhớ bé/con lắm", request.call_args.args[1])

    def test_local_transport_retryability_is_preserved(self):
        translator = SemanticTranslator("ollama")
        with patch("core.engines.translation.ollama_client.OllamaClient.translate",
                   side_effect=OllamaClientError("local offline", retryable=True, code="provider_transport")):
            with self.assertRaises(OllamaClientError) as raised:
                translator._opencode_request("system", "prompt")
        self.assertTrue(raised.exception.retryable)
        self.assertEqual(raised.exception.code, "provider_transport")

    def test_visual_ollama_translation_is_one_review_required_draft_pass(self):
        from config import settings
        from core.video_intelligence import VideoIntelligence

        old_provider, old_model = settings.LLM_PROVIDER, settings.OLLAMA_MODEL
        settings.LLM_PROVIDER, settings.OLLAMA_MODEL = "ollama", "qwen2.5:7b"
        try:
            intelligence = VideoIntelligence()
            payload = [{"id": 4, "start": 2.0, "end": 3.2, "asr_text": "你来了"}]
            raw_result = {"segments": {4: {"id": 4, "start": 2.0, "end": 3.2,
                "text_zh": "你来了", "literal_vi": "Bạn đến rồi", "natural_vi": "Bạn đến rồi",
                "final_vi": "Bạn đến rồi", "needs_review": False, "review_reason": ""}},
                "screen_texts": [], "summary": ""}
            client = type("LocalClient", (), {"has_credentials": True, "model": "qwen2.5:7b"})()
            with patch("core.engines.translation.ollama_client.OllamaClient", return_value=client), \
                 patch.object(intelligence, "_request_text_result",
                              return_value=("{}", raw_result)) as request, \
                 patch.object(intelligence, "_correct_source",
                              side_effect=AssertionError("fast draft must not run source correction")), \
                 patch.object(intelligence, "_review_primary_text",
                              side_effect=AssertionError("fast draft must not run OCR verification")):
                result = intelligence._translate_text(payload, [], "", 0.0, 4.0)
            request.assert_called_once()
            self.assertEqual(result["segments"][4]["text_zh"], "你来了")
            self.assertEqual(result["segments"][4]["final_vi"], "Bạn đến rồi")
            self.assertTrue(result["segments"][4]["needs_review"])
            self.assertIn("Bản nháp", result["segments"][4]["review_reason"])
            self.assertEqual(result["segments"][4]["translation_provider"], "ollama")
        finally:
            settings.LLM_PROVIDER, settings.OLLAMA_MODEL = old_provider, old_model


if __name__ == "__main__":
    unittest.main()
