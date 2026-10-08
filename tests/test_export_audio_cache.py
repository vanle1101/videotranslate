"""Real small media verifies that visual edits reuse audio, never fake a hit."""
import json
import os
import shutil
import zipfile
from pathlib import Path
from unittest.mock import Mock

import pytest

from config import settings
from core.media_process import run_media
from core.audio_ducking import PremiumAudioMixer
from core.streaming import audio_cache
from core.streaming.export import HQExporter


pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg required")


@pytest.fixture
def media(tmp_path, monkeypatch):
    for name in ("BASE_DIR", "WORKSPACE_DIR", "TEMP_DIR", "OUTPUT_DIR"):
        monkeypatch.setattr(settings, name, tmp_path)
    monkeypatch.setattr(settings, "SEPARATION_ENGINE", "dsp")
    monkeypatch.setattr(settings, "SUPPRESSION_MODE", "DSP_MONO_ADAPTIVE_FORMANT")
    source, voice = tmp_path / "source.mp4", tmp_path / "voice.wav"
    run_media(["ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i", "color=c=blue:s=160x240:r=10:d=1",
               "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=1", "-c:v", "libx264",
               "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(source)])
    run_media(["ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i",
               "sine=frequency=880:sample_rate=44100:duration=0.5", "-ac", "2", str(voice)])
    rows = [{"id": 0, "start": .2, "end": .8, "final_vi": "Xin chào", "audio_path": str(voice), "revision": 1}]
    return source, rows, tmp_path


def export(media, task_id="cache-test", **kwargs):
    source, rows, _ = media
    return HQExporter().export(task_id, source, rows, 1, **kwargs)


def records(root):
    return list((root / "cache/export_audio").glob("*.mix"))


def test_real_caption_only_export_skips_all_audio_processing_and_keeps_audio_bytes(media, monkeypatch):
    source, rows, root = media
    first = export(media)
    original = run_media(["ffmpeg", "-v", "error", "-i", first["output_path"], "-map", "0:a", "-f", "s16le", "pipe:1"], capture_output=True)
    assert len(records(root)) == 1
    exporter = HQExporter()
    monkeypatch.setattr(exporter, "_mix_audio", Mock(side_effect=AssertionError("Repeated audio pipeline")))
    monkeypatch.setattr(exporter.suppressor, "process_file", Mock(side_effect=AssertionError("Repeated separation")))
    monkeypatch.setattr(exporter.mixer, "mix", Mock(side_effect=AssertionError("Repeated mixing")))
    stages = []
    result = exporter.export("restyled", source, rows, 1, caption_style={"position": "top", "text_color": "#FFFFFF", "background_color": "#123456"},
                             progress_callback=lambda pct, stage: stages.append(stage))
    assert result["separation_engine"] == first["separation_engine"] == "dsp"
    assert result["warnings"] == first["warnings"] == []
    assert any("dùng lại" in stage for stage in stages)
    assert not any("Trích xuất" in stage or "Trộn giọng" in stage for stage in stages)
    assert len(records(root)) == 1
    reused = run_media(["ffmpeg", "-v", "error", "-i", result["output_path"], "-map", "0:a", "-f", "s16le", "pipe:1"], capture_output=True)
    assert original, 'First real export has no decoded audio'
    if original != reused:
        mismatch = next((i for i, pair in enumerate(zip(original, reused)) if pair[0] != pair[1]),
                        min(len(original), len(reused)))
        details = {"original_pcm_bytes": len(original), "reused_pcm_bytes": len(reused),
                   "first_different_byte": mismatch}
        for label, rendered in (("original", first), ("reused", result)):
            details[label] = json.loads(run_media(
                ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_streams",
                 "-show_entries", "stream=sample_rate,channels,duration,nb_frames,time_base,start_time",
                 "-of", "json", rendered["output_path"]], capture_output=True))
        pytest.fail(f'Caption-only export changed decoded audio: {details}')
    HQExporter.validate_output(result["output_path"], 1)


@pytest.mark.parametrize("change", ["voice", "revision", "timing", "source", "ducking", "gain", "mode", "engine", "code"])
def test_identity_tracks_every_audio_change(media, monkeypatch, change):
    source, rows, root = media
    exporter = HQExporter()
    def identity():
        return audio_cache.build_audio_cache_identity(source, rows, 1, exporter.mixer, exporter.suppressor)
    before = identity()
    assert before is not None
    if change in {"voice", "source"}:
        path = Path(rows[0]["audio_path"]) if change == "voice" else source
        info = path.stat()
        with path.open("r+b") as stream:
            stream.seek(-1, 2)
            byte = stream.read(1)
            stream.seek(-1, 2)
            stream.write(bytes([byte[0] ^ 1]))
        os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
    elif change == "revision":
        rows[0]["revision"] += 1
    elif change == "timing":
        rows[0]["start"] += .05
    elif change == "ducking":
        exporter.mixer.duck_amount_db -= 3
    elif change == "gain":
        exporter.mixer.voice_gain_db += 2
    elif change == "mode":
        monkeypatch.setattr(settings, "SUPPRESSION_MODE", "AUTO")
    elif change == "engine":
        monkeypatch.setattr(settings, "SEPARATION_ENGINE", "none")
    else:
        original = audio_cache._digest
        monkeypatch.setattr(audio_cache, "_digest", lambda path, cancel=None: "changed-code" if Path(path).name == "audio_ducking.py" else original(path, cancel))
    assert identity().key != before.key


@pytest.mark.parametrize("change", ["voice", "settings"])
def test_real_voice_or_machine_gain_change_rebuilds_mix(media, monkeypatch, change):
    export(media)
    if change == "voice":
        run_media(["ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i",
                   "sine=frequency=650:sample_rate=44100:duration=0.5", "-ac", "2", media[1][0]["audio_path"]])
    else:
        monkeypatch.setattr(settings, "BGM_VOLUME_NORMAL_DB", settings.BGM_VOLUME_NORMAL_DB - 4)
    exporter = HQExporter()
    rebuild = Mock(wraps=exporter._mix_audio)
    monkeypatch.setattr(exporter, "_mix_audio", rebuild)
    result = exporter.export("audio-edited", media[0], media[1], 1)
    assert rebuild.call_count == 1
    assert len(records(media[2])) == 2
    HQExporter.validate_output(result["output_path"], 1)


@pytest.mark.parametrize("damage", ["deleted", "corrupt", "truncated"])
def test_deleted_or_corrupted_cache_regenerates_real_audio(media, monkeypatch, damage):
    export(media)
    cached = records(media[2])[0]
    if damage == "deleted":
        cached.unlink()
    elif damage == "corrupt":
        cached.write_bytes(b"Not an audio record")
    else:
        with cached.open("r+b") as stream:
            stream.truncate(100)
    exporter = HQExporter()
    build = Mock(wraps=exporter._mix_audio)
    monkeypatch.setattr(exporter, "_mix_audio", build)
    result = exporter.export("repaired", media[0], media[1], 1)
    assert build.call_count == 1
    assert len(records(media[2])) == 1
    HQExporter.validate_output(result["output_path"], 1)


@pytest.mark.parametrize("failure", ["cancel", "render", "validate", "publish"])
def test_failed_or_cancelled_export_never_publishes_audio_cache(media, monkeypatch, failure):
    exporter = HQExporter()
    cancelled = False
    if failure == "cancel":
        compose = exporter.composer.compose
        def cancel_after_render(**kwargs):
            nonlocal cancelled
            result = compose(**kwargs)
            cancelled = True
            return result
        monkeypatch.setattr(exporter.composer, "compose", cancel_after_render)
    elif failure == "render":
        monkeypatch.setattr(exporter.composer, "compose", Mock(side_effect=RuntimeError("broken render")))
    elif failure == "validate":
        monkeypatch.setattr(exporter, "validate_output", Mock(side_effect=ValueError("bad video")))
    publish = Mock(side_effect=RuntimeError("cancel before commit")) if failure == "publish" else None
    with pytest.raises((RuntimeError, ValueError)):
        exporter.export("no-cache", media[0], media[1], 1, cancel_check=lambda: cancelled, publish_callback=publish)
    assert not records(media[2])
    assert not (media[2] / "douyin_translated_no-cache_hq.mp4").exists()
    assert not list(media[2].glob("hq_export_*"))


def test_invalid_audio_cannot_be_cached_even_when_mixer_reports_success(media):
    exporter = HQExporter()
    identity = audio_cache.build_audio_cache_identity(media[0], media[1], 1, exporter.mixer, exporter.suppressor)
    invalid = media[2] / "fake.wav"
    invalid.write_bytes(b"mock successful audio")
    assert audio_cache.prepare_audio_cache(identity, invalid, {"separation_engine": "dsp", "warnings": []}, media[2]) is None
    assert not records(media[2])
    assert not (media[2] / "cache_master.flac").exists()


def test_cache_bounded_pruning_only_removes_owned_records(media, monkeypatch):
    export(media)
    directory = media[2] / "cache/export_audio"
    record = records(media[2])[0]
    size = record.stat().st_size
    retained = directory / "user-notes.txt"
    retained.write_text("keep")
    monkeypatch.setattr(audio_cache, "MAX_CACHE_BYTES", size + 500)
    media[1][0]["revision"] += 1
    export(media, "new-revision")
    assert not record.exists()
    assert sum(path.stat().st_size for path in records(media[2])) <= audio_cache.MAX_CACHE_BYTES
    assert retained.read_text() == "keep"
    assert not list(directory.glob("*.tmp"))


def test_cache_preserves_separator_warning_metadata(media, monkeypatch):
    monkeypatch.setattr(settings, "SEPARATION_ENGINE", "unsupported-test-engine")
    result = export(media)
    exporter = HQExporter()
    monkeypatch.setattr(exporter, "_mix_audio", Mock(side_effect=AssertionError("audio should be cached")))
    again = exporter.export("warning", media[0], media[1], 1)
    assert again["separation_engine"] == result["separation_engine"] == "dsp"
    assert again["warnings"] == result["warnings"] and again["warnings"]


def test_cancel_while_copying_cached_audio_propagates_and_cleans_scratch(media):
    export(media)
    exporter = HQExporter()
    identity = audio_cache.build_audio_cache_identity(media[0], media[1], 1, exporter.mixer, exporter.suppressor)
    calls = 0
    def cancel():
        nonlocal calls
        calls += 1
        return calls >= 2
    with pytest.raises(RuntimeError, match="hủy"):
        audio_cache.load_audio_cache(identity, media[2], cancel)
    assert not (media[2] / "cached_master.flac").exists()
    assert len(records(media[2])) == 1


def test_manifest_cannot_fake_valid_audio_or_crash_on_wrong_shape(media):
    export(media)
    exporter = HQExporter()
    identity = audio_cache.build_audio_cache_identity(media[0], media[1], 1, exporter.mixer, exporter.suppressor)
    record = records(media[2])[0]
    for manifest in ([], {"schema": 1, "identity": identity.key, "duration": 1,
                          "metadata": {"separation_engine": "dsp", "warnings": []}, "sha256": "wrong"}):
        with zipfile.ZipFile(record, "w", compression=zipfile.ZIP_STORED) as container:
            container.writestr("manifest.json", json.dumps(manifest))
            container.writestr("mix.flac", b"fake audio")
        assert audio_cache.load_audio_cache(identity, media[2]) is None
        assert not (media[2] / "cached_master.flac").exists()


def test_sidechain_keeps_full_tail_when_both_tracks_end_at_requested_duration(media, monkeypatch):
    """Finite pad upstream used to nondeterministically drop 70-164 ms here."""
    import wave
    background, voice = media[2] / "background.wav", media[2] / "timeline.wav"
    for path, frequency in ((background, 440), (voice, 880)):
        run_media(["ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i",
                   f"sine=frequency={frequency}:sample_rate=44100:duration=1", "-ac", "2", str(path)])
    real_run = run_media
    observed = []
    def capture(command, cancel_check=None):
        observed.append(command[command.index("-filter_complex") + 1])
        return real_run(command, cancel_check)
    monkeypatch.setattr("core.audio_ducking.run_media", capture)
    for i in range(4):
        result = media[2] / f"mix-{i}.wav"
        PremiumAudioMixer().mix(background, voice, result, total_duration=1)
        with wave.open(str(result)) as audio:
            assert audio.getnframes() == 44100
            audio.setpos(43700)
            assert any(audio.readframes(400)), "tail audio must survive, not just be silence padded afterward"
    assert all("apad=whole_dur" not in graph and "atrim=duration=1" in graph for graph in observed)
