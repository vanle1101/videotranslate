"""Chunk-local OCR IDs must stay distinct in a mixed-language owner's aggregate."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.video_intelligence import VideoIntelligence, VideoIntelligenceError


def screen(start, end):
    return {"id": "o0", "start": start, "end": end,
            "bbox": [.1, .7, .5, .1], "confidence": .98,
            "text_zh": "标题", "text_vi": "Tiêu đề", "kind": "title",
            "needs_review": True, "review_reason": "Cần kiểm tra",
            "source_region_verified": False, "source_method": "local-ocr"}


@pytest.fixture
def aggregate(monkeypatch):
    processor = VideoIntelligence()
    monkeypatch.setattr(processor, "TARGET_CHUNK_SECONDS", 2.)
    processor._source_dialogue = []
    source = [SimpleNamespace(id=i, start=float(i * 2), end=float(i * 2 + 1),
                              text_zh="你好", asr_text="你好") for i in range(2)]
    saved, calls = [], []

    def analyze(path, start, end, rows, summary, cancel_check):
        calls.append((start, end))
        return {"segments": {row.id: {"id": row.id, "start": row.start,
                    "end": row.end, "text_zh": row.text_zh, "literal_vi": "Xin chào",
                    "natural_vi": "Xin chào", "final_vi": "Xin chào",
                    "needs_review": False, "review_reason": ""} for row in rows},
                "screen_texts": [screen(start, end)], "summary": f"summary-{end}"}

    monkeypatch.setattr(processor, "analyze_chunk", analyze)
    monkeypatch.setattr(processor, "_read_checkpoint", lambda stage: deepcopy(saved))

    def write(stage, payload, cancel_check):
        saved[:] = deepcopy(payload)

    monkeypatch.setattr(processor, "_write_checkpoint", write)
    return processor, source, saved, calls


@pytest.mark.parametrize("restored_chunks", [0, 1, 2])
def test_fresh_and_restored_aggregate_revalidate_without_losing_evidence(aggregate, restored_chunks):
    processor, source, saved, calls = aggregate
    expected = processor._prepass(Path("unused.mp4"), source, total_duration=4.)
    # Persisted IDs belong to each provider/OCR chunk and remain unchanged.
    assert [entry["result"]["screen_texts"][0]["id"] for entry in saved] == ["o0", "o0"]
    saved[:] = saved[:restored_chunks]
    calls.clear()
    published = []
    actual = processor._prepass(Path("unused.mp4"), source, total_duration=4.,
        chunk_callback=lambda result, lo, hi: published.append((result, lo, hi)))

    assert actual == expected
    assert len(calls) == 2 - restored_chunks
    assert actual["summary"] == "summary-4.0"
    checked = processor._checked_checkpoint_result(actual, source, 0., 4.)
    assert len({row["id"] for row in checked["screen_texts"]}) == 2
    for index, row in enumerate(checked["screen_texts"]):
        original = screen(index * 2., index * 2. + 2.)
        assert {**row, "id": "o0"} == original
    # Each callback is still independently valid with local IDs.
    for result, lo, hi in published:
        processor._checked_checkpoint_result(result,
            [row for row in source if lo <= row.start < hi], lo, hi)
        assert result["screen_texts"][0]["id"] == "o0"


@pytest.mark.parametrize("corruption", ["duplicate", "bounds", "bbox", "confidence"])
def test_namespace_does_not_rescue_invalid_chunk_evidence(aggregate, monkeypatch, corruption):
    processor, source, saved, calls = aggregate
    original = processor.analyze_chunk

    def corrupt(*args):
        result = original(*args)
        row = result["screen_texts"][0]
        if corruption == "duplicate":
            result["screen_texts"].append(deepcopy(row))
        elif corruption == "bounds":
            row["end"] += 10
        elif corruption == "bbox":
            row["bbox"] = [.9, .9, .5, .5]
        else:
            row["confidence"] = float("nan")
        return result

    monkeypatch.setattr(processor, "analyze_chunk", corrupt)
    with pytest.raises(VideoIntelligenceError, match="OCR"):
        processor._prepass(Path("unused.mp4"), source, total_duration=4.)
    assert not saved
