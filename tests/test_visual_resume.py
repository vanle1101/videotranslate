"""Offline regression tests for real prepass interruption/resume boundaries."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from config import settings
from core.engines.translation.opencode_client import OpenCodeClientError
from core.video_intelligence import VideoIntelligence, VideoIntelligenceError


@pytest.fixture
def source(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(settings, "LLM_PROVIDER", "opencode")
    monkeypatch.setattr(settings, "OPENCODE_MODEL", "test-model")
    monkeypatch.setattr(settings, "OPENCODE_API_KEY", "must-not-be-written-to-checkpoint")
    video = tmp_path / "source.mp4"
    video.write_bytes(b"source identity only; media and provider are isolated in this test")
    return video


def segments():
    return [SimpleNamespace(id=0, start=0., end=2., text_zh="你好"),
            SimpleNamespace(id=1, start=24., end=26., text_zh="再见")]


def result_for(rows, summary="previous context"):
    return {"segments": {seg.id: {"id": seg.id, "start": seg.start, "end": seg.end,
            "text_zh": seg.text_zh, "literal_vi": "Xin chào.", "natural_vi": "Xin chào.",
            "final_vi": "Xin chào.", "needs_review": False, "review_reason": None,
            "source_evidence_ids": ["o0"]} for seg in rows},
            "screen_texts": [], "summary": summary,
            "translation_sources": [{"provider": "opencode", "model": "test-model", "evidence_mode": "asr-ocr-text"}]}


def test_bounded_prepass_owns_only_global_interval_and_keeps_context(source, monkeypatch):
    intelligence = VideoIntelligence()
    rows = segments()
    calls = []
    def analyze(path, start, end, owned, summary, cancel):
        calls.append((start, end, [row.id for row in owned], list(intelligence._source_dialogue), summary))
        return result_for(owned)
    monkeypatch.setattr(intelligence, "analyze_chunk", analyze)
    result = intelligence.prepass(source, rows[1:], total_duration=48, start_time=24, end_time=48,
                                  context_segments=rows, previous_summary="previous exchange")
    assert set(result["segments"]) == {1}
    assert calls[0][:3] == (24, 48, [1])
    assert [row["id"] for row in calls[0][3]] == [0, 1]
    assert calls[0][4] == "previous exchange"
    assert intelligence._source_dialogue == []
    calls.clear()
    intelligence.prepass(source, rows[1:], total_duration=48, start_time=24, end_time=48,
                          context_segments=rows, previous_summary="previous exchange")
    assert not calls, "Exact interval resumes from its validated checkpoint"
    intelligence.prepass(source, rows[1:], total_duration=48, start_time=24, end_time=48,
                          context_segments=rows, previous_summary="changed exchange")
    assert calls, "Changed context must invalidate its provider cache"


def test_bounded_dense_interval_publishes_four_speech_rows_before_next_request(source, monkeypatch):
    intelligence = VideoIntelligence()
    rows = [SimpleNamespace(id=sid, start=sid * 2., end=sid * 2. + 1., text_zh="你好") for sid in range(10)]
    lifecycle = []
    def analyze(path, start, end, owned, summary, cancel):
        lifecycle.append(("request", [row.id for row in owned]))
        return result_for(owned)
    def publish(result, start, end):
        lifecycle.append(("publish", list(result["segments"])))
    monkeypatch.setattr(intelligence, "analyze_chunk", analyze)
    result = intelligence.prepass(source, rows, total_duration=7200, start_time=0, end_time=24,
                                  context_segments=rows, chunk_callback=publish)
    assert set(result["segments"]) == set(range(10))
    assert lifecycle == [("request", [0, 1, 2, 3]), ("publish", [0, 1, 2, 3]),
                         ("request", [4, 5, 6, 7]), ("publish", [4, 5, 6, 7]),
                         ("request", [8, 9]), ("publish", [8, 9])]


@pytest.mark.parametrize("start,end", [(-1, 24), (24, 49), (30, 24), (0, 25)])
def test_bounded_prepass_rejects_invalid_range_before_provider(source, monkeypatch, start, end):
    intelligence = VideoIntelligence()
    provider = Mock()
    monkeypatch.setattr(intelligence, "analyze_chunk", provider)
    with pytest.raises(VideoIntelligenceError):
        intelligence.prepass(source, segments()[1:], total_duration=48, start_time=start, end_time=end)
    provider.assert_not_called()


def test_text_batch_cache_key_includes_wider_dialogue_and_source_corrections():
    payload = [{"id": 1, "start": 1.0, "end": 2.0, "asr_text": "这话应该我来问吧"}]
    observed = []
    base = {"speech": payload, "wider_source_dialogue": [
        {"id": 0, "start": 0.0, "end": 1.0, "text_zh": "拜托姐"},
        {"id": 1, "start": 1.0, "end": 2.0, "text_zh": payload[0]["asr_text"]},
    ], "ocr": []}
    changed = {**base, "wider_source_dialogue": [
        {"id": 0, "start": 0.0, "end": 1.0, "text_zh": "拜托哥"},
        base["wider_source_dialogue"][1],
    ]}

    first = VideoIntelligence._text_batch_context_digest(payload, observed, "", base)
    second = VideoIntelligence._text_batch_context_digest(payload, observed, "", changed)
    assert first != second


def test_source_correction_updates_private_context_without_mutating_input_rows():
    source_dialogue = [{"id": 0, "start": 0.0, "end": 1.0, "asr_text": "旧识别"},
                       {"id": 1, "start": 1.0, "end": 2.0, "asr_text": "拜托姐"}]
    original = json.loads(json.dumps(source_dialogue, ensure_ascii=False))
    corrections = {0: {"text_zh": "修正后的句子", "evidence_ids": ["ocr0"],
                       "needs_review": False, "source_supported": True, "review_reason": ""}}

    VideoIntelligence._apply_source_corrections_to_context(source_dialogue, corrections)

    assert source_dialogue[0]["text_zh"] == "修正后的句子"
    assert source_dialogue[0]["asr_text"] == "旧识别"
    assert source_dialogue[1]["asr_text"] == "拜托姐"
    assert source_dialogue[0].get("source_needs_review") is None
    assert source_dialogue[0].get("source_evidence_ids") == ["ocr0"]
    assert original[0].get("text_zh") is None


def test_source_correction_with_unrelated_ocr_id_stays_asr_and_uncertain():
    intelligence = VideoIntelligence()
    client = Mock()
    client.translate.return_value = json.dumps({"segments": [{
        "id": 0, "text_zh": "拜托哥", "evidence_ids": ["ocr0"],
        "needs_review": False, "review_reason": "",
    }]}, ensure_ascii=False)
    payload = [{"id": 0, "start": 0.0, "end": 2.0, "asr_text": "拜托姐"}]
    observed = [{"id": "ocr0", "start": 0.0, "end": 2.0,
                 "text_zh": "拜托姐", "confidence": .99, "kind": "subtitle"}]

    result = intelligence._correct_source_checked(client, payload, observed, "context")

    assert result[0]["text_zh"] == "拜托姐"
    assert result[0]["evidence_ids"] == []
    assert result[0]["needs_review"] is True


def test_accepted_chunk_source_correction_reaches_next_chunk_context_only():
    source_dialogue = [{"id": 0, "start": 0.0, "end": 1.0, "asr_text": "旧识别"},
                       {"id": 1, "start": 24.0, "end": 25.0, "asr_text": "下一句"}]
    result = {"segments": {0: {"id": 0, "text_zh": "拜托姐", "needs_review": False,
                               "source_evidence_ids": ["ocr0"]}}}

    VideoIntelligence._merge_chunk_source_context(source_dialogue, result)

    assert source_dialogue[0]["text_zh"] == "拜托姐"
    assert source_dialogue[0]["source_evidence_ids"] == ["ocr0"]
    assert source_dialogue[1].get("text_zh") is None


def interrupt_after_first(source, monkeypatch):
    first = VideoIntelligence()
    calls = []

    def analyze(path, start, end, rows, summary, cancel_check):
        calls.append(start)
        if start:
            raise OpenCodeClientError("provider request timed out")
        return result_for(rows)

    monkeypatch.setattr(first, "analyze_chunk", analyze)
    with pytest.raises(OpenCodeClientError, match="timed out"):
        first.prepass(source, segments(), 48)
    assert calls == [0., 24.]
    return first


def test_new_instance_resumes_only_remaining_chunks_and_previous_context(source, monkeypatch):
    first = interrupt_after_first(source, monkeypatch)
    assert first._checkpoint_context is None
    checkpoints = list(source.parent.glob("cache/visual_checkpoints/*/*.json"))
    assert len(checkpoints) == 1
    assert "must-not-be-written" not in checkpoints[0].read_text(encoding="utf-8")
    second = VideoIntelligence()
    analyze = Mock(side_effect=lambda path, start, end, rows, summary, cancel: result_for(rows, "new context"))
    monkeypatch.setattr(second, "analyze_chunk", analyze)
    progress = []
    final = second.prepass(source, segments(), 48, progress_callback=progress.append)
    assert analyze.call_count == 1
    assert analyze.call_args.args[1:3] == (24., 48)
    assert analyze.call_args.args[4] == "previous context"
    assert set(final["segments"]) == {0, 1}
    assert final["segments"][0]["source_evidence_ids"] == ["o0"]
    assert progress == [50., 100.]
    third = VideoIntelligence()
    monkeypatch.setattr(third, "analyze_chunk", Mock(side_effect=AssertionError("completed source must not repeat")))
    assert third.prepass(source, segments(), 48) == final


def test_chunk_callback_is_detached_and_restores_before_later_timeout(source, monkeypatch):
    first = VideoIntelligence()
    published = []

    def publish(result, start, end):
        published.append((set(result["segments"]), start, end))
        result["segments"][0]["final_vi"] = "Callback must not change the checkpoint"

    def analyze(path, start, end, rows, summary, cancel):
        if start:
            assert published == [({0}, 0., 24.)]
            raise OpenCodeClientError("later timeout")
        return result_for(rows)

    monkeypatch.setattr(first, "analyze_chunk", analyze)
    with pytest.raises(OpenCodeClientError):
        first.prepass(source, segments(), 48, chunk_callback=publish)
    restored = []
    second = VideoIntelligence()
    monkeypatch.setattr(second, "analyze_chunk", Mock(side_effect=OpenCodeClientError("still unavailable")))
    with pytest.raises(OpenCodeClientError):
        second.prepass(source, segments(), 48, chunk_callback=lambda result, start, end: restored.append(result))
    assert restored[0]["segments"][0]["final_vi"] == "Xin chào."


@pytest.mark.parametrize("corruption", ["truncated", "digest", "missing_output", "false_complete"])
def test_corrupt_or_false_complete_checkpoint_cannot_skip_work(source, monkeypatch, corruption):
    interrupt_after_first(source, monkeypatch)
    path = next(source.parent.glob("cache/visual_checkpoints/*/*.json"))
    record = json.loads(path.read_text(encoding="utf-8"))
    if corruption == "truncated":
        path.write_text('{"payload":', encoding="utf-8")
    else:
        if corruption == "digest":
            record["digest"] = "bad"
        else:
            record["payload"][0]["result"]["segments"] = {}
            if corruption == "false_complete":
                record["payload"][0]["end"] = 48
                record["payload"][0]["result"]["status"] = "completed"
            record["digest"] = VideoIntelligence._checkpoint_digest(record["payload"])
        path.write_text(json.dumps(record), encoding="utf-8")
    second = VideoIntelligence()
    analyze = Mock(side_effect=lambda path, start, end, rows, summary, cancel: result_for(rows))
    monkeypatch.setattr(second, "analyze_chunk", analyze)
    assert set(second.prepass(source, segments(), 48)["segments"]) == {0, 1}
    assert analyze.call_count == 2


@pytest.mark.parametrize("change", ["source", "model", "recognition", "chunking"])
def test_source_and_configuration_changes_invalidate_resume(source, monkeypatch, change):
    interrupt_after_first(source, monkeypatch)
    rows = segments()
    if change == "source":
        source.write_bytes(b"changed source")
    elif change == "model":
        monkeypatch.setattr(settings, "OPENCODE_MODEL", "different-model")
    elif change == "recognition":
        rows[0].text_zh = "新的文字"
    second = VideoIntelligence()
    if change == "chunking":
        second.TARGET_CHUNK_SECONDS = 23
    analyze = Mock(side_effect=lambda path, start, end, rows, summary, cancel: result_for(rows))
    monkeypatch.setattr(second, "analyze_chunk", analyze)
    second.prepass(source, rows, 48)
    assert analyze.call_args_list[0].args[1] == 0.


def test_cancel_keeps_only_previously_finished_chunks(source, monkeypatch):
    cancelled = False
    first = VideoIntelligence()

    def progress(value):
        nonlocal cancelled
        cancelled = True

    monkeypatch.setattr(first, "analyze_chunk", lambda path, start, end, rows, summary, cancel: result_for(rows))
    with pytest.raises(VideoIntelligenceError, match="hủy"):
        first.prepass(source, segments(), 48, cancel_check=lambda: cancelled, progress_callback=progress)
    second = VideoIntelligence()
    analyze = Mock(side_effect=lambda path, start, end, rows, summary, cancel: result_for(rows))
    monkeypatch.setattr(second, "analyze_chunk", analyze)
    second.prepass(source, segments(), 48)
    assert analyze.call_count == 1
    assert analyze.call_args.args[1] == 24.


def test_cancelled_inflight_result_is_never_saved(source, monkeypatch):
    cancelled = False
    first = VideoIntelligence()

    def analyze(path, start, end, rows, summary, cancel):
        nonlocal cancelled
        cancelled = True
        return result_for(rows)

    monkeypatch.setattr(first, "analyze_chunk", analyze)
    with pytest.raises(VideoIntelligenceError, match="hủy"):
        first.prepass(source, segments(), 48, cancel_check=lambda: cancelled)
    assert not list(source.parent.glob("cache/visual_checkpoints/*/*.json"))
    assert not list(source.parent.glob("cache/visual_checkpoints/*/*.tmp"))


def test_completed_ocr_survives_later_provider_failure(source, monkeypatch):
    observed = [{"id": "o0", "start": 0., "end": 2., "text_zh": "你好",
                 "bbox": [.1, .8, .4, .1], "confidence": .99}]
    first = VideoIntelligence()
    extract = Mock(return_value=observed)
    monkeypatch.setattr(first.screen_ocr, "extract", extract)
    monkeypatch.setattr(first, "_translate_text", Mock(side_effect=OpenCodeClientError("timeout")))
    with pytest.raises(OpenCodeClientError):
        first.prepass(source, segments()[:1], 2)
    extract.assert_called_once()
    second = VideoIntelligence()
    monkeypatch.setattr(second.screen_ocr, "extract", Mock(side_effect=AssertionError("OCR must resume")))
    translate = Mock(return_value=result_for(segments()[:1]))
    monkeypatch.setattr(second, "_translate_text", translate)
    assert 0 in second.prepass(source, segments()[:1], 2)["segments"]
    assert translate.call_args.args[1] == observed


def test_verified_text_batch_survives_next_batch_timeout(source, monkeypatch):
    rows = [SimpleNamespace(id=i, start=float(i), end=i + .8, text_zh="你好") for i in range(13)]
    monkeypatch.setattr("core.video_intelligence.OpenCodeZenClient", Mock(return_value=SimpleNamespace(has_credentials=True, model="test-model")))
    requests = []

    def request(client, prompt, payload, observed, start, end, cancel):
        requests.append([item["id"] for item in payload])
        if len(requests) == 3:
            raise OpenCodeClientError("timeout in batch two")
        result = result_for([SimpleNamespace(id=item["id"], start=item["start"], end=item["end"], text_zh=item["asr_text"]) for item in payload], f"batch-{payload[0]['id']}")
        return "validated raw", result

    def correction(client, payload, observed, context, cancel):
        return {item["id"]: {"text_zh": item["asr_text"], "evidence_ids": [], "needs_review": False, "review_reason": ""} for item in payload}

    first = VideoIntelligence()
    monkeypatch.setattr(first.screen_ocr, "extract", Mock(return_value=[]))
    monkeypatch.setattr(first, "_correct_source", correction)
    monkeypatch.setattr(first, "_request_text_result", request)
    with pytest.raises(VideoIntelligenceError, match="timeout"):
        first.prepass(source, rows, 13)
    assert requests == [list(range(4)), list(range(4)), list(range(4, 8))]
    second = VideoIntelligence()
    monkeypatch.setattr(second.screen_ocr, "extract", Mock(side_effect=AssertionError("OCR already retained")))
    monkeypatch.setattr(second, "_correct_source", correction)
    monkeypatch.setattr(second, "_request_text_result", request)
    final = second.prepass(source, rows, 13)
    assert requests[3:] == [list(range(4, 8)), list(range(4, 8)),
                           list(range(8, 12)), list(range(8, 12)), [12], [12]]
    assert set(final["segments"]) == set(range(13))
    assert all(item["needs_review"] for item in final["segments"].values())


def test_source_mutated_during_execution_does_not_publish_checkpoint(source, monkeypatch):
    first = VideoIntelligence()

    def analyze(path, start, end, rows, summary, cancel):
        source.write_bytes(b"replaced while processing")
        return result_for(rows)

    monkeypatch.setattr(first, "analyze_chunk", analyze)
    with pytest.raises(VideoIntelligenceError, match="thay đổi"):
        first.prepass(source, segments(), 48)
    assert not list(source.parent.glob("cache/visual_checkpoints/*/*.json"))
