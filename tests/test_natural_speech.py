"""Offline regressions for measured speech fitting and independent rewrite review."""
import asyncio
import json
import logging
import math
import shutil
import wave
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


def test_rewrite_verifies_exact_candidate_in_a_separate_provider_request(monkeypatch):
    translator = SemanticTranslator(provider="opencode")
    request = Mock(side_effect=[candidate_response(), json.dumps({
        "equivalent": True, "natural": True, "reason": "Giữ chủ thể và ý chưa thực hiện."})])
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
