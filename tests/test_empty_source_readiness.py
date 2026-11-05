"""Empty recognition is unresolved evidence, never an implicit silence verdict."""
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from core.streaming.pipeline import SegmentItem, StreamingPipelineSession
from core.streaming.output_gate import final_output_metadata, preview_output_metadata
from core.streaming import session_store as store
from test_nonvisual_parallel import make_session
from test_session_store import persisted


@pytest.mark.parametrize("status", ["READY", "PLAYED", "WAITING"])
def test_blank_row_cannot_satisfy_preview_or_final_gate(status):
    row = SegmentItem(7, 0, 2, 2)
    row.status, row.subtitle_timing_source = status, "empty"
    for value in (row, row.to_dict()):
        assert not preview_output_metadata([value], lambda _: True)["preview_output_ready"]
        result = final_output_metadata([value], valid_audio=lambda _: True,
                                      source_complete=True, total_duration=2)
        assert result["missing_speech_ids"] == [7]
        assert not result["media_complete"]
    row.confirmed_silence = True
    assert preview_output_metadata([row], lambda _: False)["preview_output_ready"]
    assert final_output_metadata([row], valid_audio=lambda _: False,
                                 source_complete=True, total_duration=2)["media_complete"]


def test_source_complete_without_materialized_rows_remains_speech_free():
    assert final_output_metadata([], valid_audio=lambda _: False,
                                 source_complete=True, total_duration=2)["media_complete"]


def test_missing_native_transcript_never_reaches_review_provider(make_session, monkeypatch):
    session = make_session("missing-native", 1, 20)
    row = session.segments[0]
    row.text_zh, row.source_language = "", "ko"
    provider = Mock(side_effect=AssertionError("no source to review"))
    monkeypatch.setattr("core.streaming.pipeline.review_native_rows", provider)
    with pytest.raises(RuntimeError, match="Chưa nhận diện"):
        asyncio.run(session._process_segment(row))
    assert row.failed_stage == "ASR" and row.needs_review
    provider.assert_not_called()


@pytest.mark.parametrize("source", ["", "原文"])
@pytest.mark.parametrize("review", [False, True])
def test_empty_draft_never_retires_old_audio_or_publishes_ready(make_session, source, review):
    session = make_session("missing-draft", 1, 20)
    row = session.segments[0]
    row.text_zh, row.needs_review = source, review
    path = session.segments_dir / "retained.wav"
    path.write_bytes(b"saved audio must survive")
    row.audio_path, row.audio_url = str(path), "/retained"
    session._ensure_tts_engine = Mock(side_effect=AssertionError("must not synthesize"))
    with pytest.raises(RuntimeError):
        asyncio.run(StreamingPipelineSession._synthesize_segment(session, row))
    assert path.read_bytes() == b"saved audio must survive"
    assert row.audio_path == str(path) and row.audio_url == "/retained"
    assert not row.superseded_audio_paths
    assert row.status == ("TRANSLATING" if source else "ASR")
    assert not row.confirmed_silence
    session._update_ready.assert_not_awaited()


@pytest.mark.parametrize("parallel", [False, True])
def test_empty_asr_is_source_failure_before_translation_or_speech(make_session, parallel):
    session = make_session("empty-recognition", 1, 20)
    row = session.segments[0]
    row.text_zh, row.asr_pretranscribed = "", False
    session.asr_engine = SimpleNamespace(transcribe=Mock(return_value=[]))
    session.raw_audio_16k = session.cache_dir / "source.wav"
    session._run_ffmpeg = AsyncMock()
    session._synthesize_segment = StreamingPipelineSession._synthesize_segment.__get__(session)
    session.translator = SimpleNamespace(translate_single_segment=Mock())
    action = session._recognize_nonvisual_segment if parallel else session._process_segment
    with pytest.raises(RuntimeError, match="Chưa nhận diện"):
        asyncio.run(action(row))
    assert row.status == row.failed_stage == "ASR"
    assert not row.asr_pretranscribed and row.needs_review
    assert not row.confirmed_silence
    session.translator.translate_single_segment.assert_not_called()
    session._update_ready.assert_not_awaited()


@pytest.mark.parametrize("status", ["READY", "PLAYED"])
def test_legacy_blank_ready_restores_as_missing_source_without_rewriting_manifest(persisted, status):
    row = persisted.segments[0]
    row.text_zh = row.final_vi = row.asr_text = ""
    row.status, row.subtitle_timing_source, row.asr_pretranscribed = status, "empty", True
    old_audio = Path(row.audio_path)
    old_bytes = old_audio.read_bytes()
    persisted.persist()
    manifest = store._project_path(persisted.task_id)
    before = manifest.read_bytes()
    assert not store._availability(store._read(persisted.task_id))["ready"]
    restored = store.restore_saved_session(persisted.task_id)
    actual = restored.segments[0]
    assert actual.status == "FAILED" and actual.failed_stage == "ASR"
    assert actual.needs_review and not actual.asr_pretranscribed
    assert restored._startup_failed and restored.can_retry
    assert not restored.get_progress()["media_complete"]
    assert old_audio.read_bytes() == old_bytes
    assert manifest.read_bytes() == before


def test_explicit_silence_still_publishes_ready(make_session):
    session = make_session("confirmed-silence", 1, 20)
    row = session.segments[0]
    row.text_zh, row.confirmed_silence = "", True
    asyncio.run(StreamingPipelineSession._synthesize_segment(session, row))
    assert row.status == "READY" and row.subtitle_timing_source == "empty"
    session._update_ready.assert_awaited_once()


@pytest.mark.parametrize("language", [None, "ko"])
def test_parallel_saved_empty_source_stops_before_provider(make_session, monkeypatch, language):
    from core.streaming import nonvisual_translation as parallel
    session = make_session("parallel-empty", 1, 20)
    row = session.segments[0]
    row.text_zh, row.asr_pretranscribed, row.source_language = "", True, language
    session.translator = SimpleNamespace(translate_single_segment=Mock(side_effect=AssertionError("no source")))
    native = Mock(side_effect=AssertionError("no source"))
    monkeypatch.setattr(parallel, "review_native_rows", native)
    session._synthesize_segment = AsyncMock(side_effect=AssertionError("no draft"))
    asyncio.run(parallel.translate_nonvisual_rows(session, [row]))
    assert row.status == "FAILED" and row.failed_stage == "ASR"
    assert row.needs_review and not row.asr_pretranscribed
    session.translator.translate_single_segment.assert_not_called()
    native.assert_not_called()
    session._synthesize_segment.assert_not_awaited()
