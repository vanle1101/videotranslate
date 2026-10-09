"""Fault/regression tests; synthetic observations are not real media acceptance."""
import asyncio
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

import pytest

from config import settings
from core.engines.asr import native_process
from core.engines.asr import speaker_diarization as adapter
from core.engines.asr.diarization_worker import exclusive_spans
from core.engines.asr.speaker_registry import (MatchingPolicy, SpeakerRegistry,
    annotate_rows, validate_observation)

MODELS = {"embedding_sha256": "model-a", "segmentation_sha256": "model-b"}
VECTOR_A = [1.] + [0.] * 15
VECTOR_B = [0., 1.] + [0.] * 14


def observation(start=0., end=10., voices=None, embeddings=True):
    voices = voices or [(0, start + 1, end - 1, VECTOR_A)]
    return {"start": start, "end": end, "calibrated": False, "confidence_kind": "silhouette",
        "runtime_version": "1.13.8", "intervals": [{"local_speaker": sid, "start": left,
            "end": right, "silhouette": .3} for sid, left, right, vector in voices],
        "embeddings": [{"local_speaker": sid, "start": left, "end": right,
            "embedding": vector} for sid, left, right, vector in voices] if embeddings else []}


def registry(tmp_path, **kwargs):
    return SpeakerRegistry(tmp_path / "speakers.sqlite", "source-17", MODELS, **kwargs)


def test_atomic_checkpoint_reopen_and_retry_reuses_exact_ids(tmp_path):
    store = registry(tmp_path)
    first = store.record("chunk-0", observation(), 0, 10)
    reopened = registry(tmp_path)
    assert reopened.cached("chunk-0") == first
    assert reopened.record("chunk-0", observation(10, 20), 10, 20) == first
    assert len(reopened.speakers()) == 1
    assert first["intervals"][0]["speaker_id"] == "voice-000001"
    assert first["review_required"] is True and first["calibrated"] is False
    assert not list(tmp_path.glob("*.sqlite-journal"))


def test_crosschunk_local_labels_are_remapped_to_persistent_audio_id(tmp_path):
    store = registry(tmp_path)
    first = store.record("chunk-0", observation(voices=[(7, 1, 8, VECTOR_A)]), 0, 10)
    second = registry(tmp_path).record("chunk-1", observation(10, 20, [(2, 11, 18, VECTOR_A)]), 10, 20)
    assert first["intervals"][0]["speaker_id"] == second["intervals"][0]["speaker_id"]
    assert second["intervals"][0]["match"]["cosine"] == pytest.approx(1)
    assert second["intervals"][0]["match"]["status"] == "PROVISIONAL"


def test_second_different_voice_and_reversed_labels_keep_separate_ids(tmp_path):
    store = registry(tmp_path)
    first = store.record("one", observation(voices=[(0, 1, 4, VECTOR_A), (1, 5, 8, VECTOR_B)]), 0, 10)
    second = store.record("two", observation(10, 20, [(4, 11, 14, VECTOR_B), (3, 15, 18, VECTOR_A)]), 10, 20)
    assert [row["speaker_id"] for row in second["intervals"]] == [row["speaker_id"] for row in reversed(first["intervals"])]
    assert len(store.speakers()) == 2


def test_ambiguous_cosine_margin_is_not_forced_match_or_verified(tmp_path):
    store = registry(tmp_path)
    store.record("one", observation(voices=[(0, 1, 4, VECTOR_A), (1, 5, 8, VECTOR_B)]), 0, 10)
    middle = [1., 1.] + [0.] * 14
    value = store.record("two", observation(10, 20, [(0, 11, 18, middle)]), 10, 20)
    match = value["intervals"][0]["match"]
    assert match["reason"] == "ambiguous_match" and match["margin"] == pytest.approx(0)
    assert value["intervals"][0]["speaker_id"] not in match["candidate_ids"]
    assert store.speakers()[-1]["reference_count"] == 0


def test_two_local_voices_cannot_collapse_to_one_registry_speaker(tmp_path):
    store = registry(tmp_path)
    store.record("one", observation(), 0, 10)
    value = store.record("two", observation(10, 20, [(0, 11, 14, VECTOR_A), (1, 15, 18, VECTOR_A)]), 10, 20)
    assert len({row["speaker_id"] for row in value["intervals"]}) == 2
    assert value["intervals"][1]["match"]["reason"] == "ambiguous_match"


def test_negative_and_unavailable_silhouette_do_not_become_probability(tmp_path):
    value = observation()
    value["intervals"][0]["silhouette"] = -.25
    result = registry(tmp_path).record("negative", value, 0, 10)
    assert result["intervals"][0]["silhouette"] == -.25
    value["intervals"][0]["silhouette"] = None
    result = registry(tmp_path).record("unavailable", value, 0, 10)
    assert result["intervals"][0]["silhouette"] is None
    assert all("confidence" not in item and "confidence" not in item["match"] for item in result["intervals"])


def test_short_voice_without_embedding_stays_provisional_and_unknown_score(tmp_path):
    value = observation(voices=[(0, 1, 1.4, VECTOR_A)], embeddings=False)
    result = registry(tmp_path).record("short", value, 0, 10)
    assert result["intervals"][0]["match"]["reason"] == "insufficient_exclusive_audio"
    assert result["intervals"][0]["match"]["cosine"] is None


def test_owned_intervals_are_clipped_without_moving_absolute_source_times(tmp_path):
    value = observation(100, 120, [(0, 102, 118, VECTOR_A)])
    result = registry(tmp_path).record("owned", value, 104, 115)
    assert [(row["start"], row["end"]) for row in result["intervals"]] == [(104, 115)]
    assert result["scan_start"] == 100 and result["scan_end"] == 120


@pytest.mark.parametrize("failure", ["NaN", "missing_voice", "out_of_bounds", "empty_vector", "overlap", "calibrated"])
def test_invalid_observations_never_publish_checkpoint(tmp_path, failure):
    store = registry(tmp_path)
    value = observation()
    if failure == "NaN": value["intervals"][0]["silhouette"] = float("nan")
    if failure == "missing_voice": value["embeddings"][0]["local_speaker"] = 8
    if failure == "out_of_bounds": value["intervals"][0]["end"] = 11
    if failure == "empty_vector": value["embeddings"][0]["embedding"] = [0.] * 16
    if failure == "overlap": value["intervals"].append({"local_speaker": 1, "start": 2, "end": 3, "silhouette": None})
    if failure == "calibrated": value["calibrated"] = True
    with pytest.raises(ValueError): store.record("failed", value, 0, 10)
    assert store.cached("failed") is None and store.speakers() == []


def test_sqlite_write_failure_rolls_back_ids_and_checkpoint(tmp_path, monkeypatch):
    store = registry(tmp_path)
    original = json.dumps
    def fault(value, *args, **kwargs):
        if isinstance(value, dict) and value.get("request_key") == "disk-full":
            raise sqlite3.OperationalError("database or disk is full")
        return original(value, *args, **kwargs)
    monkeypatch.setattr(json, "dumps", fault)
    with pytest.raises(sqlite3.OperationalError, match="disk is full"):
        store.record("disk-full", observation(), 0, 10)
    assert store.cached("disk-full") is None and store.speakers() == []
    monkeypatch.setattr(json, "dumps", original)
    assert store.record("retry", observation(), 0, 10)["intervals"][0]["speaker_id"] == "voice-000001"


@pytest.mark.parametrize("change", ["source", "model", "policy"])
def test_changed_registry_ownership_preserves_old_data(tmp_path, change):
    store = registry(tmp_path)
    first = store.record("saved", observation(), 0, 10)
    with pytest.raises(ValueError, match="changed"):
        SpeakerRegistry(store.path, "source-18" if change == "source" else "source-17",
            {"embedding_sha256": "new"} if change == "model" else MODELS,
            MatchingPolicy(min_cosine=.8) if change == "policy" else None)
    assert registry(tmp_path).cached("saved") == first


def test_embedding_prototype_count_is_bounded(tmp_path):
    store = registry(tmp_path, policy=MatchingPolicy(max_references=2))
    for index in range(12):
        store.record(str(index), observation(index * 10, (index + 1) * 10), index * 10, (index + 1) * 10)
    assert store.speakers()[0]["reference_count"] == 2


def test_annotation_preserves_words_ids_and_absolute_times_without_verification(tmp_path):
    result = registry(tmp_path).record("one", observation(), 0, 10)
    rows = [{"id": 91, "start": 1.1, "end": 2.2, "text_zh": "不是回去", "words": [
        {"word": "不是", "start": 1.1, "end": 1.6}, {"word": "回去", "start": 1.6, "end": 2.2}]}]
    original = deepcopy(rows)
    annotated = annotate_rows(rows, result)
    assert rows == original
    assert annotated[0]["speaker_id"] == "voice-000001"
    assert annotated[0]["speaker_evidence"]["verified"] is False
    assert "confidence" not in annotated[0]["speaker_evidence"]
    assert annotated[0]["text_zh"] == rows[0]["text_zh"]
    assert [(word["word"], word["start"], word["end"]) for word in annotated[0]["words"]] == [(word["word"], word["start"], word["end"]) for word in rows[0]["words"]]
    from core.semantic_segments import build_semantic_units
    unit = build_semantic_units([annotated[0]])[0]
    assert not unit["can_combine_dubbing"]


def test_conflicting_and_overlapping_voice_words_remain_unknown(tmp_path):
    value = observation(voices=[(0, 1, 4, VECTOR_A), (1, 3, 7, VECTOR_B)], embeddings=False)
    result = registry(tmp_path).record("overlap", value, 0, 10)
    row = {"id": 3, "start": 3.1, "end": 3.5, "text_zh": "我", "words": [{"word": "我", "start": 3.1, "end": 3.5}]}
    annotated = annotate_rows([row], result)[0]
    assert annotated["speaker_id"] is None and annotated["words"][0]["speaker_id"] is None
    assert annotated["speaker_diagnostics"]["review_required"]


def test_embedding_excludes_overlapping_parts():
    intervals = [{"local_speaker": 0, "start": 1, "end": 10}, {"local_speaker": 1, "start": 3, "end": 4},
                 {"local_speaker": 1, "start": 7, "end": 12}]
    assert exclusive_spans(intervals[0], intervals) == [(1, 3), (4, 7)]


def test_disjoint_short_voice_samples_keep_each_measured_span():
    value = observation(voices=[(0, 1, 2, VECTOR_A), (1, 2.1, 3, VECTOR_B), (0, 3.1, 4, VECTOR_A)], embeddings=False)
    value["embeddings"] = [{"local_speaker": 0, "start": 1, "end": 4,
        "source_spans": [{"start": 1, "end": 2}, {"start": 3.1, "end": 4}],
        "audio_seconds": 1.9, "embedding": VECTOR_A}]
    assert validate_observation(value) is value
    value["embeddings"][0]["source_spans"] = [{"start": 1, "end": 4}]
    with pytest.raises(ValueError, match="Overlapping voices"):
        validate_observation(value)


def test_source_overlap_links_short_voice_without_guessing_from_turn_order(tmp_path):
    store = registry(tmp_path)
    first = store.record("first", observation(0, 10, [(2, 7, 9, VECTOR_A)], embeddings=False), 0, 10)
    repeated = store.record("overlap", observation(6, 16, [(9, 7.05, 9.05, VECTOR_A)], embeddings=False), 10, 16)
    # No speech in the owned range; the linked ID is still stored in the
    # registry transaction, without duplicating the overlapping source words.
    assert repeated["intervals"] == [] and len(store.speakers()) == 1
    later = store.record("owned", observation(6, 16, [(9, 7.05, 12., VECTOR_A)], embeddings=False), 10, 16)
    assert later["intervals"][0]["speaker_id"] == first["intervals"][0]["speaker_id"]
    assert later["intervals"][0]["match"]["reason"] == "provisional_source_overlap_match"


def test_policies_and_changed_media_cannot_alias_voice_scope(tmp_path):
    first = SpeakerRegistry(tmp_path / "first.sqlite", "source", MODELS,
                            media_identity={"source": "file-a", "size": 10})
    with pytest.raises(ValueError, match="changed"):
        SpeakerRegistry(first.path, "source", MODELS, media_identity={"source": "file-b", "size": 11})
    second = SpeakerRegistry(tmp_path / "second.sqlite", "source", MODELS,
        MatchingPolicy(min_cosine=.8), media_identity={"source": "file-a", "size": 10})
    assert first.scope_id != second.scope_id


def test_word_agreement_cannot_override_row_overlap(tmp_path):
    value = observation(voices=[(0, 1, 9, VECTOR_A), (1, 4, 6, VECTOR_B)], embeddings=False)
    result = registry(tmp_path).record("overlap-row", value, 0, 10)
    row = {"id": 1, "start": 1.1, "end": 8.9, "text_zh": "甲乙", "words": [
        {"word": "甲", "start": 1.2, "end": 2.2}, {"word": "乙", "start": 7.2, "end": 8.2}]}
    annotated = annotate_rows([row], result)[0]
    assert annotated["speaker_id"] is None
    assert all(word["speaker_id"] == "voice-000001" for word in annotated["words"])
    assert annotated["speaker_evidence"]["reason"] == "overlapping_voices"


@pytest.mark.parametrize("mutation", ["wrong_key", "wrong_source", "outside", "claimed_confidence", "overlap", "NaN"])
def test_corrupt_saved_checkpoint_is_rejected_in_both_cache_paths(tmp_path, mutation):
    store = registry(tmp_path)
    result = store.record("saved", observation(), 0, 10)
    if mutation == "wrong_key": result["request_key"] = "other"
    if mutation == "wrong_source": result["source_id"] = "other"
    if mutation == "outside": result["intervals"][0]["end"] = 30
    if mutation == "claimed_confidence": result["intervals"][0]["match"]["confidence"] = .99
    if mutation == "overlap": result["intervals"][0]["overlap"] = True
    if mutation == "NaN": result["intervals"][0]["match"]["cosine"] = float("nan")
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE chunks SET result=? WHERE key=?", (json.dumps(result), "saved"))
    with pytest.raises(ValueError): store.cached("saved")
    with pytest.raises(ValueError): store.record("saved", observation(), 0, 10)


def test_huge_finite_vector_does_not_silently_normalize_to_zero(tmp_path):
    value = observation()
    value["embeddings"][0]["embedding"] = [1e308] * 16
    with pytest.raises(ValueError, match="Empty speaker"):
        registry(tmp_path).record("huge", value, 0, 10)


@pytest.fixture
def worker_fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "TEMP_DIR", tmp_path)
    media = tmp_path / "video.mp4"
    media.write_bytes(b"source fixture")
    monkeypatch.setattr(adapter, "installed_assets", lambda path: (media, media, MODELS))
    original = subprocess.Popen
    calls = []
    def install(program):
        def launch(command, **kwargs):
            calls.append(json.loads(Path(command[-2]).read_text(encoding="utf-8")))
            process = original([sys.executable, "-B", "-X", "utf8", "-c", program, command[-1], command[-2]], **kwargs)
            calls[-1]["process"] = process
            return process
        monkeypatch.setattr(native_process.subprocess, "Popen", launch)
    return media, adapter.BoundedSpeakerDiarizer(tmp_path / "voices.sqlite", "source"), calls, install


def worker_program(failure=None):
    value = observation()
    value.update(event="diarization", engine="sherpa_onnx")
    records = [value, {"event": "completed", "intervals": 1, "embeddings": 1}]
    if failure == "count": records[-1]["intervals"] = 2
    if failure == "no_end": records.pop()
    if failure == "duplicate": records.insert(1, value)
    if failure == "schema": value["intervals"][0]["end"] = 22
    if failure == "after_end": records.append(value)
    return "import sys,json,importlib.metadata; records=" + repr(records) + "; records[0]['runtime_version']=importlib.metadata.version('sherpa-onnx'); out=open(sys.argv[1],'w'); [out.write(json.dumps(row)+'\\n') for row in records]; out.close()"


def test_complete_native_result_is_cached_without_new_process(worker_fixture):
    media, engine, calls, install = worker_fixture
    install(worker_program())
    result = engine.diarize(media, 0., 10.)
    assert not result["cache_hit"] and calls[0]["process"].poll() == 0
    reopened = adapter.BoundedSpeakerDiarizer(engine.registry_path, "source")
    cached = reopened.diarize(media, 0., 10.)
    assert cached["cache_hit"] and len(calls) == 1
    assert cached["intervals"] == result["intervals"]
    assert not list(settings.TEMP_DIR.glob("diarization-worker-*"))


@pytest.mark.parametrize("failure", ["count", "no_end", "duplicate", "schema", "after_end"])
def test_invalid_worker_never_publishes_partial_result(worker_fixture, failure):
    media, engine, calls, install = worker_fixture
    install(worker_program(failure))
    with pytest.raises(native_process.ASRProcessError): engine.diarize(media, 0., 10.)
    with sqlite3.connect(engine.registry_path) as db:
        assert db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM speakers").fetchone()[0] == 0
    assert calls[0]["process"].poll() is not None


def test_native_diarization_cancel_stops_child_and_releases_shared_slot(worker_fixture):
    media, engine, calls, install = worker_fixture
    install("import time; time.sleep(30)")
    start = time.monotonic()
    with pytest.raises(asyncio.CancelledError):
        engine.diarize(media, 0., 10., cancel_check=lambda: time.monotonic() - start > .25)
    assert time.monotonic() - start < 5 and calls[0]["process"].poll() is not None
    assert native_process.WORKER_SLOT.acquire(blocking=False)
    native_process.WORKER_SLOT.release()
    assert not list(settings.TEMP_DIR.glob("diarization-worker-*"))


def test_native_diarization_timeout_is_finite_and_no_checkpoint(worker_fixture, monkeypatch):
    media, engine, calls, install = worker_fixture
    monkeypatch.setattr(settings, "DIARIZATION_PROCESS_TIMEOUT", .2, raising=False)
    install("import time; time.sleep(30)")
    with pytest.raises(TimeoutError): engine.diarize(media, 0., 10.)
    assert calls[0]["process"].poll() is not None


@pytest.mark.parametrize("bounds", [(0, 91), (-1, 10), (10, 0), (0, float("inf")), (0, 0)])
def test_unbounded_or_invalid_intervals_never_spawn_native_worker(worker_fixture, bounds):
    media, engine, calls, install = worker_fixture
    install("raise AssertionError('must not spawn')")
    with pytest.raises(ValueError): engine.diarize(media, *bounds)
    assert not calls


def test_replaced_media_cannot_reuse_old_speaker_prototypes(worker_fixture):
    media, engine, calls, install = worker_fixture
    install(worker_program())
    first = engine.diarize(media, 0., 10.)
    media.write_bytes(b"replacement source, different input")
    with pytest.raises(ValueError, match="changed"):
        engine.diarize(media, 0., 10.)
    assert len(calls) == 1
    with sqlite3.connect(engine.registry_path) as db:
        assert db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 1


def test_existing_verified_segmentation_archive_is_not_deleted(tmp_path, monkeypatch):
    from scripts import download_diarization_models as installer
    archive = tmp_path / installer.assets.SEGMENTATION_ARCHIVE_NAME
    archive.write_bytes(b"user archive fixture")
    embedding = tmp_path / installer.assets.EMBEDDING_NAME
    embedding.write_bytes(b"embedding fixture")
    graph = b"graph fixture"
    import io
    class FakeArchive:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def getmembers(self):
            from types import SimpleNamespace
            return [SimpleNamespace(name="sherpa-onnx-pyannote-segmentation-3-0/model.onnx",
                                    size=len(graph), isfile=lambda: True)]
        def extractfile(self, member): return io.BytesIO(graph)
    monkeypatch.setattr(installer.tarfile, "open", lambda *args: FakeArchive())
    monkeypatch.setattr(installer.assets, "SEGMENTATION_SIZE", len(graph))
    monkeypatch.setattr(installer.assets, "validate_graph", lambda path, *args: Path(path))
    monkeypatch.setattr(installer.assets, "installed_assets", lambda directory: tuple(directory.iterdir()))
    monkeypatch.setattr(installer, "download", lambda item: Path(item["path"]))
    installer.install(tmp_path)
    assert archive.read_bytes() == b"user archive fixture"
    assert (tmp_path / installer.assets.SEGMENTATION_NAME).read_bytes() == graph
    assert not list(tmp_path.glob("*.download"))
