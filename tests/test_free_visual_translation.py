"""Offline acceptance of explicit free ASR/OCR translation and evidence gates."""
import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from config import settings
from core.streaming.pipeline import SegmentItem, StreamingPipelineSession
from core.video_intelligence import VideoIntelligence, VideoIntelligenceError


@pytest.fixture
def free_config(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "openrouter-free")
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(settings, "BASE_DIR", tmp_path)
    return tmp_path


def evidence(text="你来了", **changes):
    return {"id": "o0", "start": 0, "end": 2, "text_zh": text, "confidence": .98,
            "bbox": [.2, .7, .4, .06], **changes}


def translation(text="你来了", **changes):
    return {"id": 0, "start": 0, "end": 2, "text_zh": text, "literal_vi": "Anh đến rồi",
            "natural_vi": "Anh đến rồi", "final_vi": "Anh đến rồi",
            "needs_review": False, "review_reason": "", **changes}


def install_responses(monkeypatch, *, source="你来了", observed=None, corrected=None,
                      source_review=False, draft_review=False, kind="subtitle", verified_kind=None):
    observed = [evidence()] if observed is None else observed
    corrected = source if corrected is None else corrected
    correction = {"segments": [{"id": 0, "text_zh": corrected,
                                "evidence_ids": [row["id"] for row in observed],
                                "needs_review": source_review,
                                "review_reason": "Nguồn chưa rõ" if source_review else ""}]}
    draft = {"segments": [translation(corrected, needs_review=draft_review,
                                       review_reason="Nghĩa chưa rõ" if draft_review else "")],
             "screen_texts": [{"id": row["id"], "text_vi": "Anh đến rồi", "kind": kind,
                               "needs_review": False, "review_reason": ""} for row in observed],
             "summary": "Hai người gặp nhau"}
    verified = deepcopy(draft)
    verified["segments"][0]["needs_review"] = False
    verified["segments"][0]["review_reason"] = ""
    if verified_kind:
        for row in verified["screen_texts"]:
            row["kind"] = verified_kind
    client = Mock(has_credentials=True, model="example/translator:free")
    client.translate.side_effect = list(map(json.dumps, (correction, draft, verified)))
    monkeypatch.setattr("core.video_intelligence.OpenRouterFreeClient", Mock(return_value=client))
    return client, observed


def test_primary_free_needs_no_gemini_and_never_constructs_or_encodes_media(free_config, monkeypatch):
    gemini = Mock(side_effect=AssertionError("Gemini must never be constructed"))
    monkeypatch.setattr("core.video_intelligence.GeminiClient", gemini)
    client, observed = install_responses(monkeypatch)
    processor = VideoIntelligence()
    encode = Mock(side_effect=AssertionError("No Gemini media should be encoded"))
    monkeypatch.setattr(processor, "_encode_chunk", encode)
    monkeypatch.setattr(processor, "_encode_contact_sheet", encode)
    monkeypatch.setattr(processor.screen_ocr, "extract", Mock(return_value=observed))
    seg = SegmentItem(0, 0, 2, 2)
    seg.text_zh = "你来了"
    result = processor.analyze_chunk(free_config / "video.mp4", 0, 2, [seg])
    assert processor.provider == "openrouter-free" and processor.client is None
    assert not processor.used_text_fallback
    gemini.assert_not_called()
    encode.assert_not_called()
    assert client.translate.call_count == 3
    checked = result["segments"][0]
    assert not checked["needs_review"]
    assert checked["source_evidence_ids"] == ["o0"]
    assert checked["translation_provider"] == "openrouter-free"
    assert checked["source_method"] == "text-ai" and checked["evidence_mode"] == "asr-ocr-text"
    assert not result["screen_texts"][0]["needs_review"]
    assert "hết hạn mức" not in json.dumps(result, ensure_ascii=False)
    assert "Gemini" not in client.translate.call_args.args[0]
    session = StreamingPipelineSession("free-primary", free_config / "video.mp4", visual_translation=True)
    assert session.video_intelligence.provider == "openrouter-free"
    assert "ASR + OCR miễn phí" in session.source_processing_label()
    assert "Gemini" not in session.source_processing_label()
    gemini.assert_not_called()


def test_opencode_muse_routes_asr_ocr_only_to_selected_model(free_config, monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "opencode")
    monkeypatch.setattr(settings, "OPENCODE_MODEL", "muse-spark-1.3-contributor-free")
    client, observed = install_responses(monkeypatch)
    client.model = settings.OPENCODE_MODEL
    constructor = Mock(return_value=client)
    monkeypatch.setattr("core.video_intelligence.OpenCodeZenClient", constructor)
    forbidden = Mock(side_effect=AssertionError("Wrong provider"))
    monkeypatch.setattr("core.video_intelligence.OpenRouterFreeClient", forbidden)
    monkeypatch.setattr("core.video_intelligence.GeminiClient", forbidden)
    processor = VideoIntelligence()
    monkeypatch.setattr(processor.screen_ocr, "extract", Mock(return_value=observed))
    segment = SegmentItem(0, 0, 2, 2)
    segment.text_zh = "你来了"
    result = processor.analyze_chunk(free_config / "video.mp4", 0, 2, [segment])
    constructor.assert_called_once_with(model=settings.OPENCODE_MODEL, timeout=120)
    assert result["segments"][0]["translation_provider"] == "opencode"
    assert result["segments"][0]["translation_model"] == settings.OPENCODE_MODEL
    assert result["segments"][0]["evidence_mode"] == "asr-ocr-text"
    assert result["segments"][0]["source_method"] == "text-ai"
    assert not result["segments"][0]["needs_review"]
    assert "OpenCode" in client.translate.call_args.args[0]
    assert "OpenRouter" not in client.translate.call_args.args[0]
    forbidden.assert_not_called()
    session = StreamingPipelineSession("muse-primary", free_config / "video.mp4", visual_translation=True)
    assert "OpenCode" in session.source_processing_label()
    assert "Gemini" not in session.source_processing_label()


def test_opencode_failure_preserves_provider_without_openrouter_fallback(free_config, monkeypatch):
    from core.engines.translation.opencode_client import OpenCodeRequestError
    monkeypatch.setattr(settings, "LLM_PROVIDER", "opencode")
    client = Mock(has_credentials=True, translate=Mock(side_effect=OpenCodeRequestError("OpenCode FreeTierError")))
    monkeypatch.setattr("core.video_intelligence.OpenCodeZenClient", Mock(return_value=client))
    forbidden = Mock(side_effect=AssertionError("No provider switch"))
    monkeypatch.setattr("core.video_intelligence.OpenRouterFreeClient", forbidden)
    processor = VideoIntelligence()
    with pytest.raises(VideoIntelligenceError, match="OpenCode FreeTierError"):
        processor._translate_text([{"id": 0, "start": 0, "end": 2, "asr_text": "你来了"}], [evidence()], "", 0, 2)
    forbidden.assert_not_called()


@pytest.mark.parametrize("case", ["no_ocr", "low_confidence", "other_time", "contradiction",
                                 "source_review", "draft_review", "unsupported_change", "title_change"])
def test_free_primary_does_not_accept_unverified_provider_certainty(free_config, monkeypatch, case):
    options = {}
    if case == "no_ocr":
        options["observed"] = []
    elif case == "low_confidence":
        options["observed"] = [evidence(confidence=.85)]
    elif case == "other_time":
        options["observed"] = [evidence(start=5, end=6)]
    elif case == "contradiction":
        options["observed"] = [evidence(text="你没来")]
    elif case in ("source_review", "draft_review"):
        options[case] = True
    elif case == "unsupported_change":
        options.update(corrected="你已经走了", observed=[evidence()])
    elif case == "title_change":
        options.update(source="你来啦", corrected="你来了", kind="title")
    client, observed = install_responses(monkeypatch, **options)
    processor = VideoIntelligence()
    payload = [{"id": 0, "start": 0, "end": 2, "asr_text": options.get("source", "你来了")}]
    result = processor._translate_text(payload, observed, "", 0, 2)
    assert result["segments"][0]["needs_review"]
    assert result["segments"][0]["review_reason"]
    assert "Gemini" not in json.dumps(result, ensure_ascii=False)


def test_free_primary_allows_ocr_supported_correction_without_blanket_review(free_config, monkeypatch):
    _, observed = install_responses(monkeypatch, source="你来啦", corrected="你来了")
    result = VideoIntelligence()._translate_text(
        [{"id": 0, "start": 0, "end": 2, "asr_text": "你来啦"}], observed, "", 0, 2)
    assert result["segments"][0]["text_zh"] == "你来了"
    assert not result["segments"][0]["needs_review"]


@pytest.mark.parametrize("confidence,kind,verified_kind,review", [
    (.98, "title", "title", False), (.80, "title", "title", True),
    (.98, "subtitle", "title", True),
])
def test_primary_ocr_titles_require_clear_local_evidence_and_stable_classification(
        free_config, monkeypatch, confidence, kind, verified_kind, review):
    _, observed = install_responses(monkeypatch, observed=[evidence(confidence=confidence)],
                                    kind=kind, verified_kind=verified_kind)
    result = VideoIntelligence()._translate_text(
        [{"id": 0, "start": 0, "end": 2, "asr_text": "你来了"}], observed, "", 0, 2)
    assert result["screen_texts"][0]["needs_review"] is review


def test_primary_error_is_free_specific_and_does_not_claim_gemini_quota(free_config, monkeypatch):
    client = Mock(has_credentials=True, model="example/translator:free")
    client.translate.return_value = "invalid private response"
    monkeypatch.setattr("core.video_intelligence.OpenRouterFreeClient", Mock(return_value=client))
    with pytest.raises(VideoIntelligenceError) as caught:
        VideoIntelligence()._translate_text([], [], "", 0, 2)
    assert "ASR + OCR miễn phí" in str(caught.value)
    assert "Gemini" not in str(caught.value) and "private response" not in str(caught.value)


@pytest.mark.parametrize("provider", ["unknown-provider", "free", "muse", ""])
def test_unknown_visual_provider_is_rejected_before_cache(free_config, monkeypatch, provider):
    monkeypatch.setattr(settings, "LLM_PROVIDER", provider)
    with pytest.raises(VideoIntelligenceError):
        VideoIntelligence()
    with pytest.raises(ValueError):
        StreamingPipelineSession("unsupported", free_config / "video.mp4", visual_translation=True)
    assert not (free_config / "workspace" / "cache" / "unsupported").exists()


@pytest.mark.parametrize("stage", [1, 2], ids=["draft", "review"])
@pytest.mark.parametrize("fallback", [False, True], ids=["primary", "quota_fallback"])
def test_schema_retry_requests_complete_response_and_preserves_review(free_config, monkeypatch, stage, fallback):
    client, observed = install_responses(monkeypatch)
    replies = list(client.translate.side_effect)
    rejected = json.loads(replies[stage])
    rejected["segments"][0].pop("literal_vi")
    rejected["segments"][0].update(needs_review=True, review_reason="Phủ định chưa rõ")
    rejected["screen_texts"][0].update(needs_review=True, review_reason="Chữ còn mờ")
    replies.insert(stage, json.dumps(rejected))
    client.translate.side_effect = replies
    result = VideoIntelligence()._translate_text(
        [{"id": 0, "start": 0, "end": 2, "asr_text": "你来了"}], observed, "", 0, 2,
        quota_fallback=fallback)
    assert client.translate.call_count == 4
    original_prompt = client.translate.call_args_list[stage].args[0]
    retry_prompt = client.translate.call_args_list[stage + 1].args[0]
    assert retry_prompt.startswith(original_prompt)
    assert "SỬA ĐỊNH DẠNG JSON" in retry_prompt and "literal_vi" in retry_prompt
    assert "Bộ dịch trả nội dung câu thoại" in retry_prompt
    checked = result["segments"][0]
    assert checked["id"] == 0 and checked["start"] == 0 and checked["end"] == 2
    assert checked["literal_vi"] == "Anh đến rồi"
    assert checked["needs_review"] and checked["review_reason"] == "Phủ định chưa rõ"
    assert result["screen_texts"][0]["needs_review"]
    assert result["screen_texts"][0]["review_reason"] == "Chữ còn mờ"


@pytest.mark.parametrize("invalid", ["missing_field", "missing_id", "changed_time"])
def test_schema_retry_fails_after_one_invalid_repair(free_config, monkeypatch, invalid):
    client, observed = install_responses(monkeypatch)
    replies = list(client.translate.side_effect)
    rejected = json.loads(replies[1])
    if invalid == "missing_field":
        rejected["segments"][0].pop("final_vi")
    elif invalid == "missing_id":
        rejected["segments"] = []
    else:
        rejected["segments"][0]["end"] = 3
    client.translate.side_effect = [replies[0], json.dumps(rejected), json.dumps(rejected)]
    with pytest.raises(VideoIntelligenceError):
        VideoIntelligence()._translate_text(
            [{"id": 0, "start": 0, "end": 2, "asr_text": "你来了"}], observed, "", 0, 2)
    assert client.translate.call_count == 3


@pytest.mark.parametrize("when", ["before", "first_response", "repair_response"])
def test_schema_retry_checks_cancellation_before_requests_and_after_responses(free_config, when):
    cancelled = when == "before"
    calls = 0
    def translate(*args, **kwargs):
        nonlocal cancelled, calls
        calls += 1
        cancelled = when == "first_response" or (when == "repair_response" and calls == 2)
        return "invalid JSON" if calls == 1 else json.dumps({"segments": [], "screen_texts": [], "summary": ""})
    client = Mock(translate=Mock(side_effect=translate))
    with pytest.raises(VideoIntelligenceError, match="hủy"):
        VideoIntelligence()._request_text_result(client, "original prompt", [], [], 0, 2, lambda: cancelled)
    assert calls == {"before": 0, "first_response": 1, "repair_response": 2}[when]


def test_schema_retry_does_not_retry_transport_or_auth_errors(free_config):
    from core.engines.translation.openrouter_client import OpenRouterClientError
    client = Mock(translate=Mock(side_effect=OpenRouterClientError("Không kết nối được")))
    with pytest.raises(OpenRouterClientError, match="Không kết nối"):
        VideoIntelligence()._request_text_result(client, "prompt", [], [], 0, 2)
    client.translate.assert_called_once()


def test_schema_repair_cannot_hide_prior_uncertain_screen_by_ignoring_it(free_config, monkeypatch):
    client, observed = install_responses(monkeypatch)
    replies = list(client.translate.side_effect)
    rejected = json.loads(replies[1])
    rejected["segments"][0].pop("natural_vi")
    rejected["screen_texts"][0].update(needs_review=True, review_reason="Cần kiểm tra vùng chữ")
    ignored = json.loads(replies[1])
    ignored["screen_texts"][0].update(kind="ignore", text_vi="")
    client.translate.side_effect = [replies[0], json.dumps(rejected), json.dumps(ignored), json.dumps(ignored)]
    result = VideoIntelligence()._translate_text(
        [{"id": 0, "start": 0, "end": 2, "asr_text": "你来了"}], observed, "", 0, 2)
    assert result["screen_texts"][0]["id"] == "o0"
    assert result["screen_texts"][0]["needs_review"]
    assert result["screen_texts"][0]["review_reason"] == "Cần kiểm tra vùng chữ"


@pytest.mark.parametrize("source,screen", [
    ("支持", "不支持"),
    ("这个方法真的可以让我们的账号获得更多流量和关注", "这个方法真的不可以让我们的账号获得更多流量和关注"),
    ("我喜欢他", "我不喜欢他"),
    ("只有十个人", "只有一百个人"),
])
def test_confident_model_cannot_approve_asr_that_omits_ocr_negation_or_number(free_config, monkeypatch, source, screen):
    _, observed = install_responses(monkeypatch, source=source, observed=[evidence(text=screen)])
    result = VideoIntelligence()._translate_text(
        [{"id": 0, "start": 0, "end": 2, "asr_text": source}], observed, "", 0, 2)
    assert result["segments"][0]["needs_review"]
    assert result["screen_texts"][0]["needs_review"]


def test_ordered_exact_ocr_fragments_can_ground_a_complete_sentence():
    assert VideoIntelligence._ocr_supports_text("今天阳光很好", [
        {"start": 0, "end": 1, "text_zh": "今天阳光"},
        {"start": 1, "end": 2, "text_zh": "阳光很好"},
    ])
    assert not VideoIntelligence._ocr_supports_text("今天阳光很好", [
        {"start": 1, "end": 2, "text_zh": "今天阳光"},
        {"start": 0, "end": 1, "text_zh": "阳光很好"},
    ])


@pytest.mark.parametrize("uncertain", [False, True])
def test_review_joins_full_chunk_evidence_across_independently_sliced_batches(free_config, monkeypatch, uncertain):
    # 25 OCR IDs but one sentence: the supporting speech box lands in batch 2,
    # while the sentence and its correction are returned in batch 1.
    observed = [evidence(text="标题", id=f"o{i}") for i in range(24)]
    observed.append(evidence(id="o24"))
    correction = {"segments": [{"id": 0, "text_zh": "你来了", "evidence_ids": ["o24"],
                                "needs_review": False, "review_reason": ""}]}
    first = {"segments": [translation()], "screen_texts": [
        {"id": f"o{i}", "text_vi": "Tiêu đề", "kind": "title", "needs_review": False, "review_reason": ""}
        for i in range(24)], "summary": ""}
    second = {"segments": [], "screen_texts": [{"id": "o24", "text_vi": "Anh đến rồi",
        "kind": "subtitle", "needs_review": uncertain, "review_reason": "Chữ mờ" if uncertain else ""}], "summary": ""}
    verified_second = deepcopy(second)
    verified_second["screen_texts"][0].update(needs_review=False, review_reason="")
    client = Mock(has_credentials=True, model="example/translator:free")
    client.translate.side_effect = list(map(json.dumps, [correction, first, first, second, verified_second]))
    monkeypatch.setattr("core.video_intelligence.OpenRouterFreeClient", Mock(return_value=client))
    result = VideoIntelligence()._translate_text(
        [{"id": 0, "start": 0, "end": 2, "asr_text": "你来了"}], observed, "", 0, 2)
    assert client.translate.call_count == 5
    assert result["segments"][0]["needs_review"] is uncertain
    screen = next(item for item in result["screen_texts"] if item["id"] == "o24")
    assert screen["needs_review"] is uncertain
    if uncertain:
        assert screen["review_reason"] == "Chữ mờ"
    else:
        assert result["segments"][0]["source_evidence_ids"] == ["o24"]


@pytest.mark.parametrize("refs", [["other-time"], ["missing"], [7]])
def test_wrong_source_citation_retains_asr_as_unapproved_draft(refs):
    processor = VideoIntelligence.__new__(VideoIntelligence)
    raw = {"segments": [{"id": 0, "text_zh": "偷换成另一个人的话", "evidence_ids": refs,
                         "needs_review": False, "review_reason": ""}]}
    client = SimpleNamespace(translate=lambda *args, **kwargs: json.dumps(raw))
    result = processor._correct_source(client, [{"id": 0, "start": 1, "end": 2, "asr_text": "你好"}],
        [{"id": "other-time", "start": 5, "end": 6, "text_zh": "别人的话"}], "")
    assert result[0]["text_zh"] == "你好"
    assert result[0]["evidence_ids"] == []
    assert result[0]["needs_review"] is True
    assert "sai thời điểm" in result[0]["review_reason"]


def test_repaired_response_keeps_low_confidence_from_otherwise_malformed_draft(free_config):
    valid = {"segments": [translation()], "screen_texts": [], "summary": ""}
    invalid = deepcopy(valid)
    invalid["segments"][0]["confidence"] = .2
    invalid["screen_texts"] = None
    client = Mock(translate=Mock(side_effect=[json.dumps(invalid), json.dumps(valid)]))
    _, result = VideoIntelligence()._request_text_result(client, "prompt",
        [{"id": 0, "start": 0, "end": 2, "asr_text": "你来了"}], [], 0, 2)
    assert result["segments"][0]["needs_review"]


@pytest.mark.parametrize("raw", ["bad JSON", {"segments": []}, {"segments": [{"id": 7}]}])
def test_source_repair_contract_error_stays_reviewable_with_original_asr(free_config, raw):
    client = Mock(translate=Mock(return_value=raw if isinstance(raw, str) else json.dumps(raw)))
    result = VideoIntelligence()._correct_source(client,
        [{"id": 0, "start": 0, "end": 2, "asr_text": "你来了"}], [], "")
    assert result[0]["text_zh"] == "你来了" and result[0]["needs_review"]
    assert result[0]["evidence_ids"] == []
    client.translate.assert_called_once()


def test_source_repair_does_not_swallow_cancellation_or_quota(free_config):
    from core.engines.translation.openrouter_client import OpenRouterClientError
    payload = [{"id": 0, "start": 0, "end": 2, "asr_text": "你来了"}]
    client = Mock(translate=Mock(side_effect=OpenRouterClientError("hạn mức")))
    with pytest.raises(OpenRouterClientError, match="hạn mức"):
        VideoIntelligence()._correct_source(client, payload, [], "")
    with pytest.raises(VideoIntelligenceError, match="hủy"):
        VideoIntelligence()._correct_source(client, payload, [], "", lambda: True)
    assert client.translate.call_count == 1


def test_quota_draft_edit_preserves_review_flags_but_uses_verified_source_placement(free_config, monkeypatch):
    import asyncio
    from pathlib import Path
    from core.subtitle_cues import build_caption_layout
    client, observed = install_responses(monkeypatch)
    result = VideoIntelligence()._translate_text(
        [{"id": 0, "start": 0, "end": 2, "asr_text": "你来了"}], observed, "", 0, 2, quota_fallback=True)
    assert result["segments"][0]["needs_review"]
    assert result["screen_texts"][0]["needs_review"]
    assert result["screen_texts"][0]["source_region_verified"]
    session = StreamingPipelineSession("placement-edit", free_config / "source.mp4", visual_translation=True)
    seg = SegmentItem(0, 0, 2, 2)
    seg.text_zh, seg.final_vi, seg.status, seg.needs_review = "你来了", "Bản nháp", "NEEDS_REVIEW", True
    session.segments[0] = seg
    session.screen_texts = result["screen_texts"]
    session.total_duration = 2
    def synthesize(*, output_path, **kwargs):
        Path(output_path).write_bytes(b"stub")
    def fit(source, output, ratio, **kwargs):
        Path(output).write_bytes(b"fitted")
        return 1
    session.tts_engine = SimpleNamespace(synthesize=synthesize)
    session.aligner = SimpleNamespace(min_speed=.9, max_speed=1.15, get_audio_duration=lambda _: 1,
                                     apply_atempo=fit)
    monkeypatch.setattr("core.streaming.pipeline.trim_tts_padding", lambda _: None)
    monkeypatch.setattr("core.streaming.pipeline.build_speech_timing", lambda *args: {
        "subtitle_cues": [{"start": .2, "end": 1.8, "text": "Anh đã đến rồi."}],
        "subtitle_timing_source": "audio-onset-estimate", "speech_start": .2, "speech_end": 1.8})
    snapshot = asyncio.run(session.edit_segment(0, "Anh đã đến rồi."))
    assert not snapshot["needs_review"]
    assert session.screen_texts[0]["needs_review"] is True
    assert session.screen_texts[0]["mask_only"] is True
    plan = build_caption_layout([snapshot], session.screen_texts, (1080, 1920))
    assert plan["cues"][0]["background"] == "yellow"
    assert plan["cues"][0]["text"] == "Anh đã đến rồi."
