"""Separate playable drafts from speech that can be published in a full result.

An unresolved row may deliberately be playable without synthesized speech so
the user can inspect later rows. That playback policy is not proof of silence
and must never authorize omitting a known source utterance from the final MP4.
The same check accepts runtime objects and their durable manifest dictionaries.
"""
from collections.abc import Mapping


def _value(row, field, default=None):
    return row.get(field, default) if isinstance(row, Mapping) else getattr(row, field, default)


def missing_spoken_output_ids(rows):
    missing = []
    for row in rows:
        if str(_value(row, "final_vi", "") or "").strip() or _value(row, "confirmed_silence", False) is True:
            continue
        has_source_speech = any(str(_value(row, field, "") or "").strip()
                                for field in ("text_zh", "asr_text"))
        if has_source_speech or _value(row, "needs_review", False) or _value(row, "status") == "NEEDS_REVIEW":
            missing.append(_value(row, "id"))
    return missing


def missing_speech_message(ids):
    # Keep the diagnostic bounded for long videos, while the API exposes every
    # affected stable ID separately. User-visible sentence numbers are one-based.
    shown = ", ".join(str(value + 1) for value in ids[:8])
    suffix = ", …" if len(ids) > 8 else ""
    return (f"{len(ids)} câu có lời gốc nhưng chưa có lời Việt/giọng đọc (câu {shown}{suffix}). "
            "Phần xem trước được giữ; kiểm tra và bổ sung lời, hoặc chỉ xác nhận im lặng nếu nguồn thực sự không có thoại, trước khi xuất đầy đủ.")


def final_output_metadata(rows):
    rows = list(rows)
    missing = missing_spoken_output_ids(rows)
    pending_review = bool(missing or any(_value(row, "needs_review", False) for row in rows))
    return {"missing_speech_ids": missing, "final_output_blocked": bool(missing),
            # This is an editorial state, never a claim of certain semantic truth.
            "content_review_state": "REVIEW_REQUIRED" if pending_review else "NO_PENDING_REVIEW"}
