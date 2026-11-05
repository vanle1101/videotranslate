"""Native missing-speech locations survive the bounded source/session boundary."""
import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from core.engines.asr.native_process import ASRProcessError
from core.streaming.chunked_source import merge_source_missing_spans, prepare_interval
from core.streaming.pipeline import SegmentItem, _transient_failure
from core.streaming.session_store import list_saved_sessions, restore_saved_session, _project_path
from test_preview_chunked_pipeline import preview
from test_session_store import persisted
from pathlib import Path
from test_preview_chunked_pipeline import wav


def gap_error(spans):
    error = ASRProcessError("Speech missing from source transcript")
    error.code = "source_speech_unrecognized"
    error.spans = spans
    return error


@pytest.mark.parametrize("mode", ["recovered", "interior", "bounded", "eof"])
def test_clipped_scan_tail_gets_real_context_without_waiving_gap(preview, monkeypatch, mode):
    session, *_ = preview
    session.total_duration = 32. if mode == "eof" else 120.
    scans = []
    async def ffmpeg(command):
        wav(Path(command[-1]))
        if command[-1].endswith("_16k.wav"):
            scans.append(float(command[command.index("-t") + 1]))
    def recognize(*args, **kwargs):
        if mode == "recovered" and len(scans) > 1:
            return [{"start": 30., "end": 34., "text_zh": "完整的话"}]
        raise gap_error([[3., 3.4]] if mode == "interior" else [[scans[-1] - .352, scans[-1]]])
    def suppress(**options):
        options["output_audio_path"].write_bytes(b"bounded background")
        return {}
    monkeypatch.setattr(session, "_run_ffmpeg", ffmpeg)
    monkeypatch.setattr(session.faster_whisper, "transcribe", recognize)
    monkeypatch.setattr(session.vocal_suppressor, "process_file", suppress)
    if mode == "recovered":
        result = asyncio.run(prepare_interval(session, 0., 24.))
        assert scans == [32., 40.]
        assert result["end"] == 40.
        assert result["rows"][0]["end"] == 34.
    else:
        with pytest.raises(ASRProcessError) as caught:
            asyncio.run(prepare_interval(session, 0., 24.))
        assert scans == ([32., 40., 48., 56.] if mode == "bounded" else [32.])
        assert caught.value.code == "source_speech_unrecognized"
        assert not list((session.cache_dir / "source_preparation").glob("[0-9]*.json"))


def test_bounded_gap_is_global_logged_and_does_not_replay_or_change_prior_rows(preview, monkeypatch, caplog):
    session, _, _, _, events = preview
    session._chunked_source_started = True
    session._source_prepared_seconds = 32.
    session._source_preparation_stage = "asr"
    session._source_preparation_interval = (32., 64.)
    manual = SegmentItem(0, 0, 2, 2)
    manual.text_zh, manual.final_vi = "原句", "Câu đã sửa."
    manual.verification = {"status": "manual"}
    session.segments[0] = manual
    before = deepcopy(manual.to_dict())
    failure = AsyncMock(side_effect=gap_error([[7.25, 8.75], [12., 13.]]))
    monkeypatch.setattr("core.streaming.chunked_source.prepare_interval", failure)
    session.is_running = True
    assert not asyncio.run(session._prepare_source_until(56.))
    assert failure.await_count == 1
    assert session._source_prepared_seconds == 32.
    assert manual.to_dict() == before
    expected = [[39.25, 40.75], [44., 45.]]
    snapshot = session.get_progress()
    assert snapshot["source_missing_speech_spans"] == expected
    assert snapshot["source_preparation_stage"] == "asr"
    assert snapshot["source_failure_code"] == "source_speech_unrecognized"
    assert snapshot["source_failure_start"] == 32.
    assert not snapshot["source_coverage_complete"]
    assert "39.25" in caplog.text and "40.75" in caplog.text
    blocked = [data for event, data in events if event == "progress" and data.get("phase") == "source_blocked"]
    assert blocked and "39.25–40.75s" in blocked[-1]["stage"]
    assert not any(data.get("phase") == "recovery_wait" for event, data in events if event == "progress")


def test_gap_is_not_transient_even_with_transport_context():
    error = gap_error([[1., 2.]])
    error.__context__ = TimeoutError("old worker context")
    assert not _transient_failure(error)


@pytest.mark.parametrize("spans", [[[True, 2]], [[-1, 2]], [[2, 1]],
    [[0, float("nan")]], [[0, float("inf")]], [[1, 40]], [[1, 2, 3]], "1-2"])
def test_invalid_local_spans_do_not_become_global_diagnostics(spans):
    with pytest.raises(ValueError):
        merge_source_missing_spans(spans, offset=32., total_duration=100., interval_end=64.)


def test_merge_retains_measured_gap_edges_and_separate_nearby_utterances():
    assert merge_source_missing_spans([[2, 3], [1, 2.2], [3.1, 4]], offset=10., total_duration=20.) == [
        [11., 13.], [13.1, 14.]]


def test_only_committed_source_coverage_retires_gap_diagnosis(preview):
    session, _, _, _, _ = preview
    session.is_running = True
    session._source_missing_speech_spans = [[3., 4.], [30., 34.]]
    session._source_failure_code = "source_speech_unrecognized"
    session._source_failure_start = 0.
    assert asyncio.run(session._prepare_source_until(24.))
    assert session._source_prepared_seconds == 32.
    assert session._source_missing_speech_spans == [[32., 34.]]
    assert session._source_failure_code == "source_speech_unrecognized"
    assert asyncio.run(session._prepare_source_until(56.))
    assert session._source_missing_speech_spans == []
    assert session._source_failure_code is session._source_failure_start is None


def test_gap_stage_persists_and_reloads_without_changing_manual_rows(persisted):
    session = persisted
    session.total_duration = 40.
    session._chunked_source_started = True
    session._source_prepared_seconds = 10.
    session._source_missing_speech_spans = [[13.125, 14.875]]
    session._source_preparation_stage = "asr"
    session._source_failure_code = "source_speech_unrecognized"
    session._source_failure_start = 10.
    row = session.segments[0]
    row.verification = {"status": "manual"}
    session.persist()
    restored = restore_saved_session(session.task_id)
    snapshot = restored.get_progress()
    assert snapshot["source_missing_speech_spans"] == [[13.125, 14.875]]
    assert snapshot["source_preparation_stage"] == "asr"
    assert snapshot["source_failure_code"] == "source_speech_unrecognized"
    assert snapshot["source_failure_start"] == 10.
    assert "13.12–14.88s" in snapshot["stage"]
    assert restored.segments[0].final_vi == row.final_vi
    assert restored.segments[0].verification == {"status": "manual"}
    assert restored._source_prepared_seconds == 10.
    saved = next(item for item in list_saved_sessions() if item["task_id"] == session.task_id)
    assert saved["source_missing_speech_spans"] == snapshot["source_missing_speech_spans"]


def test_invalid_durable_gap_does_not_replace_good_manifest(persisted):
    old = _project_path(persisted.task_id).read_bytes()
    persisted._source_missing_speech_spans = [[1., 300.]]
    with pytest.raises(ValueError):
        persisted.persist()
    assert _project_path(persisted.task_id).read_bytes() == old


@pytest.mark.parametrize("mode", ["oversize", "attempted", "budget_boundary", "interior", "conditional", "no_next"])
def test_forward_diagnostic_records_only_decoded_windows(mode, monkeypatch):
    from types import SimpleNamespace
    import numpy as np
    from core.engines.asr.utterance_language import transcribe_local_speech, UnrecognizedSpeechError
    from core.engines.asr.speech_gap_diagnostic import validate_gap_diagnostic

    def region(start, end):
        return {"start": round(start * 16000), "end": round(end * 16000)}

    def row(start, end):
        return SimpleNamespace(start=start, end=end, text="hello",
            words=[SimpleNamespace(start=start, end=end, word="hello", probability=.95)])

    original = [region(1., 2.), region(2.5, 2.7), region(4., 5.)]
    vocal = [region(.808, 2.)]
    expected_calls = [(39872, 1.), (39872, 0.), (76672, 0.)]
    expected_gap = [(2.5, 2.7)]
    expected_forward = [.508, 5.3]
    if mode == "oversize":
        original = [region(11.680, 18.), region(18.688, 18.816), region(20., 25.856)]
        vocal = [region(11.488, 18.)]
        expected_calls = [(126848, 1.), (126848, 0.)]
        expected_gap, expected_forward = [(18.688, 18.816)], None
    elif mode == "budget_boundary":
        original[-1] = region(4., 12.208)
        expected_calls[-1] = (192000, 0.)
        expected_forward = [.508, 12.508]
    elif mode == "interior":
        original = [region(1., 2.), region(2.5, 4.), region(5., 6.)]
        expected_calls = [(60672, 1.)]
        expected_gap, expected_forward = [(.808, 2.)], None
    elif mode == "conditional":
        def conditional(model, audio, start, end, bounds, rows, info, options, reader, **kwargs):
            return rows, "en", 0., {"conditional_selected": True}
        monkeypatch.setattr("core.engines.asr.language_disagreement.recover_language", conditional)
        expected_calls, expected_forward = [(39872, 1.)], None
    elif mode == "no_next":
        original.pop()
        expected_calls, expected_forward = expected_calls[:2], None

    calls = []
    class Model:
        def transcribe(self, audio, **options):
            calls.append((len(audio), float(audio[0]), options))
            value = row(.3, 6.812) if mode == "oversize" else row(.3, 1.492)
            if mode == "interior":
                value = row(1.992, 3.492)
            return iter([value]), SimpleNamespace(language="en", language_probability=.95)

    rows, _ = transcribe_local_speech(Model(), np.zeros(27 * 16000), {"word_timestamps": True},
        regions=original, vocal_audio=np.ones(27 * 16000), vocal_regions=vocal,
        language_reader=object() if mode == "conditional" else None)
    with pytest.raises(UnrecognizedSpeechError) as caught:
        list(rows)
    assert [call[:2] for call in calls] == expected_calls
    assert all(call[2] == {"word_timestamps": True, "language": None, "vad_filter": False,
                          "multilingual": True, "condition_on_previous_text": False} for call in calls)
    assert caught.value.spans == expected_gap
    diagnostic = caught.value.diagnostic
    assert diagnostic["forward_window"] == expected_forward
    assert validate_gap_diagnostic(diagnostic) == diagnostic
    origins = ["original_audio", "vocal_stem"] if mode == "interior" else ["original_audio"]
    assert diagnostic["gaps"] == [{"span": list(expected_gap[0]), "vad_origins": origins}]
    assert diagnostic["selected_evidence"] == "vocal_stem_unverified"
    assert diagnostic["final_evidence"] == ("original_audio" if mode == "conditional" else "vocal_stem_unverified")
