"""Bounded cache of finalized speech, its timing and independent rewrite proof.

This is not a translation cache: a hit requires the exact source, original
translation, context, voice, model revisions and synthesis/review implementation.
Only callers that completed synthesis and semantic verification may publish.
"""
import asyncio
import hashlib
import io
import json
import math
import os
import re
import tempfile
import threading
import wave
import zipfile
from dataclasses import dataclass
from pathlib import Path


SCHEMA = 1
MAX_CACHE_BYTES = 64 * 1024 * 1024
MAX_RECORD_BYTES = 256 * 1024
_LOCK = threading.RLock()
_OWNED_NAME = re.compile(r"[0-9a-f]{64}\.speech\Z")
_SECRET_FIELDS = {"api_key", "apikey", "api-key", "authorization", "password",
                  "secret", "access_token", "refresh_token", "token", "credentials"}


@dataclass(frozen=True)
class SpeechCacheIdentity:
    key: str
    text: str
    duration: float
    max_speed: float


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _reject_secrets(value):
    if isinstance(value, dict):
        for name, item in value.items():
            if str(name).lower() in _SECRET_FIELDS:
                raise ValueError("Do not pass credentials into speech cache identities.")
            _reject_secrets(item)
    elif isinstance(value, (tuple, list)):
        for item in value:
            _reject_secrets(item)


def _file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _code_revision():
    directory = Path(__file__).parent
    files = (Path(__file__), directory / "natural_speech.py",
             directory / "timing_aligner.py", directory / "speech_timing.py",
             directory.parent / "translation" / "semantic_translator.py",
             directory.parent.parent / "translation_context.py",
             directory.parent / "translation" / "opencode_client.py",
             directory.parent / "translation" / "gemini_client.py",
             directory.parent / "translation" / "openrouter_client.py")
    return {path.name: _file_hash(path) for path in files}


def build_speech_cache_identity(*, source, text, duration, voice, engine,
                                engine_revision, provider, model, context=None,
                                ref_audio=None, max_speed=1.15):
    """Build a key from explicit, credential-free provider/model identifiers.

    ``engine_revision`` must cover the adapter, installed runtime and local model
    assets. It may be the revision returned by VoicePreviewManager._engine_revision.
    A missing reference file or unserializable identity is an error, never a hit.
    """
    duration, max_speed = float(duration), float(max_speed)
    if (not math.isfinite(duration) or duration <= 0
            or not math.isfinite(max_speed) or max_speed < 1
            or not isinstance(text, str) or not text.strip()
            or not engine or not engine_revision):
        raise ValueError("Incomplete speech cache identity.")
    inputs = {"schema": SCHEMA, "source": source, "text": text.strip(),
              "duration": duration, "max_speed": max_speed, "voice": voice,
              "engine": engine, "engine_revision": engine_revision,
              "provider": provider, "model": model, "context": context,
              "reference_audio": _file_hash(ref_audio) if ref_audio else None,
              "implementation": _code_revision()}
    _reject_secrets(inputs)
    encoded = _json(inputs)
    if len(encoded) > 1024 * 1024:
        raise ValueError("Speech cache identity is too large.")
    return SpeechCacheIdentity(hashlib.sha256(encoded).hexdigest(), text.strip(),
                               duration, max_speed)


def synthesis_cache_identity(*, source, text, duration, voice, engine, aligner,
                             translator=None, context=None, ref_audio=None):
    """Opt in only known production adapters with explicit model identities.

    An unknown/plugin adapter still synthesizes normally; it must not share a
    cache namespace merely because it reports the same friendly engine name.
    """
    from config import settings
    from core.voice_catalog import resolve_voice
    from core.voice_preview import VoicePreviewManager
    from core.engines.translation.semantic_translator import SemanticTranslator
    adapters = {
        ("core.engines.tts.edge_fallback", "EdgeTTSFallbackEngine"): "edge-tts",
        ("core.engines.tts.piper_engine", "PiperEngine"): "piper-tts",
        ("core.engines.tts.vieneu_engine", "VieNeuEngine"): "vieneu-tts",
    }
    kind = type(engine)
    engine_name = adapters.get((kind.__module__, kind.__name__))
    if engine_name is None:
        return None
    if translator is not None and type(translator) is not SemanticTranslator:
        return None
    provider = getattr(translator, "provider", None) if translator is not None else None
    models = {"opencode": settings.OPENCODE_MODEL, "gemini": settings.GEMINI_MODEL,
              "openrouter-free": settings.OPENROUTER_MODEL}
    if translator is not None and provider not in models:
        # Browser-selected/unknown model identity cannot prove an exact hit.
        return None
    if engine_name == "edge-tts":
        # Match the adapter's legacy default behavior exactly. A display alias
        # passed directly here is not a native Microsoft ID.
        actual_voice = voice if isinstance(voice, str) and voice.startswith("vi-VN-") else engine.voice
    else:
        actual_voice = voice
    _, chosen_voice = resolve_voice(actual_voice, engine_name)
    return build_speech_cache_identity(
        source=source, text=text, duration=duration, voice=chosen_voice, engine=engine_name,
        engine_revision=VoicePreviewManager._engine_revision(engine_name),
        provider=provider, model=models.get(provider), context=context,
        ref_audio=ref_audio, max_speed=aligner.max_speed)


def _cache_dir(directory):
    if directory is None:
        from config import settings
        directory = settings.WORKSPACE_DIR / "cache" / "speech"
    return Path(directory)


def _cancelled(cancel_check):
    if cancel_check and cancel_check():
        raise asyncio.CancelledError


def _validate_identity(identity):
    if not isinstance(identity, SpeechCacheIdentity) or not re.fullmatch(r"[0-9a-f]{64}", identity.key):
        raise ValueError("Invalid speech cache identity.")


def _valid_audio(data, identity, result):
    if not 44 < len(data) < MAX_CACHE_BYTES:
        return False
    try:
        with wave.open(io.BytesIO(data), "rb") as audio:
            channels, width, rate, count = (audio.getnchannels(), audio.getsampwidth(),
                                          audio.getframerate(), audio.getnframes())
            if (audio.getcomptype() != "NONE" or channels not in (1, 2) or width != 2
                    or not 8000 <= rate <= 96000 or count <= 0
                    or count / rate > identity.duration + .001
                    or abs(count / rate - result["tts_duration"] / result["speed_ratio"]) > .15):
                return False
            frames = audio.readframes(count)
            return len(frames) == count * channels * width and any(frames)
    except (EOFError, OSError, wave.Error):
        return False


def _validated_result(result, identity):
    """Reject truncated/invalid metadata and any rewrite without its exact proof."""
    fields = ("text", "tts_duration", "speed_ratio", "boundaries", "pacing_verification")
    if not isinstance(result, dict) or any(name not in result for name in fields):
        return None
    text = result.get("text")
    measured, ratio = result.get("tts_duration"), result.get("speed_ratio")
    if (not isinstance(text, str) or not text.strip()
            or isinstance(measured, bool) or not isinstance(measured, (float, int))
            or not math.isfinite(measured) or measured <= 0
            or isinstance(ratio, bool) or not isinstance(ratio, (float, int))
            or not math.isfinite(ratio) or not 1 <= ratio <= identity.max_speed
            or measured / ratio > identity.duration + .08):
        return None
    proof = result.get("pacing_verification")
    if text != identity.text or proof is not None:
        if (not isinstance(proof, dict) or proof.get("status") != "verified"
                or proof.get("text") != text):
            return None
    boundaries = result.get("boundaries")
    if not isinstance(boundaries, list):
        return None
    previous = -1.0
    for boundary in boundaries:
        if not isinstance(boundary, dict) or not isinstance(boundary.get("text"), str):
            return None
        start, end = boundary.get("start"), boundary.get("end")
        if (any(isinstance(v, bool) or not isinstance(v, (float, int))
                or not math.isfinite(v) for v in (start, end))
                or not 0 <= start <= end or start < previous or end > measured + 1):
            return None
        previous = start
    validated = {name: result[name] for name in fields}
    try:
        _reject_secrets(validated)
        serialized = _json(validated)
        if len(serialized) > MAX_RECORD_BYTES:
            return None
        return json.loads(serialized)
    except (TypeError, ValueError):
        return None


def _prune(directory, incoming, keep=None):
    entries = []
    for path in directory.glob("*.speech"):
        if not _OWNED_NAME.fullmatch(path.name) or path.is_symlink():
            continue
        try:
            stat = path.stat()
            entries.append((stat.st_mtime_ns, path, stat.st_size))
        except FileNotFoundError:
            continue
    total = sum(size for _, path, size in entries if path != keep)
    for _, path, size in sorted(entries):
        if total + incoming <= MAX_CACHE_BYTES:
            return True
        if path == keep:
            continue
        try:
            path.unlink()
            total -= size
        except OSError:
            continue
    return total + incoming <= MAX_CACHE_BYTES


def store_speech_cache(identity, audio_path, result, *, cancel_check=None, cache_dir=None):
    """Publish one atomic WAV+JSON record after a successful finalized synthesis.

    Invalid data or cache I/O failure returns False; cancellation propagates and
    cannot publish a new entry. Existing valid output/audio is never modified.
    """
    _validate_identity(identity)
    _cancelled(cancel_check)
    result = _validated_result(result, identity)
    if result is None:
        return False
    directory = _cache_dir(cache_dir)
    try:
        audio_path = Path(audio_path)
        if audio_path.stat().st_size >= MAX_CACHE_BYTES:
            return False
        data = audio_path.read_bytes()
        if not _valid_audio(data, identity, result):
            return False
        manifest = _json({"schema": SCHEMA, "identity": identity.key,
                          "audio_sha256": hashlib.sha256(data).hexdigest(), "result": result})
        record = io.BytesIO()
        with zipfile.ZipFile(record, "w", compression=zipfile.ZIP_STORED) as archive:
            archive.writestr("manifest.json", manifest)
            archive.writestr("speech.wav", data)
        payload = record.getvalue()
        if len(payload) > MAX_CACHE_BYTES:
            return False
        with _LOCK:
            _cancelled(cancel_check)
            directory.mkdir(parents=True, exist_ok=True)
            destination = directory / f"{identity.key}.speech"
            with tempfile.NamedTemporaryFile(prefix="speech-cache-", suffix=".tmp", dir=directory, delete=False) as stream:
                temporary = Path(stream.name)
                try:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                except BaseException:
                    stream.close()
                    temporary.unlink(missing_ok=True)
                    raise
            try:
                _cancelled(cancel_check)
                if not _prune(directory, len(payload), keep=destination):
                    return False
                _cancelled(cancel_check)
                temporary.replace(destination)
            finally:
                temporary.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def load_speech_cache(identity, output_path, *, cancel_check=None, cache_dir=None):
    """Restore a validated exact hit atomically; misses never touch output_path."""
    _validate_identity(identity)
    _cancelled(cancel_check)
    path = _cache_dir(cache_dir) / f"{identity.key}.speech"
    try:
        with _LOCK:
            if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_CACHE_BYTES:
                return None
            with zipfile.ZipFile(path) as archive:
                if sorted(archive.namelist()) != ["manifest.json", "speech.wav"]:
                    return None
                if any(item.compress_type != zipfile.ZIP_STORED or item.flag_bits & 1
                       for item in archive.infolist()):
                    return None
                if (archive.getinfo("manifest.json").file_size > MAX_RECORD_BYTES
                        or archive.getinfo("speech.wav").file_size >= MAX_CACHE_BYTES):
                    return None
                manifest = json.loads(archive.read("manifest.json"))
                data = archive.read("speech.wav")
            if (not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA
                    or manifest.get("identity") != identity.key
                    or manifest.get("audio_sha256") != hashlib.sha256(data).hexdigest()):
                return None
            result = _validated_result(manifest.get("result"), identity)
            if result is None or not _valid_audio(data, identity, result):
                return None
            _cancelled(cancel_check)
            output = Path(output_path)
            output.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(prefix="speech-cache-", suffix=".tmp", dir=output.parent, delete=False) as stream:
                temporary = Path(stream.name)
                try:
                    stream.write(data)
                except BaseException:
                    stream.close()
                    temporary.unlink(missing_ok=True)
                    raise
            try:
                _cancelled(cancel_check)
                temporary.replace(output)
            finally:
                temporary.unlink(missing_ok=True)
            try:
                path.touch()
            except OSError:
                pass
            return result
    except (OSError, EOFError, ValueError, KeyError, zipfile.BadZipFile):
        return None
