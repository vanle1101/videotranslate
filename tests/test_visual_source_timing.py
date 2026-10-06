"""Visual analysis receives complete ASR words at measured sentence boundaries."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from config import settings
from core.streaming.pipeline import StreamingPipelineSession


@pytest.fixture
def visual_session(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "BASE_DIR", tmp_path)
    monkeypatch.setattr(settings, "TEMP_DIR", tmp_path / "temp")
    monkeypatch.setattr(settings, "LLM_PROVIDER", "gemini")
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "offline-test-key")
    session = StreamingPipelineSession("grounded-source", tmp_path / "source.mp4",
                                       tts_engine_name="edge-tts", visual_translation=True)
    session.total_duration = 32.601667
    return session


def prepare_start(session, monkeypatch):
    monkeypatch.setattr("core.streaming.export.HQExporter._video_size", Mock(return_value=(1080, 1920)))
    monkeypatch.setattr(session, "_run_ffmpeg", AsyncMock())
    monkeypatch.setattr(session, "_worker_loop", AsyncMock())
    monkeypatch.setattr(session.segmenter, "get_audio_duration", Mock(return_value=32.601667))
    monkeypatch.setattr(session.vocal_suppressor, "process_file", Mock(return_value={}))
    monkeypatch.setattr(session.video_intelligence, "media_info",
                        Mock(return_value={"duration": 32.601667, "has_audio": True}))


def test_visual_source_uses_whole_audio_words_and_exact_timestamps(visual_session, monkeypatch):
    session = visual_session
    prepare_start(session, monkeypatch)
    rows = [{"id": 5, "start": .16, "end": 3.74, "text_zh": "做自媒体这些常见的谣言"},
            {"id": 9, "start": 3.8, "end": 9.31, "text_zh": "你都信过哪些"},
            {"id": 12, "start": 29.72, "end": 32.6, "text_zh": "还有不会进粉丝群"}]
    original_rows = deepcopy(rows)
    asr = Mock(return_value=rows)
    monkeypatch.setattr(session.faster_whisper, "transcribe", asr)
    vad = Mock(side_effect=AssertionError("Fixed VAD slots must not replace ASR boundaries"))
    monkeypatch.setattr(session.segmenter, "segment_audio", vad)
    received = []

    def analyze(path, segments, **kwargs):
        received.extend((item.id, item.start, item.end, item.duration, item.text_zh) for item in segments)
        return {"segments": {item.id: {"text_zh": item.text_zh, "final_vi": "Bản dịch",
                                      "start": 0, "end": 1} for item in segments}, "screen_texts": []}

    monkeypatch.setattr(session.video_intelligence, "prepass", analyze)

    async def run():
        await session._start()
        await session.worker_task

    asyncio.run(run())
    asr.assert_called_once_with(session.raw_audio_16k, language="zh")
    vad.assert_not_called()
    assert rows == original_rows
    assert len(received) == len(rows)
    for index, row in enumerate(rows):
        assert received[index] == (index, row["start"], row["end"], row["end"] - row["start"], row["text_zh"])
        segment = session.segments[index]
        assert (segment.start, segment.end, segment.text_zh) == (row["start"], row["end"], row["text_zh"])
    assert session.segments[2].text_zh.endswith("粉丝群"), "A partial screen subtitle must not remove audible words"


def test_audio_only_whisper_preserves_whole_audio_sentence_timing_without_visual_analysis(visual_session, monkeypatch):
    session = visual_session
    prepare_start(session, monkeypatch)
    session.visual_translation = False
    session.asr_engine_name = "faster-whisper"
    vad = Mock(side_effect=AssertionError("VAD chunks must not merge separate ASR sentences"))
    monkeypatch.setattr(session.segmenter, "segment_audio", vad)
    rows = [{"start": .4, "end": 2.1, "text_zh": "第一句。"},
            {"start": 2.8, "end": 5.4, "text_zh": "第二句。"}]
    asr = Mock(return_value=deepcopy(rows))
    monkeypatch.setattr(session.faster_whisper, "transcribe", asr)
    visual = Mock(side_effect=AssertionError("Visual opt-out must not call Gemini"))
    monkeypatch.setattr(session.video_intelligence, "prepass", visual)

    async def run():
        await session._start()
        await session.worker_task

    asyncio.run(run())
    vad.assert_not_called()
    asr.assert_called_once_with(session.raw_audio_16k, language="zh")
    visual.assert_not_called()
    assert [(segment.start, segment.end, segment.text_zh)
            for segment in session.segments.values()] == [
        (row["start"], row["end"], row["text_zh"]) for row in rows]
    assert all(segment.asr_pretranscribed for segment in session.segments.values())


def test_measured_long_sentence_is_not_split_at_guessed_word_times(visual_session):
    sentence = {"start": 2.13, "end": 12.37, "text_zh": "这是一整句实际测量时间的话"}
    result = visual_session._grounded_visual_segments([sentence])
    assert len(result) == 1
    assert result[0]["start"] == 2.13 and result[0]["end"] == 12.37
    assert result[0]["text_zh"] == sentence["text_zh"]


@pytest.mark.parametrize("rows", [None, [None], [{"start": 0, "end": 2, "text_zh": 42}],
    [{"start": -1, "end": 2, "text_zh": "错误"}], [{"start": True, "end": 2, "text_zh": "错误"}],
    [{"start": 0, "end": float("nan"), "text_zh": "错误"}],
    [{"start": 2, "end": 1, "text_zh": "错误"}], [{"start": 0, "end": 35, "text_zh": "错误"}],
    [{"start": 0, "end": 3, "text_zh": "第一句"}, {"start": 2, "end": 4, "text_zh": "第二句"}],
])
def test_invalid_asr_rows_fail_without_inventing_boundaries(visual_session, rows):
    with pytest.raises(ValueError, match="Faster-Whisper"):
        visual_session._grounded_visual_segments(rows)


def test_too_long_asr_sentence_without_measured_words_fails_and_empty_audio_has_no_fake_rows(visual_session):
    visual_session.total_duration = 90
    with pytest.raises(ValueError, match="mốc từ"):
        visual_session._grounded_visual_segments([{"start": 0, "end": 46, "text_zh": "长句"}])
    assert visual_session._grounded_visual_segments([]) == []
    assert visual_session._grounded_visual_segments([{"start": 0, "end": 1, "text_zh": " "}]) == []


def test_over_limit_sentence_splits_only_at_measured_words_and_keeps_gaps(visual_session):
    visual_session.total_duration = 60
    words = [{"word": "第一部分", "start": 1.2, "end": 6.9},
             {"word": "第二部分", "start": 7.6, "end": 14.2},
             {"word": "第三部分", "start": 15.8, "end": 21.7},
             {"word": "第四部分", "start": 23.1, "end": 30.2},
             {"word": "第五部分", "start": 32.8, "end": 38.6},
             {"word": "最后部分", "start": 42.1, "end": 47.8}]
    rows = visual_session._grounded_visual_segments([
        {"start": 1.1, "end": 48.2, "text_zh": "完整句子", "words": words}])
    assert "".join(row["text_zh"] for row in rows) == "".join(word["word"] for word in words)
    assert [(row["start"], row["end"]) for row in rows] == [
        (1.1, 6.9), (7.6, 14.2), (15.8, 21.7), (23.1, 30.2), (32.8, 38.6), (42.1, 48.2)]
    assert all(row["duration"] <= 8 for row in rows)


def test_long_row_reuses_loaded_whisper_for_word_timestamps(visual_session):
    visual_session.total_duration = 60
    visual_session.raw_audio_16k = visual_session.cache_dir / "raw_audio_16k.wav"
    words = [SimpleNamespace(word="第一句", start=0, end=7),
             SimpleNamespace(word="最后一句", start=42, end=48)]
    transcribe = Mock(return_value=(iter([SimpleNamespace(words=words)]), None))
    visual_session.faster_whisper.model = SimpleNamespace(transcribe=transcribe)
    rows = visual_session._grounded_visual_segments([{"start": 0, "end": 48, "text_zh": "完整句子"}])
    assert [(row["start"], row["end"]) for row in rows] == [(0, 7), (42, 48)]
    transcribe.assert_called_once_with(str(visual_session.raw_audio_16k), language="zh", beam_size=5,
                                      word_timestamps=True, vad_filter=False, clip_timestamps=[0, 48])


def test_mixed_provider_metadata_does_not_claim_text_fallback_watched_video(visual_session, monkeypatch):
    session = visual_session
    prepare_start(session, monkeypatch)
    monkeypatch.setattr(session.faster_whisper, "transcribe", Mock(return_value=[
        {"start": 0, "end": 2, "text_zh": "第一句"}, {"start": 3, "end": 5, "text_zh": "第二句"}]))
    session.faster_whisper.model = object()
    session.video_intelligence.screen_ocr._engine = object()
    closed = Mock(wraps=session.video_intelligence.screen_ocr.close)
    monkeypatch.setattr(session.video_intelligence.screen_ocr, "close", closed)
    monkeypatch.setattr(session.video_intelligence, "prepass", Mock(return_value={"segments": {
        0: {"text_zh": "第一句", "final_vi": "Câu thứ nhất", "translation_provider": "gemini",
            "translation_model": "gemini-fixture"},
        1: {"text_zh": "第二句", "final_vi": "Câu thứ hai", "translation_provider": "openrouter-free",
            "translation_model": "example/model:free", "needs_review": True, "review_reason": "Từ chưa rõ"},
    }, "screen_texts": []}))
    events = []
    session.event_callback = lambda event, payload: events.append((event, payload))

    async def run():
        await session._start()
        await session.worker_task

    asyncio.run(run())
    closed.assert_called_once()
    assert session.faster_whisper.model is None
    assert session.video_intelligence.screen_ocr._engine is None
    gemini, fallback = [session.segments[index].to_dict() for index in (0, 1)]
    assert (gemini["source_method"], gemini["translation_provider"], gemini["evidence_mode"]) == ("video-ai", "gemini", "audio-video")
    assert (fallback["source_method"], fallback["translation_provider"], fallback["translation_model"], fallback["evidence_mode"]) == (
        "text-ai", "openrouter-free", "example/model:free", "asr-ocr-text")
    assert fallback["needs_review"] and fallback["review_reason"] == "Từ chưa rõ"
    init = next(payload for event, payload in events if event == "init")
    assert init["translation_sources"] == session.get_telemetry()["translation_sources"] == session.translation_sources
    assert "Gemini + OpenRouter" in init["asr_engine"]
    assert any("OpenRouter" in warning and "chưa được AI xem/nghe video" in warning for warning in init["warnings"])


@pytest.mark.parametrize("error", [RuntimeError("failed visual analysis"), asyncio.CancelledError()])
def test_visual_models_released_when_prepass_fails_or_is_cancelled(visual_session, monkeypatch, error):
    session = visual_session
    prepare_start(session, monkeypatch)
    monkeypatch.setattr(session.faster_whisper, "transcribe", Mock(return_value=[]))
    session.faster_whisper.model = object()
    session.video_intelligence.screen_ocr._engine = object()
    run_blocking = session._run_blocking

    async def interrupted_prepass(function, *args, **kwargs):
        if function == session.video_intelligence.prepass:
            raise error
        return await run_blocking(function, *args, **kwargs)

    monkeypatch.setattr(session, "_run_blocking", interrupted_prepass)

    async def run():
        with pytest.raises(type(error)):
            await session._start()

    asyncio.run(run())
    assert session.video_intelligence.screen_ocr._engine is None
    assert session.faster_whisper.model is None
    assert not session.initialized


def test_text_fallback_review_synthesizes_draft_without_reverting_to_legacy_asr(visual_session, monkeypatch):
    from core.streaming.pipeline import SegmentItem
    segment = SegmentItem(0, 0, 2, 2)
    segment.source_method = "text-ai"
    segment.needs_review = True
    segment.final_vi = "Bản nháp chưa kiểm chứng"
    synthesize = AsyncMock()
    monkeypatch.setattr(visual_session, "_synthesize_segment", synthesize)
    visual_session.asr_engine = Mock()
    asyncio.run(visual_session._process_segment(segment))
    assert segment.needs_review
    synthesize.assert_awaited_once_with(segment)
    visual_session.asr_engine.transcribe.assert_not_called()


def test_ocr_only_text_fallback_keeps_provider_warning_even_without_speech(visual_session, monkeypatch):
    session = visual_session
    prepare_start(session, monkeypatch)
    monkeypatch.setattr(session.faster_whisper, "transcribe", Mock(return_value=[]))
    sources = [{"provider": "openrouter-free", "model": "example/ocr:free", "evidence_mode": "asr-ocr-text"}]
    monkeypatch.setattr(session.video_intelligence, "prepass", Mock(return_value={
        "segments": {}, "screen_texts": [], "translation_sources": sources}))

    async def run():
        await session._start()
        await session.worker_task

    asyncio.run(run())
    assert session.translation_sources == sources
    assert any("OpenRouter" in warning for warning in session.warnings)
    assert "Gemini" not in session.source_processing_label()


@pytest.mark.parametrize("provider, model", [("openrouter-free", "example/ocr:free"),
                                             ("opencode", "muse-spark-1.3-contributor-free")])
def test_intentionally_free_primary_reports_asr_ocr_without_quota_warning(visual_session, monkeypatch, provider, model):
    session = visual_session
    session.video_intelligence.provider = provider
    prepare_start(session, monkeypatch)
    monkeypatch.setattr(session.faster_whisper, "transcribe", Mock(return_value=[
        {"start": 0, "end": 2, "text_zh": "第一句"}]))
    sources = [{"provider": provider, "model": model, "evidence_mode": "asr-ocr-text"}]
    monkeypatch.setattr(session.video_intelligence, "prepass", Mock(return_value={
        "segments": {0: {"text_zh": "第一句", "final_vi": "Câu thứ nhất", "translation_provider": provider,
                         "translation_model": model, "needs_review": False}},
        "screen_texts": [], "translation_sources": sources}))

    async def run():
        await session._start()
        await session.worker_task

    asyncio.run(run())
    assert session.translation_sources == sources
    assert not any("hạn mức" in warning or "Gemini" in warning for warning in session.warnings)
    assert session.segments[0].source_method == "text-ai"
    assert session.segments[0].translation_provider == provider
    assert session.segments[0].translation_model == model
    assert "ASR + OCR miễn phí" in session.source_processing_label()
    assert "Gemini" not in session.source_processing_label()
