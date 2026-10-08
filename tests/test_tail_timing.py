"""Opt-in bounded tail timing for a prepared following dialogue gap."""
from array import array
import math
import shutil
import wave
import pytest

from core.engines.alignment.dub_timing import available_reflow_duration, plan_reflow
from core.streaming.audio_cache import resolve_dub_timing


def _row(identity, start, end, **extra):
    return {"id": identity, "start": start, "end": end, "status": "WAITING",
            "audio_duration": None, **extra}


def _rows():
    # Production-shaped row 26: source 36.67..37.57, next source starts at
    # 38.63. The durable limit is capped one second after source end and before
    # the next source/dub boundary.
    return [
        _row(26, 36.67, 37.57, dub_start=37.02, dub_end=37.92,
             dub_tail_limit=38.57),
        _row(27, 38.63, 39.2, dub_start=38.63, dub_end=39.2),
    ]


def test_real_short_gap_fits_measured_audio_without_overlap_or_source_mutation():
    rows = _rows()
    before = [dict(row) for row in rows]
    assert available_reflow_duration(rows, 26, total_duration=40.0) == pytest.approx(2.25)
    plan = plan_reflow(rows, 26, 1.67, total_duration=40.0)
    assert plan == {26: pytest.approx({"dub_start": 36.9, "dub_end": 38.57})}
    assert plan[26]["dub_end"] <= rows[0]["dub_tail_limit"] + 1e-9
    assert plan[26]["dub_end"] <= rows[1]["start"] + 1e-9
    assert plan[26]["dub_end"] <= rows[1]["dub_start"] + 1e-9
    assert rows == before


def test_saved_tail_ceiling_does_not_compound_on_repeated_fit():
    rows = _rows()
    first = plan_reflow(rows, 26, 1.67, total_duration=40.0)
    restored = [{**row, **first.get(row["id"], {})} for row in rows]
    second = plan_reflow(restored, 26, 1.67, total_duration=40.0)
    assert second[26] == pytest.approx(first[26])
    assert plan_reflow(restored, 26, 2.25001, total_duration=40.0) is None


def test_tail_ceiling_is_required_for_extended_resolver_window():
    row = {"start": 36.67, "end": 37.57, "dub_start": 36.9, "dub_end": 38.57,
           "dub_tail_limit": 38.57}
    assert resolve_dub_timing(row) == pytest.approx((36.9, 38.57))
    with pytest.raises(ValueError, match="bằng chứng"):
        resolve_dub_timing({**row, "dub_tail_limit": None})
    with pytest.raises(ValueError, match="bằng chứng|lồng tiếng"):
        resolve_dub_timing({**row, "dub_end": 38.58})
    with pytest.raises(ValueError, match="bằng chứng|lồng tiếng"):
        resolve_dub_timing({**row, "dub_tail_limit": 38.571})


def test_saved_tail_is_clamped_by_newly_discovered_next_source_and_dub_start():
    # A resumed project may have persisted a generous ceiling before a later
    # source interval was prepared. The current neighbours always win.
    rows = [
        _row(26, 36.67, 37.57, dub_start=37.02, dub_end=37.92,
             dub_tail_limit=38.57),
        _row(27, 38.20, 39.2, dub_start=38.05, dub_end=39.05),
    ]
    plan = plan_reflow(rows, 26, 1.20, total_duration=40.0)
    assert plan[26]["dub_end"] <= 38.05 + 1e-9
    assert plan[26]["dub_end"] <= rows[1]["start"] + 1e-9


def test_tail_ceiling_never_crosses_prepared_source_or_video_end():
    rows = [_row(26, 36.67, 37.57, dub_start=37.02, dub_end=37.92,
                 dub_tail_limit=38.57)]
    # The known/prepared source ends before the durable ceiling, so no plan can
    # reserve audio beyond that verified source interval.
    assert available_reflow_duration(rows, 26, total_duration=38.20) == pytest.approx(1.88)
    assert plan_reflow(rows, 26, 1.89, total_duration=38.20) is None


def test_tail_limit_must_be_at_least_source_end_and_not_more_than_one_second():
    rows = [_row(26, 36.67, 37.57, dub_tail_limit=37.56),
            _row(27, 38.63, 39.2)]
    assert available_reflow_duration(rows, 26, total_duration=40.0) == 0
    rows[0]["dub_tail_limit"] = 38.571
    assert available_reflow_duration(rows, 26, total_duration=40.0) == 0


def test_persisted_extended_tail_cannot_hide_a_new_source_dialogue_collision():
    rows = [
        _row(26, 36.67, 37.57, dub_start=37.02, dub_end=38.57,
             dub_tail_limit=38.57),
        # This dub starts later, but its newly observed source is before the
        # saved tail. A short retry must not reuse the invalid saved interval.
        _row(27, 38.25, 39.2, dub_start=38.6, dub_end=39.55),
    ]
    assert plan_reflow(rows, 26, 1.0, total_duration=40.0) is None
    assert available_reflow_duration(rows, 26, total_duration=40.0) == 0


@pytest.mark.parametrize("bad_limit", [True, "38.57", float("nan"), 37.56, 38.571])
def test_tail_evidence_is_validated_even_without_persisted_dub_window(bad_limit):
    with pytest.raises(ValueError, match="bằng chứng|Bằng chứng"):
        resolve_dub_timing({"start": 36.67, "end": 37.57, "dub_tail_limit": bad_limit})


def test_extended_tail_captions_start_at_audible_pcm_after_prefix_silence(tmp_path):
    from core.engines.alignment.speech_timing import build_speech_timing
    from core.subtitle_cues import speech_caption_cues

    rate = 24000
    path = tmp_path / "tail.wav"
    # A fitted clip has an audible 1.452 s narration after 100 ms encoder
    # padding. The durable narration window must not reveal its words during
    # that prefix, or show the next speaker early.
    samples = array("h", [0]) * 2400
    samples.extend(round(6000 * math.sin(2 * math.pi * 440 * i / rate))
                   for i in range(round(1.452 * rate)))
    with wave.open(str(path), "wb") as wav:
        wav.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        wav.writeframes(samples.tobytes())
    timing = build_speech_timing("Bố còn khóc cả buổi chiều.", 37.02, 38.57, path)
    row = {"start": 36.67, "end": 37.57, "dub_start": 37.02, "dub_end": 38.57,
           "dub_tail_limit": 38.57, "final_vi": "Bố còn khóc cả buổi chiều.", **timing}
    cues = speech_caption_cues(row)
    assert cues
    assert timing["speech_start"] >= 37.09
    assert all(cue["start"] >= timing["speech_start"] for cue in cues)
    assert all(cue["end"] <= min(38.57, timing["speech_end"]) + 1e-9 for cue in cues)
    assert max(cue["end"] for cue in cues) < 38.63


def test_actual_preceding_measured_wavs_leave_room_for_complete_row26():
    rows = [
        _row(23, 33.45, 34.25, dub_start=33.23654166666667, dub_end=34.025083333333335,
             status="READY", audio_duration=.7885416666666667),
        _row(24, 34.25, 35.73, dub_start=34.025083333333335, dub_end=35.728,
             status="READY", audio_duration=1.7029166666666666),
        _row(25, 35.73, 36.67, dub_start=35.728, dub_end=37.02,
             status="READY", audio_duration=1.292),
        _row(26, 36.67, 37.57, dub_start=37.02, dub_end=37.92,
             dub_tail_limit=38.57),
        _row(27, 38.63, 39.53, status="READY", audio_duration=.8947916666666667),
    ]
    before = [dict(row) for row in rows]
    assert available_reflow_duration(rows, 26, total_duration=48) == pytest.approx(1.6750833333333333)
    duration = 1.67 / 1.15
    plan = plan_reflow(rows, 26, duration, total_duration=48)
    assert plan == {26: pytest.approx({"dub_start": 37.02, "dub_end": 37.02 + duration})}
    assert rows == before
    assert plan[26]["dub_end"] < min(38.57, rows[-1]["start"])
    saved = [{**row, **plan.get(row["id"], {})} for row in rows]
    saved[3].update(status="READY", audio_duration=duration)
    assert plan_reflow(saved, 26, duration, total_duration=48) == plan
    # Removing the durable tail restores the exact original legacy capacity;
    # no global endpoint tolerance was enlarged.
    legacy = [{**row, "dub_tail_limit": None} for row in rows]
    assert available_reflow_duration(legacy, 26, total_duration=48) == pytest.approx(1.025083333333334)
    assert plan_reflow(legacy, 26, duration, total_duration=48) is None


def test_export_preserves_complete_pcm_beyond_source_end_with_bounded_evidence(tmp_path):
    from core.streaming.export import HQExporter
    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg is required to verify complete exported PCM")
    rate = 24000
    frames = round(1.67 / 1.15 * rate)
    samples = array("h", (round(6000 * math.sin(2 * math.pi * 440 * i / rate))
                           for i in range(frames)))
    audio = tmp_path / "row26.wav"
    with wave.open(str(audio), "wb") as wav:
        wav.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        wav.writeframes(samples.tobytes())
    row = {"id": 26, "start": 36.67, "end": 37.57, "dub_start": 37.02,
           "dub_end": 37.02 + frames / rate, "dub_tail_limit": 38.57,
           "audio_path": str(audio), "final_vi": "Bố còn khóc cả buổi chiều."}
    output = tmp_path / "voice_timeline.wav"
    HQExporter._assemble_voice_timeline([row], 48, output)
    with wave.open(str(output)) as wav:
        assert wav.getnframes() == 48 * 44100
        wav.setpos(round(36.9 * 44100))
        assert wav.readframes(round(.1 * 44100)) == bytes(round(.1 * 44100) * 4)
        # The last audible 10 ms lie past the original source end, proving the
        # export did not silently crop narration back to its old 900 ms slot.
        wav.setpos(round((row["dub_end"] - .02) * 44100))
        tail = array("h", wav.readframes(round(.01 * 44100)))
        assert max(abs(value) for value in tail) > 100
        wav.setpos(round(38.58 * 44100))
        assert wav.readframes(round(.05 * 44100)) == bytes(round(.05 * 44100) * 4)

