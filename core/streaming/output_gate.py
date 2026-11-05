"""Separate playable drafts from speech that can be published in a full result.

Legacy rows may retain a playable status without synthesized speech. That
status is not proof of silence and must never authorize omitting an unresolved
source interval from the final MP4.
The same check accepts runtime objects and their durable manifest dictionaries.
"""
from collections.abc import Mapping
import math


def _spoken_text(row):
    return str(_value(row, "final_vi", "") or _value(row, "text_vi", "")
               or _value(row, "vi_text", "") or "").strip()


def _value(row, field, default=None):
    return row.get(field, default) if isinstance(row, Mapping) else getattr(row, field, default)


def missing_spoken_output_ids(rows):
    from core.multilingual_translation import is_untranslated_placeholder
    missing = []
    for row in rows:
        text = _spoken_text(row)
        if _value(row, "confirmed_silence", False) is True:
            continue
        if text and not is_untranslated_placeholder(text):
            continue
        # An empty ASR result is missing evidence, not a silence verdict.
        # Materialized rows require speech or explicit silence confirmation.
        missing.append(_value(row, "id"))
    return missing


def missing_speech_message(ids):
    # Keep the diagnostic bounded for long videos, while the API exposes every
    # affected stable ID separately. User-visible sentence numbers are one-based.
    shown = ", ".join(str(value + 1) for value in ids[:8])
    suffix = ", …" if len(ids) > 8 else ""
    return (f"{len(ids)} câu chưa có lời Việt/giọng đọc hoặc chưa đủ bằng chứng nguồn (câu {shown}{suffix}). "
            "Phần xem trước được giữ; kiểm tra và bổ sung lời, hoặc chỉ xác nhận im lặng nếu nguồn thực sự không có thoại, trước khi xuất đầy đủ.")


def review_segment_ids(rows):
    """Editorial uncertainty is independent of whether the media can render."""
    pending = []
    for row in rows:
        proof = _value(row, "verification") or {}
        status = proof.get("status") if isinstance(proof, Mapping) else None
        if (_value(row, "needs_review", False) or _value(row, "status") == "NEEDS_REVIEW"
                or status in {"unresolved", "incomplete", "failed", "pending"}):
            pending.append(_value(row, "id"))
    return pending


def invalid_dub_timing_ids(rows, total_duration):
    """Check the complete narration windows without changing source timestamps."""
    from core.streaming.audio_cache import resolve_dub_timing
    rows = list(rows)
    if type(total_duration) not in (int, float) or not math.isfinite(total_duration) or total_duration <= 0:
        return [_value(row, "id") for row in rows]
    invalid, windows = [], []
    for row in rows:
        if _value(row, "omit_voice", False) or _value(row, "confirmed_silence", False) is True:
            continue
        if not _spoken_text(row) and not _value(row, "audio_path"):
            continue
        try:
            start, end = resolve_dub_timing({key: _value(row, key) for key in (
                "start", "end", "dub_start", "dub_end", "dub_tail_limit")})
            if end > total_duration + 1 / 44100:
                raise ValueError("Narration exceeds the source duration")
            windows.append((start, end, _value(row, "id")))
        except (TypeError, ValueError):
            invalid.append(_value(row, "id"))
    latest_end, latest_id = -1., None
    for start, end, row_id in sorted(windows, key=lambda item: item[0]):
        if start < latest_end - 1 / 44100:
            invalid.extend((latest_id, row_id))
        if end > latest_end:
            latest_end, latest_id = end, row_id
    return list(dict.fromkeys(invalid))


def final_output_metadata(rows, *, valid_audio=None, source_complete=None, total_duration=None):
    """Expose independent media and editorial states.

    Legacy callers still receive their text-only gate. A positive media_complete
    claim requires explicit source coverage, audio validation and a full timeline.
    Audio validation must check decoded media/duration, not just file presence.
    """
    rows = list(rows)
    missing = missing_spoken_output_ids(rows)
    review_ids = review_segment_ids(rows)
    missing_audio = ([] if valid_audio is None else [
        _value(row, "id") for row in rows
        if _spoken_text(row) and _value(row, "confirmed_silence", False) is not True
        and (_value(row, "omit_voice", False) or not valid_audio(row))])
    invalid_timing = [] if total_duration is None else invalid_dub_timing_ids(rows, total_duration)
    invalid_duration = total_duration is not None and (
        type(total_duration) not in (int, float) or not math.isfinite(total_duration) or total_duration <= 0)
    omitted = [_value(row, "id") for row in rows if _value(row, "omit_voice", False)]
    blocked = bool(missing or missing_audio or invalid_timing or omitted
                   or source_complete is False or invalid_duration)
    checked = valid_audio is not None and source_complete is not None and total_duration is not None
    pending_review = bool(missing or review_ids)
    source_unverified = source_complete is False
    output_is_draft = pending_review or source_unverified
    quality_label = ("Bản nháp — source coverage chưa hoàn tất" if source_unverified else
                     "Bản nháp — cần kiểm tra" if pending_review else
                     "Không còn mục chờ kiểm tra")
    return {"missing_speech_ids": missing, "final_output_blocked": blocked,
            "missing_audio_ids": missing_audio, "invalid_timing_ids": invalid_timing,
            "source_coverage_complete": source_complete,
            "media_complete": not blocked if checked else None,
            "output_is_draft": output_is_draft,
            "output_quality_label": quality_label,
            "output_review_segment_ids": list(dict.fromkeys([*missing, *review_ids])),
            "source_coverage_unverified": source_unverified,
            # This is an editorial state, never a claim of certain semantic truth.
            "content_review_state": "REVIEW_REQUIRED" if pending_review else "NO_PENDING_REVIEW"}


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
