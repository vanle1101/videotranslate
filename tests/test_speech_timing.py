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


def paused_pcm(path, intervals, duration=4):
    rate = 24000
    times = np.arange(round(duration * rate)) / rate
    speaking = np.zeros(len(times), dtype=bool)
    for left, right in intervals:
        speaking |= (times >= left) & (times < right)
    samples = (.2 * np.sin(2 * math.pi * 440 * times) * speaking * 32767).astype("<i2")
    with wave.open(str(path), "wb") as audio:
        audio.setparams((1, 2, rate, 0, "NONE", "not compressed"))
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


@pytest.mark.parametrize("boundaries", [[], [{"text": "mismatched", "start": .1, "end": 3.8}]])
def test_estimated_next_sentence_waits_for_real_speech_after_long_pause(tmp_path, boundaries):
    audio = tmp_path / "paused.wav"
    paused_pcm(audio, [(.1, .6), (3, 3.8)])
    result = build_speech_timing("Ừ. Sao vậy?", 0, 4, audio, word_boundaries=boundaries)
    assert result["subtitle_timing_source"] == "audio-pause-estimate"
    first, second = result["subtitle_cues"]
    assert first["text"] == "Ừ." and second["text"] == "Sao vậy?"
    assert first["end"] == pytest.approx(.6, abs=.011)
    assert second["start"] == pytest.approx(3.01, abs=.011)
    assert not any(cue["start"] <= 1 < cue["end"] for cue in result["subtitle_cues"])
    assert result["speech_end"] == pytest.approx(3.8)


def test_local_sentence_timing_uses_final_pcm_after_trim_and_global_offset(tmp_path):
    from core.engines.alignment.speech_timing import trim_tts_padding
    audio = tmp_path / "paused.wav"
    paused_pcm(audio, [(.4, .8), (2.7, 3.6)])
    before = build_speech_timing("Ừ. Sao vậy?", 10, 14, audio)
    removed = trim_tts_padding(audio)
    after = build_speech_timing("Ừ. Sao vậy?", 10, 14, audio)
    assert after["subtitle_timing_source"] == "audio-pause-estimate"
    for original, trimmed in zip(before["subtitle_cues"], after["subtitle_cues"]):
        assert original["start"] - trimmed["start"] == pytest.approx(removed, abs=.011)
    assert after["subtitle_cues"][1]["start"] >= 10 + 2.7 - removed


def test_estimated_words_never_start_inside_a_measured_long_pause(tmp_path):
    audio = tmp_path / "paused.wav"
    paused_pcm(audio, [(.1, .6), (2, 2.5), (3, 3.8)])
    text = "Hôm nay chúng ta cùng tìm hiểu điều này thật kỹ để tránh nhầm lẫn."
    result = build_speech_timing(text, 5, 9, audio)
    words = [word for cue in result["subtitle_cues"] for word in cue["words"]]
    assert " ".join(word["text"] for word in words) == text
    assert all(not 5.6 < word["start"] < 7 and not 7.5 < word["start"] < 8 for word in words)
    assert all(5 <= word["start"] < word["end"] <= 8.8 for word in words)


def test_sentence_pauses_preserve_measured_provider_boundaries(tmp_path):
    audio = tmp_path / "paused.wav"
    paused_pcm(audio, [(.1, .6), (3, 3.8)])
    result = build_speech_timing("Ừ. Sao vậy?", 0, 4, audio, word_boundaries=[
        {"text": "Ừ", "start": .1, "end": .6},
        {"text": "Sao", "start": 3, "end": 3.3},
        {"text": "vậy", "start": 3.3, "end": 3.8},
    ])
    first, second = result["subtitle_cues"]
    assert result["subtitle_timing_source"] == "edge-word-boundary"
    assert second["start"] - first["start"] == pytest.approx(2.9)
    assert first["end"] < .7 and second["start"] >= 3


def test_sentence_after_newline_is_not_combined_with_earlier_speaker(tmp_path):
    audio = tmp_path / "paused.wav"
    paused_pcm(audio, [(.1, .6), (3, 3.8)])
    result = build_speech_timing("Ừ.\nSao vậy?", 0, 4, audio)
    first, second = result["subtitle_cues"]
    assert first["text"] == "Ừ." and second["text"] == "Sao vậy?"
    assert second["start"] >= 3


def test_short_phoneme_gaps_do_not_get_promoted_to_sentence_boundaries(tmp_path):
    audio = tmp_path / "brief-gaps.wav"
    paused_pcm(audio, [(.1, .4), (.5, .8), (.9, 1.3)], duration=2)
    result = build_speech_timing("Xin chào bạn.", 0, 2, audio)
    assert result["subtitle_timing_source"] == "audio-onset-estimate"
    assert len(result["subtitle_cues"]) == 1


def test_comma_pause_does_not_force_next_sentence_into_second_audio_island(tmp_path):
    audio = tmp_path / "comma-pause.wav"
    paused_pcm(audio, [(.1, .4), (.8, 3.8)])
    text = "Nào, chúng ta bắt đầu nhé. Đi thôi."
    result = build_speech_timing(text, 0, 4, audio)
    next_sentence = [cue for cue in result["subtitle_cues"] if cue["text"] == "Đi thôi."]
    assert next_sentence and next_sentence[0]["start"] > 2
    assert any(cue["start"] <= .6 < cue["end"] for cue in result["subtitle_cues"]), "Keep current page through a normal comma pause"
    assert result["subtitle_timing_source"] == "audio-pause-estimate"


def test_single_page_hides_during_long_pause_without_changing_words(tmp_path):
    audio = tmp_path / "dramatic-pause.wav"
    paused_pcm(audio, [(.1, .7), (2.7, 3.8)])
    result = build_speech_timing("Chúng ta cùng chờ một chút nhé.", 0, 4, audio)
    cues = result["subtitle_cues"]
    assert len(cues) == 2 and cues[0]["text"] == cues[1]["text"]
    assert cues[0]["end"] == pytest.approx(.7, abs=.011)
    assert cues[1]["start"] >= 2.7
    assert not any(cue["start"] <= 1 < cue["end"] for cue in cues)


def test_real_sample_comma_pause_does_not_blink_or_lose_boundary_word(tmp_path):
    audio = tmp_path / "comma.wav"
    paused_pcm(audio, [(.04, .47), (.75, 1.91)], duration=2.06)
    text = "Về nhất, phá kỷ lục của trường."
    result = build_speech_timing(text, 2.44, 4.5, audio, word_boundaries=[
        {"text": "Về", "start": .04, "end": .24},
        {"text": "nhất", "start": .24, "end": .515},
        {"text": "phá", "start": .70, "end": .95},
        {"text": "kỷ", "start": .95, "end": 1.125},
        {"text": "lục", "start": 1.125, "end": 1.375},
        {"text": "của", "start": 1.375, "end": 1.565},
        {"text": "trường", "start": 1.565, "end": 1.91}])
    assert len(result["subtitle_cues"]) == 1
    cue = result["subtitle_cues"][0]
    assert cue["start"] < 2.91 < 3.19 < cue["end"]
    assert " ".join(word["text"] for word in cue["words"]) == text
