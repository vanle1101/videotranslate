import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from core.streaming import chunked_source
from core.streaming.pipeline import SegmentItem
from test_nonvisual_parallel import make_session


@pytest.mark.parametrize("engine", ["faster-whisper", "whisper"])
def test_fresh_full_whisper_routes_to_bounded_language_detection(make_session, monkeypatch, engine):
    session = make_session("fresh-native", 0, 20)
    session.asr_engine_name = engine
    session._ensure_tts_engine = lambda: None
    monkeypatch.setattr("core.streaming.preparation_checkpoint.load", lambda _: None)
    bounded, legacy = AsyncMock(), AsyncMock()
    session._start_chunked_visual, session._start_legacy_source = bounded, legacy
    asyncio.run(session._start())
    bounded.assert_awaited_once()
    legacy.assert_not_awaited()


@pytest.mark.parametrize("state", ["prepared", "rows", "saved", "sensevoice"])
def test_full_source_routing_preserves_legacy_and_selected_sensevoice(make_session, monkeypatch, state):
    session = make_session("legacy-route", 0, 20)
    session.asr_engine_name = "faster-whisper"
    session._ensure_tts_engine = lambda: None
    saved = {"validated": True} if state == "saved" else None
    loader = Mock(return_value=saved)
    monkeypatch.setattr("core.streaming.preparation_checkpoint.load", loader)
    if state == "prepared":
        session._prepared = True
    elif state == "rows":
        session.segments[3] = SegmentItem(3, 0, 1, 1)
    elif state == "sensevoice":
        session.asr_engine_name = "sensevoice"
        session.sensevoice = SimpleNamespace(is_available=True)
    bounded, legacy = AsyncMock(), AsyncMock()
    session._start_chunked_visual, session._start_legacy_source = bounded, legacy
    asyncio.run(session._start())
    bounded.assert_not_awaited()
    if state == "saved":
        legacy.assert_awaited_once_with(preparation=saved)
    else:
        legacy.assert_awaited_once_with()
        loader.assert_not_called()
    if state == "sensevoice":
        assert session.asr_engine is session.sensevoice


def test_fresh_full_korean_source_reaches_auto_detect_without_forced_chinese(make_session, monkeypatch):
    session = make_session("full-korean", 0, 20)
    session.total_duration = 10
    session.asr_engine_name = "faster-whisper"
    session._ensure_tts_engine = lambda: None
    session._source_preparation_active = False
    monkeypatch.setattr("core.streaming.preparation_checkpoint.load", lambda _: None)
    monkeypatch.setattr(chunked_source, "ensure_source_identity", lambda _: "fixed")
    monkeypatch.setattr(chunked_source, "_source_identity", lambda _: {"source": "fixed"})
    monkeypatch.setattr(chunked_source, "_load", lambda *args: None)
    monkeypatch.setattr("core.streaming.prepared_background.background_provenance", lambda _: {})
    async def annotate(session, rows, start, end):
        return rows
    monkeypatch.setattr("core.streaming.speaker_source.annotate_source_rows", annotate)
    async def ffmpeg(command):
        from pathlib import Path
        Path(command[-1]).write_bytes(b"fixture media")
    session._run_ffmpeg = ffmpeg
    observed = []
    def recognize(path, **kwargs):
        observed.append(kwargs["language"])
        assert kwargs["language"] is None
        kwargs["metadata_callback"]({"language": "ko", "probability": .99, "speech_segments": 1})
        return [{"start": 0., "end": 2., "duration": 2., "text_zh": "우리 친구잖아"}]
    session.faster_whisper.transcribe = recognize
    session._grounded_visual_segments = lambda rows: rows
    def suppress(**kwargs):
        kwargs["output_audio_path"].write_bytes(b"bgm")
        return {}
    session.vocal_suppressor.process_file = suppress
    records = []
    async def bounded():
        records.append(await chunked_source.prepare_interval(session, 0., 10.))
    session._start_chunked_visual = bounded
    session._start_legacy_source = AsyncMock(side_effect=AssertionError("forced Chinese legacy route"))
    asyncio.run(session._start())
    assert observed == [None]
    row = records[0]["rows"][0]
    assert row["text_zh"] == "우리 친구잖아" and row["source_language"] == "ko"
    assert row["evidence_mode"] == "native-recovery-ko"
