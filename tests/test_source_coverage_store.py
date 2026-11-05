"""Source scan checkpoints preserve islands without publishing source holes."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from config import settings
from core.streaming import session_store as store
from core.streaming.pipeline import active_streaming_sessions
from test_session_store import persisted


def ledger():
    return [{"start": 0., "end": 32., "state": "prepared", "missing_speech_spans": [], "error_code": None},
            {"start": 32., "end": 64., "state": "unresolved", "missing_speech_spans": [[46.07, 46.89]],
             "error_code": "source_gap"},
            {"start": 64., "end": 100., "state": "prepared", "missing_speech_spans": [], "error_code": None}]


def source_islands(session):
    session.total_duration = 100.
    session._chunked_source_started = True
    session._source_prepared_seconds = 100.  # Deliberately stale success cursor.
    session._source_scanned_seconds = 0.
    session._source_coverage_intervals = ledger()
    session._visual_completed_seconds = 100.
    session._visual_prepass_complete = True
    session._preview_ready = True
    later = deepcopy(session.segments[0])
    later.id, later.start, later.end = 70, 80., 82.
    later.text_zh, later.final_vi = "后来", "Đoạn sau người dùng sửa."
    later.verification = {"status": "manual"}
    later.audio_path = str(session.segments_dir / "seg_70.wav")
    Path(later.audio_path).write_bytes(Path(session.segments[0].audio_path).read_bytes())
    session.segments[70] = later
    return later


@pytest.mark.parametrize("compressed", [False, True])
def test_source_ledger_roundtrip_derives_cursors_preserves_diagnostics_ids_and_audio(persisted, monkeypatch, compressed):
    monkeypatch.setattr(store, "COMPRESS_AFTER_BYTES", 0 if compressed else store.MAX_BYTES)
    late = source_islands(persisted)
    original_audio = {sid: Path(row.audio_path).read_bytes() for sid, row in persisted.segments.items()}
    persisted.persist()
    path = store._project_path(persisted.task_id)
    original_manifest = path.read_bytes()
    assert (json.loads(original_manifest).get("storage") == "zlib-project-1") == compressed
    for _ in range(2):
        saved = store._read(persisted.task_id)
        assert saved["session"]["_source_coverage_intervals"] == ledger()
        assert saved["session"]["_source_prepared_seconds"] == 32.
        assert saved["session"]["_source_scanned_seconds"] == 100.
        restored = store.restore_saved_session(persisted.task_id)
        assert restored._source_coverage_intervals == ledger()
        assert restored._source_prepared_seconds == 32.
        assert restored._source_scanned_seconds == 100.
        assert set(restored.segments) == {0, 70}
        assert restored.segments[70].final_vi == late.final_vi
        assert restored.segments[70].verification == {"status": "manual"}
        assert restored._startup_failed and not restored._preview_ready
        assert {sid: Path(row.audio_path).read_bytes() for sid, row in restored.segments.items()} == original_audio
        assert path.read_bytes() == original_manifest  # Opening never rewrites a project.
        active_streaming_sessions.pop(persisted.task_id)
    restored.persist()
    assert store._read(persisted.task_id)["session"]["_source_coverage_intervals"] == ledger()


def test_stale_ready_output_and_completed_scan_never_hide_unresolved_source(persisted, monkeypatch):
    source_islands(persisted)
    persisted.output_filename = "result.mp4"
    (settings.OUTPUT_DIR / persisted.output_filename).write_bytes(b"test output fixture")
    monkeypatch.setattr(store, "_valid_output", lambda *_: True)
    persisted.persist()
    data = store._read(persisted.task_id)
    available = store._availability(data)
    assert not available["source_coverage_complete"]
    assert not available["media_complete"] and not available["ready"]
    assert available["status"] != "COMPLETED" and not available["output_filename"]
    row = store.list_saved_sessions()[0]
    assert row["checkpoint_source_prepared_seconds"] == 32
    assert row["source_scanned_seconds"] == 100
    assert row["source_coverage_intervals"] == ledger()
    data["session"]["output_incomplete"] = True
    partial = store._availability(data)
    assert partial["output_incomplete"] and partial["output_filename"] == "result.mp4"
    assert not partial["source_coverage_complete"] and not partial["media_complete"]


@pytest.mark.parametrize("completed,ready", [(24., True), (32., True), (32.000000001, False), (80., False)])
def test_preview_requires_contiguous_verified_prefix_even_when_later_audio_is_ready(persisted, completed, ready):
    source_islands(persisted)
    persisted.translation_mode = "preview"
    persisted._visual_completed_seconds = completed
    persisted.persist()
    available = store._availability(store._read(persisted.task_id))
    assert available["preview_ready"] is ready
    assert available["preview_source_unprepared"] is not ready
    assert not available["ready"]


def test_legacy_manifest_retains_default_coverage_without_rewrite(persisted):
    persisted._chunked_source_started = True
    persisted._source_prepared_seconds = 2.
    persisted._visual_prepass_complete = True
    persisted.persist()
    path = store._project_path(persisted.task_id)
    data = store._decode_project(path.read_text(encoding="utf-8"))
    data["session"].pop("_source_coverage_intervals", None)
    data["session"].pop("_source_scanned_seconds", None)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    original = path.read_bytes()
    read = store._read(persisted.task_id)
    assert "_source_coverage_intervals" not in read["session"]
    assert read["session"]["_source_scanned_seconds"] == 2.
    assert store._availability(read)["source_coverage_complete"]
    restored = store.restore_saved_session(persisted.task_id)
    assert restored._source_prepared_seconds == 2.
    assert restored._source_scanned_seconds == 2.
    assert path.read_bytes() == original


def test_read_derives_frontier_from_records_instead_of_saved_scan_claim(persisted):
    source_islands(persisted)
    persisted.persist()
    path = store._project_path(persisted.task_id)
    data = store._decode_project(path.read_text(encoding="utf-8"))
    data["session"]["_source_coverage_intervals"].pop(1)
    data["session"]["_source_prepared_seconds"] = 100.
    data["session"]["_source_scanned_seconds"] = 100.
    path.write_text(json.dumps(data), encoding="utf-8")
    before = path.read_bytes()
    read = store._read(persisted.task_id)
    assert read["session"]["_source_prepared_seconds"] == 32.
    assert read["session"]["_source_scanned_seconds"] == 32.
    assert not store._availability(read)["source_coverage_complete"]
    assert path.read_bytes() == before


@pytest.mark.parametrize("invalid", [None, {}, [dict(ledger()[0], end=float("nan"))],
    [dict(ledger()[0], state="READY")], [ledger()[0], dict(ledger()[1], start=31)],
    [dict(ledger()[1], missing_speech_spans=[[1, 2]])], [dict(ledger()[2], end=101)]])
def test_malformed_ledger_cannot_replace_or_restore_checkpoint(persisted, invalid):
    source_islands(persisted)
    persisted.persist()
    path = store._project_path(persisted.task_id)
    before = path.read_bytes()
    persisted._source_coverage_intervals = invalid
    with pytest.raises(ValueError):
        store.save_session(persisted)
    assert path.read_bytes() == before
    data = store._decode_project(before.decode("utf-8"))
    data["session"]["_source_coverage_intervals"] = invalid
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError):
        store._read(persisted.task_id)


@pytest.mark.parametrize("cursor", [True, -1, 101, float("nan"), "100"])
def test_invalid_scan_cursor_is_rejected_before_derivation(persisted, cursor):
    source_islands(persisted)
    persisted._source_scanned_seconds = cursor
    with pytest.raises(ValueError):
        store.save_session(persisted)


def test_fitted_audio_in_later_verified_island_roundtrips_with_own_ceiling(persisted):
    later = source_islands(persisted)
    later.dub_start, later.dub_end, later.dub_tail_limit = 80.1, 82.7, 83.
    persisted.persist()
    restored = store.restore_saved_session(persisted.task_id)
    actual = restored.segments[70]
    assert (actual.dub_start, actual.dub_end, actual.dub_tail_limit) == (80.1, 82.7, 83.)
    assert actual.audio_path == later.audio_path and actual.status == "READY"


@pytest.mark.parametrize("start,end,dub_start,dub_end,tail", [
    (30., 31.5, 30., 32.1, 32.2),  # A tail crosses into unresolved source.
    (30., 31.5, 30., 31.8, 32.000000001),  # Even unused tail authority cannot cross.
    (64., 66., 63.9, 66., None),  # A shifted start crosses from a later island.
    (31., 31.9, 31., 32.1, None),  # Normal 0.35 s fitting is not a hole waiver.
])
def test_saved_fitting_cannot_cross_unresolved_packet(persisted, start, end, dub_start, dub_end, tail):
    source_islands(persisted)
    row = persisted.segments[0]
    row.start, row.end, row.duration = start, end, end - start
    row.dub_start, row.dub_end, row.dub_tail_limit = dub_start, dub_end, tail
    before = store._project_path(persisted.task_id).read_bytes()
    with pytest.raises(ValueError, match="nguồn|chuẩn bị"):
        store.save_session(persisted)
    assert store._project_path(persisted.task_id).read_bytes() == before
