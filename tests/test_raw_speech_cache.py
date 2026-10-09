"""Offline PCM/cache regressions; fixtures never substitute for real Edge QA."""
import asyncio
import json
import logging
import shutil
import wave
import zipfile
from pathlib import Path

import pytest

for name in ("app", "ai", "pipeline", "errors"):
    logging.getLogger(name).addHandler(logging.NullHandler())

from config import settings
from core.engines.alignment import speech_cache as cache
from core.engines.alignment.natural_speech import synthesize_natural_speech
from core.engines.alignment.timing_aligner import SpeechBudgetError
from core.engines.translation.semantic_translator import SemanticTranslator
from core.engines.tts.edge_fallback import EdgeTTSFallbackEngine
from core.voice_preview import VoicePreviewManager


def pcm(path, seconds=.5, *, silent=False):
    with wave.open(str(path), "wb") as audio:
        audio.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        audio.writeframes((b"\x00\x00" if silent else b"\x30\x03") * round(24000 * seconds))
    return path


def key(**changes):
    options = dict(text="Lời nguyên vẹn.", voice="vi-VN-HoaiMyNeural", engine="edge-tts",
                   engine_revision={"runtime": "offline-revision"})
    return cache.build_raw_speech_cache_identity(**{**options, **changes})


def save(tmp_path):
    identity, source, directory = key(), pcm(tmp_path / "raw.wav", 1.8), tmp_path / "cache"
    boundaries = [{"text": identity.text, "start": 0., "end": 1.8}]
    assert cache.store_raw_speech_cache(identity, source, boundaries, cache_dir=directory)
    return identity, source, directory, boundaries


def rewrite_capsule(directory, identity, mutate):
    path = directory / f"{identity.key}.speech"
    with zipfile.ZipFile(path) as archive:
        manifest, audio = json.loads(archive.read("manifest.json")), archive.read("speech.wav")
    manifest, audio = mutate(manifest, audio)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr("speech.wav", audio)


def test_raw_round_trip_retains_full_pcm_and_original_boundaries(tmp_path):
    identity, source, directory, boundaries = save(tmp_path)
    output = tmp_path / "restored.wav"
    result = cache.load_raw_speech_cache(identity, output, cache_dir=directory)
    assert result == {"text": identity.text, "audio_duration": 1.8, "boundaries": boundaries}
    assert source.read_bytes() == output.read_bytes()
    assert list(directory.glob("*.tmp")) == []
    # A raw capsule must never pass the finalized-output validator.
    final = cache.SpeechCacheIdentity(identity.key, identity.text, 2., 1.15)
    assert cache.load_speech_cache(final, tmp_path / "final.wav", cache_dir=directory) is None


@pytest.mark.parametrize("change", [{"text": "Lời khác."}, {"voice": "vi-VN-NamMinhNeural"},
    {"engine": "piper-tts"}, {"engine_revision": {"runtime": "changed"}}])
def test_raw_tts_input_changes_invalidate_exact_hit(tmp_path, change):
    identity, _, directory, _ = save(tmp_path)
    changed = key(**change)
    assert changed.key != identity.key
    existing = tmp_path / "existing.wav"
    existing.write_bytes(b"keep existing output")
    assert cache.load_raw_speech_cache(changed, existing, cache_dir=directory) is None
    assert existing.read_bytes() == b"keep existing output"


def test_reference_pcm_hash_changes_raw_identity(tmp_path):
    reference = pcm(tmp_path / "reference.wav")
    first = key(ref_audio=reference)
    pcm(reference, .7)
    assert key(ref_audio=reference).key != first.key
    with pytest.raises(FileNotFoundError):
        key(ref_audio=tmp_path / "missing.wav")


@pytest.mark.parametrize("fault", ["syntax", "hash", "stage", "text", "duration", "boundary", "truncated"])
def test_corrupt_raw_cache_is_a_miss_and_does_not_overwrite_output(tmp_path, fault):
    identity, _, directory, _ = save(tmp_path)
    if fault == "syntax":
        (directory / f"{identity.key}.speech").write_bytes(b"broken archive")
    else:
        def mutate(manifest, audio):
            if fault == "hash":
                manifest["audio_sha256"] = "0" * 64
            elif fault == "stage":
                manifest["stage"] = "finalized"
            elif fault == "text":
                manifest["result"]["text"] = "Different sentence"
            elif fault == "duration":
                manifest["result"]["audio_duration"] = .2
            elif fault == "boundary":
                manifest["result"]["boundaries"][0]["start"] = -1
            else:
                import hashlib
                audio = audio[:-20]
                manifest["audio_sha256"] = hashlib.sha256(audio).hexdigest()
            return manifest, audio
        rewrite_capsule(directory, identity, mutate)
    existing = tmp_path / "existing.wav"
    existing.write_bytes(b"prior output")
    assert cache.load_raw_speech_cache(identity, existing, cache_dir=directory) is None
    assert existing.read_bytes() == b"prior output"


@pytest.mark.parametrize("fault", ["empty", "header", "truncated", "silent", "boundaries"])
def test_invalid_raw_pcm_or_metadata_never_enters_cache(tmp_path, fault):
    source = pcm(tmp_path / "raw.wav", silent=fault == "silent")
    boundaries = []
    if fault == "empty":
        source.write_bytes(b"")
    elif fault == "header":
        source.write_bytes(b"not WAV")
    elif fault == "truncated":
        source.write_bytes(source.read_bytes()[:-5])
    elif fault == "boundaries":
        boundaries = [{"text": "bad", "start": float("nan"), "end": .5}]
    directory = tmp_path / "cache"
    assert not cache.store_raw_speech_cache(key(), source, boundaries, cache_dir=directory)
    assert not directory.exists()


def cancel_at(number):
    calls = 0
    def check():
        nonlocal calls
        calls += 1
        return calls >= number
    return check


@pytest.mark.parametrize("number", [1, 2, 3, 4])
def test_cancelled_store_preserves_prior_cache_and_cleans_scratch(tmp_path, number):
    identity, source, directory, boundaries = save(tmp_path)
    path = directory / f"{identity.key}.speech"
    before = path.read_bytes()
    with pytest.raises(asyncio.CancelledError):
        cache.store_raw_speech_cache(identity, source, boundaries, cache_dir=directory, cancel_check=cancel_at(number))
    assert path.read_bytes() == before
    assert list(directory.glob("*.tmp")) == []


@pytest.mark.parametrize("number", [1, 2, 3])
def test_cancelled_load_never_replaces_old_wav(tmp_path, number):
    identity, _, directory, _ = save(tmp_path)
    output = tmp_path / "out.wav"
    output.write_bytes(b"old WAV")
    with pytest.raises(asyncio.CancelledError):
        cache.load_raw_speech_cache(identity, output, cache_dir=directory, cancel_check=cancel_at(number))
    assert output.read_bytes() == b"old WAV"
    assert list(tmp_path.glob("*.tmp")) == []


def test_disk_failure_does_not_damage_prior_capsule_or_source(tmp_path, monkeypatch):
    identity, source, directory, boundaries = save(tmp_path)
    prior, original = (directory / f"{identity.key}.speech").read_bytes(), source.read_bytes()
    monkeypatch.setattr(cache.os, "fsync", lambda *_: (_ for _ in ()).throw(OSError("injected disk full")))
    assert not cache.store_raw_speech_cache(identity, source, boundaries, cache_dir=directory)
    assert (directory / f"{identity.key}.speech").read_bytes() == prior
    assert source.read_bytes() == original and list(directory.glob("*.tmp")) == []


def test_raw_cache_bound_only_prunes_its_own_capsules(tmp_path, monkeypatch):
    identity, source, directory, boundaries = save(tmp_path)
    size = (directory / f"{identity.key}.speech").stat().st_size
    unrelated = directory / "user.wav"
    unrelated.write_bytes(b"keep")
    monkeypatch.setattr(cache, "MAX_CACHE_BYTES", size + 100)
    other = key(text="Câu khác.")
    assert cache.store_raw_speech_cache(other, source, boundaries, cache_dir=directory)
    assert not (directory / f"{identity.key}.speech").exists()
    assert unrelated.read_bytes() == b"keep"


class PcmAligner:
    max_speed = 1.15
    @staticmethod
    def get_audio_duration(path):
        with wave.open(str(path), "rb") as stream:
            return stream.getnframes() / stream.getframerate()
    @staticmethod
    def apply_atempo(source, output, ratio, fit_duration):
        assert ratio == 1.
        shutil.copyfile(source, output)
        return ratio


@pytest.fixture
def production_types(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(settings, "TEMP_DIR", tmp_path)
    monkeypatch.setattr(VoicePreviewManager, "_engine_revision", staticmethod(lambda _: {"runtime": "offline-fixture"}))
    def forbidden(*args, **kwargs):
        pytest.fail("An offline raw-cache regression attempted a real provider request")
    monkeypatch.setattr(EdgeTTSFallbackEngine, "synthesize", forbidden)
    monkeypatch.setattr(SemanticTranslator, "_opencode_request", forbidden)


def fixture_synthesizer(engine, monkeypatch, calls, durations):
    boundaries = {}
    def synthesize(*, text, output_path, **kwargs):
        calls.append(text)
        result = pcm(output_path, durations[text])
        boundaries[str(output_path)] = [{"text": text, "start": 0., "end": durations[text]}]
        return result
    monkeypatch.setattr(engine, "synthesize", synthesize)
    monkeypatch.setattr(engine, "take_word_boundaries", lambda path: boundaries.pop(str(path)))


def test_two_pass_resume_reuses_raw_pcm_but_rechecks_and_synthesizes_verified_shorter_text(production_types, tmp_path, monkeypatch):
    original, shorter = "Lời nguyên vẹn.", "Lời ngắn."
    engine, calls, aligner = EdgeTTSFallbackEngine(), [], PcmAligner()
    fixture_synthesizer(engine, monkeypatch, calls, {original: 1.8, shorter: .5})
    options = dict(text=original, source="完整的话。", duration=1., engine=engine, aligner=aligner)
    with pytest.raises(SpeechBudgetError):
        synthesize_natural_speech(**options, translator=None, output_path=tmp_path / "first.wav")
    assert calls == [original] and not (tmp_path / "first.wav").exists()
    fresh, translator = EdgeTTSFallbackEngine(), SemanticTranslator("opencode")
    fixture_synthesizer(fresh, monkeypatch, calls, {original: 1.8, shorter: .5})
    reviews = []
    proof = {"status": "verified", "text": shorter, "reason": "offline gate fixture"}
    def rewrite(*args, **kwargs):
        reviews.append(args)
        return {"final_vi": shorter, "pacing_verification": proof}
    monkeypatch.setattr(translator, "rewrite_for_pacing", rewrite)
    result = synthesize_natural_speech(**{**options, "engine": fresh}, translator=translator, output_path=tmp_path / "second.wav")
    assert calls == [original, shorter] and len(reviews) == 1
    assert result["text"] == shorter and result["pacing_verification"] == proof
    assert result["speed_ratio"] == 1.
    raw_key = cache.raw_synthesis_cache_identity(text=original, voice=None, engine=engine)
    restored = tmp_path / "restored-original.wav"
    raw_result = cache.load_raw_speech_cache(raw_key, restored)
    assert raw_result["audio_duration"] == 1.8 and raw_result["boundaries"][0]["end"] == 1.8
    assert list(tmp_path.glob("speech-fit-*")) == []


def test_slot_or_translator_change_cannot_make_overlong_cached_pcm_completed(production_types, tmp_path, monkeypatch):
    engine, calls = EdgeTTSFallbackEngine(), []
    fixture_synthesizer(engine, monkeypatch, calls, {"Lời nguyên vẹn.": 1.8})
    options = dict(text="Lời nguyên vẹn.", source="完整的话。", duration=1., engine=engine, aligner=PcmAligner())
    for name in ("first.wav", "retry.wav"):
        with pytest.raises(SpeechBudgetError):
            synthesize_natural_speech(**options, output_path=tmp_path / name)
        assert not (tmp_path / name).exists()
    result = synthesize_natural_speech(**{**options, "duration": 2.}, output_path=tmp_path / "longer-slot.wav")
    assert calls == ["Lời nguyên vẹn."]
    assert result["text"] == "Lời nguyên vẹn." and result["speed_ratio"] == 1.


def test_resume_repairs_only_corrupt_raw_capsule_without_synthesizing_healthy_one(production_types, tmp_path, monkeypatch):
    engine, calls = EdgeTTSFallbackEngine(), []
    fixture_synthesizer(engine, monkeypatch, calls, {"Câu đầu.": 1.8, "Câu sau.": 1.8})
    for text in ("Câu đầu.", "Câu sau."):
        with pytest.raises(SpeechBudgetError):
            synthesize_natural_speech(text=text, source="源", duration=1., engine=engine,
                aligner=PcmAligner(), output_path=tmp_path / "out.wav")
    identity = cache.raw_synthesis_cache_identity(text="Câu đầu.", voice=None, engine=engine)
    (settings.WORKSPACE_DIR / "cache/speech_raw" / f"{identity.key}.speech").write_bytes(b"corrupt cache")
    for text in ("Câu đầu.", "Câu sau."):
        result = synthesize_natural_speech(text=text, source="源", duration=2., engine=engine,
            aligner=PcmAligner(), output_path=tmp_path / "out.wav")
        assert result["text"] == text
    assert calls == ["Câu đầu.", "Câu sau.", "Câu đầu."]


def test_raw_cache_preserves_padding_and_remaps_word_timing_on_each_new_fit(production_types, tmp_path, monkeypatch):
    engine, calls = EdgeTTSFallbackEngine(), []
    original = b"\x00\x00" * 4800 + b"\x30\x03" * 19200 + b"\x00\x00" * 7200
    def synthesize(*, text, output_path, **kwargs):
        calls.append(text)
        with wave.open(str(output_path), "wb") as audio:
            audio.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
            audio.writeframes(original)
        return output_path
    monkeypatch.setattr(engine, "synthesize", synthesize)
    monkeypatch.setattr(engine, "take_word_boundaries", lambda _: [{"text": "Câu.", "start": .2, "end": 1.}])
    options = dict(text="Câu.", source="原文", duration=2., engine=engine, aligner=PcmAligner())
    first = synthesize_natural_speech(**options, output_path=tmp_path / "first.wav")
    identity = cache.raw_synthesis_cache_identity(text="Câu.", voice=None, engine=engine)
    restored = tmp_path / "untouched.wav"
    raw = cache.load_raw_speech_cache(identity, restored)
    with wave.open(str(restored), "rb") as audio:
        assert audio.readframes(audio.getnframes()) == original
    assert raw["boundaries"] == [{"text": "Câu.", "start": .2, "end": 1.}]
    # Change only available time to miss the finalized-output cache while
    # reusing raw speech and recomputing padding/word timing from scratch.
    second = synthesize_natural_speech(**{**options, "duration": 2.1}, output_path=tmp_path / "second.wav")
    assert calls == ["Câu."]
    assert first["boundaries"] == second["boundaries"]
    assert 0 <= second["boundaries"][0]["start"] < .2
    assert (tmp_path / "first.wav").read_bytes() == (tmp_path / "second.wav").read_bytes()


def test_unknown_adapter_cannot_publish_raw_provider_cache():
    class OtherEdge(EdgeTTSFallbackEngine):
        pass
    assert cache.raw_synthesis_cache_identity(text="Câu.", voice=None, engine=OtherEdge()) is None


def test_raw_identity_rejects_secret_config_and_is_tied_to_loaded_adapter(monkeypatch):
    with pytest.raises(ValueError, match="credentials"):
        key(engine_revision={"api_key": "not a real secret"})
    engine = EdgeTTSFallbackEngine()
    monkeypatch.setattr(VoicePreviewManager, "_engine_revision", staticmethod(lambda _: {"adapter": "current-disk-code", "runtime": "same"}))
    before = cache.raw_synthesis_cache_identity(text="Câu.", voice=None, engine=engine)
    monkeypatch.setattr(VoicePreviewManager, "_engine_revision", staticmethod(lambda _: {"adapter": "changed-disk-code", "runtime": "same"}))
    assert cache.raw_synthesis_cache_identity(text="Câu.", voice=None, engine=engine).key == before.key
