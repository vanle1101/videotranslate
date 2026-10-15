"""Bounded contextual ASR evidence; surrounding speech never owns target text."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re
import unicodedata

from config import settings
from core.chinese_text import comparable_audio_chinese

REVISION = 1
PADDING_SECONDS = 1.0
MAX_WINDOW_SECONDS = 12.0
_CODE_FILES = (Path(__file__), Path(__file__).parent / "source_language.py",
               Path(__file__).parent / "engines/asr/review_evidence_worker.py")
_LOADED_CODE = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in _CODE_FILES}


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def window(row):
    start, end = row.get("start"), row.get("end")
    if not finite(start) or not finite(end) or not 0 <= start < end:
        return None
    left, right = row.get("source_asr_start"), row.get("source_asr_end")
    if (finite(left) and finite(right) and 0 <= left <= start < end <= right
            and right - left <= MAX_WINDOW_SECONDS and (left < start or right > end)):
        return {"start": left, "end": right}
    left, right = max(0., start - PADDING_SECONDS), end + PADDING_SECONDS
    return {"start": left, "end": right} if right - left <= MAX_WINDOW_SECONDS else None


def exact_readings(row):
    return {item.get("engine"): item.get("text_zh") for item in
            (row.get("verification") or {}).get("audio_evidence", []) if isinstance(item, dict)}


def disagreement(readings):
    a, b = (readings.get(engine) for engine in ("sensevoice", "faster-whisper-small"))
    return (isinstance(a, str) and isinstance(b, str) and bool(comparable_audio_chinese(a))
            and comparable_audio_chinese(a) != comparable_audio_chinese(b))


def _file_identity(path):
    info = path.stat()
    return [str(path.resolve()), info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_ino]


def identity(row, source, language, readings):
    """No model loading: durable attempts track source, assets and crop strategy."""
    root = settings.BASE_DIR / "workspace" / "models"
    assets = {}
    for model, names in (("faster-whisper-small", ("model.bin", "config.json", "tokenizer.json", "vocabulary.json", "vocabulary.txt")),
                         ("sensevoice_onnx", ("model.int8.onnx", "tokens.txt"))):
        for name in names:
            path = root / model / name
            assets[f"{model}/{name}"] = _file_identity(path) if path.is_file() else None
    value = {"revision": REVISION, "code": _LOADED_CODE, "source": _file_identity(Path(source)), "assets": assets,
             "source_language": language, "target": {key: row.get(key) for key in
                 ("id", "start", "end", "source_asr_row_id", "source_asr_start", "source_asr_end",
                  "source_piece_index", "source_piece_count")}, "window": window(row), "exact": readings,
             "decode": {"word_timestamps": True, "beam_size": 5, "vad_filter": False,
                        "condition_on_previous_text": False, "compute_type": "int8"}}
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False).encode("utf-8")).hexdigest()


def pending(row, source, language):
    audit = row.get("verification") or {}
    if (audit.get("status") != "unresolved" or audit.get("source_supported") is True
            or not window(row) or not disagreement(exact_readings(row))):
        return False
    saved = audit.get("contextual_audio")
    try:
        return not (isinstance(saved, dict) and clean_proof(saved).get("input_hash") ==
                    identity(row, source, language, exact_readings(row)))
    except ValueError:
        return True
    except OSError:
        return False


def _content(text):
    return "".join(c for c in unicodedata.normalize("NFC", text) if c.isalnum()).casefold()


def attribution(proof):
    """Return target-only text or an explicit negative; no fuzzy matching."""
    words = proof["words"]
    if not proof["words_complete"] or not words:
        return "", "missing_word_timing"
    if _content("".join(word["word"] for word in words)) != _content(proof["full_text"]):
        return "", "incomplete_word_coverage"
    previous = proof["decode_start"]
    selected = []
    for word in words:
        left, right = word["start"], word["end"]
        if left < previous or right < left or left < proof["decode_start"] or right > proof["decode_end"]:
            return "", "invalid_word_timing"
        previous = right
        if not _content(word["word"]):
            continue
        if right == left:
            return "", "zero_length_word"
        if right <= proof["target_start"] or left >= proof["target_end"]:
            continue
        if left < proof["target_start"] or right > proof["target_end"]:
            return "", "boundary_straddle"
        selected.append(word["word"])
    text = "".join(selected).strip()
    if not comparable_audio_chinese(text):
        return "", "no_attributed_words"
    if comparable_audio_chinese(text) != comparable_audio_chinese(proof["sensevoice_text"]):
        return text, "independent_disagreement"
    return text, "corroborated"


def clean_proof(value):
    """Typed durable provenance, including the real decoded region and words."""
    if not isinstance(value, dict) or type(value.get("revision")) is not int or value["revision"] != REVISION:
        raise ValueError("Invalid contextual audio revision")
    if not isinstance(value.get("input_hash"), str) or not re.fullmatch(r"[a-f0-9]{64}", value["input_hash"]):
        raise ValueError("Invalid contextual audio identity")
    for name in ("target_start", "target_end", "decode_start", "decode_end", "requested_start", "requested_end"):
        if not finite(value.get(name)):
            raise ValueError("Invalid contextual audio interval")
    if not (0 <= value["requested_start"] == value["decode_start"] <= value["target_start"]
            < value["target_end"] <= value["decode_end"] <= value["requested_end"] + 1 / 16000
            and value["requested_end"] - value["requested_start"] <= MAX_WINDOW_SECONDS):
        raise ValueError("Contextual audio does not contain target")
    for name in ("full_text", "sensevoice_text", "exact_whisper_text", "target_text", "reason"):
        if not isinstance(value.get(name), str) or len(value[name]) > 20000 or "\x00" in value[name]:
            raise ValueError("Invalid contextual audio text")
    if type(value.get("words_complete")) is not bool or not isinstance(value.get("words"), list) or len(value["words"]) > 2000:
        raise ValueError("Invalid contextual audio words")
    words = []
    for word in value["words"]:
        if (not isinstance(word, dict) or not isinstance(word.get("word"), str)
                or len(word["word"]) > 2000 or "\x00" in word["word"]
                or not finite(word.get("start")) or not finite(word.get("end"))):
            raise ValueError("Invalid contextual audio word")
        words.append({key: word[key] for key in ("word", "start", "end")})
    result = {key: deepcopy(value[key]) for key in ("revision", "input_hash", "target_start", "target_end",
        "decode_start", "decode_end", "requested_start", "requested_end", "full_text", "sensevoice_text",
        "exact_whisper_text", "target_text", "reason", "words_complete")}
    result["words"] = words
    text, reason = attribution(result)
    if result["target_text"] != text or result["reason"] != reason:
        raise ValueError("Contextual attribution verdict does not match evidence")
    return result


def recover(source, row, language, readings, *, checkpoint=None, cancel_check=None):
    """One serialized disposable Whisper worker; failures never become negatives."""
    from core.engines.asr.native_process import (
        ASRProcessError, check_cancelled, positive_deadline, native_worker_slot, iter_native_records)
    check_cancelled(cancel_check)
    decode = window(row)
    if decode is None or not disagreement(readings):
        return None
    key = identity(row, source, language, readings)
    stage = {"kind": "contextual_audio", "input_hash": key}
    saved = (row.get("verification") or {}).get("contextual_audio")
    if checkpoint is not None and getattr(checkpoint, "force", False):
        saved = None
    elif isinstance(saved, dict) and saved.get("input_hash") == key:
        try:
            return clean_proof(saved)
        except ValueError:
            pass
    cached = checkpoint.load(stage) if checkpoint else None
    if isinstance(cached, dict) and cached.get("input_hash") == key:
        try:
            return clean_proof(cached)
        except ValueError:
            pass
    request = {"video_path": str(Path(source).resolve()), "engine": "faster-whisper-small",
        "source_language": language, "contextual": True,
        "rows": [{"id": row["id"], **decode}],
        "whisper_model_path": str(settings.BASE_DIR / "workspace" / "models" / "faster-whisper-small")}
    with native_worker_slot(cancel_check):
        records = iter_native_records("core.engines.asr.review_evidence_worker", request,
            timeout=positive_deadline("ASR_REVIEW_PROCESS_TIMEOUT", 600), cancel_check=cancel_check,
            prefix="review-asr-worker-", threads=4)
        evidence, completed = None, False
        try:
            for record in records:
                if completed or record.get("engine") != "faster-whisper-small":
                    raise ASRProcessError("Invalid contextual audio engine/state")
                if record.get("event") == "completed":
                    if type(record.get("rows")) is not int or record["rows"] != 1 or evidence is None:
                        raise ASRProcessError("Incomplete contextual audio")
                    completed = True
                elif (record.get("event") == "evidence" and type(record.get("id")) is int
                      and record["id"] == row["id"] and evidence is None):
                    evidence = record
                else:
                    raise ASRProcessError("Invalid contextual audio row")
            if not completed:
                raise ASRProcessError("Missing contextual audio completion")
        finally:
            records.close()
    check_cancelled(cancel_check)
    if identity(row, source, language, readings) != key:
        raise ASRProcessError("Source or model assets changed during contextual audio recovery")
    try:
        proof = {"revision": REVISION, "input_hash": key, "target_start": row["start"], "target_end": row["end"],
            "requested_start": decode["start"], "requested_end": decode["end"],
            "decode_start": evidence["decode_start"], "decode_end": evidence["decode_end"],
            "full_text": evidence["text"], "words": evidence["words"], "words_complete": evidence["words_complete"],
            "sensevoice_text": readings["sensevoice"], "exact_whisper_text": readings["faster-whisper-small"]}
        # Validate structure before evaluating numeric attribution.
        proof.update(target_text="", reason="")
        proof["target_text"], proof["reason"] = attribution(proof)
        proof = clean_proof(proof)
    except (KeyError, TypeError, ValueError) as error:
        raise ASRProcessError("Invalid contextual audio provenance") from error
    if checkpoint:
        checkpoint.store(stage, proof)
    return proof
