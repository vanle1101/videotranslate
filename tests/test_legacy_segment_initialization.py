import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from config import settings
from core.streaming import pipeline
from core.streaming.nonvisual_translation import translate_nonvisual_rows
from core.streaming.pipeline import SegmentItem, StreamingPipelineSession


@pytest.fixture
def legacy_session(tmp_path, monkeypatch):
    for name in ("BASE_DIR", "WORKSPACE_DIR", "INPUT_DIR", "OUTPUT_DIR", "TEMP_DIR"):
        directory = tmp_path if name == "BASE_DIR" else tmp_path / name
        directory.mkdir(exist_ok=True)
        monkeypatch.setattr(settings, name, directory)
    session = StreamingPipelineSession("legacy-init", tmp_path / "source.mp4",
        asr_engine_name="sensevoice", translation_mode="full", visual_translation=False)
    session.segmenter = SimpleNamespace(
        get_audio_duration=Mock(return_value=4.),
        segment_audio=Mock(return_value=[
            {"id": 0, "start": 0., "end": 2., "duration": 2.},
            {"id": 1, "start": 2., "end": 4., "duration": 2.},
        ]))
    session.sensevoice = SimpleNamespace(
        is_available=True, name="SenseVoice", recognizer=None,
        transcribe=Mock(return_value=[{"text_zh": "你好", "source_language": "zh",
            "source_language_probability": .4, "source_language_uncertain": True,
            "source_audio_evidence": {"engine": "sensevoice", "text_zh": "你好"}}]))
    session.faster_whisper = SimpleNamespace(name="Faster-Whisper", model=None,
        transcribe=Mock(side_effect=AssertionError("Unexpected Whisper dispatch")))
    session.asr_engine = session.sensevoice
    session.is_running = True
    session.is_stopped = False
    session.vocal_suppressor = SimpleNamespace(name="offline", suppression_level_db=0.,
        process_file=Mock(return_value={}))
    session.translator = SimpleNamespace(
        translate_single_segment=Mock(return_value={"final_vi": "Xin chào."}))
    session.tts_engine = SimpleNamespace(name="offline-tts")
    session._ensure_tts_engine = Mock()
    session._run_ffmpeg = AsyncMock()
    session._emit_initialization = AsyncMock()
    session._segment_progress = AsyncMock()
    session._update_ready = AsyncMock()
    session._recover_transient_speech_after_source = AsyncMock()
    session._recover_pacing_after_source = AsyncMock()
    session._resume_pending_chunk_reviews = AsyncMock()
    session._persist_if_enabled = Mock()
    session.persist = Mock()
    session._run_blocking = AsyncMock(side_effect=lambda function, *args, **kwargs: function(*args, **kwargs))
    session._synthesize_segment = AsyncMock(side_effect=lambda row, **kwargs: _ready(row))
    monkeypatch.setattr("core.streaming.export.HQExporter._video_size", Mock(return_value=(640, 360)))
    monkeypatch.setattr("core.streaming.preparation_checkpoint.load", Mock(return_value=None))
    monkeypatch.setattr("core.streaming.preparation_checkpoint.save", Mock())
    return session


def _ready(row):
    row.status, row.failed_stage, row.error = "READY", None, None


async def prepare_legacy(session):
    worker_loop = session._worker_loop
    session._worker_loop = AsyncMock()
    await session._start_legacy_source()
    assert session.worker_task is not None
    await session.worker_task
    session._worker_loop = worker_loop
    session.worker_task = None
    session.is_running = True
    session.is_stopped = False
    session.initialized = True
    return session


def test_segment_item_initializes_unrecognized_asr_state():
    row = SegmentItem(0, 0., 2., 2.)
    assert row.asr_pretranscribed is False
    assert row.status == "WAITING"
    assert row.failed_stage is None
    assert row._retry_synthesis is False
    assert row.confirmed_silence is False
    assert row.verification is None
    assert all(getattr(row, name) is None for name in pipeline.SOURCE_METADATA_FIELDS)


def test_legacy_vad_rows_dispatch_asr_without_attribute_error(legacy_session):
    async def run():
        session = await prepare_legacy(legacy_session)
        rows = list(session.segments.values())
        assert all(type(row) is SegmentItem and row.asr_pretranscribed is False for row in rows)
        await session._worker_loop()
        assert session.sensevoice.transcribe.call_count == 2
        assert session.faster_whisper.transcribe.call_count == 0
        assert all(row.asr_pretranscribed is True and row.status == "READY" for row in rows), [
            (row.status, row.error) for row in rows
        ]
        session.segmenter.segment_audio.assert_called_once_with(session.raw_audio_16k)
        assert not session._chunked_source_started
        for row, call in zip(rows, session.sensevoice.transcribe.call_args_list):
            assert call.args == (session.cache_dir / f"slice_{row.id}.wav",)
            assert call.kwargs == {"language": "zh"}
            assert row.text_zh == "你好"
            assert row.source_language_uncertain is True
            assert row.source_language_probability == .4
            assert row.source_audio_evidence == {"engine": "sensevoice", "text_zh": "你好"}
            assert row.needs_review is True
            assert row.verification["semantic_verified"] is False
        assert session._final_output_metadata()["final_output_blocked"] is True
    asyncio.run(run())


@pytest.mark.parametrize("first_result", [RuntimeError("ASR unavailable"), []])
def test_failed_legacy_asr_retries_and_keeps_healthy_row(legacy_session, first_result):
    async def run():
        session = await prepare_legacy(legacy_session)
        rows = list(session.segments.values())
        recognized = deepcopy(session.sensevoice.transcribe.return_value)
        session.sensevoice.transcribe.side_effect = [first_result, recognized, recognized]
        await session._worker_loop()
        failed, healthy = rows
        assert failed.status == "FAILED" and failed.failed_stage == "ASR"
        assert failed.asr_pretranscribed is False
        assert healthy.status == "READY" and healthy.asr_pretranscribed is True
        healthy_before = deepcopy(vars(healthy))
        assert failed.confirmed_silence is False
        assert session._final_output_metadata()["final_output_blocked"] is True
        assert session.can_retry
        await session.retry_failed_synthesis()
        await session.worker_task
        assert session.sensevoice.transcribe.call_count == 3
        assert failed.status == "READY" and failed.failed_stage is None
        assert failed.asr_pretranscribed is True
        assert failed.confirmed_silence is False
        assert failed.source_language_uncertain is True
        assert failed.source_language_probability == .4
        assert failed.verification["semantic_verified"] is False
        assert vars(healthy) == healthy_before
        assert session._final_output_metadata()["final_output_blocked"] is True
    asyncio.run(run())


def test_pretranscribed_legacy_row_skips_asr(legacy_session):
    async def run():
        session = await prepare_legacy(legacy_session)
        row = session.segments[0]
        row.text_zh = "已识别"
        row.asr_pretranscribed = True
        row.source_language_uncertain = True
        await translate_nonvisual_rows(session, [row])
        assert session.sensevoice.transcribe.call_count == 0
        assert row.text_zh == "已识别"
        assert row.status == "READY"
        assert row.source_language_uncertain is True
    asyncio.run(run())


def test_legacy_process_segment_dispatch_and_tts_retry(legacy_session):
    async def run():
        session = await prepare_legacy(legacy_session)
        row = session.segments[0]
        await session._process_segment(row)
        session.sensevoice.transcribe.assert_called_once_with(
            session.cache_dir / "slice_0.wav", language="zh")
        assert row.asr_pretranscribed is True
        assert row.source_language_uncertain is True
        assert row.source_language_probability == .4
        row.status, row.failed_stage = "FAILED", "TTS"
        session.error = "TTS unavailable"
        session.is_running = False
        session.segments[1].status = "READY"
        await session.retry_failed_synthesis()
        await session.worker_task
        assert row.status == "READY"
        assert session.sensevoice.transcribe.call_count == 1
        assert session.translator.translate_single_segment.call_count == 1
        assert row.source_language_uncertain is True
        assert session._final_output_metadata()["final_output_blocked"] is True
    asyncio.run(run())


def test_legacy_checkpoint_restores_explicit_asr_state(legacy_session):
    async def run():
        session = legacy_session
        session._prepared = False
        worker_loop = session._worker_loop
        session._worker_loop = AsyncMock()
        await session._start_legacy_source(preparation={
            "assets": {"raw_audio_16k": {"path": str(session.cache_dir / "raw.wav")},
                       "bgm_audio_path": {"path": str(session.cache_dir / "bgm.ogg")}},
            "duration": 4., "video_size": [640, 360], "suppression_stats": {},
            "segments": [
                {"id": 0, "start": 0., "end": 2., "duration": 2.,
                 "text_zh": "已保存", "emotion": "", "asr_pretranscribed": True},
                {"id": 1, "start": 2., "end": 4., "duration": 2.,
                 "text_zh": "", "emotion": "", "asr_pretranscribed": False},
            ]})
        await session.worker_task
        session._worker_loop = worker_loop
        assert session.segments[0].asr_pretranscribed is True
        assert session.segments[1].asr_pretranscribed is False
    asyncio.run(run())
