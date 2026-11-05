import json
import math
import shutil
import struct
import wave
from types import SimpleNamespace

import pytest

from config import settings
from core.engines.separator.realtime_suppressor import RealtimeVocalSuppressor
from core.media_process import run_media
from core.streaming.chunked_source import _asset, _media_identity, _record_path
from core.streaming.prepared_background import background_provenance, load_prepared_background, prepared_background_identity


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg required for real packet validation")
    monkeypatch.setattr(settings, "BASE_DIR", tmp_path)
    monkeypatch.setattr(settings, "SEPARATION_ENGINE", "realtime")
    monkeypatch.setattr(settings, "SUPPRESSION_MODE", "AUTO")
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source identity fixture")
    cache = tmp_path / "workspace/cache/prepared-test"
    root = cache / "source_preparation"
    root.mkdir(parents=True)
    session = SimpleNamespace(video_path=source, cache_dir=cache, total_duration=.3)
    records = []
    for index, (start, end) in enumerate(((0., .13), (.13, .3))):
        raw = root / f"{index}.wav"
        with wave.open(str(raw), "wb") as handle:
            handle.setnchannels(2)
            handle.setsampwidth(2)
            handle.setframerate(48000)
            handle.writeframes(b"".join(struct.pack("<hh", value, value) for value in
                (round(8000 * math.sin(2 * math.pi * (440 + index * 100) * n / 48000))
                 for n in range(round((end - start) * 48000)))))
        bgm = root / f"{index}.ogg"
        run_media(["ffmpeg", "-v", "error", "-nostdin", "-y", "-i", str(raw), "-c:a", "libopus", str(bgm)])
        record = {"identity": _media_identity(session), "start": start, "end": end, "rows": [],
                  "raw_audio": _asset(raw), "bgm": _asset(bgm),
                  "background_provenance": background_provenance(RealtimeVocalSuppressor())}
        _record_path(session, start).write_text(json.dumps(record), encoding="utf-8")
        records.append(record)
    return session, records, tmp_path / "export"


def load(prepared, **kwargs):
    session, _, task_dir = prepared
    return load_prepared_background(session.video_path, "prepared-test", session.total_duration, task_dir, **kwargs)


def rewrite(prepared, index=0):
    session, records, _ = prepared
    _record_path(session, records[index]["start"]).write_text(json.dumps(records[index]), encoding="utf-8")


def test_reuses_opus_packets_at_measured_boundaries(prepared):
    path, metadata = load(prepared)
    with wave.open(str(path), "rb") as handle:
        assert handle.getnframes() == 13230
        assert handle.getframerate() == 44100
        assert handle.getnchannels() == 2
        assert any(handle.readframes(14400))
    assert metadata["background_source"] == "prepared_intervals"
    assert metadata["background_intervals"] == 2
    assert metadata["separation_engine"] == "dsp"


@pytest.mark.parametrize("engine", ["none", "roformer", "unknown"])
def test_never_substitutes_dsp_for_different_engine(prepared, monkeypatch, engine):
    monkeypatch.setattr(settings, "SEPARATION_ENGINE", engine)
    assert load(prepared) is None


@pytest.mark.parametrize("damage", ["partial", "gap", "asset", "digest", "source", "legacy", "implementation", "duration"])
def test_invalid_intervals_fall_back_without_publishing(prepared, damage):
    session, records, task_dir = prepared
    if damage == "partial":
        _record_path(session, .13).unlink()
    elif damage == "gap":
        records[0]["end"] = .12
        rewrite(prepared)
    elif damage == "asset":
        from pathlib import Path
        Path(records[0]["bgm"]["path"]).write_bytes(b"corrupt")
    elif damage == "digest":
        records[0]["bgm"]["sha256"] = "0" * 64
        rewrite(prepared)
    elif damage == "source":
        session.video_path.write_bytes(b"a different source")
    elif damage == "legacy":
        records[0].pop("background_provenance")
        rewrite(prepared)
    elif damage == "implementation":
        records[0]["background_provenance"]["implementation"] = "changed"
        rewrite(prepared)
    elif damage == "duration":
        # Valid asset hashes but wrong decoded interval duration must miss.
        records[0]["bgm"] = records[1]["bgm"]
        rewrite(prepared)
    assert load(prepared) is None
    assert not (task_dir / "prepared_background.wav").exists()


def test_suppression_mode_change_invalidates(prepared, monkeypatch):
    monkeypatch.setattr(settings, "SUPPRESSION_MODE", "DSP_STEREO_CENTER_CANCEL")
    assert load(prepared) is None


def test_cancellation_is_not_treated_as_cache_miss(prepared):
    with pytest.raises(RuntimeError, match="hủy"):
        load(prepared, cancel_check=lambda: True)


def test_unowned_packet_is_rejected_even_with_matching_hash(prepared, tmp_path):
    from pathlib import Path
    _, records, _ = prepared
    external = tmp_path / "unowned.ogg"
    external.write_bytes(Path(records[0]["bgm"]["path"]).read_bytes())
    records[0]["bgm"] = _asset(external)
    rewrite(prepared)
    assert load(prepared) is None


def test_asr_setting_changes_do_not_invalidate_audio(prepared, monkeypatch):
    monkeypatch.setattr(settings, "WHISPER_MODEL_SIZE", "different-model")
    monkeypatch.setattr(settings, "WHISPER_COMPUTE_TYPE", "float32")
    assert load(prepared) is not None


def test_provenance_does_not_depend_on_previous_suppression_result():
    suppressor = RealtimeVocalSuppressor()
    before = background_provenance(suppressor)
    suppressor.suppression_level_db = -20
    assert background_provenance(suppressor) == before


def test_identity_matches_loaded_metadata_without_media_decode(prepared, monkeypatch):
    session, _, task_dir = prepared
    with monkeypatch.context() as patch:
        def forbidden(*args, **kwargs):
            raise AssertionError("Identity must not run media tools")
        patch.setattr("core.streaming.prepared_background.run_media", forbidden)
        identity = prepared_background_identity(session.video_path, "prepared-test", .3)
    assert len(identity) == 64
    assert not task_dir.exists()
    assert load(prepared)[1]["background_interval_digest"] == identity


@pytest.mark.parametrize("damage", ["partial", "provenance", "source", "hash"])
def test_identity_rejects_unproven_chain(prepared, damage):
    session, records, _ = prepared
    if damage == "partial":
        _record_path(session, .13).unlink()
    elif damage == "source":
        session.video_path.write_bytes(b"modified")
    else:
        if damage == "provenance":
            records[0].pop("background_provenance")
        else:
            records[0]["bgm"]["sha256"] = "changed"
        rewrite(prepared)
    assert prepared_background_identity(session.video_path, "prepared-test", .3) is None


def test_export_mix_uses_prepared_packets_without_separation(prepared, monkeypatch):
    from unittest.mock import Mock
    from core.streaming.export import HQExporter
    session, _, task_dir = prepared
    task_dir.mkdir()
    exporter = HQExporter()
    monkeypatch.setattr(exporter.suppressor, "process_file", Mock(side_effect=AssertionError("DSP ran again")))
    master, metadata = exporter._mix_audio(session.video_path, [], .3, task_dir,
                                         lambda *args: None, lambda: None, None, task_id="prepared-test")
    assert master.is_file() and master.stat().st_size > 44
    assert metadata["background_source"] == "prepared_intervals"
    assert metadata["background_interval_digest"] == prepared_background_identity(session.video_path, "prepared-test", .3)
    assert not (task_dir / "raw_audio.wav").exists()
