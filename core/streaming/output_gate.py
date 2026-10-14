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


def draft_review_metadata(rows, review_summary=None):
    """Editorial uncertainty is a label, never permission to skip media gates."""
    rows = list(rows)
    summary = review_summary or {}
    issues = [_value(row, "id") for row in rows if _value(row, "needs_review", False)
              or (_value(row, "verification", {}) or {}).get("status") in
              {"unresolved", "incomplete", "failed", "pending"}]
    draft = bool(issues or summary.get("status") != "completed" or summary.get("unresolved", 0))
    return {"output_is_draft": draft, "output_quality_label": "Bản nháp — cần kiểm tra" if draft else "Đã rà tự động",
            "review_issue_ids": issues}


def final_output_metadata(rows, review_summary=None):
    rows = list(rows)
    missing = missing_spoken_output_ids(rows)
    pending_review = bool(missing or any(_value(row, "needs_review", False) for row in rows)
                          or review_summary and review_summary.get("status") != "completed")
    return {"missing_speech_ids": missing, "final_output_blocked": bool(missing),
            **draft_review_metadata(rows, review_summary),
            # This is an editorial state, never a claim of certain semantic truth.
            "content_review_state": "REVIEW_REQUIRED" if pending_review else "NO_PENDING_REVIEW"}


def final_media_metadata(rows, valid_audio):
    """Separate translated text from physically available, fitted speech."""
    missing, unready = [], []
    for row in rows:
        if _value(row, "confirmed_silence", False) is True:
            continue
        if _value(row, "status") not in {"READY", "PLAYED"}:
            unready.append(_value(row, "id"))
        if str(_value(row, "final_vi", "") or "").strip() and not valid_audio(row):
            missing.append(_value(row, "id"))
    return {"missing_audio_ids": missing, "unready_segment_ids": unready,
            "final_media_blocked": bool(missing or unready)}


def preview_output_metadata(rows, valid_audio):
    """Require audible translated speech, or an actually speech-free prefix.

    Source coverage is checked separately. A READY row containing unresolved
    source speech with no Vietnamese and no WAV is not a translated sample.
    A draft with real speech remains inspectable without approving its meaning.
    """
    rows = list(rows)
    playable, missing_audio = [], []
    untranslated = missing_spoken_output_ids(rows)
    spoken = [row for row in rows if str(_value(row, "final_vi", "") or "").strip()
              and _value(row, "confirmed_silence", False) is not True]
    for row in spoken:
        if _value(row, "status") in {"READY", "PLAYED"} and valid_audio(row):
            playable.append(_value(row, "id"))
        else:
            missing_audio.append(_value(row, "id"))
    return {"preview_output_ready": bool(playable or not spoken and not untranslated),
            "preview_spoken_segments": len(playable),
            "preview_untranslated_ids": untranslated,
            "preview_missing_audio_ids": missing_audio}
