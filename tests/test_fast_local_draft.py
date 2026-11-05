"""Fast local drafts skip automatic pronoun review without hiding source gaps."""
import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from config import settings
from core.streaming.chunked_source import _exclude_rows_over_unresolved_source
from test_session_store import persisted


def test_asr_sentence_crossing_unresolved_gap_is_excluded_with_measured_diagnostic():
    intervals = [
        {"start": 0.0, "end": 10.0, "state": "prepared"},
        {"start": 10.0, "end": 11.0, "state": "unresolved"},
        {"start": 11.0, "end": 20.0, "state": "prepared"},
    ]
    rows = [
        {"id": 0, "start": 8.0, "end": 9.0, "text_zh": "前一句"},
        {"id": 1, "start": 9.8, "end": 11.2, "text_zh": "跨越未识别区域"},
        {"id": 2, "start": 11.2, "end": 12.0, "text_zh": "后一句"},
    ]

    kept, diagnostics = _exclude_rows_over_unresolved_source(rows, intervals)

    assert [row["id"] for row in kept] == [0, 2]
    assert [(row["start"], row["end"]) for row in kept] == [(8.0, 9.0), (11.2, 12.0)]
    assert diagnostics == [{
        "code": "recognized_row_overlaps_unresolved_source", "row_id": 1,
        "start": 9.8, "end": 11.2, "text_zh": "跨越未识别区域",
        "unresolved_spans": [[10.0, 11.0]],
        "action": "excluded_without_timestamp_changes",
    }]


def test_asr_row_outside_all_prepared_or_unresolved_intervals_remains_an_error():
    with pytest.raises(ValueError, match="outside source coverage"):
        _exclude_rows_over_unresolved_source(
            [{"id": 2, "start": 20.0, "end": 21.0, "text_zh": "outside"}],
            [{"start": 0.0, "end": 10.0, "state": "prepared"}],
        )


def test_ollama_chunk_resume_skips_automatic_translation_and_speaker_review(persisted, monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "ollama")
    persisted._chunked_source_started = True
    persisted.visual_translation = True
    row = persisted.segments[0]
    row.source_method = "text-ai"
    row.translation_provider = "ollama"
    row.verification = {"status": "incomplete"}
    review = AsyncMock(side_effect=AssertionError("automatic review must be skipped"))
    monkeypatch.setattr(persisted, "_review_translations", review)

    asyncio.run(persisted._resume_pending_chunk_reviews(synthesize_pending=False))

    review.assert_not_awaited()


def test_ollama_visual_chunk_keeps_draft_flag_and_sends_it_to_tts(persisted, monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "ollama")
    row = persisted.segments[0]
    row.status = "WAITING"
    row.source_method = "text-ai"
    row.translation_provider = "ollama"
    row.final_vi = "Chào bạn"
    row.verification = {"status": "incomplete"}
    persisted.pause_event.set()
    persisted.is_stopped = False
    persisted._persist_if_enabled = Mock()
    persisted.emit = AsyncMock()
    persisted._process_segment = AsyncMock()
    persisted._deferred_speech_identity = Mock(return_value="draft-identity")
    review = AsyncMock(side_effect=AssertionError("automatic review must be skipped"))
    monkeypatch.setattr(persisted, "_review_translations", review)

    asyncio.run(persisted._finish_visual_chunk({row.id}, {row.id}, {row.id: "draft"}))

    review.assert_not_awaited()
    persisted._process_segment.assert_awaited_once_with(row)
    assert row.needs_review is True
    assert row.verification["status"] == "unresolved"
    assert row.verification["draft_mode"] == "fast-local"
