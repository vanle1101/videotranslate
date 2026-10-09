"""Explicit human speaker assertions, scoped to exactly the selected source rows.

An audio cluster is not a character/relationship proof. This module records a
user action separately, keeps automatic evidence unchanged, and never approves
the meaning of a translation. Listener assertions belong only to selected rows:
the same voice can speak to different people later in the video.
"""
from __future__ import annotations

from copy import deepcopy
import math
import logging
import re
import time
import uuid

from core.voice_catalog import resolve_voice


def validate_confirmation(value):
    """Strict durable schema; no provider verdict or draft text can enter it."""
    if value is None:
        return None
    required = {"confirmation_id", "method", "anchor_segment_id", "affected_ids", "speaker_id",
                "scope_id", "label", "self_address", "listener_address", "voice_id", "created_at", "selection"}
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError("Xác nhận người nói thiếu metadata hợp lệ.")
    if (value["method"] != "user_confirmation" or not isinstance(value["confirmation_id"], str)
            or not re.fullmatch(r"[0-9a-f]{32}", value["confirmation_id"])
            or type(value["anchor_segment_id"]) is not int or value["anchor_segment_id"] < 0
            or value["selection"] not in {"anchor", "scoped_voice"}
            or type(value["created_at"]) not in (int, float) or not math.isfinite(value["created_at"])
            or value["created_at"] < 0):
        raise ValueError("Mã xác nhận người nói không hợp lệ.")
    ids = value["affected_ids"]
    if (not isinstance(ids, list) or not ids or len(ids) > 50000
            or any(type(sid) is not int or sid < 0 for sid in ids) or len(set(ids)) != len(ids)):
        raise ValueError("Phạm vi xác nhận người nói không hợp lệ.")
    for key, limit in (("label", 100), ("self_address", 80), ("listener_address", 80)):
        if (not isinstance(value[key], str) or len(value[key]) > limit or "\x00" in value[key]
                or (key == "label" and not value[key].strip())):
            raise ValueError("Tên/cách xưng hô người nói không hợp lệ.")
    for key in ("speaker_id", "scope_id"):
        identity = value[key]
        if identity is not None and not ((type(identity) is int and identity >= 0)
                or (isinstance(identity, str) and identity.strip() and len(identity) <= 200 and "\x00" not in identity)):
            raise ValueError("Danh tính/phạm vi giọng được xác nhận không hợp lệ.")
    if value["selection"] == "scoped_voice" and (value["speaker_id"] is None or not value["scope_id"]):
        raise ValueError("Xác nhận nhiều câu cần cùng danh tính và phạm vi giọng.")
    if value["voice_id"] is not None:
        engine, voice = resolve_voice(value["voice_id"], "edge-tts")
        if value["voice_id"] != "edge:" + voice or engine != "edge-tts":
            raise ValueError("Giọng theo người nói phải là giọng Microsoft Edge hợp lệ.")
    return deepcopy(value)


def _idle(session):
    owners = [getattr(session, name, None) for name in
              ("start_task", "worker_task", "review_task", "_chunk_followup_task")]
    return not (session.is_running or session.is_editing
                or any(owner is not None and not owner.done() for owner in owners))


def confirm_speaker(session, *, anchor_segment_id, expected_revision, apply_same_voice=False,
                    label, self_address="", listener_address="", voice_id=None):
    """Validate/reserve/commit without yielding; rollback all metadata on disk failure."""
    from core.streaming.pipeline import SegmentEditConflict, ProjectEditSaveError
    if not _idle(session):
        raise SegmentEditConflict("Hãy dừng/chờ xử lý xong trước khi xác nhận người nói.")
    if type(anchor_segment_id) is not int or type(expected_revision) is not int or expected_revision < 0:
        raise ValueError("Mã câu/phiên bản xác nhận không hợp lệ.")
    anchor = session.segments.get(anchor_segment_id)
    if anchor is None:
        raise KeyError(anchor_segment_id)
    if anchor.revision != expected_revision:
        raise SegmentEditConflict("Câu đã thay đổi; tải lại bản mới trước khi xác nhận người nói.")
    if type(apply_same_voice) is not bool:
        raise ValueError("Phạm vi xác nhận không hợp lệ.")
    for text in (label, self_address, listener_address):
        if not isinstance(text, str):
            raise ValueError("Tên/cách xưng hô phải là chữ.")
    speaker_id = getattr(anchor, "speaker_id", None)
    scope = (getattr(anchor, "speaker_evidence", None) or {}).get("scope_id")
    # Unknown or scope-less hypotheses are never a global wildcard.
    same_voice = bool(apply_same_voice and speaker_id is not None and scope)
    selected = [row for row in session.segments.values() if row is anchor or (same_voice
        and getattr(row, "speaker_id", None) == speaker_id
        and (getattr(row, "speaker_evidence", None) or {}).get("scope_id") == scope)]
    selected.sort(key=lambda row: (row.start, row.id))
    resolved_voice = None
    if voice_id is not None:
        _, native = resolve_voice(voice_id, "edge-tts")
        if session.tts_engine_name != "edge-tts":
            raise ValueError("Giọng riêng theo người nói hiện hỗ trợ dự án Microsoft Edge.")
        resolved_voice = "edge:" + native
    confirmation = validate_confirmation({"confirmation_id": uuid.uuid4().hex, "method": "user_confirmation",
        "anchor_segment_id": anchor.id, "affected_ids": [row.id for row in selected], "speaker_id": speaker_id,
        "scope_id": scope, "label": label.strip(), "self_address": self_address.strip(),
        "listener_address": listener_address.strip(), "voice_id": resolved_voice,
        "created_at": time.time(), "selection": "scoped_voice" if same_voice else "anchor"})
    comparable = {name: value for name, value in confirmation.items()
                  if name not in {"confirmation_id", "created_at", "voice_id", "affected_ids"}}
    if all(isinstance(prior := getattr(row, "speaker_confirmation", None), dict)
           and all(prior.get(name) == value for name, value in comparable.items())
           and prior.get("affected_ids") == [row.id]
           and (resolved_voice is None or prior.get("voice_id") == resolved_voice)
           for row in selected):
        # Re-saving an unchanged assertion must not invalidate healthy stage
        # caches or manufacture another review/API request.
        return {"segments": [session.segment_snapshot(row) for row in selected],
            "affected_ids": confirmation["affected_ids"],
            "speaker_confirmation": {**deepcopy(anchor.speaker_confirmation), "affected_ids": confirmation["affected_ids"]},
            "progress": session.get_progress()}
    selected_ids = set(confirmation["affected_ids"])
    selected_keys = {str(sid) for sid in selected_ids}
    dependent = []
    for row in session.segments.values():
        sources = (row.verification or {}).get("address_context_sources", {})
        if row.id in selected_ids or (isinstance(sources, dict) and sources
                and any(key in selected_keys for key in sources)):
            dependent.append(row)
    before_rows = {row.id: deepcopy(vars(row)) for row in dependent}
    fields = ("review_summary", "error", "output_video_url", "output_filename", "output_review_url",
              "auto_export_signature", "total_processed_duration", "progress")
    before_session = {name: deepcopy(getattr(session, name)) for name in fields if hasattr(session, name)}
    changed = []
    try:
        row_proof = {name: value for name, value in confirmation.items() if name != "affected_ids"}
        for row in selected:
            # Each row carries only its own claim scope. Repeating a 10,000-ID
            # selection in every row would make a long-video manifest grow
            # quadratically. The shared UUID links one action across rows;
            # the API returns the full selected list once.
            row.speaker_confirmation = {**row_proof, "affected_ids": [row.id]}
            if resolved_voice is not None:
                prior_voice = getattr(row, "voice_id", None) or "edge:" + session.voice
                if prior_voice != resolved_voice:
                    row.voice_id = resolved_voice
                    if row.final_vi.strip() and not row.confirmed_silence:
                        row.tts_voice_outdated = True
                        row.status, row.failed_stage = "FAILED", "TTS"
                        row.error = "Đã lưu giọng mới; bấm Tiếp tục để tạo lại riêng giọng câu này."
                        row._retry_synthesis = True
        for row in dependent:
            # Manual wording/approval is owned by the user, never an AI rewrite.
            # A new identity does not establish semantic correctness on its own.
            if (row.verification or {}).get("status") != "manual":
                row.speaker_review_pending = True
                row.verification = {**(row.verification or {}), "status": "incomplete",
                    "semantic_verified": False, "address_verified": False}
                row.needs_review = True
                row.review_reason = "Có xác nhận người nói mới; cần rà nghĩa và xưng hô theo bằng chứng đã lưu."
            row.revision += 1
            if row.audio_url:
                row.audio_url = f"/api/streaming/audio/{session.task_id}/{row.id}?rev={row.revision}"
            changed.append(row)
        session._refresh_review_counts()
        session._invalidate_output()
        if any(getattr(row, "tts_voice_outdated", False) for row in selected):
            session.error = "Đã lưu giọng mới; bấm Tiếp tục để tạo lại các câu đã chọn, giữ phần còn lại."
            session.total_processed_duration = sum(row.duration for row in session.segments.values()
                                                   if row.status in {"READY", "PLAYED"})
        session.persist()
    except BaseException as error:
        for sid, values in before_rows.items():
            session.segments[sid].__dict__.clear()
            session.segments[sid].__dict__.update(values)
        for name, value in before_session.items():
            setattr(session, name, value)
        for name in set(fields) - before_session.keys():
            session.__dict__.pop(name, None)
        if isinstance(error, (OSError, ValueError, TypeError)):
            logging.getLogger("errors").error("SPEAKER_CONFIRMATION_SAVE_FAILED run_id=%s anchor_segment_id=%s error_type=%s",
                session.task_id, anchor.id, type(error).__name__)
            raise ProjectEditSaveError("Chưa lưu được xác nhận xuống ổ đĩa. Lời thoại, giọng và video cũ được giữ; "
                "kiểm tra dung lượng/quyền ghi rồi thử lại.") from None
        raise
    for row in changed:
        session._pacing_failures.pop(row.id, None)
    logging.getLogger("pipeline").info("SPEAKER_CONFIRMED run_id=%s confirmation_id=%s anchor_segment_id=%s selected_count=%s dependent_count=%s",
        session.task_id, confirmation["confirmation_id"], anchor.id, len(selected), len(changed))
    return {"segments": [session.segment_snapshot(row) for row in changed],
        "affected_ids": confirmation["affected_ids"], "speaker_confirmation": confirmation,
        "progress": session.get_progress()}
