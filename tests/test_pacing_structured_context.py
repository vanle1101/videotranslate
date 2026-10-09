"""Offline schema/context/cache faults; not real Muse/media acceptance tests."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from unittest.mock import Mock

import pytest

from config import settings
from core.ai_execution import AIExecutionLayer
from core.engines.translation import opencode_client
from core.engines.translation.semantic_translator import (
    SemanticTranslator, PacingReviewRejected, _source_semantics,
)
from core.structured_response import StructuredResponseError
from core.translation_context import dialogue_context


def line(text="Con chưa xong."):
    return {"literal_vi": text, "natural_vi": text, "final_vi": text,
        "needs_review": False, "review_reason": ""}


def rows():
    proof = {"speaker_id": "child-a", "verified": True, "method": "audio_diarization",
        "confidence": .97, "model": "actual-upstream-model", "scope_id": "same-video"}
    return [
        {"id": 112, "start": 181., "end": 182., "text_zh": "不是回来", "vi": "Không về...",
            "speaker_id": "child-a", "speaker_evidence": proof},
        {"id": 113, "start": 182.1, "end": 183.35, "text_zh": "逗爸爸玩的", "vi": "...để đùa với bố.",
            "is_focus": True, "speaker_id": "child-a", "speaker_evidence": deepcopy(proof),
            "source_asr_row_id": 31, "source_asr_start": 181., "source_asr_end": 183.35,
            "source_piece_index": 1, "source_piece_count": 2},
    ]


@pytest.mark.parametrize("raw,code", [
    ('{"final_vi":"Đúng.","final_vi":"Sai."}', "invalid_content"),
    ('{"duration":NaN}', "invalid_type"), ('{"duration":Infinity}', "invalid_type"),
    ('prefix {"final_vi":"Lời thật."} suffix', "invalid_json"),
    ('[]', "invalid_type"), (None, "invalid_type"),
])
def test_pacing_parser_cannot_guess_an_object_or_collapse_ambiguous_fields(raw, code):
    with pytest.raises(StructuredResponseError) as caught:
        SemanticTranslator._json_response(raw)
    assert caught.value.code == code


def test_one_explicit_json_fence_keeps_supported_framing_but_remains_strict():
    assert SemanticTranslator._json_response('Kết quả:\n```JSON\n{"ok":true}\n```') == {"ok": True}
    with pytest.raises(StructuredResponseError):
        SemanticTranslator._json_response('```json\n{"ok":true,"ok":false}\n```')


def test_malformed_translation_is_repaired_once_with_original_source_and_context(monkeypatch):
    monkeypatch.setattr(settings, "OPENCODE_SCHEMA_REPAIR_ATTEMPTS", 4)
    translator = SemanticTranslator("opencode")
    request = Mock(side_effect=['{"literal_vi":', json.dumps(line())])
    monkeypatch.setattr(translator, "_opencode_request", request)
    original = rows()
    before = deepcopy(original)
    result = translator.translate_single_segment("逗爸爸玩的", 1.25, original)
    assert result["final_vi"] == "Con chưa xong."
    assert request.call_count == 2 and original == before
    first, repair = [call.args for call in request.call_args_list]
    assert first[0] == repair[0] and first[1] in repair[1]
    assert "không thay đổi nguồn, nghĩa" in repair[1]
    assert "source_asr_row_id" in first[0] and "不是回来" in first[0]


@pytest.mark.parametrize("reply,code", [
    ({"literal_vi": "Đúng.", "natural_vi": "Đúng."}, "missing_field"),
    ({**line(), "needs_review": "false"}, "invalid_type"),
    ({**line(), "final_vi": None}, "invalid_type"),
])
def test_schema_repair_exhaustion_is_typed_and_never_becomes_a_translation(monkeypatch, reply, code):
    translator = SemanticTranslator("opencode")
    request = Mock(return_value=json.dumps(reply))
    monkeypatch.setattr(translator, "_opencode_request", request)
    with pytest.raises(StructuredResponseError) as caught:
        translator.translate_single_segment("妈妈", 1)
    assert caught.value.code == code and request.call_count == 2


def test_zero_repairs_means_one_request_and_no_retry_for_transport_errors(monkeypatch):
    translator = SemanticTranslator("opencode")
    monkeypatch.setattr(settings, "OPENCODE_SCHEMA_REPAIR_ATTEMPTS", 0)
    request = Mock(return_value="{broken")
    monkeypatch.setattr(translator, "_opencode_request", request)
    with pytest.raises(StructuredResponseError):
        translator.translate_single_segment("妈妈", 1)
    assert request.call_count == 1
    request.reset_mock(side_effect=True)
    request.side_effect = RuntimeError("safe provider timeout")
    with pytest.raises(RuntimeError, match="provider timeout"):
        translator.translate_single_segment("妈妈", 1)
    assert request.call_count == 1


@pytest.mark.parametrize("reply", [{**line(), "final_vi": "中文"}, {**line(), "final_vi": ""}])
def test_invalid_content_is_not_repaired_by_reinterpreting_source(monkeypatch, reply):
    translator = SemanticTranslator("opencode")
    request = Mock(return_value=json.dumps(reply))
    monkeypatch.setattr(translator, "_opencode_request", request)
    with pytest.raises(StructuredResponseError) as caught:
        translator.translate_single_segment("妈妈", 1)
    assert caught.value.code == "semantic_content" and request.call_count == 1


@pytest.mark.parametrize("stage,bad", [
    ("fidelity", {"equivalent": "true", "natural": True, "reason": "wrong type"}),
    ("fidelity", {"equivalent": True, "reason": "missing natural"}),
    ("fluency", {"natural": "true", "reason": "wrong type"}),
    ("fluency", {"natural": True}),
])
def test_each_pacing_reviewer_repairs_its_own_schema_without_regenerating_draft(monkeypatch, stage, bad):
    translator = SemanticTranslator("opencode")
    fidelity = {"equivalent": True, "natural": True, "address_preserved": True, "reason": "Giữ ý nguồn."}
    fluency = {"natural": True, "reason": "Câu Việt có cấu trúc rõ."}
    replies = [line(), bad, fidelity, fluency] if stage == "fidelity" else [line(), fidelity, bad, fluency]
    request = Mock(side_effect=[json.dumps(reply) for reply in replies])
    monkeypatch.setattr(translator, "_opencode_request", request)
    result = translator.rewrite_for_pacing("我还没做完", "Con vẫn chưa làm việc đó xong.", 1.25, rows())
    assert result["pacing_verification"]["status"] == "verified" and request.call_count == 4
    assert "Nguồn Trung" in request.call_args_list[0].args[1]
    if stage == "fluency":
        blind = request.call_args_list[-1].args[1]
        assert not any("\u3400" <= char <= "\u9fff" for char in blind)
        assert "semantic_context" not in blind and "Con vẫn chưa" not in blind


@pytest.mark.parametrize("verdict", [
    {"equivalent": False, "natural": True, "address_preserved": True, "reason": "Mất phủ định."},
    {"equivalent": True, "natural": False, "address_preserved": True, "reason": "Cụm Việt sai."},
    {"equivalent": True, "natural": True, "address_preserved": False, "reason": "Đảo vai con/bố."},
])
def test_schema_valid_negative_verdict_never_triggers_repair_to_change_vote(monkeypatch, verdict):
    translator = SemanticTranslator("opencode")
    request = Mock(side_effect=[json.dumps(line()), json.dumps(verdict)])
    monkeypatch.setattr(translator, "_opencode_request", request)
    with pytest.raises(PacingReviewRejected):
        translator.rewrite_for_pacing("我还没做完", "Con vẫn chưa làm xong việc đó.", 1.25, rows())
    assert request.call_count == 2


def test_semantic_unit_and_source_proof_reach_draft_fidelity_but_not_blind_fluency(monkeypatch):
    translator = SemanticTranslator("opencode")
    requests = Mock(side_effect=[json.dumps(line("Không về đùa với bố.")),
        json.dumps({"equivalent": True, "natural": True, "address_preserved": True, "reason": "Giữ mục đích/phủ định."}),
        json.dumps({"natural": True, "reason": "Cụm Việt nối đúng trước/sau."})])
    monkeypatch.setattr(translator, "_opencode_request", requests)
    translator.rewrite_for_pacing("逗爸爸玩的", "Không phải quay về để đùa với bố đâu.", 1.25, rows())
    draft = requests.call_args_list[0].args[1]
    fidelity = json.loads(requests.call_args_list[1].args[1])
    unit, = fidelity["semantic_context"]["units"]
    assert unit["source_ids"] == [112, 113] and unit["same_speaker_confirmed"] is True
    assert unit["text_zh"] == "不是回来逗爸爸玩的"
    assert "source_asr_row_id" in draft and unit["source_parts"][1]["source_piece_index"] == 1
    blind = json.loads(requests.call_args_list[2].args[1])
    assert set(blind) == {"candidate", "nearby_vietnamese_dialogue"}
    assert "speaker" not in json.dumps(blind) and "source" not in json.dumps(blind)


def test_unknown_continuity_and_legacy_missing_metadata_do_not_create_fake_audio_evidence():
    source = rows()
    for row in source:
        row.pop("speaker_evidence")
    unit, = _source_semantics(source, [source[1]])["units"]
    assert unit["kind"] == "contextual_exchange" and unit["same_speaker_confirmed"] is False
    assert "text_zh" not in unit and not unit["can_combine_dubbing"]
    legacy = _source_semantics([{"zh": "妈妈", "vi": "Mẹ ơi."}])
    assert legacy["units"] == [] and legacy["omitted_legacy_rows"] == 1


def test_repeated_dialogue_projection_preserves_proof_without_mutating_source():
    source = rows()
    source[0].update(utterance_id="continuous-a", utterance_evidence={
        "utterance_id": "continuous-a", "method": "user_confirmation", "verified": True,
        "confirmation_id": "recorded-action", "scope_id": "same-video"})
    before = deepcopy(source)
    projected = dialogue_context(dialogue_context(source, [source[1]]), [source[1]])
    assert projected[0]["utterance_evidence"] == source[0]["utterance_evidence"]
    assert projected[1]["speaker_evidence"] == source[1]["speaker_evidence"]
    assert projected[1]["source_piece_index"] == 1 and source == before


@pytest.fixture
def real_adapter_offline_boundary(tmp_path, monkeypatch):
    layer = AIExecutionLayer(cache_root=tmp_path / "ai-cache")
    monkeypatch.setattr(opencode_client, "_shared_execution_layer", lambda: layer)
    monkeypatch.setattr(opencode_client, "resolve_api_key", lambda _: "offline-test-key")
    diagnostics = Mock()
    monkeypatch.setattr("core.engines.translation.semantic_translator.retain_diagnostic", diagnostics)
    calls = []
    def request(client, prompt, model, system, max_tokens, context, timeout):
        calls.append({"prompt": prompt, "system": system, "model": model, "timeout": timeout})
        return json.dumps(line())
    monkeypatch.setattr(opencode_client.OpenCodeZenClient, "_translate", request)
    return layer, calls, diagnostics


def test_real_adapter_validation_enables_cache_and_invalidates_on_source_proof_changes(real_adapter_offline_boundary):
    layer, calls, diagnostics = real_adapter_offline_boundary
    translator = SemanticTranslator("opencode")
    original = rows()
    for _ in range(2):
        translator.translate_single_segment("逗爸爸玩的", 1.25, original)
    assert len(calls) == 1 and layer.snapshot()["cache_hits"] == 1
    changed = deepcopy(original)
    changed[1]["speaker_evidence"]["confidence"] = .98
    translator.translate_single_segment("逗爸爸玩的", 1.25, changed)
    assert len(calls) == 2 and diagnostics.call_count >= 3
    assert calls[0]["timeout"] <= settings.OPENCODE_TASK_TIMEOUTS["translation"]


def test_real_adapter_rejects_and_diagnoses_bad_raw_before_cache_then_repairs(real_adapter_offline_boundary, monkeypatch):
    layer, calls, diagnostics = real_adapter_offline_boundary
    responses = iter(['{"final_vi":', json.dumps(line())])
    def request(*args):
        calls.append(1)
        return next(responses)
    monkeypatch.setattr(opencode_client.OpenCodeZenClient, "_translate", request)
    translator = SemanticTranslator("opencode")
    result = translator.translate_single_segment("妈妈", 1)
    assert result["final_vi"] == "Con chưa xong." and len(calls) == 2
    errors = [call.kwargs.get("error") for call in diagnostics.call_args_list if call.kwargs.get("error")]
    assert len(errors) == 1 and errors[0].code == "invalid_json"
    assert layer.snapshot()["cache_hits"] == 0
    assert len(list(layer.cache.root.glob("*.json"))) == 2
    repeated = translator.translate_single_segment("妈妈", 1)
    assert repeated == result and len(calls) == 2 and layer.snapshot()["cache_hits"] == 1


def test_parallel_request_contracts_do_not_cross_stage_or_thread(real_adapter_offline_boundary):
    _, calls, _ = real_adapter_offline_boundary
    translator = SemanticTranslator("opencode")
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda text: translator.translate_single_segment(text, 1), ["妈妈", "爸爸"]))
    assert len(results) == len(calls) == 2
    assert {call["prompt"] for call in calls} == {"Dịch câu: 妈妈", "Dịch câu: 爸爸"}


@pytest.mark.parametrize("bad,code", [
    ({"theme": "Đối thoại", "terms": []}, "missing_field"),
    ({"theme": "Đối thoại", "pronouns": "Chưa xác định", "terms": ["not a glossary object"]}, "invalid_type"),
    ({"theme": "Đối thoại", "pronouns": "Chưa xác định", "terms": [{"src": "称呼", "tgt": 1, "note": ""}]}, "invalid_type"),
])
def test_summary_schema_has_bounded_repair_and_cannot_use_malformed_glossary(monkeypatch, bad, code):
    translator = SemanticTranslator("opencode")
    request = Mock(return_value=json.dumps(bad))
    monkeypatch.setattr(translator, "_opencode_request", request)
    with pytest.raises(StructuredResponseError) as caught:
        translator._extract_context_and_glossary('[]')
    assert caught.value.code == code and request.call_count == 2


def test_pacing_actual_adapter_caches_distinct_draft_fidelity_fluency_contracts(real_adapter_offline_boundary, monkeypatch):
    layer, calls, _ = real_adapter_offline_boundary
    def request(client, prompt, model, system, max_tokens, context, timeout):
        calls.append({"prompt": prompt, "timeout": timeout})
        if system.startswith("Kiểm định độc lập"):
            return json.dumps({"equivalent": True, "natural": True, "address_preserved": True,
                "reason": "Giữ đúng phủ định, người nói và hành động."})
        if system.startswith("Bạn là biên tập viên tiếng Việt bản ngữ"):
            return json.dumps({"natural": True, "reason": "Khẩu ngữ đúng cấu trúc."})
        return json.dumps(line())
    monkeypatch.setattr(opencode_client.OpenCodeZenClient, "_translate", request)
    translator = SemanticTranslator("opencode")
    for _ in range(2):
        result = translator.rewrite_for_pacing("我还没做完", "Con vẫn chưa làm xong việc đó.", 1.25, rows())
        assert result["pacing_verification"]["address_preserved"] is True
    assert len(calls) == 3 and layer.snapshot()["cache_hits"] == 3
    blind, = [call for call in calls if "nearby_vietnamese_dialogue" in call["prompt"]]
    assert set(json.loads(blind["prompt"])) == {"candidate", "nearby_vietnamese_dialogue"}
    assert blind["timeout"] <= settings.OPENCODE_TASK_TIMEOUTS["fluency_review"]
