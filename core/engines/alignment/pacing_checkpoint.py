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


def _root(*, legacy=False):
    from config import settings
    from core.runtime_context import current_execution_context
    run_id = current_execution_context().run_id
    if not legacy and run_id != "unscoped":
        # Failed wording is a recovery checkpoint, not an expendable global
        # LRU entry. The existing project cache lifecycle owns its deletion.
        return Path(settings.WORKSPACE_DIR) / "cache" / run_id / "pacing_history"
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
    observations = feedback.get("measured_candidates", [])
    if not isinstance(observations, list) or len(observations) > 16:
        raise ValueError("Invalid feedback measurements")
    for row in observations:
        if (not isinstance(row, dict) or not isinstance(row.get("text"), str)
                or not 0 < len(row["text"]) <= 4000):
            raise ValueError("Invalid feedback wording")
        if "measured_seconds" in row and (type(row["measured_seconds"]) not in (int, float)
                or not math.isfinite(row["measured_seconds"]) or row["measured_seconds"] <= 0):
            raise ValueError("Invalid feedback duration")
    return deepcopy(data)


def load(key, *, cancel_check=None):
    _cancel(cancel_check)
    if not isinstance(key, str) or not re.fullmatch(r"[a-f0-9]{64}", key):
        return {"measured": [], "feedback": {}}
    try:
        path = _root() / (key + ".json")
        if not path.exists() and _root() != _root(legacy=True):
            path = _root(legacy=True) / (key + ".json")
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
            if root == _root(legacy=True):
                # Only unscoped utility calls use the disposable bounded cache.
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


class NeighborPacingHistory:
    """History for an external bounded proposal loop, never a loop of its own.

    Callers retain proposal limits, proof validation, measured synthesis and
    atomic timeline publication. This object persists observations only.
    """
    def __init__(self, *, scope, feedback, cancel_check=None, **identity_options):
        self.cancel_check = cancel_check
        self.key = None
        try:
            key = identity(**identity_options)
            _reject_secrets(scope)
            if key is not None:
                self.key = hashlib.sha256(json.dumps(["neighbor-v1", key, scope],
                    sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
        except (OSError, ValueError, TypeError, KeyError):
            logging.getLogger("pipeline").warning("NEIGHBOR_HISTORY_IDENTITY_UNAVAILABLE")
        saved = load(self.key, cancel_check=cancel_check)
        self.measured = saved["measured"]
        # Current timing is authoritative; old rejection detail remains useful.
        self.feedback = {**saved["feedback"], **deepcopy(feedback)}
        observed = {row["text"]: {"text": row["text"], "measured_seconds": row["measured_seconds"]}
                    for row in self.measured}
        for row in [*saved["feedback"].get("measured_candidates", []), *feedback.get("measured_candidates", [])]:
            observed[row["text"]] = deepcopy(row)
        self.feedback["measured_candidates"] = list(observed.values())[-16:]

    def reject_duplicate(self, candidate):
        from core.engines.translation.semantic_translator import pacing_candidate_key, PacingReviewRejected
        key = pacing_candidate_key(candidate)
        tried = [row["text"] for row in self.feedback.get("measured_candidates", [])]
        tried += [row["text"] for row in self.feedback.get("rejected_candidates", [])]
        if key in {pacing_candidate_key(text) for text in tried}:
            raise PacingReviewRejected("AI lặp lời đã kiểm tra trong cùng điều kiện.",
                candidate=candidate, reason="Lời đã bị từ chối hoặc đo vượt thời lượng.", code="duplicate")

    def rejected(self, error):
        from core.engines.translation.semantic_translator import accumulate_pacing_rejection
        self.feedback = accumulate_pacing_rejection(self.feedback, error)
        store(self.key, self.measured, self.feedback, cancel_check=self.cancel_check)

    def timing_failed(self, *, text, proof, error, max_speed):
        candidate = getattr(error, "measured_candidate", None)
        seconds = candidate.get("measured_seconds") if isinstance(candidate, dict) else None
        if type(seconds) not in (float, int) or not math.isfinite(seconds) or seconds <= 0:
            required = getattr(error, "required_dub_duration", None)
            seconds = ((required - .012) * max_speed if type(required) in (float, int)
                       and math.isfinite(required) and required > .012 else None)
        self.feedback = deepcopy(self.feedback)
        self.feedback["measured_candidates"] = [*self.feedback.get("measured_candidates", []),
            {"text": text, **({"measured_seconds": seconds} if seconds is not None else {})}][-16:]
        if seconds is not None:
            self.measured = [*self.measured, {"text": text, "measured_seconds": seconds,
                                            "pacing_verification": deepcopy(proof)}][-16:]
        store(self.key, self.measured, self.feedback, cancel_check=self.cancel_check)
        return seconds
