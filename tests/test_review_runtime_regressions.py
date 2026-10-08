"""Offline regressions; no provider or production cache is used."""
import json
from unittest.mock import Mock

import pytest

from config import settings
from core.runtime_context import execution_context
from core.translation_review import AutomaticTranslationReviewer


def source(index=0):
    return {"id": index, "start": float(index), "end": float(index + 1),
            "text_zh": "你好", "literal_vi": "Bản cũ", "natural_vi": "Bản cũ",
            "final_vi": "Bản cũ", "needs_review": False, "review_reason": ""}


def components():
    client = Mock(has_credentials=True, model="offline-probe")
    scanner = Mock()
    scanner.extract.side_effect = lambda path, start, end, check: [
        {"start": float(i), "end": float(i + 1), "text_zh": "你好",
         "confidence": .99, "bbox": [.2, .7, .5, .1]}
        for i in range(int(start), int(end))]
    prompts = []
    def respond(prompt, **kwargs):
        prompts.append(prompt)
        rows = json.loads(prompt.split("Câu cần kiểm định: ", 1)[1].split("\nOCR mới tại máy", 1)[0])
        return {"segments": [{**row, "literal_vi": "Xin chào", "natural_vi": "Xin chào",
            "final_vi": "Xin chào", "needs_review": False, "review_reason": "",
            "semantic_verified": True, "verification_reason": "Đối chiếu lời chào.",
            "source_evidence_ids": [f"review{row['id']}"]} for row in rows],
            "screen_texts": [], "summary": ""}
    client.translate.side_effect = respond
    return client, scanner, prompts


@pytest.fixture(autouse=True)
def provider(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "opencode")


def test_later_batch_uses_evidence_qualified_corrected_context():
    client, scanner, prompts = components()
    reviewer = AutomaticTranslationReviewer(client, scanner, audio_evidence=False)
    result = reviewer.review("unused", [source(i) for i in range(13)], [])
    context = json.loads(prompts[2].split("Ngữ cảnh lân cận (không tạo thêm ID): ", 1)[1])
    assert result["segments"][10]["verification"]["status"] == "corrected"
    assert next(row for row in context if row["start"] == 10)["final_vi"] == "Xin chào"


def test_subset_review_retains_remote_source_context_without_reviewing_other_ids():
    client, scanner, prompts = components()
    reviewer = AutomaticTranslationReviewer(client, scanner, audio_evidence=False)
    target = source(12)
    prior = {**source(0), "text_zh": "拜托姐"}
    later = {**source(30), "text_zh": "下一句"}
    reviewer._address_reading = Mock(return_value={})
    result = reviewer.review("unused", [target], [], context_segments=[prior, target, later])
    context = json.loads(prompts[0].split("Ngữ cảnh lân cận (không tạo thêm ID): ", 1)[1])
    assert next(row for row in context if row["id"] == 0)["text_zh"] == "拜托姐"
    assert next(row for row in context if row["id"] == 30)["text_zh"] == "下一句"
    assert set(result["segments"]) == {12}
    assert result["summary"]["checked"] == 1
    assert scanner.extract.call_args.args[1:3] == (12., 13.)


def test_resume_reuses_verified_work_but_force_review_runs_again(tmp_path):
    video = tmp_path / "input.mp4"
    video.write_bytes(b"offline-source-identity")
    client, scanner, _ = components()
    reviewer = AutomaticTranslationReviewer(client, scanner, audio_evidence=False,
                                             checkpoint_dir=tmp_path / "checkpoints")
    first = reviewer.review(video, [source()], [])
    second = reviewer.review(video, [source()], [])
    assert first == second
    assert client.translate.call_count == 2
    assert scanner.extract.call_count == 1
    reviewer.review(video, [source()], [], force_review=True)
    assert client.translate.call_count == 4
    assert scanner.extract.call_count == 2


def test_audio_failure_retains_safe_diagnostic_and_segment_ids(caplog):
    client, scanner, _ = components()
    audio = Mock()
    audio.collect.side_effect = RuntimeError("secret-token-do-not-log decoder failed")
    reviewer = AutomaticTranslationReviewer(client, scanner, audio)
    data = {"segments": {0: {**source(), "needs_review": True,
        "verification": {"source_supported": False}}}, "screen_texts": [], "translation_sources": []}
    with execution_context("review-regression"):
        result = reviewer.resolve_audio_uncertainty("unused", data)
    diagnostic = result["segments"][0]["verification"]["diagnostic"]
    assert diagnostic["code"] == "asr_failed"
    assert diagnostic["run_id"] == "review-regression"
    assert diagnostic["segment_ids"] == [0]
    assert "REVIEW_STAGE_FAILED" in caplog.text
    assert "secret-token-do-not-log" not in caplog.text + json.dumps(result)
