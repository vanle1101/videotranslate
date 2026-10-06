"""Offline contracts for bounded multimodal translation and review recovery."""
import asyncio
import json
from copy import deepcopy
from pathlib import Path
import shutil
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from config import settings
from core.engines.translation.gemini_client import GeminiClient, GeminiError, GeminiIncompleteError
from core.streaming.pipeline import SegmentItem, StreamingPipelineSession
from core.video_intelligence import VideoIntelligence, VideoIntelligenceError


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "gemini")
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "unit-test-key")
    monkeypatch.setattr(settings, "BASE_DIR", tmp_path)
    monkeypatch.setattr(settings, "TEMP_DIR", tmp_path / "temp")
    settings.TEMP_DIR.mkdir()
    return tmp_path


def row(seg, **changes):
    return {"id": seg.id, "start": seg.start, "end": seg.end, "text_zh": "你来了",
            "literal_vi": "Anh đến rồi", "natural_vi": "Anh đến rồi", "final_vi": "Anh đến rồi",
            "needs_review": False, "review_reason": "", **changes}


def response(seg, **changes):
    return {"segments": [row(seg)], "screen_texts": [], "summary": "Hai người gặp nhau", **changes}


def measured_screen(identifier="o0", **changes):
    return {"id": identifier, "start": 3.333333, "end": 4.666667,
            "text_zh": "连赞会限流", "bbox": [.183, .537, .289, .04], "confidence": .981,
            **changes}


def translated_screen(identifier="o0", **changes):
    return {"id": identifier, "text_vi": "Nhấn thích liên tục sẽ bị hạn chế lượt tiếp cận",
            "kind": "title", "needs_review": False, "review_reason": "", **changes}


def test_measured_ocr_ids_geometry_source_and_timing_cannot_be_replaced_by_model():
    seg = SegmentItem(0, 3, 5, 2)
    observed = [measured_screen()]
    original = deepcopy(observed)
    model = translated_screen(start=0, end=999, bbox=[0, 0, 1, 1], text_zh="模型猜的字",
                              confidence=1.0, source_method="guessed", mask_only=True)
    result = VideoIntelligence.validate_result(response(seg, screen_texts=[model]), [seg], 3, 5, observed)
    screen = result["screen_texts"][0]
    for field in ("id", "start", "end", "text_zh", "bbox", "confidence"):
        assert screen[field] == original[0][field]
    assert screen["source_method"] == "local-ocr"
    assert "mask_only" not in screen
    assert screen["text_vi"] == model["text_vi"] and screen["kind"] == "title"
    assert observed == original


@pytest.mark.parametrize("rows", [[], [translated_screen("other")],
                                   [translated_screen(), translated_screen()],
                                   [translated_screen("o0"), translated_screen("o1")],
                                   [translated_screen(None)], [translated_screen(0)],
                                   [translated_screen(True)]])
def test_measured_ocr_missing_duplicate_unknown_and_wrong_id_types_rejected(rows):
    seg = SegmentItem(0, 3, 5, 2)
    with pytest.raises(VideoIntelligenceError):
        VideoIntelligence.validate_result(response(seg, screen_texts=rows), [seg], 3, 5, [measured_screen()])


def test_every_measured_ocr_id_requires_explicit_classification_even_when_ignored():
    seg = SegmentItem(0, 3, 5, 2)
    observed = [measured_screen(), measured_screen("o1", text_zh="水印")]
    result = VideoIntelligence.validate_result(response(seg, screen_texts=[
        translated_screen("o1", kind="ignore", text_vi=""), translated_screen(),
    ]), [seg], 3, 5, observed)
    assert [screen["id"] for screen in result["screen_texts"]] == ["o0"]
    with pytest.raises(VideoIntelligenceError):
        VideoIntelligence.validate_result(response(seg, screen_texts=[translated_screen()]), [seg], 3, 5, observed)


@pytest.mark.parametrize("changes", [{"text_vi": " "}, {"text_vi": "原文未翻译"},
                                      {"needs_review": True, "review_reason": "Chữ bị che, chưa rõ nghĩa"}])
def test_ocr_uncertainty_and_untranslated_text_never_become_verified(changes):
    seg = SegmentItem(0, 3, 5, 2)
    result = VideoIntelligence.validate_result(response(seg, screen_texts=[translated_screen(**changes)]),
                                               [seg], 3, 5, [measured_screen()])
    assert result["screen_texts"][0]["needs_review"] is True
    if "review_reason" in changes:
        assert result["screen_texts"][0]["review_reason"] == changes["review_reason"]


@pytest.mark.parametrize("identifier", [[], {}, ["o0"]])
def test_non_scalar_measured_ocr_ids_fail_with_sanitized_validation_error(identifier):
    seg = SegmentItem(0, 3, 5, 2)
    with pytest.raises(VideoIntelligenceError):
        VideoIntelligence.validate_result(response(seg, screen_texts=[translated_screen(identifier)]),
                                           [seg], 3, 5, [measured_screen()])


@pytest.mark.parametrize("changes", [{"text_vi": "Chữ\x00hỏng"}, {"review_reason": {"unexpected": "data"}},
                                      {"review_reason": ["bad"]}])
def test_measured_ocr_rejects_control_character_and_nontext_reason(changes):
    seg = SegmentItem(0, 3, 5, 2)
    with pytest.raises(VideoIntelligenceError):
        VideoIntelligence.validate_result(response(seg, screen_texts=[translated_screen(**changes)]),
                                           [seg], 3, 5, [measured_screen()])


def test_model_cannot_clear_uncertain_measured_ocr_evidence():
    seg = SegmentItem(0, 3, 5, 2)
    observed = [measured_screen(needs_review=True, review_reason="Vùng chữ bị che")]
    checked = VideoIntelligence.validate_result(response(seg, screen_texts=[translated_screen()]),
                                                [seg], 3, 5, observed)["screen_texts"][0]
    assert checked["needs_review"] is True
    assert "Vùng chữ bị che" in checked["review_reason"]


def test_fallback_cannot_clear_uncertain_speech_from_matching_punctuation(config, monkeypatch):
    seg = SegmentItem(0, 3, 5, 2)
    payload = [{"id": 0, "start": 3, "end": 5, "asr_text": "超过S10个人"}]
    observed = [measured_screen(text_zh="超过@10个人")]
    draft = response(seg, segments=[row(seg, text_zh="超过S10个人", final_vi="Gắn @ hơn 10 người",
                                       needs_review=True, review_reason="Chưa rõ số lượng và ngữ cảnh")],
                     screen_texts=[translated_screen()])
    fallback = Mock()
    fallback.has_credentials = True
    fallback.translate.side_effect = [json.dumps({"segments": [{"id": 0, "text_zh": "超过S10个人",
                                                               "evidence_ids": [], "needs_review": True,
                                                               "review_reason": "Chưa rõ số lượng và ngữ cảnh"}]}),
                                      json.dumps(draft), json.dumps(draft)]
    monkeypatch.setattr("core.video_intelligence.OpenRouterFreeClient", Mock(return_value=fallback))
    processor = VideoIntelligence(client=Mock())
    result = processor._text_fallback(payload, observed, "", 3, 5, GeminiError("quota"))
    checked = result["segments"][0]
    assert checked["needs_review"] is True
    assert checked["review_reason"] == "Chưa rõ số lượng và ngữ cảnh"
    assert checked["text_zh"] == "超过S10个人"
    assert checked["source_method"] == "text-ai" and checked["translation_provider"] == "openrouter-free"
    assert checked["evidence_mode"] == "asr-ocr-text"
    assert result["translation_sources"][0]["provider"] == "openrouter-free"


def test_text_fallback_remains_a_reviewable_draft_even_when_model_claims_certainty(config, monkeypatch):
    seg = SegmentItem(0, 3, 5, 2)
    draft = response(seg, segments=[row(seg, text_zh="你好", final_vi="Xin chào")])
    client = Mock(has_credentials=True, model="free-model")
    client.translate.side_effect = [
        json.dumps({"segments": [{"id": 0, "text_zh": "你好", "evidence_ids": [],
                                  "needs_review": False, "review_reason": ""}]}),
        json.dumps(draft), json.dumps(draft),
    ]
    monkeypatch.setattr("core.video_intelligence.OpenRouterFreeClient", Mock(return_value=client))
    result = VideoIntelligence(client=Mock())._text_fallback(
        [{"id": 0, "start": 3, "end": 5, "asr_text": "你好"}], [], "", 3, 5, GeminiError("quota"))
    checked = result["segments"][0]
    assert checked["final_vi"] == "Xin chào"
    assert checked["needs_review"]
    assert "bản nháp" in checked["review_reason"]
    assert checked["translation_provider"] == "openrouter-free"


def test_nonempty_source_cannot_silently_disappear_as_model_claimed_silence():
    seg = SegmentItem(0, 3, 5, 2)
    seg.text_zh = "你好，等一下"
    result = VideoIntelligence.validate_result(response(seg, segments=[row(
        seg, text_zh="", literal_vi="", natural_vi="", final_vi="")]), [seg], 3, 5)
    assert result["segments"][0]["needs_review"]
    assert result["segments"][0]["review_reason"]


@pytest.mark.parametrize("kind", ["title", "subtitle"])
def test_confident_fallback_screen_draft_cannot_cover_source_in_export(config, monkeypatch, kind):
    from core.subtitle import SubtitleGenerator
    from core.subtitle_cues import normalize_screen_texts

    draft = {"segments": [], "screen_texts": [translated_screen(kind=kind)], "summary": ""}
    client = Mock(has_credentials=True, model="free-model")
    client.translate.side_effect = [json.dumps(draft), json.dumps(draft)]
    monkeypatch.setattr("core.video_intelligence.OpenRouterFreeClient", Mock(return_value=client))
    result = VideoIntelligence(client=Mock())._text_fallback(
        [], [measured_screen()], "", 3, 5, GeminiError("quota"))
    checked = result["screen_texts"][0]
    assert checked["text_vi"] == draft["screen_texts"][0]["text_vi"]
    assert checked["needs_review"] is True
    assert checked["review_reason"]
    assert checked["translation_provider"] == "openrouter-free"
    assert normalize_screen_texts(result["screen_texts"]) == []

    output = config / "draft-screen.ass"
    SubtitleGenerator().generate_ass([], output, screen_texts=result["screen_texts"])
    assert "Dialogue:" not in output.read_text(encoding="utf-8")


@pytest.mark.parametrize("when", ["before", "after"])
def test_fallback_cancellation_prevents_requests_or_discards_results(config, monkeypatch, when):
    seg = SegmentItem(0, 3, 5, 2)
    cancelled = when == "before"
    client = Mock(has_credentials=True, model="free-model")
    def translate(*args, **kwargs):
        nonlocal cancelled
        cancelled = True
        return json.dumps(response(seg))
    client.translate.side_effect = translate
    monkeypatch.setattr("core.video_intelligence.OpenRouterFreeClient", Mock(return_value=client))
    processor = VideoIntelligence(client=Mock())
    with pytest.raises(VideoIntelligenceError, match="hủy"):
        processor._text_fallback([{"id": 0, "start": 3, "end": 5, "asr_text": "你好"}],
                                 [], "", 3, 5, GeminiError("quota"), lambda: cancelled)
    assert client.translate.call_count == (0 if when == "before" else 1)
    assert not processor.used_text_fallback


def test_fallback_batches_large_ocr_result_without_changing_measured_ids(config, monkeypatch):
    observed = [measured_screen(f"o{i}") for i in range(30)]
    client = Mock(has_credentials=True, model="free-model")
    client.translate.side_effect = [
        json.dumps({"segments": [], "screen_texts": [translated_screen(f"o{i}") for i in range(24)], "summary": "tóm tắt 1"}),
        json.dumps({"segments": [], "screen_texts": [translated_screen(f"o{i}") for i in range(24)], "summary": "tóm tắt 1"}),
        json.dumps({"segments": [], "screen_texts": [translated_screen(f"o{i}") for i in range(24, 30)], "summary": "tóm tắt 2"}),
        json.dumps({"segments": [], "screen_texts": [translated_screen(f"o{i}") for i in range(24, 30)], "summary": "tóm tắt 2"}),
    ]
    monkeypatch.setattr("core.video_intelligence.OpenRouterFreeClient", Mock(return_value=client))
    result = VideoIntelligence(client=Mock())._text_fallback([], observed, "", 3, 5, GeminiError("quota"))
    assert client.translate.call_count == 4
    assert [screen["id"] for screen in result["screen_texts"]] == [f"o{i}" for i in range(30)]
    assert all(screen["bbox"] == observed[0]["bbox"] for screen in result["screen_texts"])
    assert result["translation_sources"] == [{"provider": "openrouter-free", "model": "free-model", "evidence_mode": "asr-ocr-text"}]
    assert "ASR KHÔNG phải bản chép chắc chắn đúng" in client.translate.call_args.args[0]


def test_invalid_fallback_response_does_not_persist_raw_transcript(config, monkeypatch):
    client = Mock(has_credentials=True, model="free-model")
    client.translate.return_value = "not JSON private transcript"
    monkeypatch.setattr("core.video_intelligence.OpenRouterFreeClient", Mock(return_value=client))
    with pytest.raises(VideoIntelligenceError):
        VideoIntelligence(client=Mock())._text_fallback([], [], "", 0, 2, GeminiError("quota"))
    assert not list(settings.TEMP_DIR.iterdir())


def test_mixed_chunk_provenance_remains_exact_for_every_row(config, monkeypatch):
    processor = VideoIntelligence(client=Mock())
    segments = [SegmentItem(0, 0, 2, 2), SegmentItem(1, 25, 27, 2)]
    calls = 0
    def analyze(path, start, end, rows, summary, cancel_check):
        nonlocal calls
        provider = "gemini" if calls == 0 else "openrouter-free"
        calls += 1
        return processor._with_provenance({"segments": {s.id: row(s) for s in rows}, "screen_texts": [], "summary": ""}, provider, f"model-{calls}")
    monkeypatch.setattr(processor, "analyze_chunk", analyze)
    result = processor.prepass(config / "video.mp4", segments, 30)
    assert result["segments"][0]["translation_provider"] == "gemini"
    assert result["segments"][1]["translation_provider"] == "openrouter-free"
    assert [source["provider"] for source in result["translation_sources"]] == ["gemini", "openrouter-free"]


def test_validation_preserves_fixed_segments_and_marks_uncertain_text():
    seg = SegmentItem(4, 3, 7, 4)
    raw = response(seg)
    result = VideoIntelligence.validate_result(raw, [seg], 0, 10)
    assert result["segments"][4]["start"] == 3
    for changes in ({"confidence": 0.4}, {"final_vi": "你来了"}, {"literal_vi": ""}, {"needs_review": True}):
        raw["segments"] = [row(seg, **changes)]
        checked = VideoIntelligence.validate_result(raw, [seg], 0, 10)["segments"][4]
        assert checked["needs_review"] and checked["review_reason"]
    raw["segments"] = [row(seg, text_zh="", literal_vi="", natural_vi="", final_vi="")]
    assert not VideoIntelligence.validate_result(raw, [seg], 0, 10)["segments"][4]["needs_review"]


@pytest.mark.parametrize("change", [
    {"segments": []}, {"segments": [{"id": 999}]}, {"segments": "wrong"}, {"screen_texts": None},
])
def test_invalid_contract_does_not_fall_back_or_fill_missing_rows(change):
    seg = SegmentItem(0, 0, 2, 2)
    with pytest.raises(VideoIntelligenceError):
        VideoIntelligence.validate_result(response(seg, **change), [seg], 0, 2)


@pytest.mark.parametrize("change", [{"id": True}, {"id": "0"}, {"start": 1}, {"end": float("nan")},
                                   {"needs_review": "false"}, {"final_vi": 42}])
def test_invalid_id_timing_and_field_types_are_rejected(change):
    seg = SegmentItem(0, 0, 2, 2)
    with pytest.raises(VideoIntelligenceError):
        VideoIntelligence.validate_result(response(seg, segments=[row(seg, **change)]), [seg], 0, 2)


def test_screen_boxes_are_bounded_and_large_boxes_require_review():
    seg = SegmentItem(0, 0, 2, 2)
    screen = {"start": 0, "end": 2, "text_zh": "千金垂爱", "text_vi": "Thiên kim phải lòng",
              "kind": "title", "bbox": [.1, .1, .8, .4], "needs_review": False}
    result = VideoIntelligence.validate_result(response(seg, screen_texts=[screen]), [seg], 0, 2)
    assert result["screen_texts"][0]["needs_review"]
    for changes in ({"bbox": [.9, .1, .8, .1]}, {"end": 4}, {"bbox": [0, 0, float("nan"), .2]}):
        with pytest.raises(VideoIntelligenceError):
            VideoIntelligence.validate_result(response(seg, screen_texts=[{**screen, **changes}]), [seg], 0, 2)


def test_inline_client_closes_and_uses_selected_model_only(config, monkeypatch):
    sdk = Mock()
    sdk.models.generate_content.return_value = SimpleNamespace(
        text='{"segments":[]}', candidates=[SimpleNamespace(finish_reason="STOP")])
    factory = Mock(return_value=sdk)
    monkeypatch.setattr("google.genai.Client", factory)
    assert GeminiClient(model="chosen-model").analyze_media(b"video-bytes", "schema") == '{"segments":[]}'
    call = sdk.models.generate_content.call_args.kwargs
    assert call["model"] == "chosen-model"
    assert call["contents"][1].inline_data.data == b"video-bytes"
    assert call["contents"][1].video_metadata.fps == 3
    assert call["config"]["max_output_tokens"] == 16384
    sdk.files.upload.assert_not_called()
    sdk.close.assert_called_once()
    sdk.models.generate_content.side_effect = RuntimeError("private-key-must-not-leak")
    with pytest.raises(GeminiError) as caught:
        GeminiClient().analyze_media(b"video-bytes", "schema")
    assert "private-key" not in str(caught.value)


def test_wrong_provider_or_missing_key_fails_before_media(config, monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "muse")
    with pytest.raises(VideoIntelligenceError):
        VideoIntelligence(client=Mock())
    with pytest.raises(ValueError):
        StreamingPipelineSession("wrong-provider", config / "video.mp4", visual_translation=True)
    assert not (config / "workspace" / "cache" / "wrong-provider").exists()
    monkeypatch.setattr(settings, "LLM_PROVIDER", "gemini")
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    with pytest.raises(VideoIntelligenceError):
        VideoIntelligence(client=Mock())


def test_chunk_boundaries_preserve_ids_and_carry_context(config, monkeypatch):
    intelligence = VideoIntelligence(client=Mock())
    intelligence.TARGET_CHUNK_SECONDS = 45
    segments = [SegmentItem(0, 0, 5, 5), SegmentItem(1, 43, 49, 6), SegmentItem(2, 88, 92, 4)]
    calls = []
    def analyze(path, start, end, rows, summary, cancel_check):
        calls.append((start, end, [s.id for s in rows], summary))
        return {"segments": {s.id: row(s) for s in rows}, "screen_texts": [], "summary": f"chunk-{len(calls)}"}
    monkeypatch.setattr(intelligence, "analyze_chunk", analyze)
    result = intelligence.prepass(config / "video.mp4", segments, total_duration=98)
    assert calls == [(0, 43, [0], ""), (43, 88, [1], "chunk-1"), (88, 98, [2], "chunk-2")]
    assert set(result["segments"]) == {0, 1, 2}


def test_truncation_reduces_chunk_once_without_provider_fallback(config, monkeypatch):
    intelligence = VideoIntelligence(client=Mock())
    calls = []
    def analyze(path, start, end, rows, summary, cancel_check):
        calls.append((start, end))
        if len(calls) == 1:
            raise GeminiIncompleteError("MAX_TOKENS")
        return {"segments": {}, "screen_texts": [], "summary": ""}
    monkeypatch.setattr(intelligence, "analyze_chunk", analyze)
    intelligence.prepass(config / "video.mp4", [], total_duration=24)
    assert calls == [(0, 24), (0, 12), (12, 24)]
    analyze_twice = Mock(side_effect=GeminiIncompleteError("MAX_TOKENS"))
    monkeypatch.setattr(intelligence, "analyze_chunk", analyze_twice)
    with pytest.raises(GeminiIncompleteError):
        intelligence.prepass(config / "video.mp4", [], total_duration=24)
    assert analyze_twice.call_count == 2
    blocked = Mock(side_effect=GeminiIncompleteError("SAFETY"))
    monkeypatch.setattr(intelligence, "analyze_chunk", blocked)
    with pytest.raises(GeminiIncompleteError):
        intelligence.prepass(config / "video.mp4", [], total_duration=24)
    assert blocked.call_count == 1


def test_cancellation_between_chunks_prevents_more_calls(config, monkeypatch):
    intelligence = VideoIntelligence(client=Mock())
    cancelled = False
    calls = []
    def analyze(*args):
        nonlocal cancelled
        calls.append(args)
        cancelled = True
        return {"segments": {}, "screen_texts": [], "summary": ""}
    monkeypatch.setattr(intelligence, "analyze_chunk", analyze)
    with pytest.raises(VideoIntelligenceError, match="hủy"):
        intelligence.prepass(config / "video.mp4", [], total_duration=100, cancel_check=lambda: cancelled)
    assert len(calls) == 1


def test_encoder_keeps_audio_and_removes_its_temporary_file(config, monkeypatch):
    intelligence = VideoIntelligence(client=Mock())
    commands = []
    def media(cmd, cancel_check):
        commands.append(cmd)
        Path(cmd[-1]).write_bytes(b"compressed-video")
    monkeypatch.setattr("core.video_intelligence.run_media", media)
    assert intelligence._encode_chunk(config / "video.mp4", 0, 32) == b"compressed-video"
    assert "-an" not in commands[0] and "0:a:0?" in commands[0]
    assert commands[0][commands[0].index("-r") + 1] == "3"
    assert not list(settings.TEMP_DIR.iterdir())
    monkeypatch.setattr("core.video_intelligence.run_media", Mock(side_effect=RuntimeError("encoding failed")))
    with pytest.raises(RuntimeError):
        intelligence._encode_chunk(config / "video.mp4", 0, 32)
    assert not list(settings.TEMP_DIR.iterdir())


def test_second_media_pass_reviews_draft_and_catches_unsupported_completion(config, monkeypatch):
    seg = SegmentItem(0, 0, 2, 2)
    draft = response(seg)
    verified = response(seg, segments=[row(seg, final_vi="Còn...", needs_review=True,
                                           review_reason="Câu bị cắt ở cuối video")])
    client = Mock()
    client.analyze_media.side_effect = [json.dumps(draft), json.dumps(verified)]
    intelligence = VideoIntelligence(client=client)
    monkeypatch.setattr(intelligence, "_encode_chunk", Mock(return_value=b"same-compressed-video"))
    monkeypatch.setattr(intelligence.screen_ocr, "extract", Mock(return_value=[]))
    monkeypatch.setattr(intelligence, "_encode_contact_sheet", Mock(return_value=b"contact-sheet"))
    result = intelligence.analyze_chunk(config / "video.mp4", 0, 2, [seg])
    assert result["segments"][0]["needs_review"]
    assert client.analyze_media.call_count == 2
    assert all(call.args[0] == b"same-compressed-video" for call in client.analyze_media.call_args_list)
    assert "BẢN NHÁP CHƯA XÁC MINH" in client.analyze_media.call_args_list[1].args[1]
    assert "KHÔNG suy đoán, thay đổi hoặc trả lại" in client.analyze_media.call_args_list[1].args[1]
    assert "bbox phải theo" not in client.analyze_media.call_args_list[1].args[1]


def test_context_overlap_does_not_change_requested_ids_or_output_window(config, monkeypatch):
    seg = SegmentItem(7, 24, 26, 2)
    client = Mock()
    client.analyze_media.return_value = json.dumps(response(seg))
    intelligence = VideoIntelligence(client=client)
    intelligence._video_duration = 30
    monkeypatch.setattr(intelligence.screen_ocr, "extract", Mock(return_value=[]))
    encode = Mock(return_value=b"media")
    monkeypatch.setattr(intelligence, "_encode_chunk", encode)
    monkeypatch.setattr(intelligence, "_encode_contact_sheet", Mock(return_value=b"sheet"))
    result = intelligence.analyze_chunk(config / "video.mp4", 24, 26, [seg])
    assert encode.call_args.args[1:3] == (22.5, 27.5)
    assert list(result["segments"]) == [7]
    assert result["segments"][7]["start"] == 24
    assert "[24.000, 26.000]" in client.analyze_media.call_args.args[1]


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg unavailable")
def test_contact_sheet_encodes_a_real_tiny_tail_and_cleans_jpeg(config):
    from core.media_process import run_media
    source = config / "tiny.mp4"
    run_media(["ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i",
               "color=c=gray:s=160x90:r=25", "-t", "0.12", "-an", str(source)])
    intelligence = VideoIntelligence(client=Mock())
    image = intelligence._encode_contact_sheet(source, 0, .12)
    assert image.startswith(b"\xff\xd8")
    assert not list(settings.TEMP_DIR.iterdir())


def test_review_rows_reach_draft_tts_without_repeating_asr_or_translation(config, monkeypatch):
    from unittest.mock import AsyncMock
    sess = StreamingPipelineSession("visual-review", config / "video.mp4", visual_translation=True)
    seg = SegmentItem(0, 0, 2, 2)
    seg.source_method, seg.needs_review = "video-ai", True
    seg.final_vi, seg.review_reason = "Bản nháp chưa rõ", "Âm thanh bị che"
    sess.segments = {0: seg}
    sess.total_duration = 2
    sess.asr_engine = Mock()
    sess.translator = Mock()
    sess.tts_engine = Mock()
    synthesize = AsyncMock()
    monkeypatch.setattr(sess, "_synthesize_segment", synthesize)
    asyncio.run(sess._process_segment(seg))
    synthesize.assert_awaited_once_with(seg)
    assert seg.needs_review and seg.review_reason == "Âm thanh bị che"
    sess.asr_engine.transcribe.assert_not_called()
    sess.translator.translate_single_segment.assert_not_called()


def test_validated_rows_bypass_asr_and_translation(config, monkeypatch):
    sess = StreamingPipelineSession("visual-valid", config / "video.mp4", visual_translation=True)
    seg = SegmentItem(0, 0, 2, 2)
    seg.source_method, seg.final_vi = "video-ai", "Xin chào"
    from unittest.mock import AsyncMock
    synthesize = AsyncMock()
    monkeypatch.setattr(sess, "_synthesize_segment", synthesize)
    sess.asr_engine = Mock()
    sess.translator = Mock()
    asyncio.run(sess._process_segment(seg))
    synthesize.assert_awaited_once_with(seg)
    sess.asr_engine.transcribe.assert_not_called()
    sess.translator.translate_single_segment.assert_not_called()


def test_silent_video_fails_explicitly_before_cloud_usage(config, monkeypatch):
    sess = StreamingPipelineSession("silent-visual", config / "video.mp4", visual_translation=True)
    sess.video_intelligence.media_info = Mock(return_value={"duration": 10, "has_audio": False})
    title = {"start": 0, "end": 10, "text_zh": "你好", "text_vi": "Xin chào", "kind": "title",
             "bbox": [.1, .1, .6, .1], "needs_review": False}
    sess.video_intelligence.prepass = Mock(return_value={"segments": {}, "screen_texts": [title]})
    sess.segmenter.segment_audio = Mock()
    sess.vocal_suppressor.process_file = Mock()
    sess.edge_tts.synthesize = Mock()
    async def run():
        with pytest.raises(ValueError, match="không có luồng âm thanh"):
            await sess.start()
    asyncio.run(run())
    assert sess.screen_texts == [] and sess.get_progress()["status"] == "FAILED"
    sess.video_intelligence.prepass.assert_not_called()
    sess.segmenter.segment_audio.assert_not_called()
    sess.vocal_suppressor.process_file.assert_not_called()
    sess.edge_tts.synthesize.assert_not_called()
