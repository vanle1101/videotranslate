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
    assert rescue.translator.rewrite_for_pacing.call_count == 3
    assert caplog.text.count("PACING_NEIGHBOR_REWRITE_REJECTED") == 3
    assert "focus_id=1 neighbor_id=0 attempt=3 code=semantic_mismatch" in caplog.text
    assert "PRIVATE-" not in caplog.text
