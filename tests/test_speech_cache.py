"""Speech cache regressions; synthetic PCM never stands in for provider QA."""
import asyncio
import json
import logging
import shutil
import wave
import zipfile
from types import SimpleNamespace

import pytest

from core.engines.alignment import speech_cache as cache

for _logger_name in ("app", "ai", "pipeline", "errors"):
    logging.getLogger(_logger_name).addHandler(logging.NullHandler())


def identity(**changes):
    options = dict(source="我还没做。", text="Tôi vẫn chưa làm việc đó.", duration=2,
                   voice="vi-VN-HoaiMyNeural", engine="edge-tts",
                   engine_revision={"adapter": "abc", "runtime": "7.0"},
                   provider="opencode", model="muse", context=[{"zh": "做了吗？"}])
    return cache.build_speech_cache_identity(**{**options, **changes})


def audio(path, seconds=.5):
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(b"\x30\x03" * round(16000 * seconds))
    return path


def result(rewritten=True):
    text = "Tôi chưa làm." if rewritten else "Tôi vẫn chưa làm việc đó."
    return {"text": text, "tts_duration": .5, "speed_ratio": 1.0,
            "boundaries": [{"text": text, "start": 0, "end": .5}],
            "pacing_verification": {"status": "verified", "text": text,
                                    "provider": "opencode", "reason": "Giữ phủ định."} if rewritten else None}


def save(tmp_path, *, rewritten=True):
    key = identity()
    source = audio(tmp_path / "input.wav")
    directory = tmp_path / "cache"
    data = result(rewritten)
    assert cache.store_speech_cache(key, source, data, cache_dir=directory)
    return key, source, directory, data


def mutate_manifest(directory, key, change):
    path = directory / f"{key.key}.speech"
    with zipfile.ZipFile(path) as record:
        manifest = json.loads(record.read("manifest.json"))
        data = record.read("speech.wav")
    change(manifest)
    with zipfile.ZipFile(path, "w") as record:
        record.writestr("manifest.json", json.dumps(manifest))
        record.writestr("speech.wav", data)


@pytest.mark.parametrize("rewritten", [True, False])
def test_round_trip_retains_audio_timing_and_proof(tmp_path, rewritten):
    key, source, directory, expected = save(tmp_path, rewritten=rewritten)
    output = tmp_path / "restored.wav"
    actual = cache.load_speech_cache(key, output, cache_dir=directory)
    assert actual == expected
    assert output.read_bytes() == source.read_bytes()
    assert list(directory.glob("*.tmp")) == []
    assert not any("我还没做" in item.read_bytes().decode("utf-8", errors="ignore") for item in directory.iterdir())


@pytest.mark.parametrize("changes", [
    {"source": "我做了。"}, {"text": "Tôi đã làm rồi."}, {"duration": 2.1},
    {"voice": "Nam Minh"}, {"engine": "piper"}, {"engine_revision": "new-model"},
    {"provider": "gemini"}, {"model": "another-model"}, {"context": []}, {"max_speed": 1.1},
])
def test_every_semantic_and_runtime_input_invalidates_hit(tmp_path, changes):
    key, _, directory, _ = save(tmp_path)
    changed = identity(**changes)
    assert changed.key != key.key
    output = tmp_path / "untouched.wav"
    output.write_bytes(b"existing")
    assert cache.load_speech_cache(changed, output, cache_dir=directory) is None
    assert output.read_bytes() == b"existing"


def test_code_and_reference_audio_revisions_invalidate(tmp_path, monkeypatch):
    reference = audio(tmp_path / "reference.wav")
    first = identity(ref_audio=reference)
    audio(reference, .7)
    assert identity(ref_audio=reference).key != first.key
    first = identity()
    monkeypatch.setattr(cache, "_code_revision", lambda: {"revision": "changed"})
    assert identity().key != first.key


@pytest.mark.parametrize("mutate", [
    lambda row: row.update(identity="0" * 64),
    lambda row: row.update(audio_sha256="0" * 64),
    lambda row: row.update(schema=999),
    lambda row: row["result"].update(pacing_verification=None),
    lambda row: row["result"]["pacing_verification"].update(text="Different verified line"),
    lambda row: row["result"]["pacing_verification"].update(status="failed"),
    lambda row: row["result"].update(tts_duration=1.9),
    lambda row: row["result"].update(speed_ratio=2.0),
    lambda row: row["result"].update(boundaries=[{"text": "bad", "start": -1, "end": .5}]),
    lambda row: row["result"].pop("boundaries"),
])
def test_invalid_metadata_or_proof_is_a_miss_not_success(tmp_path, mutate):
    key, _, directory, _ = save(tmp_path)
    mutate_manifest(directory, key, mutate)
    output = tmp_path / "existing.wav"
    output.write_bytes(b"keep")
    assert cache.load_speech_cache(key, output, cache_dir=directory) is None
    assert output.read_bytes() == b"keep"


def test_corrupt_record_and_truncated_pcm_are_rejected(tmp_path):
    key, source, directory, data = save(tmp_path)
    path = directory / f"{key.key}.speech"
    path.write_bytes(b"not a ZIP")
    assert cache.load_speech_cache(key, tmp_path / "out.wav", cache_dir=directory) is None
    source.write_bytes(source.read_bytes()[:-30])
    assert not cache.store_speech_cache(key, source, data, cache_dir=directory)


@pytest.mark.parametrize("invalid", [
    {"pacing_verification": None}, {"tts_duration": float("nan")}, {"speed_ratio": True},
    {"boundaries": None}, {"text": ""},
])
def test_invalid_success_is_never_cached(tmp_path, invalid):
    key, source, directory = identity(), audio(tmp_path / "input.wav"), tmp_path / "cache"
    assert not cache.store_speech_cache(key, source, {**result(), **invalid}, cache_dir=directory)
    assert not directory.exists()


def cancel_at(number):
    calls = 0
    def check():
        nonlocal calls
        calls += 1
        return calls >= number
    return check


@pytest.mark.parametrize("check_number", [1, 2, 3, 4])
def test_cancel_during_store_never_publishes_or_replaces_entry(tmp_path, check_number):
    key, source, directory, data = save(tmp_path)
    before = (directory / f"{key.key}.speech").read_bytes()
    with pytest.raises(asyncio.CancelledError):
        cache.store_speech_cache(key, source, data, cache_dir=directory,
                                 cancel_check=cancel_at(check_number))
    assert (directory / f"{key.key}.speech").read_bytes() == before
    assert list(directory.glob("*.tmp")) == []


@pytest.mark.parametrize("check_number", [1, 2, 3])
def test_cancel_during_restore_preserves_existing_output(tmp_path, check_number):
    key, _, directory, _ = save(tmp_path)
    output = tmp_path / "output.wav"
    output.write_bytes(b"old complete result")
    with pytest.raises(asyncio.CancelledError):
        cache.load_speech_cache(key, output, cache_dir=directory,
                                cancel_check=cancel_at(check_number))
    assert output.read_bytes() == b"old complete result"
    assert list(tmp_path.glob("*.tmp")) == []


def test_cache_evicts_its_own_old_records_within_byte_limit(tmp_path, monkeypatch):
    key, source, directory, data = save(tmp_path)
    size = (directory / f"{key.key}.speech").stat().st_size
    unrelated = directory / "user-file.txt"
    unrelated.write_text("keep")
    monkeypatch.setattr(cache, "MAX_CACHE_BYTES", size + 100)
    other = identity(source="Different source")
    assert cache.store_speech_cache(other, source, data, cache_dir=directory)
    assert not (directory / f"{key.key}.speech").exists()
    assert (directory / f"{other.key}.speech").exists()
    assert unrelated.read_text() == "keep"
    assert sum(p.stat().st_size for p in directory.glob("*.speech")) <= cache.MAX_CACHE_BYTES


def test_secret_configuration_is_rejected_before_persistence(tmp_path):
    with pytest.raises(ValueError, match="credentials"):
        identity(engine_revision={"api_key": "not-a-real-key"})
    key = identity()
    data = result()
    data["pacing_verification"]["authorization"] = "not-a-real-token"
    assert not cache.store_speech_cache(key, audio(tmp_path / "source.wav"), data,
                                       cache_dir=tmp_path / "cache")


def test_missing_reference_fails_instead_of_reusing_other_voice(tmp_path):
    with pytest.raises(FileNotFoundError):
        identity(ref_audio=tmp_path / "deleted.wav")


@pytest.fixture
def production_identity_environment(tmp_path, monkeypatch):
    """Use real adapter types and voice resolution, without model/provider I/O."""
    from config import settings
    from core.voice_preview import VoicePreviewManager
    monkeypatch.setattr(settings, "WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(settings, "TEMP_DIR", tmp_path)
    monkeypatch.setattr(settings, "OPENCODE_MODEL", "opencode/model-a")
    monkeypatch.setattr(settings, "GEMINI_MODEL", "gemini-model-a")
    monkeypatch.setattr(settings, "OPENROUTER_MODEL", "router/model-a")
    revisions = {"edge-tts": "edge-v1", "piper-tts": "piper-v1", "vieneu-tts": "vieneu-v1"}
    monkeypatch.setattr(VoicePreviewManager, "_engine_revision", staticmethod(lambda name: revisions[name]))
    return settings, revisions


def production_identity(engine, **changes):
    options = dict(source="我还没做。", text="Tôi vẫn chưa làm việc đó.", duration=2,
                   voice=None, engine=engine, aligner=SimpleNamespace(max_speed=1.15),
                   translator=None, context=[{"zh": "做了吗？"}])
    return cache.synthesis_cache_identity(**{**options, **changes})


def test_known_production_engine_adapter_and_local_model_revisions_are_distinct(production_identity_environment, monkeypatch):
    from core.engines.tts.edge_fallback import EdgeTTSFallbackEngine
    from core.engines.tts.piper_engine import PiperEngine
    from core.engines.tts.vieneu_engine import VieNeuEngine
    from core import voice_catalog
    monkeypatch.setattr(voice_catalog, "_vieneu_presets", lambda: [{"name": "Trúc Ly", "aliases": []}])
    # Constructors may initialize inference runtimes; identity needs no runtime.
    edge = EdgeTTSFallbackEngine("vi-VN-HoaiMyNeural")
    piper, vieneu = object.__new__(PiperEngine), object.__new__(VieNeuEngine)
    keys = [production_identity(edge), production_identity(piper, voice="0"),
            production_identity(vieneu, voice="Trúc Ly")]
    assert all(key is not None for key in keys)
    assert len({key.key for key in keys}) == 3
    _, revisions = production_identity_environment
    revisions["vieneu-tts"] = "vieneu-v2-new-local-model"
    assert production_identity(vieneu, voice="Trúc Ly").key != keys[2].key
    assert production_identity(edge).key == keys[0].key


def test_unknown_or_subclassed_adapter_cannot_reuse_known_engine_cache(production_identity_environment):
    from core.engines.tts.edge_fallback import EdgeTTSFallbackEngine
    class OtherEdge(EdgeTTSFallbackEngine):
        pass
    assert production_identity(OtherEdge()) is None
    assert production_identity(SimpleNamespace(name="Edge-TTS")) is None


@pytest.mark.parametrize("provider,setting", [("opencode", "OPENCODE_MODEL"),
                                              ("gemini", "GEMINI_MODEL"),
                                              ("openrouter-free", "OPENROUTER_MODEL")])
def test_production_selected_translation_model_invalidates_cache(production_identity_environment, monkeypatch, provider, setting):
    from core.engines.tts.edge_fallback import EdgeTTSFallbackEngine
    from core.engines.translation.semantic_translator import SemanticTranslator
    settings, _ = production_identity_environment
    engine, translator = EdgeTTSFallbackEngine(), SemanticTranslator(provider)
    first = production_identity(engine, translator=translator)
    monkeypatch.setattr(settings, setting, "changed-model")
    second = production_identity(engine, translator=translator)
    assert first is not None and second is not None
    assert first.key != second.key


def test_manual_and_automatic_namespace_cannot_share_rewrite(production_identity_environment):
    from core.engines.tts.edge_fallback import EdgeTTSFallbackEngine
    from core.engines.translation.semantic_translator import SemanticTranslator
    engine = EdgeTTSFallbackEngine()
    manual = production_identity(engine)
    automatic = production_identity(engine, translator=SemanticTranslator("opencode"))
    assert manual.key != automatic.key
    assert production_identity(engine, translator=SemanticTranslator("muse")) is None


def test_effective_default_and_explicit_voice_invalidate_correctly(production_identity_environment):
    from core.engines.tts.edge_fallback import EdgeTTSFallbackEngine
    engine = EdgeTTSFallbackEngine("vi-VN-HoaiMyNeural")
    first = production_identity(engine)
    assert first.key == production_identity(engine, voice="vi-VN-HoaiMyNeural").key
    different = production_identity(engine, voice="vi-VN-NamMinhNeural")
    assert different.key != first.key
    engine.voice = "vi-VN-NamMinhNeural"
    assert production_identity(engine).key == different.key


def test_edge_alias_cannot_cache_default_audio_under_another_voice(production_identity_environment):
    from core.engines.tts.edge_fallback import EdgeTTSFallbackEngine
    engine = EdgeTTSFallbackEngine("vi-VN-HoaiMyNeural")
    default = production_identity(engine)
    # Edge ignores non-native aliases at this low-level boundary.
    alias = production_identity(engine, voice="edge:vi-VN-NamMinhNeural")
    native = production_identity(engine, voice="vi-VN-NamMinhNeural")
    assert alias.key == default.key
    assert alias.key != native.key


def test_unknown_translator_cannot_reuse_verified_production_result(production_identity_environment):
    from core.engines.tts.edge_fallback import EdgeTTSFallbackEngine
    from core.engines.translation.semantic_translator import SemanticTranslator
    class OtherTranslator(SemanticTranslator):
        pass
    engine = EdgeTTSFallbackEngine()
    assert production_identity(engine, translator=SimpleNamespace(provider="opencode")) is None
    assert production_identity(engine, translator=OtherTranslator("opencode")) is None


def test_all_enabled_review_adapters_are_in_implementation_identity(monkeypatch):
    before = cache._code_revision()
    original = cache._file_hash
    for name in ("gemini_client.py", "openrouter_client.py", "opencode_client.py"):
        monkeypatch.setattr(cache, "_file_hash", lambda path, target=name:
                            "changed" if path.name == target else original(path))
        assert cache._code_revision()[name] != before[name]


class PcmAligner:
    """Measure actual fixture PCM; these tests exercise cache wiring, not FFmpeg."""
    max_speed = 1.15

    @staticmethod
    def get_audio_duration(path):
        with wave.open(str(path)) as stream:
            return stream.getnframes() / stream.getframerate()

    @staticmethod
    def apply_atempo(source, output, ratio, fit_duration):
        assert ratio == 1.0
        shutil.copyfile(source, output)
        return ratio


def install_fixture_synthesizer(monkeypatch, engine, *, long_text=None):
    calls = []
    def synthesize(*, text, output_path, **kwargs):
        calls.append(text)
        return audio(output_path, 1.5 if text == long_text else .5)
    monkeypatch.setattr(engine, "synthesize", synthesize)
    monkeypatch.setattr(engine, "take_word_boundaries", lambda path: [])
    return calls


def test_second_natural_speech_call_reuses_audio_and_proof_without_synthesis_or_rewrite(production_identity_environment, tmp_path, monkeypatch):
    from core.engines.alignment.natural_speech import synthesize_natural_speech
    from core.engines.tts.edge_fallback import EdgeTTSFallbackEngine
    from core.engines.translation.semantic_translator import SemanticTranslator
    original, shorter = "Tôi vẫn chưa làm việc đó.", "Tôi chưa làm."
    engine, translator = EdgeTTSFallbackEngine(), SemanticTranslator("opencode")
    calls = install_fixture_synthesizer(monkeypatch, engine, long_text=original)
    reviewed = []
    proof = {"status": "verified", "text": shorter, "provider": "opencode", "reason": "Giữ phủ định."}
    def rewrite(*args, **kwargs):
        reviewed.append(args)
        return {"final_vi": shorter, "pacing_verification": proof}
    monkeypatch.setattr(translator, "rewrite_for_pacing", rewrite)
    options = dict(source="我还没做。", text=original, duration=1, engine=engine,
                   aligner=PcmAligner(), translator=translator, voice=engine.voice)
    first_output = tmp_path / "first.wav"
    first = synthesize_natural_speech(**options, output_path=first_output)
    assert calls == [original, shorter]
    assert len(reviewed) == 1
    assert first["pacing_verification"] == proof
    # A cache hit must be independent of the original producer instances and
    # original output file: simulate a later process with fresh adapter objects.
    fresh_engine, fresh_translator = EdgeTTSFallbackEngine(), SemanticTranslator("opencode")
    def forbidden(*args, **kwargs):
        pytest.fail("Exact valid cache hit unexpectedly called a provider")
    monkeypatch.setattr(fresh_engine, "synthesize", forbidden)
    monkeypatch.setattr(fresh_translator, "rewrite_for_pacing", forbidden)
    second_output = tmp_path / "second.wav"
    second = synthesize_natural_speech(**{**options, "engine": fresh_engine, "translator": fresh_translator},
                                       output_path=second_output)
    assert second == first
    assert first_output.read_bytes() == second_output.read_bytes()


def test_manual_edit_does_not_restore_automatic_shorter_text(production_identity_environment, tmp_path, monkeypatch):
    from core.engines.alignment.natural_speech import synthesize_natural_speech
    from core.engines.tts.edge_fallback import EdgeTTSFallbackEngine
    from core.engines.translation.semantic_translator import SemanticTranslator
    engine = EdgeTTSFallbackEngine()
    text = "Tôi vẫn chưa làm việc đó."
    calls = install_fixture_synthesizer(monkeypatch, engine)
    automatic = production_identity(engine, translator=SemanticTranslator("opencode"))
    assert cache.store_speech_cache(automatic, audio(tmp_path / "automatic.wav"), result())
    output = tmp_path / "manual.wav"
    manual = synthesize_natural_speech(source="我还没做。", text=text, duration=2,
                engine=engine, aligner=PcmAligner(), translator=None, context=[{"zh": "做了吗？"}],
                output_path=output)
    assert calls == [text]
    assert manual["text"] == text
    assert manual["pacing_verification"] is None
