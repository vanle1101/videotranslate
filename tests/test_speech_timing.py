"""Measured dub timing, service word metadata, and silence transaction checks."""
import asyncio
import json
import math
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest

from core.engines.alignment.speech_timing import audio_activity_span, build_speech_timing
from core.engines.tts.edge_fallback import EdgeTTSFallbackEngine
from core.streaming.pipeline import SegmentItem, StreamingPipelineSession


def pcm(path, *, duration=3, onset=.4, offset=2.6):
    rate = 24000
    times = np.arange(round(duration * rate)) / rate
    samples = (.2 * np.sin(2 * math.pi * 440 * times) * ((times >= onset) & (times < offset)) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(samples.tobytes())


def test_pcm_activity_excludes_encoder_silence(tmp_path):
    audio = tmp_path / "speech.wav"
    pcm(audio)
    onset, offset = audio_activity_span(audio)
    assert .4 <= onset <= .42
    assert offset == pytest.approx(2.6, abs=.01)


def test_scaled_word_boundaries_start_after_fitted_voice_onset(tmp_path):
    audio = tmp_path / "speech.wav"
    pcm(audio)
    result = build_speech_timing("Xin chào, bạn!", 10, 14, audio, 2, [
        {"text": "Xin", "start": .2, "end": .6},
        {"text": "chào", "start": .8, "end": 1.2},
        {"text": "bạn", "start": 1.6, "end": 2.2},
    ])
    assert result["subtitle_timing_source"] == "edge-word-boundary"
    assert result["speech_start"] >= 10.4
    words = result["subtitle_cues"][0]["words"]
    assert [word["text"] for word in words] == ["Xin", "chào,", "bạn!"]
    assert words[1]["start"] - words[0]["start"] == pytest.approx(.3)
    assert words[2]["start"] - words[0]["start"] == pytest.approx(.7)
    assert all(word["start"] >= result["speech_start"] for word in words)
    assert result["subtitle_cues"][-1]["end"] == result["speech_end"] == pytest.approx(12.6)


@pytest.mark.parametrize("boundaries", [[], [{"text": "Khác", "start": 0, "end": 1}],
                                       [{"text": "Xin chào", "start": float("nan"), "end": 1}]])
def test_unknown_or_mismatched_word_timing_is_explicitly_estimated(tmp_path, boundaries):
    audio = tmp_path / "speech.wav"
    pcm(audio)
    result = build_speech_timing("Xin chào", 4, 7, audio, 1, boundaries)
    assert result["subtitle_timing_source"] == "audio-onset-estimate"
    assert result["subtitle_cues"][0]["start"] >= 4.4
    assert result["subtitle_cues"][0]["words"][1]["start"] > 4.4


def test_silent_or_invalid_audio_never_creates_speculative_captions(tmp_path):
    audio = tmp_path / "speech.wav"
    pcm(audio, onset=4, offset=4)
    assert build_speech_timing("Có chữ nhưng không có tiếng", 0, 3, audio)["subtitle_cues"] == []
    audio.write_bytes(b"not a wave")
    assert build_speech_timing("Có chữ", 0, 3, audio)["subtitle_cues"] == []


def test_manual_lines_keep_all_words_and_monotonic_timing(tmp_path):
    audio = tmp_path / "speech.wav"
    pcm(audio)
    text = "Dòng đầu tiên\nDòng thứ hai\nDòng cuối cùng"
    result = build_speech_timing(text, 1, 4, audio)
    cues = result["subtitle_cues"]
    assert len(cues) == 2
    assert [cue["text"] for cue in cues] == ["Dòng đầu tiên\nDòng thứ hai", "Dòng cuối cùng"]
    assert cues[0]["end"] == cues[1]["start"]
    assert " ".join(word["text"] for cue in cues for word in cue["words"]) == " ".join(text.split())


def test_next_sentence_waits_for_its_first_spoken_word(tmp_path):
    audio = tmp_path / "speech.wav"
    pcm(audio)
    result = build_speech_timing("Xin chào. Chào bạn!", 5, 8, audio, 1, [
        {"text": "Xin", "start": .4, "end": .6},
        {"text": "chào", "start": .6, "end": .9},
        {"text": "Chào", "start": 1.8, "end": 2.1},
        {"text": "bạn", "start": 2.1, "end": 2.6},
    ])
    cues = result["subtitle_cues"]
    assert [cue["text"] for cue in cues] == ["Xin chào.", "Chào bạn!"]
    assert cues[1]["start"] == pytest.approx(result["speech_start"] + 1.4)
    assert cues[0]["start"] < 6 < cues[1]["start"]


def test_edge_requests_and_consumes_actual_service_boundaries(tmp_path):
    output = tmp_path / "speech.mp3"
    messages = [{"type": "WordBoundary", "text": "Xin", "offset": 2_000_000, "duration": 3_000_000},
                {"type": "WordBoundary", "text": "chào", "offset": 6_000_000, "duration": 4_000_000}]

    async def save(path, metadata):
        Path(path).write_bytes(b"service mp3")
        Path(metadata).write_text("\n".join(json.dumps(item) for item in messages), encoding="utf-8")

    engine = EdgeTTSFallbackEngine()
    with patch("core.engines.tts.edge_fallback.edge_tts.Communicate", return_value=SimpleNamespace(save=save)) as factory:
        engine.synthesize("Xin chào", output)
    assert factory.call_args.kwargs["boundary"] == "WordBoundary"
    assert engine.take_word_boundaries(output) == [{"text": "Xin", "start": .2, "end": .5},
                                                  {"text": "chào", "start": .6, "end": 1}]
    assert engine.take_word_boundaries(output) == []
    assert sorted(path.name for path in tmp_path.iterdir()) == ["speech.mp3"]


def test_malformed_service_metadata_does_not_discard_good_speech(tmp_path):
    output = tmp_path / "speech.mp3"

    async def save(path, metadata):
        Path(path).write_bytes(b"complete service audio")
        Path(metadata).write_text('{"type":"WordBoundary","offset":"bad"}', encoding="utf-8")

    engine = EdgeTTSFallbackEngine()
    with patch("core.engines.tts.edge_fallback.edge_tts.Communicate", return_value=SimpleNamespace(save=save)):
        engine.synthesize("Xin chào", output)
    assert output.read_bytes() == b"complete service audio"
    assert engine.take_word_boundaries(output) == []


def test_pipeline_publishes_dub_timing_only_after_real_pcm_is_ready(tmp_path, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "BASE_DIR", tmp_path)
    monkeypatch.setattr(settings, "TEMP_DIR", tmp_path)
    session = StreamingPipelineSession("timing-test", None, voice="vi-VN-HoaiMyNeural", tts_engine_name="edge-tts")
    segment = SegmentItem(0, 2, 5, 3)
    segment.final_vi = "Xin chào bạn"
    assert segment.to_dict()["subtitle_cues"] == []
    session.segments[0] = segment
    session.tts_engine = SimpleNamespace(
        synthesize=lambda text, output_path, **kwargs: pcm(output_path),
        take_word_boundaries=lambda path: [
            {"text": "Xin", "start": .4, "end": .7},
            {"text": "chào", "start": .9, "end": 1.3},
            {"text": "bạn", "start": 1.5, "end": 2.6}],
    )
    # Use real atempo and real probe: catches segment-relative/global confusion.
    asyncio.run(session._synthesize_segment(segment))
    result = segment.to_dict()
    assert result["subtitle_timing_source"] == "edge-word-boundary"
    # Outer TTS padding is trimmed before fitting, but onset remains measured.
    assert 2 <= result["speech_start"] <= 2.1
    assert result["subtitle_cues"][0]["start"] >= result["speech_start"]
    assert result["status"] == "READY" and result["audio_url"]
    assert result["subtitle_cues"][-1]["end"] <= 5


def test_outer_tts_padding_removed_before_budgeting_without_cutting_speech(tmp_path):
    from core.engines.alignment.speech_timing import trim_tts_padding
    audio = tmp_path / "padding.wav"
    pcm(audio, duration=3, onset=.4, offset=1.0)
    trim_tts_padding(audio)
    with wave.open(str(audio), "rb") as wav:
        assert wav.getnframes() / wav.getframerate() == pytest.approx(.70, abs=.02)
    onset, offset = audio_activity_span(audio)
    assert onset == pytest.approx(.04, abs=.02)
    assert offset - onset == pytest.approx(.6, abs=.02)


def test_quiet_first_and_last_syllables_survive_padding_trim(tmp_path):
    from core.engines.alignment.speech_timing import trim_tts_padding
    audio = tmp_path / "quiet-edges.wav"
    rate = 24000
    time = np.arange(rate * 3) / rate
    amplitude = np.where((time >= .4) & (time < 2.6), .004, 0)
    amplitude[(time >= 1) & (time < 2)] = .8
    samples = (amplitude * np.sin(2 * np.pi * 440 * time) * 32767).astype("<i2")
    with wave.open(str(audio), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(samples.tobytes())
    before = audio_activity_span(audio)
    assert before[0] == pytest.approx(.41, abs=.02)
    assert before[1] == pytest.approx(2.6, abs=.02)
    removed = trim_tts_padding(audio)
    assert removed < .4
    with wave.open(str(audio), "rb") as wav:
        assert wav.getnframes() / wav.getframerate() >= 2.2


@pytest.mark.parametrize("boundaries", [[], [
    {"text": "Ừ", "start": .4, "end": .7},
    {"text": "Sao", "start": 1.5, "end": 1.9},
    {"text": "vậy", "start": 2, "end": 2.6},
]])
def test_standalone_punctuation_does_not_consume_next_sentence_word(tmp_path, boundaries):
    audio = tmp_path / "speech.wav"
    pcm(audio)
    result = build_speech_timing("Ừ . Sao vậy ?", 2, 5, audio, 1, boundaries)
    cues = result["subtitle_cues"]
    assert [cue["text"] for cue in cues] == ["Ừ .", "Sao vậy ?"]
    assert [word["text"] for word in cues[1]["words"]] == ["Sao", "vậy ?"]
    assert cues[1]["start"] > cues[0]["start"]
    if boundaries:
        assert cues[1]["start"] == pytest.approx(result["speech_start"] + 1.1)


def test_failed_trim_replacement_preserves_original_pcm(tmp_path, monkeypatch):
    from core.engines.alignment.speech_timing import trim_tts_padding
    audio = tmp_path / "speech.wav"
    pcm(audio)
    original = audio.read_bytes()
    with patch.object(Path, "replace", side_effect=PermissionError("locked")):
        with pytest.raises(PermissionError, match="locked"):
            trim_tts_padding(audio)
    assert audio.read_bytes() == original
    assert sorted(path.name for path in tmp_path.iterdir()) == ["speech.wav"]


def test_trim_offset_cancels_when_word_metadata_is_anchored_to_final_audio(tmp_path):
    from core.engines.alignment.speech_timing import trim_tts_padding
    audio = tmp_path / "speech.wav"
    pcm(audio)
    boundaries = [{"text": "Ừ", "start": .4, "end": .8},
                  {"text": "Sao", "start": 1.5, "end": 1.9},
                  {"text": "vậy", "start": 2, "end": 2.6}]
    untrimmed = build_speech_timing("Ừ. Sao vậy?", 2, 5, audio, 1, boundaries)
    removed = trim_tts_padding(audio)
    trimmed = build_speech_timing("Ừ. Sao vậy?", 2, 5, audio, 1, boundaries)
    assert removed > .3
    assert trimmed["subtitle_timing_source"] == "edge-word-boundary"
    for before, after in zip(untrimmed["subtitle_cues"], trimmed["subtitle_cues"]):
        assert before["start"] - after["start"] == pytest.approx(removed, abs=.011)
