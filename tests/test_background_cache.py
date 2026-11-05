import json
import shutil
import wave
import zipfile
from pathlib import Path

import pytest

from core.engines.separator.realtime_suppressor import RealtimeVocalSuppressor
from core.streaming import background_cache as cache


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(cache.settings, "WORKSPACE_DIR", tmp_path)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"original source")
    task = tmp_path / "task"
    task.mkdir()
    return source, task, RealtimeVocalSuppressor()


def identity(setup, **kwargs):
    source, _, suppressor = setup
    return cache.build_background_identity(source, 1, suppressor, engine="dsp", **kwargs)


def audio(tmp_path, *, duration=1, channels=2, rate=44100):
    path = tmp_path / f"audio-{duration}-{channels}-{rate}.wav"
    with wave.open(str(path), "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(b"\0\0" * channels * round(rate * duration))
    return path


requires_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
                                    reason="ffmpeg is required for full media validation")


@requires_ffmpeg
def test_reuse_survives_mutable_suppressor_state(setup):
    first = identity(setup)
    setup[2].last_mode = "DSP_MONO_ADAPTIVE_FORMANT"
    setup[2].last_throughput_rtf = 123.0
    setup[2].suppression_level_db = -10
    assert identity(setup) == first
    setup[2].analysis_timeout = 15
    assert identity(setup) != first
    setup[0].write_bytes(b"replaced source")
    assert identity(setup) != first


@requires_ffmpeg
def test_actual_engine_and_dsp_mode_are_separate_identities(setup, monkeypatch):
    first = identity(setup)
    assert cache.build_background_identity(setup[0], 1, setup[2], engine="none") != first
    assert cache.build_background_identity(setup[0], 1, setup[2], engine="realtime") == first
    monkeypatch.setattr(cache.settings, "SUPPRESSION_MODE", "DSP_STEREO_CENTER_CANCEL")
    assert identity(setup) != first


def test_unknown_adapter_and_unavailable_checkpoint_disable_cache(setup, monkeypatch, tmp_path):
    assert cache.build_background_identity(setup[0], 1, object(), engine="dsp") is None
    monkeypatch.setattr(cache.settings, "BASE_DIR", tmp_path)
    assert cache.build_background_identity(setup[0], 1, setup[2], engine="roformer") is None


@requires_ffmpeg
def test_roundtrip_uses_verified_lossless_stem(setup, tmp_path):
    key = identity(setup)
    assert cache.store_background_cache(key, audio(tmp_path))
    loaded = cache.load_background_cache(key, setup[1])
    assert loaded.is_file()
    assert cache._probe(loaded)[3] == "flac"
    # A new scratch directory, as after app restart, can use the same record.
    next_task = tmp_path / "restart"
    next_task.mkdir()
    assert cache.load_background_cache(key, next_task).read_bytes() == loaded.read_bytes()


@requires_ffmpeg
@pytest.mark.parametrize("kwargs", [{"duration": .5}, {"channels": 1}, {"rate": 48000}])
def test_rejects_wrong_timeline_and_format(setup, tmp_path, kwargs):
    assert not cache.store_background_cache(identity(setup), audio(tmp_path, **kwargs))
    assert not list(tmp_path.rglob("*.bg"))


@requires_ffmpeg
@pytest.mark.parametrize("tamper", ["hash", "verified", "channels", "identity", "media"])
def test_corruption_and_unverified_legacy_records_are_misses(setup, tmp_path, tamper):
    key = identity(setup)
    assert cache.store_background_cache(key, audio(tmp_path))
    record_path = next(tmp_path.rglob("*.bg"))
    with zipfile.ZipFile(record_path) as record:
        manifest = json.loads(record.read("manifest.json"))
        media = record.read("background.flac")
    if tamper == "media":
        media = b"broken audio"
    else:
        manifest[{"hash": "sha256"}.get(tamper, tamper)] = {"verified": False, "channels": 1}.get(tamper, "wrong")
    with zipfile.ZipFile(record_path, "w", compression=zipfile.ZIP_STORED) as record:
        record.writestr("manifest.json", json.dumps(manifest))
        record.writestr("background.flac", media)
    assert cache.load_background_cache(key, setup[1]) is None
    assert not (setup[1] / "cached_background.flac").exists()


@requires_ffmpeg
def test_bounded_cache_evicts_only_owned_records(setup, tmp_path, monkeypatch):
    key = identity(setup)
    stem = audio(tmp_path)
    assert cache.store_background_cache(key, stem)
    old = next(tmp_path.rglob("*.bg"))
    size = old.stat().st_size
    foreign = old.parent / "user-file.bg"
    foreign.write_text("keep")
    monkeypatch.setattr(cache, "MAX_CACHE_BYTES", size + 100)
    second = cache.BackgroundCacheIdentity("a" * 64, 1)
    assert cache.store_background_cache(second, stem)
    assert not old.exists()
    assert foreign.read_text() == "keep"
    assert sum(p.stat().st_size for p in old.parent.iterdir() if cache._NAME.fullmatch(p.name)) <= size + 100


@pytest.mark.parametrize("operation", ["build", "store", "load"])
def test_cancellation_propagates(setup, tmp_path, operation):
    key = cache.BackgroundCacheIdentity("a" * 64, 1)
    with pytest.raises(RuntimeError, match="hủy"):
        if operation == "build":
            identity(setup, cancel_check=lambda: True)
        elif operation == "store":
            cache.store_background_cache(key, audio(tmp_path), lambda: True)
        else:
            cache.load_background_cache(key, setup[1], lambda: True)


@requires_ffmpeg
def test_failed_publication_preserves_previous_cache(setup, tmp_path, monkeypatch):
    key = identity(setup)
    stem = audio(tmp_path)
    assert cache.store_background_cache(key, stem)
    record = next(tmp_path.rglob("*.bg"))
    previous = record.read_bytes()
    original_replace = Path.replace
    def broken_replace(path, target):
        if str(target).endswith(".bg"):
            raise OSError("disk full")
        return original_replace(path, target)
    monkeypatch.setattr(Path, "replace", broken_replace)
    assert not cache.store_background_cache(key, stem)
    assert record.read_bytes() == previous
    assert not list(record.parent.glob("*.tmp"))
