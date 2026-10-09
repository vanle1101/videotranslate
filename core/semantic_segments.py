"""Translation units over measured ASR rows, without inventing dialogue turns.

ASR/VAD rows are timing containers, not necessarily complete sentences. This
module keeps their IDs and times intact and supplies *joint context* for an
unfinished clause. Only independently grounded continuity permits a combined
utterance. Unknown speaker continuity remains an explicit alternative, never
permission to merge audio, move timestamps, or confirm Vietnamese pronouns.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from typing import Any, Iterable, Mapping, Sequence


SEMANTIC_GROUPING_VERSION = 2
_SPEAKER_KEYS = ("speaker_id", "speaker", "diarization_speaker", "spk")
_TERMINAL = frozenset("。！？!?；;.")
_CLOSING = "\"'”’」』】）)]"

SEMANTIC_TRANSLATION_POLICY = """
Các semantic_units là đơn vị đọc nghĩa, KHÔNG phải mốc phụ đề mới.
- Đọc toàn bộ source_parts của một unit và ngữ cảnh trước/sau trước khi dịch;
  không diễn giải mỗi mảnh ASR như một câu độc lập. Một mệnh đề có thể nằm
  trên nhiều ID, ví dụ cấu trúc phủ định/mục đích còn tiếp ở ID kế tiếp.
- kind=contextual_exchange chỉ yêu cầu xét các cách đọc nối lời hoặc đổi lượt;
  same_speaker_confirmed=false KHÔNG chứng minh cùng/khác người. Không gộp
  giọng, tự gán nhân vật, giới tính, quan hệ hoặc xưng hô từ grouping.
- speaker_evidence.verified=false là nhãn giọng dự đoán. Silhouette/cosine
  chưa hiệu chuẩn không phải xác suất đúng và không xác nhận nhân vật hay
  người nghe; không dùng nhãn dự đoán để tự xác nhận xưng hô hoặc gộp giọng.
- speaker_confirmation chỉ là hành động người dùng đã lưu cho affected_ids.
  Chỉ cùng một confirmation_id với selection=scoped_voice mới xác nhận liên
  tục giọng giữa các câu đã chọn; tên giống nhau hoặc xác nhận riêng một câu
  không chứng minh nối lượt. Cách gọi người nghe không được lan ra câu khác.
- Chỉ xuất đúng ID được yêu cầu. Giữ nghĩa toàn unit, không mất từ hoặc lặp
  nghĩa ở ranh giới; không chép cả câu đầy đủ vào từng ID. Nếu không thể phân
  chia lời Việt giữ nghĩa theo mốc riêng, báo needs_review=true với lý do
  semantic_boundary/timing; không tự cắt từ hoặc sửa timestamp.
- can_combine_dubbing=true chỉ xác nhận liên tục người nói từ bằng chứng
  upstream; vẫn phải qua kiểm định nghĩa, timing và quy tắc chỉnh sửa thủ công.
  ID người nói không xác nhận người nghe hoặc quan hệ nhân vật.
""".strip()


def _finite(value: Any) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(float(value)))


def _digest(value: Any) -> str:
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _speaker(row: Mapping[str, Any]) -> Any:
    values = [row.get(key) for key in _SPEAKER_KEYS if row.get(key) not in (None, "")]
    # Conflicting aliases must never provide continuity evidence.
    if not values or any(value != values[0] for value in values):
        return None
    return values[0]


def _conflicting_speaker_labels(row: Mapping[str, Any]) -> bool:
    labels = [row.get(key) for key in _SPEAKER_KEYS if row.get(key) not in (None, "")]
    return bool(labels) and any(label != labels[0] for label in labels)


def source_speaker_confirmation(row: Mapping[str, Any]):
    """Project a recorded assertion only onto its explicitly selected row.

    Provider response fields are never the source of this metadata. Durable
    assertions can survive ASR revisions, so matching both the row and its
    current voice/scope avoids reusing an old claim on a newly split turn.
    The schema validates a recorded action, not the truth of its relationship.
    """
    value = row.get("speaker_confirmation")
    if value is None:
        return None
    from core.streaming.speaker_confirmation import validate_confirmation
    try:
        proof = validate_confirmation(value)
    except (ValueError, TypeError):
        return None
    if proof is None or row.get("id") not in proof["affected_ids"]:
        return None
    if _conflicting_speaker_labels(row) or proof["speaker_id"] != _speaker(row):
        return None
    if proof["selection"] == "scoped_voice":
        evidence = row.get("speaker_evidence")
        if not isinstance(evidence, Mapping) or proof["scope_id"] != evidence.get("scope_id"):
            return None
    return proof


def _grounded_identity(row: Mapping[str, Any], field: str, minimum_confidence: float):
    """Accept actual audio/user evidence; a label/keyword/ASR parent is insufficient."""
    value = _speaker(row) if field == "speaker" else row.get("utterance_id")
    if field == "speaker":
        confirmation = source_speaker_confirmation(row)
        if (confirmation is not None and confirmation["selection"] == "scoped_voice"
                and isinstance(confirmation["scope_id"], str) and confirmation["scope_id"].strip()):
            # Two independent anchor assertions do not grant continuity. One
            # explicitly scoped selection records the same user action on each
            # selected row while automatic audio evidence stays unverified.
            return (value, "user_confirmation", confirmation["scope_id"], confirmation["confirmation_id"])
    evidence = row.get(field + "_evidence")
    if (isinstance(value, bool) or not isinstance(value, (str, int)) or value == ""
            or not isinstance(evidence, Mapping)):
        return None
    if evidence.get("verified") is not True or evidence.get("method") not in (
            "audio_diarization", "user_confirmation"):
        return None
    if evidence.get(field + "_id") != value:
        return None
    # Diarizer-local speaker_0 must not alias speaker_0 in another chunk.
    scope = evidence.get("scope_id")
    if not isinstance(scope, str) or not scope.strip():
        return None
    if evidence["method"] == "audio_diarization":
        confidence = evidence.get("confidence")
        if not _finite(confidence) or not minimum_confidence <= float(confidence) <= 1:
            return None
        if not isinstance(evidence.get("model"), str) or not evidence["model"].strip():
            return None
    # A user confirmation must be a recorded action, not an AI boolean.
    elif not isinstance(evidence.get("confirmation_id"), str) or not evidence["confirmation_id"].strip():
        return None
    return (value, evidence["method"], scope,
            evidence.get("confirmation_id") if evidence["method"] == "user_confirmation" else None)


def _manual(row: Mapping[str, Any]) -> bool:
    verification = row.get("verification")
    return (row.get("manual_edit") is True or row.get("user_edited") is True
            or isinstance(verification, Mapping) and verification.get("status") == "manual")


def _sentence_finished(text: str) -> bool:
    stripped = text.rstrip().rstrip(_CLOSING).rstrip()
    # Ellipsis signals incompleteness, not proof that the next voice is the same.
    if stripped.endswith(("…", "...")):
        return False
    return bool(stripped) and stripped[-1] in _TERMINAL


def _source_part(row: Mapping[str, Any]) -> dict:
    part = {"id": row["id"], "start": row["start"], "end": row["end"],
            "text_zh": row.get("text_zh") or row.get("asr_text") or row.get("text", "")}
    for key in (*_SPEAKER_KEYS, "speaker_evidence", "utterance_id", "utterance_evidence",
                "source_asr_row_id", "source_asr_start", "source_asr_end",
                "source_piece_index", "source_piece_count", "source_needs_review",
                "source_truncated"):
        if key in row:
            part[key] = copy.deepcopy(row[key])
    confirmation = source_speaker_confirmation(row)
    if confirmation is not None:
        part["speaker_confirmation"] = confirmation
    part["manual_edit"] = _manual(row)
    if part["manual_edit"]:
        part["manual_revision"] = row.get("revision", 0)
        part["manual_text_hash"] = _digest(row.get("final_vi", row.get("vi", "")))
    return part


def _joint_text(parts: Sequence[Mapping[str, Any]]) -> str:
    # Preserve ASR text; add spaces only at a Latin token boundary, never insert
    # a Chinese sentence ending that changes an unfinished clause into a claim.
    result = ""
    for part in parts:
        text = part["text_zh"].strip()
        if result and text and re.match(r"[A-Za-z0-9]", text) and re.search(r"[A-Za-z0-9]$", result):
            result += " "
        result += text
    return result


def build_semantic_units(rows: Iterable[Mapping[str, Any]], *, max_gap: float = 0.65,
                         max_duration: float = 12.0, max_rows: int = 6,
                         minimum_speaker_confidence: float = 0.85) -> list[dict]:
    """Build JSON-safe units without changing any input row, ID, or timestamp.

    Incomplete adjacent fragments get shared context even with unknown voices.
    ``contextual_exchange`` must keep per-row outputs/audio. ``utterance`` can
    combine dubbing only when every boundary has audio/user continuity evidence
    and none of its rows is manually edited. An ASR parent ID alone is *not*
    speaker evidence. Configuration must be included in a caller's cache key;
    each unit also includes a complete source/evidence/config input hash.
    """
    if (not _finite(max_gap) or max_gap < 0 or not _finite(max_duration) or max_duration <= 0
            or not isinstance(max_rows, int) or isinstance(max_rows, bool) or max_rows < 1
            or not _finite(minimum_speaker_confidence) or not 0 <= minimum_speaker_confidence <= 1):
        raise ValueError("Invalid semantic grouping limits")
    source = list(rows)
    seen = set()
    for row in source:
        if not isinstance(row, Mapping):
            raise ValueError("Semantic source row must be an object")
        identity = row.get("id")
        if (isinstance(identity, bool) or not isinstance(identity, (str, int))
                or isinstance(identity, str) and not identity):
            raise ValueError("Semantic source row requires a stable ID")
        if identity in seen:
            raise ValueError("Duplicate semantic source ID")
        seen.add(identity)
        if not (_finite(row.get("start")) and _finite(row.get("end"))
                and float(row["end"]) > float(row["start"])):
            raise ValueError("Invalid semantic source timestamps")
        text = row.get("text_zh") or row.get("asr_text") or row.get("text", "")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Semantic source text must not be empty")
    source.sort(key=lambda row: (float(row["start"]), float(row["end"])))
    groups: list[list[Mapping[str, Any]]] = []
    for row in source:
        if not groups:
            groups.append([row])
            continue
        group, previous = groups[-1], groups[-1][-1]
        gap = float(row["start"]) - float(previous["end"])
        previous_text = previous.get("text_zh") or previous.get("asr_text") or previous.get("text", "")
        left_speaker, right_speaker = _speaker(previous), _speaker(row)
        changed_speaker = (left_speaker is not None and right_speaker is not None
                           and left_speaker != right_speaker)
        can_contextualize = (0 <= gap <= max_gap and len(group) < max_rows
            and float(row["end"]) - float(group[0]["start"]) <= max_duration
            and not _sentence_finished(previous_text) and not changed_speaker)
        if can_contextualize:
            group.append(row)
        else:
            groups.append([row])
    config = {"version": SEMANTIC_GROUPING_VERSION, "max_gap": max_gap,
              "max_duration": max_duration, "max_rows": max_rows,
              "minimum_speaker_confidence": minimum_speaker_confidence}
    result = []
    for group in groups:
        parts = [_source_part(row) for row in group]
        boundaries = []
        for left, right in zip(group, group[1:]):
            proof = None
            for field in ("speaker", "utterance"):
                if _conflicting_speaker_labels(left) or _conflicting_speaker_labels(right):
                    break
                left_proof = _grounded_identity(left, field, minimum_speaker_confidence)
                right_proof = _grounded_identity(right, field, minimum_speaker_confidence)
                if left_proof is not None and left_proof == right_proof:
                    proof = {"kind": field, "id": left_proof[0], "method": left_proof[1],
                             "scope_id": left_proof[2]}
                    if left_proof[3] is not None:
                        proof["confirmation_id"] = left_proof[3]
                    break
            boundaries.append({"left_id": left["id"], "right_id": right["id"],
                "gap": round(float(right["start"]) - float(left["end"]), 6),
                "same_speaker_confirmed": proof is not None, "evidence": proof})
        continuous = bool(boundaries) and all(item["same_speaker_confirmed"] for item in boundaries)
        manual = any(_manual(row) for row in group)
        identity_parts = [[part["id"], part["start"], part["end"]] for part in parts]
        unit = {"unit_id": "semantic-" + _digest(identity_parts)[:24],
                "kind": "single" if len(parts) == 1 else "utterance" if continuous else "contextual_exchange",
                "source_ids": [part["id"] for part in parts], "start": parts[0]["start"],
                "end": parts[-1]["end"], "source_parts": parts,
                "same_speaker_confirmed": continuous,
                "can_combine_dubbing": continuous and not manual,
                "has_manual_edit": manual, "boundaries": boundaries,
                "grouping_version": SEMANTIC_GROUPING_VERSION,
                "input_hash": _digest({"config": config, "parts": parts})}
        if continuous:
            unit["text_zh"] = _joint_text(parts)
        # Unknown dialogue never exposes a joined claim as established source.
        result.append(unit)
    return result


def semantic_context(rows: Iterable[Mapping[str, Any]], focus: Iterable[Mapping[str, Any]] = (),
                     *, max_units: int = 16, max_chars: int = 12000, **grouping_config) -> dict:
    """Bound context near a batch, with explicit truncation rather than certainty.

    Call on original source rows (not a truncated dialogue_context projection).
    The payload is safe to include in source/translation/review prompts. A unit
    may contain IDs outside the current batch; they are reference-only and must
    never be emitted as extra translations.
    """
    if (not isinstance(max_units, int) or isinstance(max_units, bool) or max_units < 1
            or not isinstance(max_chars, int) or isinstance(max_chars, bool) or max_chars < 1):
        raise ValueError("Invalid semantic context budget")
    units = build_semantic_units(rows, **grouping_config)
    focus_ids = {row["id"] for row in focus if isinstance(row, Mapping) and "id" in row}
    focused = [index for index, unit in enumerate(units) if focus_ids.intersection(unit["source_ids"])]
    anchor = focused or [max(0, len(units) - 1)]
    priority = sorted(range(len(units)), key=lambda index: (min(abs(index - value) for value in anchor), index))
    selected, size = {}, 0
    for index in priority:
        cost = len(json.dumps(units[index], ensure_ascii=False)) + 2
        if len(selected) >= max_units or size + cost > max_chars:
            continue
        selected[index] = units[index]
        size += cost
    included = {identity for unit in selected.values() for identity in unit["source_ids"]}
    return {"grouping_version": SEMANTIC_GROUPING_VERSION,
            "units": [selected[index] for index in sorted(selected)],
            "truncated": len(selected) != len(units),
            "missing_focus_ids": sorted(focus_ids - included, key=str),
            "total_units": len(units)}


__all__ = ["SEMANTIC_GROUPING_VERSION", "SEMANTIC_TRANSLATION_POLICY",
           "build_semantic_units", "semantic_context", "source_speaker_confirmation"]
