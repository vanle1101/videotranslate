"""Offline regression coverage for missing source in the legacy CLI pipeline."""

import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from core import pipeline as pipeline_module
from core.pipeline import VideoTranslationPipeline


@pytest.fixture
def mocked_pipeline(tmp_path, monkeypatch):
    # Bypass engine constructors: this test must not initialize models or call
    # real download, provider, TTS, FFmpeg, or render implementations.
    pipeline = VideoTranslationPipeline.__new__(VideoTranslationPipeline)
    monkeypatch.setattr(
        pipeline_module, "settings",
        SimpleNamespace(TEMP_DIR=tmp_path / "temp", OUTPUT_DIR=tmp_path / "output"),
    )
    monkeypatch.setattr(pipeline_module.subprocess, "run", Mock())
    pipeline.downloader = Mock()
    pipeline.downloader.download.return_value = {
        "id": "offline-source-check", "file_path": str(tmp_path / "source.mp4"),
        "title": "Offline source check",
    }
    pipeline.separator = Mock()
    pipeline.separator.separate.return_value = (
        tmp_path / "vocals.wav", tmp_path / "instrumental.wav",
    )
    pipeline.sensevoice_asr = Mock(is_available=True)
    pipeline.whisper_fallback_asr = Mock()
    pipeline.translator = Mock()
    pipeline.translator.translate.side_effect = lambda segments, **kwargs: segments
    pipeline.vieneu_tts = Mock()
    pipeline.edge_fallback_tts = Mock()
    pipeline.aligner = Mock()
    pipeline.aligner.align_and_budget.side_effect = lambda **kwargs: kwargs["segments"]
    pipeline.aligner.get_audio_duration.return_value = 12.0
    pipeline._assemble_voice_timeline = Mock()
    pipeline.mixer = Mock()
    pipeline.sub_gen = Mock()
    pipeline.composer = Mock()
    return pipeline


@pytest.mark.parametrize("asr_engine_name", ["sensevoice", "whisper"])
@pytest.mark.parametrize("empty_result", [[], None])
def test_empty_asr_stops_before_translation_tts_and_render(
    mocked_pipeline, asr_engine_name, empty_result,
):
    pipeline = mocked_pipeline
    pipeline.sensevoice_asr.is_available = asr_engine_name != "whisper"
    pipeline.sensevoice_asr.transcribe.return_value = empty_result
    pipeline.whisper_fallback_asr.transcribe.return_value = empty_result
    progress = Mock()

    with pytest.raises(RuntimeError, match="Thiếu nguồn lời thoại.*chưa xác minh"):
        asyncio.run(pipeline.run(
            "offline.mp4", asr_engine_name=asr_engine_name,
            progress_callback=progress,
        ))

    pipeline.translator.assert_not_called()
    assert pipeline.translator.mock_calls == []
    assert pipeline.vieneu_tts.mock_calls == []
    assert pipeline.edge_fallback_tts.mock_calls == []
    pipeline.aligner.align_and_budget.assert_not_called()
    pipeline._assemble_voice_timeline.assert_not_called()
    pipeline.mixer.mix.assert_not_called()
    pipeline.sub_gen.generate_srt.assert_not_called()
    pipeline.composer.compose.assert_not_called()
    assert progress.call_args.args[2] == "stage-asr"
    chosen_asr = (
        pipeline.whisper_fallback_asr if asr_engine_name == "whisper"
        else pipeline.sensevoice_asr
    )
    chosen_asr.transcribe.assert_called_once()


@pytest.mark.parametrize("custom_source", [False, True])
def test_valid_source_advances_without_fabricating_segments(mocked_pipeline, custom_source):
    pipeline = mocked_pipeline
    segments = [{"id": 17, "start": 1.2, "end": 3.4, "text": "真实的台词"}]
    pipeline.sensevoice_asr.transcribe.return_value = segments

    result = asyncio.run(pipeline.run(
        "offline.mp4", custom_segments=segments if custom_source else None,
    ))

    if custom_source:
        pipeline.sensevoice_asr.transcribe.assert_not_called()
        pipeline.whisper_fallback_asr.transcribe.assert_not_called()
    else:
        pipeline.sensevoice_asr.transcribe.assert_called_once()
    pipeline.translator._extract_context_and_glossary.assert_called_once_with("真实的台词")
    pipeline.translator.translate.assert_called_once_with(segments, target_lang="vi")
    assert pipeline.aligner.align_and_budget.call_args.kwargs["segments"] is segments
    assert result["segments"] is segments
    assert result["duration"] == 12.0
    pipeline.composer.compose.assert_called_once()
