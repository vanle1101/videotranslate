"""Exact-input pacing history; observations never approve audio or translation."""
import asyncio
from copy import deepcopy
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import re
import tempfile
import threading

from core.engines.alignment.speech_cache import synthesis_cache_identity, _reject_secrets

VERSION = 1
_LOCK = threading.RLock()
_NAME = re.compile(r"[a-f0-9]{64}\.json\Z")
try:
    _REVISION = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
except OSError:
    _REVISION = None


def identity(*, translator, max_duration, max_duration_limit, allow_bidirectional_reflow,
             source_language, max_pacing_proposals, allow_review_draft, **kwargs):
    if translator is None or _REVISION is None:
        return None
    base = synthesis_cache_identity(translator=translator, **kwargs)
    if base is None:
        return None
    policy = [VERSION, _REVISION, base.key, max_duration, max_duration_limit,
              allow_bidirectional_reflow, source_language, max_pacing_proposals,
              allow_review_draft, getattr(kwargs["aligner"], "min_speed", None)]
    return hashlib.sha256(json.dumps(policy, allow_nan=False).encode()).hexdigest()


def _root():
    from config import settings
    return Path(settings.WORKSPACE_DIR) / "cache" / "pacing_history"


def _cancel(check):
    if check and check():
        raise asyncio.CancelledError


def _checked(data):
    if not isinstance(data, dict) or set(data) != {"measured", "feedback"}:
        raise ValueError("Invalid pacing history")
    _reject_secrets(data)
    rows, feedback = data["measured"], data["feedback"]
    if not isinstance(rows, list) or len(rows) > 16 or not isinstance(feedback, dict):
        raise ValueError("Invalid pacing history size")
    for row in rows:
        if (not isinstance(row, dict) or not isinstance(row.get("text"), str)
                or not 0 < len(row["text"]) <= 4000
                or type(row.get("measured_seconds")) not in (int, float)
                or not math.isfinite(row["measured_seconds"]) or row["measured_seconds"] <= 0):
            raise ValueError("Invalid measured candidate")
        proof = row.get("pacing_verification")
        if proof is not None and (not isinstance(proof, dict) or proof.get("text") != row["text"]
                or not (proof.get("status") == "verified" or proof.get("status") == "draft"
                        and proof.get("semantic_verified") is True)):
            raise ValueError("Invalid historical proof")
    rejected = feedback.get("rejected_candidates", [])
    if not isinstance(rejected, list) or len(rejected) > 12:
        raise ValueError("Invalid rejected candidates")
    for row in rejected:
        if (not isinstance(row, dict) or not isinstance(row.get("text"), str)
                or len(row["text"]) > 2000 or not isinstance(row.get("reason"), str)
                or len(row["reason"]) > 1000 or not isinstance(row.get("code"), str)):
            raise ValueError("Invalid rejection")
    return deepcopy(data)


def load(key, *, cancel_check=None):
    _cancel(cancel_check)
    if not isinstance(key, str) or not re.fullmatch(r"[a-f0-9]{64}", key):
        return {"measured": [], "feedback": {}}
    try:
        path = _root() / (key + ".json")
        if path.is_symlink() or path.stat().st_size > 256 * 1024:
            raise ValueError("Invalid history file")
        record = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(record, dict) or record.get("version") != VERSION or record.get("key") != key:
            raise ValueError("Stale history")
        encoded = json.dumps(record["data"], sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
        if hashlib.sha256(encoded).hexdigest() != record.get("sha256"):
            raise ValueError("Corrupt history")
        return _checked(record["data"])
    except (OSError, ValueError, TypeError, KeyError):
        return {"measured": [], "feedback": {}}


def store(key, measured, feedback, *, cancel_check=None):
    _cancel(cancel_check)
    if not isinstance(key, str) or not re.fullmatch(r"[a-f0-9]{64}", key):
        return
    temporary = None
    try:
        data = _checked({"measured": measured[-16:], "feedback": feedback})
        encoded = json.dumps(data, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
        record = json.dumps({"version": VERSION, "key": key, "data": data,
            "sha256": hashlib.sha256(encoded).hexdigest()}, ensure_ascii=False, allow_nan=False)
        if len(record.encode()) > 256 * 1024:
            raise ValueError("History exceeds bound")
        with _LOCK:
            root = _root()
            root.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=root,
                    prefix="pacing_", suffix=".tmp", delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(record)
                handle.flush()
                os.fsync(handle.fileno())
            _cancel(cancel_check)
            os.replace(temporary, root / (key + ".json"))
            owned = sorted((p for p in root.iterdir() if _NAME.fullmatch(p.name) and not p.is_symlink()),
                           key=lambda p: p.stat().st_mtime_ns, reverse=True)
            for path in owned[64:]:
                path.unlink(missing_ok=True)
    except (OSError, ValueError, TypeError, KeyError):
        logging.getLogger("pipeline").warning("PACING_HISTORY_UNAVAILABLE")
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                logging.getLogger("pipeline").warning("PACING_HISTORY_CLEANUP_FAILED")
