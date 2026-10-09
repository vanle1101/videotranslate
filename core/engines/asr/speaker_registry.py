"""Atomic, source-scoped provisional audio identities and diarization checkpoints.

Every automatic identity remains a hypothesis. Similarity thresholds are an
explicit matching policy, not calibrated accuracy. No character roles exist in
this registry, and a voice confirmation does not establish an addressee.
"""
from __future__ import annotations

from copy import deepcopy
from contextlib import contextmanager
from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path
import sqlite3

VERSION = 1
MAX_SPEAKERS = 256
MAX_INTERVALS = 4096
MAX_RECORD_BYTES = 8 * 1024 * 1024


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                      separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class MatchingPolicy:
    # These are heuristic admission limits. They have NOT been calibrated as
    # correctness probabilities against this user's videos.
    min_cosine: float = .65
    min_margin: float = .10
    max_references: int = 3
    minimum_word_coverage: float = .80

    def validate(self):
        if (not _finite(self.min_cosine) or not -1 < self.min_cosine < 1
                or not _finite(self.min_margin) or not 0 < self.min_margin < 2
                or type(self.max_references) is not int or not 1 <= self.max_references <= 8
                or not _finite(self.minimum_word_coverage) or not .5 < self.minimum_word_coverage <= 1):
            raise ValueError("Invalid provisional speaker matching policy")
        return self


def _vector(values):
    if (not isinstance(values, list) or not 16 <= len(values) <= 2048
            or not all(_finite(value) for value in values)):
        raise ValueError("Invalid speaker embedding vector")
    norm = math.hypot(*values)
    if not math.isfinite(norm) or norm < 1e-8:
        raise ValueError("Empty speaker embedding vector")
    return [value / norm for value in values]


def _cosine(left, right):
    if len(left) != len(right):
        raise ValueError("Speaker embedding dimensions changed")
    return max(-1., min(1., sum(a * b for a, b in zip(left, right))))


def _valid_silhouette(interval):
    value = interval.get("silhouette")
    if value is not None and (not _finite(value) or not -1 <= value <= 1):
        return False
    if "raw_silhouette" in interval or "silhouette_status" in interval:
        raw = interval.get("raw_silhouette")
        return (_finite(raw) and ((raw == -2 and value is None and interval.get("silhouette_status") == "unavailable")
                or (raw == value and interval.get("silhouette_status") == "available")))
    return True


def validate_observation(value):
    if (not isinstance(value, dict) or not _finite(value.get("start")) or not _finite(value.get("end"))
            or not 0 <= value["start"] < value["end"] or value["end"] - value["start"] > 90.1
            or not isinstance(value.get("intervals"), list) or len(value["intervals"]) > MAX_INTERVALS
            or not isinstance(value.get("embeddings"), list) or len(value["embeddings"]) > MAX_SPEAKERS * 8
            or value.get("calibrated") is not False or value.get("confidence_kind") != "silhouette"):
        raise ValueError("Invalid bounded diarization observation")
    voices = set()
    for interval in value["intervals"]:
        if (not isinstance(interval, dict) or type(interval.get("local_speaker")) is not int
                or not 0 <= interval["local_speaker"] < MAX_SPEAKERS
                or not _finite(interval.get("start")) or not _finite(interval.get("end"))
                or not value["start"] <= interval["start"] < interval["end"] <= value["end"] + .001
                or not _valid_silhouette(interval)):
            raise ValueError("Invalid diarization interval or raw silhouette")
        voices.add(interval["local_speaker"])
    dimension = None
    for item in value["embeddings"]:
        if (not isinstance(item, dict) or type(item.get("local_speaker")) is not int
                or item["local_speaker"] not in voices
                or not _finite(item.get("start")) or not _finite(item.get("end"))
                or not value["start"] <= item["start"] < item["end"] <= value["end"] + .001):
            raise ValueError("Speaker embedding lacks a valid source interval")
        normalized = _vector(item.get("embedding"))
        if dimension is not None and dimension != len(normalized):
            raise ValueError("Inconsistent speaker embedding dimensions")
        dimension = len(normalized)
        source_spans = item.get("source_spans", [{"start": item["start"], "end": item["end"]}])
        if not isinstance(source_spans, list) or not 1 <= len(source_spans) <= MAX_INTERVALS:
            raise ValueError("Speaker embedding source spans are missing")
        previous_end, measured = item["start"], 0.
        for span in source_spans:
            if (not isinstance(span, dict) or not _finite(span.get("start")) or not _finite(span.get("end"))
                    or not previous_end <= span["start"] < span["end"] <= item["end"] + .001):
                raise ValueError("Speaker embedding source spans overlap or exceed their measured bounds")
            previous_end = span["end"]
            measured += span["end"] - span["start"]
            # Only exclusive audio may enter a voice prototype.
            if any(other["local_speaker"] != item["local_speaker"]
                   and other["start"] < span["end"] and other["end"] > span["start"]
                   for other in value["intervals"]):
                raise ValueError("Overlapping voices cannot provide a speaker embedding")
            if not any(other["local_speaker"] == item["local_speaker"]
                       and other["start"] <= span["start"] + .001 and other["end"] >= span["end"] - .001
                       for other in value["intervals"]):
                raise ValueError("Embedding extends beyond detected source speech")
        if "audio_seconds" in item and (not _finite(item["audio_seconds"]) or abs(item["audio_seconds"] - measured) > .005):
            raise ValueError("Speaker embedding measured audio duration does not match source spans")
    if len(json.dumps(value, allow_nan=False).encode()) > MAX_RECORD_BYTES:
        raise ValueError("Diarization observation exceeds its file-backed limit")
    return value


class SpeakerRegistry:
    """A small durable registry; no whole-video PCM or all-pairs clustering."""

    def __init__(self, path, source_id, model_identity, policy=None, *, media_identity=None):
        self.path = Path(path)
        if not isinstance(source_id, str) or not source_id.strip() or len(source_id) > 200:
            raise ValueError("Diarization requires an immutable source identity")
        self.source_id = source_id
        self.media_identity = deepcopy(media_identity)
        if media_identity is not None and (not isinstance(media_identity, dict) or not media_identity):
            raise ValueError("Speaker registry needs a valid media identity")
        self.policy = (policy or MatchingPolicy()).validate()
        self.model_identity = deepcopy(model_identity)
        if not isinstance(model_identity, dict) or not model_identity:
            raise ValueError("Diarization requires actual model identities")
        self.configuration_hash = digest({"version": VERSION, "models": model_identity,
                                          "matching": asdict(self.policy)})
        self.scope_id = "audio-" + digest({"source": source_id, "media": media_identity,
                                          "configuration": self.configuration_hash})[:32]
        if self.path.is_symlink():
            raise ValueError("Speaker registry cannot be a symlink")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS speakers (id TEXT PRIMARY KEY, ordinal INTEGER UNIQUE, prototypes TEXT NOT NULL, first_start REAL NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS chunks (key TEXT PRIMARY KEY, scan_start REAL NOT NULL, scan_end REAL NOT NULL, result TEXT NOT NULL)")
            db.execute("CREATE INDEX IF NOT EXISTS chunks_timeline ON chunks(scan_start,scan_end)")
            metadata = dict(db.execute("SELECT key,value FROM metadata"))
            expected = {"source_id": source_id, "configuration_hash": self.configuration_hash,
                        "scope_id": self.scope_id, "version": str(VERSION), "media_identity": digest(media_identity)}
            if metadata and metadata != expected:
                raise ValueError("Speaker source/model/policy changed; use a separate registry without overwriting the old one")
            if not metadata:
                db.executemany("INSERT INTO metadata VALUES (?,?)", expected.items())

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        try:
            db.execute("PRAGMA journal_mode=DELETE")
            db.execute("PRAGMA synchronous=FULL")
            with db:
                yield db
        finally:
            db.close()

    def cached(self, request_key):
        with self._connect() as db:
            row = db.execute("SELECT result FROM chunks WHERE key=?", (request_key,)).fetchone()
        if row is None:
            return None
        return self._validated_checkpoint(row[0], request_key)

    def _validated_checkpoint(self, serialized, request_key):
        if not isinstance(serialized, str) or len(serialized.encode()) > MAX_RECORD_BYTES:
            raise ValueError("Diarization checkpoint is oversized")
        value = json.loads(serialized)
        if (not isinstance(value, dict) or value.get("scope_id") != self.scope_id
                or value.get("configuration_hash") != self.configuration_hash
                or value.get("source_id") != self.source_id or value.get("request_key") != request_key
                or value.get("model") != self.model_identity or value.get("calibrated") is not False
                or value.get("confidence_kind") != "silhouette_and_cosine"
                or not isinstance(value.get("intervals"), list) or len(value["intervals"]) > MAX_INTERVALS
                or any(not _finite(value.get(key)) for key in ("start", "end", "scan_start", "scan_end"))
                or not 0 <= value["scan_start"] <= value["start"] < value["end"] <= value["scan_end"]
                or value["scan_end"] - value["scan_start"] > 90.1
                or type(value.get("review_required")) is not bool):
            raise ValueError("Diarization checkpoint ownership mismatch")
        for item in value["intervals"]:
            match = item.get("match") if isinstance(item, dict) else None
            if (not isinstance(item, dict) or not isinstance(item.get("speaker_id"), str)
                    or not item["speaker_id"].startswith("voice-") or type(item.get("overlap")) is not bool
                    or not _finite(item.get("start")) or not _finite(item.get("end"))
                    or not value["start"] <= item["start"] < item["end"] <= value["end"]
                    or type(item.get("local_speaker")) is not int or not 0 <= item["local_speaker"] < MAX_SPEAKERS
                    or not _valid_silhouette(item)
                    or not isinstance(match, dict) or match.get("status") != "PROVISIONAL"
                    or match.get("calibrated") is not False or match.get("review_required") is not True
                    or not isinstance(match.get("reason"), str) or not match["reason"]
                    or not isinstance(match.get("candidate_ids"), list) or len(match["candidate_ids"]) > 3
                    or not all(isinstance(sid, str) and sid.startswith("voice-") for sid in match["candidate_ids"])
                    or any(match.get(key) is not None and (not _finite(match[key]) or not lower <= match[key] <= upper)
                           for key, lower, upper in (("cosine", -1, 1), ("margin", 0, 2), ("source_overlap_seconds", 0, 90)))):
                raise ValueError("Diarization checkpoint contains invalid intervals or matching evidence")
            if "confidence" in item or "confidence" in match or "verified" in match:
                raise ValueError("Provisional diarization checkpoint cannot claim correctness confidence")
        for item in value["intervals"]:
            actual_overlap = any(other["local_speaker"] != item["local_speaker"]
                and other["start"] < item["end"] and other["end"] > item["start"] for other in value["intervals"])
            if item["overlap"] != actual_overlap:
                raise ValueError("Diarization checkpoint has inconsistent overlap evidence")
        return value

    def record(self, request_key, observation, owned_start, owned_end):
        """Persist mapping and checkpoint in one transaction, idempotently."""
        validate_observation(observation)
        if (not isinstance(request_key, str) or not request_key or len(request_key) > 200
                or not _finite(owned_start) or not _finite(owned_end)
                or not observation["start"] <= owned_start < owned_end <= observation["end"] + .001):
            raise ValueError("Invalid diarization checkpoint ownership")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            cached = db.execute("SELECT result FROM chunks WHERE key=?", (request_key,)).fetchone()
            if cached:
                return self._validated_checkpoint(cached[0], request_key)
            # Only a few overlapping bounded chunks are inspected; historic
            # checkpoints stay on disk, regardless of whole-video duration.
            previous = [self._validated_checkpoint(serialized, key)
                for key, serialized in db.execute("SELECT key,result FROM chunks WHERE scan_start<? AND scan_end>? ORDER BY scan_start DESC LIMIT 4",
                    (observation["end"], observation["start"]))]
            references = {sid: json.loads(prototypes) for sid, prototypes in
                          db.execute("SELECT id,prototypes FROM speakers ORDER BY ordinal")}
            if len(references) > MAX_SPEAKERS:
                raise ValueError("Speaker registry exceeds its bounded size")
            ordinal = db.execute("SELECT COALESCE(MAX(ordinal),0) FROM speakers").fetchone()[0]
            mapping, decisions, occupied = {}, {}, set()
            voices = sorted({row["local_speaker"] for row in observation["intervals"]},
                            key=lambda voice: min(row["start"] for row in observation["intervals"] if row["local_speaker"] == voice))
            for voice in voices:
                samples = [item for item in observation["embeddings"] if item["local_speaker"] == voice]
                vectors = [_vector(item["embedding"]) for item in samples]
                ranked = []
                if vectors:
                    representative = _vector([sum(vector[index] for vector in vectors)
                                              for index in range(len(vectors[0]))])
                    ranked = sorted(((max(_cosine(representative, _vector(ref["embedding"])) for ref in prototypes), sid)
                                     for sid, prototypes in references.items() if prototypes), reverse=True)
                best = ranked[0] if ranked else None
                second = ranked[1][0] if len(ranked) > 1 else None
                margin = best[0] - second if best and second is not None else None
                # Repeated physical audio in the source overlap can link a
                # short utterance with no safe standalone embedding. This is
                # still provisional, not a calibrated verification.
                overlap_votes = {}
                for old in previous:
                    for new_span in observation["intervals"]:
                        if new_span["local_speaker"] != voice:
                            continue
                        for old_span in old["intervals"]:
                            if old_span["overlap"]:
                                continue
                            left, right = max(new_span["start"], old_span["start"]), min(new_span["end"], old_span["end"])
                            if right > left and not any(other["local_speaker"] != voice and other["start"] < right and other["end"] > left for other in observation["intervals"]):
                                overlap_votes.setdefault(old_span["speaker_id"], []).append((left, right))
                def union_duration(spans):
                    total, right = 0., -1.
                    for left, end in sorted(spans):
                        total += max(0., end - max(left, right))
                        right = max(right, end)
                    return total
                overlap_ranked = sorted(((union_duration(spans), sid) for sid, spans in overlap_votes.items()), reverse=True)
                overlap_best = overlap_ranked[0] if overlap_ranked else None
                overlap_total = sum(item[0] for item in overlap_ranked)
                overlap_id = (overlap_best[1] if overlap_best and overlap_best[0] >= .3
                              and overlap_best[0] / overlap_total >= .9 and overlap_best[1] not in occupied else None)
                accepted = bool(best and best[0] >= self.policy.min_cosine
                                and (second is None or margin >= self.policy.min_margin)
                                and best[1] not in occupied)
                if overlap_id and (not accepted or overlap_id == best[1]):
                    sid, reason = overlap_id, "provisional_source_overlap_match"
                elif accepted and not overlap_id:
                    sid, reason = best[1], "provisional_cosine_match"
                else:
                    if len(references) >= MAX_SPEAKERS:
                        raise ValueError("Too many provisional voices; review the existing identities before resuming")
                    ordinal += 1
                    sid = f"voice-{ordinal:06d}"
                    reason = "insufficient_exclusive_audio" if not vectors else "ambiguous_match" if best and best[0] >= self.policy.min_cosine else "new_provisional_voice"
                    references[sid] = []
                    first = min(row["start"] for row in observation["intervals"] if row["local_speaker"] == voice)
                    db.execute("INSERT INTO speakers VALUES (?,?,?,?)", (sid, ordinal, "[]", first))
                mapping[voice] = sid
                occupied.add(sid)
                decisions[voice] = {"status": "PROVISIONAL", "reason": reason,
                    "cosine": best[0] if best else None, "margin": margin,
                    "candidate_ids": [item[1] for item in ranked[:3]],
                    "source_overlap_seconds": overlap_best[0] if overlap_best else None,
                    "calibrated": False, "review_required": True}
                # Never enroll ambiguous matches; doing so can contaminate an
                # existing speaker and turn a guess into future self-evidence.
                if samples and reason != "ambiguous_match":
                    candidates = references[sid] + [{"embedding": _vector(item["embedding"]),
                                   "start": item["start"], "end": item["end"],
                                   "audio_seconds": item.get("audio_seconds", item["end"] - item["start"]),
                                   "source_spans": deepcopy(item.get("source_spans", [{"start": item["start"], "end": item["end"]}]))} for item in samples]
                    candidates.sort(key=lambda item: (-item.get("audio_seconds", item["end"] - item["start"]), item["start"]))
                    unique = []
                    for item in candidates:
                        if not any(abs(item["start"] - old["start"]) < .001 and abs(item["end"] - old["end"]) < .001 for old in unique):
                            unique.append(item)
                    references[sid] = unique[:self.policy.max_references]
                    db.execute("UPDATE speakers SET prototypes=? WHERE id=?",
                               (json.dumps(references[sid], allow_nan=False), sid))
            intervals = []
            for item in observation["intervals"]:
                left, right = max(owned_start, item["start"]), min(owned_end, item["end"])
                if right <= left:
                    continue
                others = [other for other in observation["intervals"] if other["local_speaker"] != item["local_speaker"]
                          and other["start"] < right and other["end"] > left]
                boundaries = sorted({left, right, *(max(left, other["start"]) for other in others),
                                     *(min(right, other["end"]) for other in others)})
                for a, b in zip(boundaries, boundaries[1:]):
                    intervals.append({**item, "start": a, "end": b,
                        "speaker_id": mapping[item["local_speaker"]],
                        "overlap": any(other["start"] < b and other["end"] > a for other in others),
                        "match": decisions[item["local_speaker"]]})
            result = {"scope_id": self.scope_id, "configuration_hash": self.configuration_hash,
                "source_id": self.source_id, "start": owned_start, "end": owned_end,
                "scan_start": observation["start"], "scan_end": observation["end"],
                "model": self.model_identity, "intervals": intervals,
                "review_required": True if intervals else False,
                "calibrated": False, "confidence_kind": "silhouette_and_cosine",
                "runtime_version": observation.get("runtime_version"), "request_key": request_key}
            serialized = json.dumps(result, ensure_ascii=False, allow_nan=False)
            if len(serialized.encode()) > MAX_RECORD_BYTES:
                raise ValueError("Mapped diarization checkpoint exceeds its limit")
            self._validated_checkpoint(serialized, request_key)
            db.execute("INSERT INTO chunks VALUES (?,?,?,?)", (request_key, observation["start"], observation["end"], serialized))
            return result

    def speakers(self):
        with self._connect() as db:
            return [{"speaker_id": sid, "first_start": start, "reference_count": len(json.loads(prototypes)),
                     "status": "PROVISIONAL", "scope_id": self.scope_id}
                    for sid, start, prototypes in db.execute("SELECT id,first_start,prototypes FROM speakers ORDER BY ordinal")]


def annotate_rows(rows, result, *, minimum_coverage=.80):
    """Attach hypotheses without moving word timestamps, losing text or merging.

    A word or row crossing any overlapping/conflicting voice remains unknown.
    Automatic evidence is always unverified; it cannot authorize combined TTS.
    """
    if not _finite(minimum_coverage) or not .5 < minimum_coverage <= 1:
        raise ValueError("Invalid word coverage policy")
    intervals = result["intervals"]
    def identity(start, end):
        if not _finite(start) or not _finite(end) or not start < end:
            return None, "unmeasured_word", []
        spans = [item for item in intervals if item["start"] < end and item["end"] > start]
        if any(item["overlap"] for item in spans):
            return None, "overlapping_voices", spans
        voices = {item["speaker_id"] for item in spans}
        if len(voices) != 1:
            return None, "multiple_or_missing_voices", spans
        # Merge repeated same-speaker intervals before measuring coverage.
        covered, right = 0., start
        for item in sorted(spans, key=lambda item: item["start"]):
            left, current_end = max(start, item["start"]), min(end, item["end"])
            covered += max(0., current_end - max(left, right))
            right = max(right, current_end)
        if covered / (end - start) < minimum_coverage:
            return None, "insufficient_audio_coverage", spans
        return next(iter(voices)), "provisional_audio_identity", spans
    output = deepcopy(list(rows))
    for row in output:
        word_ids = []
        words = row.get("words")
        if isinstance(words, list):
            for word in words:
                if not isinstance(word, dict):
                    raise ValueError("ASR word must be an object")
                sid, reason, spans = identity(word.get("start"), word.get("end"))
                if word.get("word", "").strip():
                    word_ids.append(sid)
                word["speaker_id"] = sid
                word["speaker_review_required"] = True
                word["speaker_reason"] = reason
        sid, reason, spans = identity(row.get("start"), row.get("end"))
        if word_ids and (any(item is None for item in word_ids) or len(set(word_ids)) != 1):
            sid, reason = None, "uncertain_or_changed_word_voice"
        elif word_ids and sid is not None and sid != word_ids[0]:
            sid, reason = None, "row_word_voice_conflict"
        row["speaker_id"] = sid
        row["speaker_evidence"] = {"speaker_id": sid, "verified": False, "method": "audio_diarization",
            "model": "audio-models-" + digest(result["model"])[:32],
            "scope_id": result["scope_id"], "reason": reason,
            "start": row["start"], "end": row["end"]}
        row["speaker_diagnostics"] = {"status": "PROVISIONAL" if sid else "UNKNOWN",
            "review_required": True, "calibrated": False,
            "silhouette": [item.get("silhouette") for item in spans],
            "matches": [deepcopy(item["match"]) for item in spans], "request_key": result["request_key"]}
    return output


__all__ = ["SpeakerRegistry", "MatchingPolicy", "annotate_rows", "validate_observation", "digest"]
