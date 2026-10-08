"""Offline regressions for measured speech fitting and independent rewrite review."""
import asyncio
import json
import logging
import math
import shutil
import wave
from pathlib import Path
from array import array
from unittest.mock import Mock

import pytest

# These focused tests must not create or append production log files.
for _name in ("app", "ai", "pipeline", "errors"):
    logging.getLogger(_name).addHandler(logging.NullHandler())

from config import settings
from core.engines.alignment.natural_speech import synthesize_natural_speech
from core.engines.alignment.timing_aligner import SpeechBudgetError, TimingBudgetAligner
from core.engines.translation.semantic_translator import SemanticTranslator, PacingReviewRejected
from core.runtime_context import execution_context


class RecordedSynthesizer:
    """Synthetic PCM fixture, never a substitute for a real provider QA result."""

    def __init__(self, durations):
        self.durations = durations
        self.calls = []
        self.boundaries = {}

    def synthesize(self, *, text, output_path, **kwargs):
        self.calls.append(text)
        rate = 24000
        duration = self.durations[text] if isinstance(self.durations, dict) else self.durations
        samples = array("h", (round(6000 * math.sin(2 * math.pi * 440 * i / rate))
                              for i in range(round(duration * rate))))
        with wave.open(str(output_path), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(rate)
            audio.writeframes(samples.tobytes())
        self.boundaries[str(output_path)] = [{"text": text, "start": 0, "end": duration}]
        return output_path

    def take_word_boundaries(self, path):
        return self.boundaries.pop(str(path))


class PausedSynthesizer(RecordedSynthesizer):
    def __init__(self, *, pause=(1, 1.7), duration=3.19):
        super().__init__(duration)
        self.pause = pause
        self.original_pcm = None

    def synthesize(self, *, text, output_path, **kwargs):
        result = super().synthesize(text=text, output_path=output_path, **kwargs)
        with wave.open(str(output_path), "rb") as wav:
            params = wav.getparams()
            samples = array("h", wav.readframes(wav.getnframes()))
        left, right = (round(value * params.framerate) for value in self.pause)
        samples[left:right] = array("h", [0]) * (right - left)
        with wave.open(str(output_path), "wb") as wav:
            wav.setparams(params)
            wav.writeframes(samples.tobytes())
        self.original_pcm = output_path.read_bytes()
        return result


@pytest.fixture(autouse=True)
def reject_network(monkeypatch):
    # A missing stub must fail locally rather than consuming a provider request.
    def forbidden(*args, **kwargs):
        pytest.fail("This regression must not call a real translation provider")
    monkeypatch.setattr(SemanticTranslator, "_opencode_request", forbidden)


@pytest.fixture
def aligner(tmp_path, monkeypatch):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg and FFprobe are required for real PCM fitting")
    monkeypatch.setattr(settings, "TEMP_DIR", tmp_path)
    return TimingBudgetAligner()


def verified_candidate(text):
    return {"final_vi": text, "pacing_verification": {
        "status": "verified", "text": text, "provider": "opencode",
        "reason": "Chủ thể, hành động và phủ định được giữ nguyên.",
    }}


def assert_no_scratch(output):
    assert list(output.parent.glob("speech-fit-*")) == []


def test_short_speech_keeps_normal_speed_without_rewrite(tmp_path, aligner):
    text = "Chào bạn."
    engine = RecordedSynthesizer(.65)
    translator = Mock()
    output = tmp_path / "voice.wav"
    stages = []

    result = synthesize_natural_speech(text=text, source="你好。", duration=1.5,
        output_path=output, engine=engine, aligner=aligner, translator=translator,
        on_stage=stages.append)

    assert engine.calls == [text]
    translator.rewrite_for_pacing.assert_not_called()
    assert result["text"] == text
    assert result["speed_ratio"] == 1.0
    assert result["pacing_verification"] is None
    assert result["boundaries"][0]["text"] == text
    assert 0 < aligner.get_audio_duration(output) < 1.0
    assert stages == ["TTS", "ALIGNING"]
    assert_no_scratch(output)


def test_exact_speed_ceiling_keeps_complete_interjection_without_rewrite(tmp_path, aligner, monkeypatch):
    import subprocess
    from core.engines.alignment import timing_aligner as timing
    filters = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True,
                             text=True, check=True).stdout
    if "rubberband" not in filters:
        pytest.skip("Optional Rubber Band filter is unavailable")
    commands = []
    run = subprocess.run
    def record(command, **kwargs):
        commands.append(command)
        return run(command, **kwargs)
    monkeypatch.setattr(timing.subprocess, "run", record)
    engine, translator = RecordedSynthesizer(.46), Mock()
    output = tmp_path / "voice.wav"
    result = synthesize_natural_speech(text="Hả?", source="啥", duration=1.99 - 1.59,
        output_path=output, engine=engine, aligner=aligner, translator=translator)
    assert result["text"] == "Hả?" and result["pacing_verification"] is None
    assert result["speed_ratio"] == 1.15
    assert engine.calls == ["Hả?"]
    translator.rewrite_for_pacing.assert_not_called()
    with wave.open(str(output), "rb") as wav:
        assert wav.getnframes() == 9600 and wav.getframerate() == 24000
        samples = array("h", wav.readframes(wav.getnframes()))
    assert max(abs(value) for value in samples[-480:]) > 100
    audio_filters = [cmd[cmd.index("-filter:a") + 1] for cmd in commands if "-filter:a" in cmd]
    assert audio_filters == ["atempo=1.15000000", "rubberband=tempo=1.15000000:pitch=1"]
    assert_no_scratch(output)


def test_unavailable_exact_stretcher_never_publishes_overlong_audio(tmp_path, aligner):
    aligner._rubberband_available = False
    output = tmp_path / "voice.wav"
    output.write_bytes(b"previous-complete-output")
    with pytest.raises(SpeechBudgetError):
        synthesize_natural_speech(text="Hả?", source="啥", duration=.4, output_path=output,
            engine=RecordedSynthesizer(.46), aligner=aligner)
    assert output.read_bytes() == b"previous-complete-output"
    assert_no_scratch(output)


def test_real_speed_excess_is_not_treated_as_floating_point_noise(tmp_path, aligner):
    with pytest.raises(SpeechBudgetError):
        aligner.apply_atempo(tmp_path / "unused.wav", tmp_path / "out.wav", 1.150001, fit_duration=.4)
    assert not (tmp_path / "out.wav").exists()


def test_internal_synthesis_pause_fits_before_rewrite_and_keeps_exact_words(tmp_path, aligner, caplog):
    original = "Tôi vẫn chưa làm việc đó."
    engine, translator = PausedSynthesizer(), Mock()
    output = tmp_path / "voice.wav"
    with caplog.at_level(logging.INFO, logger="pipeline"), execution_context("pause-fit"):
        result = synthesize_natural_speech(text=original, source="我还没做。", duration=2.44,
            output_path=output, engine=engine, aligner=aligner, translator=translator)
    assert engine.calls == [original]
    translator.rewrite_for_pacing.assert_not_called()
    assert result["text"] == original and result["pacing_verification"] is None
    assert result["tts_duration"] <= 2.44 * 1.15
    assert result["speed_ratio"] <= 1.15
    assert 0 < aligner.get_audio_duration(output) <= 2.4401
    assert any("PACING_COMPACTED run_id=pause-fit" in record.getMessage() for record in caplog.records)
    assert_no_scratch(output)


def test_internal_pause_compaction_also_preserves_manual_text(tmp_path, aligner):
    text, output = "Giữ nguyên lời tôi đã sửa.", tmp_path / "voice.wav"
    engine = PausedSynthesizer()
    result = synthesize_natural_speech(text=text, source="", duration=2.44,
        output_path=output, engine=engine, aligner=aligner)
    assert result["text"] == text and engine.calls == [text]
    assert result["pacing_verification"] is None and result["speed_ratio"] <= 1.15
    assert aligner.get_audio_duration(output) <= 2.4401


def test_internal_then_edge_compaction_maps_boundaries_in_successive_timelines(tmp_path, monkeypatch):
    from core.engines.alignment import natural_speech as speech

    engine = RecordedSynthesizer(2)
    engine.take_word_boundaries = lambda path: [{"text": "cuối", "start": 1.6, "end": 1.9}]
    monkeypatch.setattr(speech, "trim_tts_padding", lambda path: 0)
    monkeypatch.setattr(speech, "audio_activity_span", lambda path: None)
    # First remove .4s internally, then .1s from the *new* outer end.
    # Concatenating these maps would wrongly cut the word twice.
    monkeypatch.setattr(speech, "compact_tts_pauses", lambda *args: [(.5, .9)])
    monkeypatch.setattr(speech, "compact_tts_edge_padding", lambda *args: [(1.5, 1.6)])
    aligner = Mock(max_speed=1.15)
    aligner.get_audio_duration.side_effect = [2, 1.5]
    def fit(raw, fitted, ratio, **kwargs):
        shutil.copyfile(raw, fitted)
        return ratio
    aligner.apply_atempo.side_effect = fit
    result = synthesize_natural_speech(text="cuối", source="", duration=1.4,
        output_path=tmp_path / "voice.wav", engine=engine, aligner=aligner)
    assert result["boundaries"][0]["start"] == pytest.approx(1.2)
    assert result["boundaries"][0]["end"] == pytest.approx(1.5)


def test_available_dialogue_gap_fits_complete_number_without_rewrite(tmp_path, aligner):
    engine, translator = RecordedSynthesizer(.8563), Mock()
    output = tmp_path / "voice.wav"
    result = synthesize_natural_speech(text="Mười chín.", source="十九", duration=.62,
        max_duration=.97, output_path=output, engine=engine, aligner=aligner, translator=translator)
    assert result["text"] == "Mười chín." and result["speed_ratio"] <= 1.15
    assert .62 < aligner.get_audio_duration(output) <= .97
    assert engine.calls == ["Mười chín."]
    translator.rewrite_for_pacing.assert_not_called()


def test_opt_in_tail_limit_allows_full_measured_speech_without_speed_shortening(tmp_path, aligner):
    # A 900 ms source row can use a proven one-second tail plus the ordinary
    # 350 ms start shift. The measured 1.67 s narration must remain complete.
    output = tmp_path / "voice.wav"
    result = synthesize_natural_speech(
        text="Mười chín.", source="十九", duration=.9, max_duration=1.67,
        max_duration_limit=2.25, output_path=output,
        engine=RecordedSynthesizer(1.67), aligner=aligner)
    assert result["text"] == "Mười chín."
    assert result["speed_ratio"] == pytest.approx(1.15)
    # The complete waveform is retained; only the configured natural speed
    # ceiling is used to fit it into the measured tail.
    assert aligner.get_audio_duration(output) == pytest.approx(1.67 / 1.15, abs=.002)


def test_tail_limit_rejects_unbounded_extra_slot(tmp_path, aligner):
    with pytest.raises(ValueError, match="Giới hạn"):
        synthesize_natural_speech(
            text="Dài.", source="長", duration=.9, max_duration=2.3,
            max_duration_limit=2.3, output_path=tmp_path / "voice.wav",
            engine=RecordedSynthesizer(1.67), aligner=aligner)


def test_adaptive_slot_does_not_change_already_fitting_speech(tmp_path, aligner):
    result = synthesize_natural_speech(text="Vâng.", source="", duration=.62, max_duration=.97,
        output_path=tmp_path / "voice.wav", engine=RecordedSynthesizer(.4), aligner=aligner)
    assert result["speed_ratio"] == 1
    assert result["tts_duration"] == pytest.approx(.4)


def test_pipeline_reflows_prior_audio_and_captions_atomically(tmp_path, aligner, monkeypatch):
    from core.streaming.pipeline import SegmentItem, StreamingPipelineSession
    monkeypatch.setattr(settings, "BASE_DIR", tmp_path)
    session = StreamingPipelineSession("reflow", None, tts_engine_name="edge-tts")
    previous = SegmentItem(5, 6.84, 7.72, .88)
    previous.status, previous.final_vi = "READY", "Đang đỉnh cao."
    previous.audio_path = str(session.segments_dir / "seg_5.wav")
    RecordedSynthesizer(.878).synthesize(text=previous.final_vi, output_path=Path(previous.audio_path))
    previous.subtitle_cues = [{"text": previous.final_vi, "start": 6.87, "end": 7.65,
                              "words": [{"text": "cao.", "start": 7.4, "end": 7.65}]}]
    previous.speech_start, previous.speech_end = 6.87, 7.65
    focus = SegmentItem(6, 7.72, 8.34, .62)
    focus.text_zh, focus.final_vi = "十九", "Mười chín."
    session.segments = {5: previous, 6: focus}
    session.total_duration = 10
    session.aligner, session.tts_engine = aligner, RecordedSynthesizer(.8563)
    session.translator = Mock()
    before = Path(previous.audio_path).read_bytes()
    asyncio.run(session._synthesize_segment(focus))
    assert focus.status == "READY" and focus.dub_start < focus.start
    assert (focus.start, focus.end, previous.start, previous.end) == (7.72, 8.34, 6.84, 7.72)
    assert previous.dub_end <= focus.dub_start
    assert Path(previous.audio_path).read_bytes() == before
    assert previous.subtitle_cues[0]["start"] < 6.87
    assert previous.subtitle_cues[0]["words"][0]["end"] == previous.subtitle_cues[0]["end"]
    assert focus.speech_end <= focus.dub_end
    assert focus.dub_end - focus.dub_start == pytest.approx(session._dub_audio_duration(focus.audio_path))
    session.translator.rewrite_for_pacing.assert_not_called()


@pytest.mark.parametrize("change", ["stop", "revision"])
def test_fit_does_not_publish_after_stop_or_a_concurrent_timeline_change(tmp_path, aligner, monkeypatch, change):
    from core.streaming.pipeline import SegmentItem, StreamingPipelineSession, SegmentEditConflict
    from core.engines.alignment.speech_timing import build_speech_timing
    monkeypatch.setattr(settings, "BASE_DIR", tmp_path)
    session = StreamingPipelineSession("stale-dub", None, tts_engine_name="edge-tts")
    row = SegmentItem(0, 1, 2, 1)
    row.final_vi = "Vâng."
    session.segments[0] = row
    session.total_duration = 3
    session.aligner, session.tts_engine = aligner, RecordedSynthesizer(.8)
    final = session.segments_dir / "seg_0.wav"
    final.write_bytes(b"previous-complete-audio")
    def race(*args, **kwargs):
        if change == "stop":
            session.is_stopped = True
        else:
            row.revision += 1
        return build_speech_timing(*args, **kwargs)
    monkeypatch.setattr("core.streaming.pipeline.build_speech_timing", race)
    with pytest.raises(asyncio.CancelledError if change == "stop" else SegmentEditConflict):
        asyncio.run(session._synthesize_segment(row))
    assert final.read_bytes() == b"previous-complete-audio"
    assert row.dub_start is None and row.dub_end is None
    assert not list(session.segments_dir.glob("pending_*.wav"))


def test_near_fit_238ms_pause_preserves_natural_floor_with_real_atempo(tmp_path, aligner):
    engine = PausedSynthesizer(pause=(1.7212, 1.9592), duration=2.84)
    output = tmp_path / "voice.wav"
    result = synthesize_natural_speech(text="Giữ nguyên câu này.", source="", duration=2.44,
        output_path=output, engine=engine, aligner=aligner)
    assert engine.calls == ["Giữ nguyên câu này."]
    assert result["speed_ratio"] <= 1.15 and aligner.get_audio_duration(output) <= 2.4401
    assert 2.78 <= result["tts_duration"] <= 2.79


def test_inadequate_silence_never_relaxes_speed_or_replaces_previous_output(tmp_path, aligner):
    output = tmp_path / "voice.wav"
    output.write_bytes(b"previous complete output")
    engine = PausedSynthesizer(pause=(1, 1.238))
    with pytest.raises(SpeechBudgetError):
        synthesize_natural_speech(text="Giữ đủ các từ này.", source="", duration=2.44,
            output_path=output, engine=engine, aligner=aligner)
    assert output.read_bytes() == b"previous complete output"
    assert_no_scratch(output)


def test_normal_fitting_speech_does_not_compact_punctuation_pauses(tmp_path, aligner, monkeypatch):
    engine = PausedSynthesizer()
    recorded = []
    fit = aligner.apply_atempo

    def capture(raw, *args, **kwargs):
        recorded.append(raw.read_bytes())
        return fit(raw, *args, **kwargs)

    monkeypatch.setattr(aligner, "apply_atempo", capture)
    result = synthesize_natural_speech(text="Giữ nhịp đọc này.", source="", duration=4,
        output_path=tmp_path / "voice.wav", engine=engine, aligner=aligner)
    assert result["speed_ratio"] == 1 and recorded == [engine.original_pcm]


def test_verified_rewrite_is_resynthesized_and_matches_published_audio(tmp_path, aligner):
    original, shorter = "Tôi vẫn chưa làm việc đó.", "Tôi chưa làm."
    engine = RecordedSynthesizer({original: 2.0, shorter: .72})
    translator = Mock(rewrite_for_pacing=Mock(return_value=verified_candidate(shorter)))
    output = tmp_path / "voice.wav"
    output.write_bytes(b"previous complete output")
    stages = []
    context = [{"zh": "做了吗？", "vi": "Làm chưa?"}]

    result = synthesize_natural_speech(text=original, source="我还没做。", duration=1,
        output_path=output, engine=engine, aligner=aligner, translator=translator,
        context=context, on_stage=stages.append)

    assert translator.rewrite_for_pacing.call_count == 1
    call = translator.rewrite_for_pacing.call_args
    assert call.args == ("我还没做。", original, pytest.approx(1.15 * .98), context)
    assert call.kwargs["measured_duration"] == pytest.approx(2.0, abs=.01)
    assert call.kwargs["feedback"]["measured_candidates"][0]["text"] == original
    assert call.kwargs["feedback"]["required_reduction_pct"] > 40
    assert engine.calls == [original, shorter]
    assert result["text"] == result["pacing_verification"]["text"] == shorter
    assert result["tts_duration"] == pytest.approx(.72, abs=.01)
    assert result["boundaries"][0]["text"] == shorter
    assert 0 < aligner.get_audio_duration(output) <= 1
    assert result["speed_ratio"] <= 1.15
    assert stages == ["TTS", "ALIGNING", "REWRITING", "TTS", "ALIGNING"]
    assert_no_scratch(output)


def test_rewrites_are_bounded_and_never_replace_output_when_nothing_fits(tmp_path, aligner):
    output = tmp_path / "voice.wav"
    previous = b"previous complete output"
    output.write_bytes(previous)
    engine = RecordedSynthesizer(2.0)
    translator = Mock(rewrite_for_pacing=Mock(side_effect=[
        verified_candidate("Tôi chưa làm."), verified_candidate("Chưa làm.")]))

    with pytest.raises(SpeechBudgetError, match="quá dài"):
        synthesize_natural_speech(text="Tôi vẫn chưa làm việc đó.", source="我还没做。",
            duration=1, output_path=output, engine=engine, aligner=aligner, translator=translator)

    assert len(engine.calls) == 3
    assert translator.rewrite_for_pacing.call_count == 2
    assert output.read_bytes() == previous
    assert_no_scratch(output)


def test_measured_overshoot_tightens_next_request_instead_of_repeating_the_same_budget(tmp_path, aligner):
    original, long_draft, fits = "Tôi vẫn chưa làm việc đó.", "Tôi vẫn chưa làm.", "Tôi chưa làm."
    engine = RecordedSynthesizer({original: 2.0, long_draft: 1.5, fits: .85})
    requests = []

    def rewrite(source, current, budget, context, **options):
        requests.append((budget, options))
        # A provider which overestimates its shortening needs measured feedback.
        candidate = fits if budget < 1.0 else long_draft
        return verified_candidate(candidate)

    translator = Mock(rewrite_for_pacing=Mock(side_effect=rewrite))
    result = synthesize_natural_speech(text=original, source="我还没做。", duration=1,
        output_path=tmp_path / "voice.wav", engine=engine, aligner=aligner, translator=translator)

    assert engine.calls == [original, long_draft, fits]
    assert requests[1][0] < requests[0][0]
    assert requests[1][1]["feedback"]["measured_candidates"][-1]["measured_seconds"] == pytest.approx(1.5, abs=.01)
    assert requests[1][1]["feedback"]["raw_budget_seconds"] == pytest.approx(1.15)
    assert result["text"] == fits and result["speed_ratio"] <= 1.15


def test_unchanged_candidate_does_not_spend_another_tts_attempt(tmp_path, aligner):
    original, fits = "Tôi vẫn chưa làm việc đó.", "Tôi chưa làm."
    engine = RecordedSynthesizer({original: 2, fits: .8})
    translator = Mock(rewrite_for_pacing=Mock(side_effect=[
        verified_candidate(original), verified_candidate(fits)]))
    result = synthesize_natural_speech(text=original, source="我还没做。", duration=1,
        output_path=tmp_path / "voice.wav", engine=engine, aligner=aligner, translator=translator)
    assert engine.calls == [original, fits]
    assert result["text"] == fits


def test_duplicate_proposals_are_bounded_and_preserve_previous_output(tmp_path, aligner):
    original = "Tôi vẫn chưa làm việc đó."
    engine = RecordedSynthesizer(2)
    translator = Mock(rewrite_for_pacing=Mock(return_value=verified_candidate(original)))
    output = tmp_path / "voice.wav"
    output.write_bytes(b"previous complete output")
    with pytest.raises(PacingReviewRejected) as rejected:
        synthesize_natural_speech(text=original, source="我还没做。", duration=1,
            output_path=output, engine=engine, aligner=aligner, translator=translator)
    assert rejected.value.code == "duplicate"
    assert translator.rewrite_for_pacing.call_count == 3
    assert engine.calls == [original]
    assert output.read_bytes() == b"previous complete output"
    assert_no_scratch(output)


def test_pacing_diagnostics_correlate_run_without_transcripts_or_provider_error_text(tmp_path, aligner, caplog):
    original, fits = "Thông tin riêng của tôi.", "Việc riêng."
    engine = RecordedSynthesizer({original: 2, fits: .8})
    translator = Mock(rewrite_for_pacing=Mock(side_effect=[
        PacingReviewRejected("secret-provider-error", candidate="private-candidate",
                             reason="secret-rejection-detail", code="semantic_mismatch"), verified_candidate(fits)]))
    with caplog.at_level(logging.INFO, logger="pipeline"), execution_context("pacing-run-123"):
        synthesize_natural_speech(text=original, source="私人信息", duration=1,
            output_path=tmp_path / "voice.wav", engine=engine, aligner=aligner, translator=translator)
    diagnostic = "\n".join(record.getMessage() for record in caplog.records if record.name == "pipeline")
    assert "PACING_MEASURED run_id=pacing-run-123" in diagnostic
    assert "request_budget_seconds=" in diagnostic and "reason=semantic_mismatch" in diagnostic
    assert "PACING_READY" in diagnostic
    for private in [original, fits, "私人信息", "private-candidate", "secret-provider-error", "secret-rejection-detail"]:
        assert private not in diagnostic


@pytest.mark.parametrize("failure", [RuntimeError("provider unavailable"), ValueError("bad schema")])
def test_untyped_provider_failures_are_not_retried_as_pacing_rejections(tmp_path, aligner, failure):
    engine = RecordedSynthesizer(2)
    translator = Mock(rewrite_for_pacing=Mock(side_effect=failure))
    with pytest.raises(type(failure), match=str(failure)):
        synthesize_natural_speech(text="Tôi chưa làm việc đó.", source="我还没做。", duration=1,
            output_path=tmp_path / "voice.wav", engine=engine, aligner=aligner, translator=translator)
    assert translator.rewrite_for_pacing.call_count == 1 and len(engine.calls) == 1


def test_rejected_pacing_draft_retries_without_speaking_unverified_words(tmp_path, aligner):
    original, shorter = "Tôi vẫn chưa làm việc đó.", "Tôi chưa làm."
    engine = RecordedSynthesizer({original: 2.0, shorter: .72})
    translator = Mock(rewrite_for_pacing=Mock(side_effect=[
        PacingReviewRejected("Rejected", candidate="Sai.", reason="Mất phủ định."), verified_candidate(shorter)]))
    output = tmp_path / "voice.wav"
    result = synthesize_natural_speech(text=original, source="我还没做。", duration=1,
        output_path=output, engine=engine, aligner=aligner, translator=translator)
    assert engine.calls == [original, shorter]
    assert translator.rewrite_for_pacing.call_count == 2
    assert translator.rewrite_for_pacing.call_args.kwargs["feedback"]["reason"] == "Mất phủ định."
    assert result["text"] == shorter


def test_rejected_pacing_drafts_stop_after_three_attempts(tmp_path, aligner):
    engine = RecordedSynthesizer(2.0)
    translator = Mock(rewrite_for_pacing=Mock(side_effect=PacingReviewRejected("Rejected")))
    output = tmp_path / "voice.wav"
    with pytest.raises(PacingReviewRejected):
        synthesize_natural_speech(text="Tôi vẫn chưa làm việc đó.", source="我还没做。", duration=1,
            output_path=output, engine=engine, aligner=aligner, translator=translator)
    assert translator.rewrite_for_pacing.call_count == 3
    assert len(engine.calls) == 1 and not output.exists()


@pytest.mark.parametrize("proof", [None, {}, {"status": "pending", "text": "Chưa làm."},
                                     {"status": "verified", "text": "Đã làm."}])
def test_unverified_rewrite_preserves_output_without_synthesizing_candidate(tmp_path, aligner, proof):
    output = tmp_path / "voice.wav"
    previous = b"previous complete output"
    output.write_bytes(previous)
    original = "Tôi vẫn chưa làm việc đó."
    engine = RecordedSynthesizer(2.0)
    translator = Mock(rewrite_for_pacing=Mock(return_value={
        "final_vi": "Chưa làm.", "pacing_verification": proof}))

    with pytest.raises(RuntimeError, match="Thiếu xác minh"):
        synthesize_natural_speech(text=original, source="我还没做。", duration=1,
            output_path=output, engine=engine, aligner=aligner, translator=translator)

    assert engine.calls == [original]
    assert output.read_bytes() == previous
    assert_no_scratch(output)


@pytest.mark.parametrize("when", ["before", "after_synthesis"])
@pytest.mark.parametrize("previous_exists", [False, True])
def test_cancelled_speech_never_publishes_new_output(tmp_path, aligner, when, previous_exists):
    output = tmp_path / "voice.wav"
    previous = b"previous complete output"
    if previous_exists:
        output.write_bytes(previous)
    engine = RecordedSynthesizer(.6)
    cancelled = when == "before"

    def stage(value):
        nonlocal cancelled
        if value == "ALIGNING":
            cancelled = True

    with execution_context("voice-cancel-test", lambda: cancelled):
        with pytest.raises(asyncio.CancelledError):
            synthesize_natural_speech(text="Chào bạn.", source="你好。", duration=1,
                output_path=output, engine=engine, aligner=aligner, on_stage=stage)

    assert engine.calls == ([] if when == "before" else ["Chào bạn."])
    assert output.exists() == previous_exists
    if previous_exists:
        assert output.read_bytes() == previous
    assert_no_scratch(output)


def candidate_response(*, needs_review=False):
    return json.dumps({"literal_vi": "Tôi vẫn chưa làm.", "natural_vi": "Tôi chưa làm.",
                       "final_vi": "Tôi chưa làm.", "needs_review": needs_review,
                       "review_reason": "Không thể chắc chắn giữ đủ nghĩa." if needs_review else ""},
                      ensure_ascii=False)


def rewrite_response(text, *, needs_review=False, reason=""):
    """Build a provider rewrite response with an explicitly chosen candidate."""
    return json.dumps({"literal_vi": text, "natural_vi": text, "final_vi": text,
                       "needs_review": needs_review, "review_reason": reason},
                      ensure_ascii=False)


def test_rewrite_verifies_exact_candidate_in_a_separate_provider_request(monkeypatch):
    translator = SemanticTranslator(provider="opencode")
    request = Mock(side_effect=[candidate_response(), json.dumps({
        "equivalent": True, "natural": True, "address_preserved": True,
        "reason": "Giữ chủ thể và ý chưa thực hiện."})])
    monkeypatch.setattr(translator, "_opencode_request", request)

    result = translator.rewrite_for_pacing("我还没做。", "Tôi vẫn chưa làm việc đó.", 1.5,
                                         [{"zh": "做了吗？", "vi": "Làm chưa?"}])

    assert request.call_count == 2
    review_input = json.loads(request.call_args_list[1].args[1])
    assert review_input["source"] == "我还没做。"
    assert review_input["candidate"] == result["final_vi"] == "Tôi chưa làm."
    assert review_input["previous"] == "Tôi vẫn chưa làm việc đó."
    assert "Làm chưa?" in review_input["context"]
    assert result["pacing_verification"]["text"] == review_input["candidate"]
    assert result["pacing_verification"]["status"] == "verified"


def test_neutral_ellipsis_passes_only_after_independent_address_safe_verdict(monkeypatch):
    """Vietnamese dialogue may omit recoverable pronouns, but the verifier must approve it."""
    translator = SemanticTranslator(provider="opencode")
    candidate = "Hỏi mấy tuổi rồi, nói mau!"
    request = Mock(side_effect=[
        rewrite_response(candidate),
        json.dumps({
            "equivalent": True,
            "natural": True,
            "address_preserved": True,
            "reason": "Lược tôi/bạn theo khẩu ngữ; người hỏi và người bị hỏi vẫn rõ trong mạch câu.",
        }, ensure_ascii=False),
    ])
    monkeypatch.setattr(translator, "_opencode_request", request)

    result = translator.rewrite_for_pacing(
        "我问你几岁了快说",
        "Tôi hỏi bạn mấy tuổi rồi, nói mau!",
        1.5,
        [{"zh": "我问你几岁了快说", "vi": "Tôi hỏi bạn mấy tuổi rồi, nói mau!"}],
    )

    assert request.call_count == 2
    assert result["final_vi"] == candidate
    assert result["pacing_verification"]["address_preserved"] is True
    review_input = json.loads(request.call_args_list[1].args[1])
    assert review_input["candidate"] == candidate


@pytest.mark.parametrize("field", ["equivalent", "natural", "address_preserved"])
@pytest.mark.parametrize("value", [False, None, "true"])
def test_neutral_ellipsis_does_not_bypass_any_independent_gate(monkeypatch, field, value):
    translator = SemanticTranslator(provider="opencode")
    candidate = "Hỏi mấy tuổi rồi, nói mau!"
    verdict = {"equivalent": True, "natural": True, "address_preserved": True,
               "reason": "Đối chiếu nguồn và câu rút gọn."}
    verdict[field] = value
    request = Mock(side_effect=[rewrite_response(candidate), json.dumps(verdict)])
    monkeypatch.setattr(translator, "_opencode_request", request)

    with pytest.raises(PacingReviewRejected):
        translator.rewrite_for_pacing("我问你几岁了快说", "Tôi hỏi bạn mấy tuổi rồi, nói mau!", 1.5)

    assert request.call_count == 2
    assert json.loads(request.call_args_list[1].args[1])["candidate"] == candidate


@pytest.mark.parametrize("uncertainty", ["source", "address"])
def test_successful_pacing_does_not_clear_existing_pipeline_uncertainty(
        tmp_path, aligner, monkeypatch, uncertainty):
    from core.streaming.pipeline import SegmentItem, StreamingPipelineSession

    monkeypatch.setattr(settings, "BASE_DIR", tmp_path)
    session = StreamingPipelineSession("pacing-uncertainty", None,
                                       voice="vi-VN-HoaiMyNeural", tts_engine_name="edge-tts")
    segment = SegmentItem(0, 0, 1.5, 1.5)
    original, shorter = "Tôi hỏi bạn mấy tuổi rồi, nói mau!", "Hỏi mấy tuổi, nói mau!"
    segment.text_zh, segment.final_vi = "我问你几岁了快说", original
    segment.needs_review = True
    segment.review_reason = "Nguồn chưa chắc." if uncertainty == "source" else "Chưa rõ quan hệ nhân vật."
    segment.verification = {
        "status": "unresolved", "source_supported": uncertainty != "source",
        "semantic_verified": False, "address_verified": False,
        "address_context": {"uncertain": uncertainty == "address"},
    }
    session.segments[0] = segment
    session.total_duration = 1.5
    session.aligner = aligner
    session.tts_engine = RecordedSynthesizer({original: 2.39, shorter: 1.0})
    session.translator = SemanticTranslator(provider="opencode")
    proof = {"equivalent": True, "natural": True, "address_preserved": True,
             "reason": "Giữ câu hỏi tuổi và thúc giục, không xác nhận quan hệ nhân vật."}
    request = Mock(side_effect=[rewrite_response(shorter), json.dumps(proof)])
    monkeypatch.setattr(session.translator, "_opencode_request", request)

    asyncio.run(session._synthesize_segment(segment))

    assert request.call_count == 2
    assert segment.final_vi == shorter and segment.audio_path
    assert segment.status == "READY"
    assert segment.needs_review is True
    assert segment.review_reason == ("Nguồn chưa chắc." if uncertainty == "source" else "Chưa rõ quan hệ nhân vật.")
    assert segment.verification["status"] == "unresolved"
    assert segment.verification["source_supported"] is (uncertainty != "source")
    assert segment.verification["semantic_verified"] is False
    assert segment.verification["address_verified"] is False
    assert segment.verification["address_context"]["uncertain"] is (uncertainty == "address")
    assert segment.verification["pacing"]["status"] == "verified"
    assert segment.verification["pacing"]["text"] == shorter


def test_dropped_contrastive_subject_is_rejected_even_when_candidate_is_fluent(monkeypatch):
    """Dropping 我 in 我来问 changes the contrastive speaker and is not safe ellipsis."""
    translator = SemanticTranslator(provider="opencode")
    candidate = "Phải hỏi mới đúng chứ."
    request = Mock(side_effect=[
        rewrite_response(candidate),
        json.dumps({
            "equivalent": False,
            "natural": True,
            "address_preserved": False,
            "reason": "Mất đối lập: nguồn nhấn mạnh tôi mới là người hỏi.",
        }, ensure_ascii=False),
    ])
    monkeypatch.setattr(translator, "_opencode_request", request)

    with pytest.raises(PacingReviewRejected) as rejected:
        translator.rewrite_for_pacing(
            "这话应该我来问吧",
            "Câu này phải để tôi hỏi mới đúng chứ.",
            1.2,
        )

    assert rejected.value.code == "semantic_mismatch"
    assert request.call_count == 2


def test_role_changing_pacing_candidate_is_rejected_by_address_gate(monkeypatch):
    """A rewrite cannot turn an unresolved speaker into chị just to save syllables."""
    translator = SemanticTranslator(provider="opencode")
    candidate = "Chị phải hỏi chứ."
    request = Mock(side_effect=[
        rewrite_response(candidate),
        json.dumps({
            "equivalent": True,
            "natural": True,
            "address_preserved": False,
            "reason": "Candidate đổi người nói thành chị khi nguồn chưa xác định vai.",
        }, ensure_ascii=False),
    ])
    monkeypatch.setattr(translator, "_opencode_request", request)

    with pytest.raises(PacingReviewRejected) as rejected:
        translator.rewrite_for_pacing(
            "这话应该我来问吧",
            "Câu này phải để tôi hỏi mới đúng chứ.",
            1.2,
        )

    assert rejected.value.code == "invalid_review"
    assert request.call_count == 2


@pytest.mark.parametrize("verdict", [
    {"equivalent": False, "natural": True, "reason": "Mất phủ định."},
    {"equivalent": True, "natural": False, "reason": "Câu khó đọc."},
    {"equivalent": "true", "natural": True, "reason": "Không đúng kiểu dữ liệu."},
    {"equivalent": True, "natural": "true", "reason": "Không đúng kiểu dữ liệu."},
    {"equivalent": True, "natural": True, "reason": " "},
    {"status": "verified"}, None, [],
])
def test_independent_review_must_confirm_both_meaning_and_naturalness(monkeypatch, verdict):
    translator = SemanticTranslator(provider="opencode")
    request = Mock(side_effect=[candidate_response(), json.dumps(verdict)])
    monkeypatch.setattr(translator, "_opencode_request", request)

    with pytest.raises(RuntimeError, match="chưa vượt qua kiểm tra"):
        translator.rewrite_for_pacing("我还没做。", "Tôi vẫn chưa làm việc đó.", 1.5)
    assert request.call_count == 2


def test_uncertain_candidate_is_rejected_before_semantic_approval(monkeypatch):
    translator = SemanticTranslator(provider="opencode")
    request = Mock(return_value=candidate_response(needs_review=True))
    monkeypatch.setattr(translator, "_opencode_request", request)

    with pytest.raises(RuntimeError, match="chưa tìm được"):
        translator.rewrite_for_pacing("我还没做。", "Tôi vẫn chưa làm việc đó.", 1.5)
    assert request.call_count == 1


def test_provider_duplicate_is_rejected_before_another_verification_request(monkeypatch):
    translator = SemanticTranslator(provider="opencode")
    request = Mock(return_value=candidate_response())
    monkeypatch.setattr(translator, "_opencode_request", request)
    with pytest.raises(PacingReviewRejected) as rejected:
        translator.rewrite_for_pacing("我还没做。", "  TÔI   chưa làm. ", 1)
    assert rejected.value.code == "duplicate" and request.call_count == 1


def test_measured_history_and_rejection_feedback_reach_rewriter_but_do_not_replace_independent_verdict(monkeypatch):
    translator = SemanticTranslator(provider="opencode")
    request = Mock(side_effect=[candidate_response(), json.dumps({
        "equivalent": False, "natural": True, "reason": "Mất ý."})])
    monkeypatch.setattr(translator, "_opencode_request", request)
    feedback = {"raw_budget_seconds": 1.15, "request_budget_seconds": .8, "required_reduction_pct": 46.7,
                "measured_candidates": [{"text": "Tôi vẫn chưa làm.", "measured_seconds": 1.5}],
                "rejected_candidate": "Không làm.", "reason": "Mất ý chưa thực hiện."}
    with pytest.raises(PacingReviewRejected) as rejected:
        translator.rewrite_for_pacing("我还没做。", "Tôi vẫn chưa làm.", .8,
                                      measured_duration=1.5, feedback=feedback)
    assert rejected.value.code == "semantic_mismatch"
    assert request.call_count == 2
    assert json.dumps(feedback, ensure_ascii=False) in request.call_args_list[0].args[1]
    assert json.loads(request.call_args_list[1].args[1])["candidate"] == "Tôi chưa làm."
