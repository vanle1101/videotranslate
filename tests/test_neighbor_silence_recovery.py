"""Recover timeline capacity from real PCM padding without editing user speech."""
import asyncio
from pathlib import Path
import wave
import logging
from unittest.mock import Mock

import numpy as np
import pytest

from config import settings
from core.streaming.pipeline import StreamingPipelineSession, SegmentItem
from core.engines.alignment.dub_timing import available_reflow_duration
from core.engines.alignment.timing_aligner import SpeechBudgetError


@pytest.fixture
def rescue(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "BASE_DIR", tmp_path)
    monkeypatch.setattr(settings, "TEMP_DIR", tmp_path)
    session = StreamingPipelineSession("silence-rescue", None)
    session._source_prepared_seconds = session.total_duration = 2.5
    session._chunked_source_started = True
    for sid, start, end in [(0, 0., 1.), (1, 1., 1.5), (2, 1.5, 2.5)]:
        row = SegmentItem(sid, start, end, end - start)
        row.final_vi = "Giữ nguyên lời."
        row.verification = {"status": "manual" if sid == 2 else "unresolved"}
        session.segments[sid] = row
        if sid == 1:
            continue
        row.audio_path = str(session.segments_dir / f"original-{sid}.wav")
        pcm = np.zeros(24000, dtype="<i2")
        pcm[1200:21600] = 1000  # .05-second lead, .10-second tail.
        with wave.open(row.audio_path, "wb") as audio:
            audio.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
            audio.writeframes(pcm.tobytes())
        row.status, row.speed_ratio, row.tts_duration = "READY", 1.15, 1.15
        row.subtitle_cues = [{"start": start + .05, "end": start + .9,
                              "words": [{"text": "Giữ", "start": start + .05, "end": start + .9}]}]
    return session


def test_reclaims_padding_without_changing_originals_manual_rows_or_speed(rescue):
    originals = {sid: (row.to_dict(), Path(row.audio_path).read_bytes())
                 for sid, row in rescue.segments.items() if row.audio_path}
    rows = rescue._dub_rows()
    before = available_reflow_duration(rows, 1, total_duration=2.5)
    shadow, staged = asyncio.run(rescue._prepare_dub_rescue(rows, rescue.segments[1], before + .025))
    assert set(staged) == {0}
    assert available_reflow_duration(shadow, 1, total_duration=2.5) >= before + .025
    assert rows == rescue._dub_rows()  # No timeline publication in preparation.
    update = staged[0]
    assert update["spoken"]["text"] == originals[0][0]["final_vi"]
    assert update["spoken"]["speed_ratio"] == 1.15
    assert update["spoken"]["timing_ratio"] == 1.
    with wave.open(update["audio_path"], "rb") as audio:
        pcm = np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2")
    assert np.count_nonzero(pcm) == 20400  # Every nonzero speech sample survives.
    cut = 1. - len(pcm) / 24000
    assert update["spoken"]["boundaries"][0]["start"] == pytest.approx(.05 - cut)
    for sid, (state, content) in originals.items():
        assert rescue.segments[sid].to_dict() == state
        assert Path(rescue.segments[sid].audio_path).read_bytes() == content
    Path(update["audio_path"]).unlink()


def test_manual_neighbor_is_never_compacted(rescue):
    rescue.segments[0].verification = {"status": "manual"}
    rows = rescue._dub_rows()
    before = available_reflow_duration(rows, 1, total_duration=2.5)
    shadow, staged = asyncio.run(rescue._prepare_dub_rescue(rows, rescue.segments[1], before + .025))
    assert not staged and shadow == rows
    assert sorted(path.name for path in rescue.segments_dir.iterdir()) == ["original-0.wav", "original-2.wav"]


def test_compaction_after_real_tempo_preserves_combined_speed_and_retires_stage(rescue):
    row = rescue.segments[0]
    row.speed_ratio = row.tts_duration = 1.05
    source_bytes = Path(row.audio_path).read_bytes()
    rows = rescue._dub_rows()
    before = available_reflow_duration(rows, 1, total_duration=2.5)
    shadow, staged = asyncio.run(rescue._prepare_dub_rescue(rows, rescue.segments[1], before + .11))
    assert set(staged) == {0}
    spoken = staged[0]["spoken"]
    assert 1.05 < spoken["speed_ratio"] <= 1.15
    assert spoken["timing_ratio"] == 1.  # Boundaries now refer to the post-tempo/cut PCM.
    assert 0 <= spoken["boundaries"][0]["start"] < spoken["boundaries"][0]["end"]
    assert spoken["boundaries"][0]["end"] <= rescue._dub_audio_duration(staged[0]["audio_path"])
    assert available_reflow_duration(shadow, 1, total_duration=2.5) >= before + .11
    assert Path(row.audio_path).read_bytes() == source_bytes
    assert len(list(rescue.segments_dir.glob("seg_*.wav"))) == 1
    Path(staged[0]["audio_path"]).unlink()


def test_cancel_during_compaction_removes_staged_copy_only(rescue, monkeypatch):
    def cancel(path, duration):
        raise asyncio.CancelledError
    monkeypatch.setattr("core.streaming.pipeline.compact_tts_edge_padding", cancel)
    rows = rescue._dub_rows()
    before = available_reflow_duration(rows, 1, total_duration=2.5)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(rescue._prepare_dub_rescue(rows, rescue.segments[1], before + .025))
    assert sorted(path.name for path in rescue.segments_dir.iterdir()) == ["original-0.wav", "original-2.wav"]
    assert rows == rescue._dub_rows()


def test_neighbor_rewrite_logs_bounded_rejections_without_dialogue(rescue, monkeypatch, caplog):
    from core.engines.translation.semantic_translator import PacingReviewRejected
    row = rescue.segments[0]
    row.verification = {"status": "verified"}
    row.final_vi = "PRIVATE-DIALOGUE"
    rescue.translator = Mock()
    rescue.translator.rewrite_for_pacing.side_effect = PacingReviewRejected(
        "PRIVATE-REJECTION", candidate="PRIVATE-CANDIDATE", code="semantic_mismatch")
    monkeypatch.setattr("core.streaming.pipeline.compact_tts_edge_padding", lambda *a: [])
    monkeypatch.setattr(rescue, "_dialogue_context_before", lambda *a: [])
    rows = rescue._dub_rows()
    before = available_reflow_duration(rows, 1, total_duration=2.5)
    with caplog.at_level(logging.INFO, logger="pipeline"):
        _, staged = asyncio.run(rescue._prepare_dub_rescue(rows, rescue.segments[1], before + .025))
    assert not staged
    assert rescue.translator.rewrite_for_pacing.call_count == 5
    calls = rescue.translator.rewrite_for_pacing.call_args_list
    assert not calls[0].kwargs['feedback'].get('rejected_candidates')
    assert calls[1].kwargs['feedback']['rejected_candidates'] == [{
        'text': 'PRIVATE-CANDIDATE', 'reason': '', 'code': 'semantic_mismatch'}]
    assert calls[2].kwargs['feedback']['rejected_candidates'] == calls[1].kwargs['feedback']['rejected_candidates']
    assert caplog.text.count("PACING_NEIGHBOR_REWRITE_REJECTED") == 5
    assert "focus_id=1 neighbor_id=0 attempt=3 code=semantic_mismatch" in caplog.text
    assert "PRIVATE-" not in caplog.text


def _fixture_speech(path, seconds):
    """Offline synthetic PCM for publication tests, never a provider result."""
    with wave.open(str(path), "wb") as audio:
        audio.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        audio.writeframes(np.full(round(seconds * 24000), 1000, dtype="<i2").tobytes())


@pytest.mark.parametrize("semantic_verified", [True, False])
def test_neighbor_draft_requires_meaning_gate_and_preserves_review_flag(rescue, monkeypatch, semantic_verified):
    row = rescue.segments[0]
    row.verification = {"status": "verified"}
    proof = {"status": "draft", "semantic_verified": semantic_verified,
             "text": "Giữ lời.", "review_reason": "Cần kiểm tra ngữ cảnh."}
    rescue.translator = Mock()
    rescue.translator.rewrite_for_pacing.return_value = {"final_vi": "Giữ lời.", "pacing_verification": proof}
    monkeypatch.setattr("core.streaming.pipeline.compact_tts_edge_padding", lambda *a: [])
    monkeypatch.setattr(rescue, "_dialogue_context_before", lambda *a: [])
    rescue.tts_engine = object()

    def speech(**kwargs):
        _fixture_speech(kwargs["output_path"], .7)
        return {"text": kwargs["text"], "tts_duration": .7, "speed_ratio": 1., "boundaries": []}

    monkeypatch.setattr("core.streaming.pipeline.synthesize_natural_speech", speech)
    rows = rescue._dub_rows()
    before = available_reflow_duration(rows, 1, total_duration=2.5)
    if not semantic_verified:
        with pytest.raises(RuntimeError, match="Thiếu xác minh"):
            asyncio.run(rescue._prepare_dub_rescue(rows, rescue.segments[1], before + .025))
        assert row.verification == {"status": "verified"}
        assert not list(rescue.segments_dir.glob("seg_*.wav"))
        return
    _, staged = asyncio.run(rescue._prepare_dub_rescue(rows, rescue.segments[1], before + .025))
    assert rescue.translator.rewrite_for_pacing.call_args.kwargs["allow_review_draft"] is True
    assert staged[0]["spoken"]["needs_review"] is True
    assert row.final_vi == "Giữ nguyên lời."  # Preparation never publishes.
    from core.engines.alignment.speech_timing import build_speech_timing
    staged[0]["timing"] = build_speech_timing("Giữ lời.", 0., 1., staged[0]["audio_path"], 1., [])
    rescue._publish_dub_plan({0: {"dub_start": 0., "dub_end": 1., "_audio_update": staged[0]}}, 1)
    assert row.final_vi == "Giữ lời."
    assert row.needs_review is True
    assert row.verification["status"] == "unresolved"
    assert row.verification["pacing"] == proof
    assert row.review_reason == "Cần kiểm tra ngữ cảnh."


@pytest.mark.parametrize("fitted_seconds,expected_status", [(.5, "READY"), (.6, None)])
def test_ordinary_synthesis_preserves_manual_neighbor_times_and_audio(
        rescue, monkeypatch, fitted_seconds, expected_status):
    # Automatic synthesis previously omitted the opt-in flag and moved these
    # intervals to make a too-long waveform appear to fit.
    rescue.segments[0].verification = {"status": "manual"}
    for sid in (0, 2):
        row = rescue.segments[sid]
        row.dub_start, row.dub_end = row.start, row.end
    originals = {sid: (rescue.segments[sid].to_dict(), Path(rescue.segments[sid].audio_path).read_bytes())
                 for sid in (0, 2)}
    focus = rescue.segments[1]
    focus.text_zh = "来源"
    rescue.tts_engine = object()
    rescue.translator = None
    monkeypatch.setattr(rescue, "_dialogue_context_before", lambda *a: [])
    calls = []

    def synthesis(**kwargs):
        calls.append(kwargs)
        _fixture_speech(kwargs["output_path"], fitted_seconds)
        return {"text": kwargs["text"], "tts_duration": fitted_seconds,
                "speed_ratio": 1., "boundaries": [], "pacing_verification": None}

    monkeypatch.setattr("core.streaming.pipeline.synthesize_natural_speech", synthesis)
    if expected_status is None:
        # The queue owner records FAILED; synthesis must propagate the error.
        with pytest.raises(SpeechBudgetError):
            asyncio.run(rescue._synthesize_segment(focus))
        assert focus.status != "READY"
    else:
        asyncio.run(rescue._synthesize_segment(focus))
        assert focus.status == expected_status
    assert len(calls) == 1
    assert calls[0]["max_duration"] == pytest.approx(.5)
    for sid, (state, pcm) in originals.items():
        assert rescue.segments[sid].to_dict() == state
        assert Path(rescue.segments[sid].audio_path).read_bytes() == pcm
    assert not list(rescue.segments_dir.glob("pending_*.wav"))
    if expected_status is None:
        assert focus.audio_path is None
        assert not (rescue.segments_dir / "seg_1.wav").exists()


def test_rejected_rescue_uses_fixed_capacity_and_cleans_staged_neighbors(rescue, monkeypatch):
    # Its recorded interval reserves 1 second although the waveform is .8 s.
    # Moving/shrinking this manual interval would incorrectly fund the rescue.
    _fixture_speech(rescue.segments[2].audio_path, .8)
    originals = {sid: (row.to_dict(), Path(row.audio_path).read_bytes())
                 for sid, row in rescue.segments.items() if row.audio_path}
    rescue.tts_engine = object()
    rescue.translator = object()
    focus = rescue.segments[1]
    calls = []

    def too_long(**kwargs):
        calls.append(kwargs)
        error = SpeechBudgetError("Offline timing conflict")
        error.required_dub_duration = .65
        raise error

    monkeypatch.setattr("core.streaming.pipeline.synthesize_natural_speech", too_long)
    rows = rescue._dub_rows()
    shadow, staged = asyncio.run(rescue._prepare_dub_rescue(rows, focus, .65))
    try:
        assert staged  # Real padding was reclaimed but is not enough.
        assert available_reflow_duration(shadow, 1, total_duration=2.5) > .65
        assert available_reflow_duration(shadow, 1, total_duration=2.5, fixed_ids={2}) < .65
    finally:
        for update in staged.values():
            Path(update["audio_path"]).unlink()
    with pytest.raises(SpeechBudgetError, match="Offline timing conflict"):
        asyncio.run(rescue._fit_dub(focus, text=focus.final_vi, source="来源",
            output_path=rescue.segments_dir / "focus.wav", translator=rescue.translator,
            neighbor_rescue=True))
    assert len(calls) == 1  # No second synthesis on an unusable rescue budget.
    for sid, (state, pcm) in originals.items():
        assert rescue.segments[sid].to_dict() == state
        assert Path(rescue.segments[sid].audio_path).read_bytes() == pcm
    assert sorted(p.name for p in rescue.segments_dir.iterdir()) == ["original-0.wav", "original-2.wav"]


def test_explicit_fixed_neighbor_is_not_rewritten_or_compacted(rescue):
    rows = rescue._dub_rows()
    shadow, staged = asyncio.run(rescue._prepare_dub_rescue(
        rows, rescue.segments[1], .6, fixed_ids={0}))
    assert not staged
    assert shadow == rows


def test_timing_conflict_reports_capacity_with_manual_intervals_fixed(rescue):
    for sid in (0, 2):
        rescue.segments[sid].verification = {"status": "manual"}
        rescue.segments[sid].dub_start = rescue.segments[sid].start
        rescue.segments[sid].dub_end = rescue.segments[sid].end
    focus = rescue.segments[1]
    error = SpeechBudgetError("Offline timing conflict")
    error.required_dub_duration = .7
    rescue._remember_pacing_failure(focus, error, [])
    assert focus.timing_issue["available_seconds"] == pytest.approx(.5)
    assert focus.timing_issue["required_seconds"] == .7
