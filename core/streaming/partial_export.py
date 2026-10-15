"""An explicit, immutable translated prefix; never a full-result approval."""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import wave

from core.streaming.audio_cache import resolve_dub_timing
from core.streaming.output_gate import missing_spoken_output_ids, draft_review_metadata


def _file_identity(path):
    path = Path(path).resolve(strict=True)
    info = path.stat()
    if not path.is_file() or not info.st_size:
        raise ValueError("Thiếu tệp nguồn hoặc giọng đọc.")
    return [str(path), info.st_size, info.st_mtime_ns, info.st_ctime_ns]


def ready_prefix(session, *, end_limit=None):
    """Stop before the first missing row; keep every selected WAV sample.

    No later island is concatenated across a missing utterance. A prepared
    silence gap is retained. Borrowed speech may not cross the first missing
    source cue or the measured source-coverage boundary.
    """
    total = float(getattr(session, "total_duration", 0))
    if not math.isfinite(total) or total <= 0:
        raise ValueError("Chưa biết thời lượng nguồn.")
    chunked = getattr(session, "_chunked_source_started", False)
    visual = getattr(session, "visual_translation", False)
    if chunked:
        coverage = min(total, float(getattr(session, "_source_prepared_seconds", 0)),
                       float(getattr(session, "_visual_completed_seconds", 0)))
    elif visual and not getattr(session, "_visual_prepass_complete", False):
        coverage = min(total, float(getattr(session, "_visual_completed_seconds", 0)))
    else:
        coverage = total if getattr(session, "initialized", False) else 0.
    if not math.isfinite(coverage) or coverage <= 0:
        raise ValueError("Chưa có đoạn nguồn đã xử lý để xuất.")
    if end_limit is not None:
        if not math.isfinite(end_limit) or not 0 < end_limit <= coverage + 1e-9:
            raise ValueError("Khoảng xuất đã lưu vượt nguồn đã chuẩn bị.")
        coverage = min(coverage, end_limit)
    source = _file_identity(session.video_path)
    from core.streaming.source_identity import local_file_signature
    expected_source = getattr(session, "_source_file_signature", None)
    if expected_source and expected_source != local_file_signature(session.video_path):
        raise ValueError("Video nguồn đã đổi; chưa thể dùng lời/giọng cũ để xuất.")
    selected, media, occupied = [], [], 0.
    boundary = coverage
    for item in sorted(session.segments.values(), key=lambda row: (row.start, row.id)):
        if item.start >= coverage:
            break
        row = item.to_dict()
        if (item.end > coverage + 1e-9 or item.status not in {"READY", "PLAYED"}
                or missing_spoken_output_ids([row]) or getattr(item, "tts_voice_outdated", False)):
            boundary = min(boundary, item.start)
            break
        try:
            start, end = resolve_dub_timing(row)
            if row.get("confirmed_silence") is not True:
                if not str(row.get("final_vi") or "").strip() or not item.audio_path:
                    raise ValueError("Thiếu lời hoặc giọng.")
                identity = _file_identity(item.audio_path)
                duration = session._cached_dub_audio_duration(item.audio_path)
                # Use the same sample grid as HQExporter.
                first, length = round(start * 44100), round(duration * 44100)
                if first < round(occupied * 44100) or length > round(end * 44100) - first + 1:
                    raise ValueError("Giọng chồng lấn hoặc vượt khoảng đã căn.")
                occupied = (first + length) / 44100
                media.append(identity)
            selected.append({**row, "audio_path": item.audio_path})
        except (OSError, ValueError, TypeError, wave.Error, EOFError):
            boundary = min(boundary, item.start)
            break
    # Reflow can extend a prior cue into a missing source cue. Drop that whole
    # cue, rather than cutting its voice or including untranslated footage.
    while selected and max(selected[-1]["end"], resolve_dub_timing(selected[-1])[1]) > boundary + 1e-9:
        boundary = min(boundary, selected.pop()["start"])
    if not selected or not any(str(row.get("final_vi") or "").strip() for row in selected):
        raise ValueError("Chưa có đoạn đầu liên tục đủ lời Việt và giọng đọc.")
    if boundary <= 0:
        raise ValueError("Chưa có đoạn đầu liên tục để xuất.")
    screens = []
    for item in getattr(session, "screen_texts", []):
        if item.get("start", 0) < boundary and item.get("end", 0) > 0:
            screens.append({**deepcopy(item), "start": max(0., item["start"]),
                            "end": min(boundary, item["end"])})
    from core.subtitle_cues import CAPTION_RENDERER_VERSION
    stable_rows = [{key: row.get(key) for key in ("id", "revision", "start", "end", "dub_start", "dub_end",
        "dub_tail_limit", "final_vi", "confirmed_silence", "audio_path", "subtitle_cues")}
        for row in selected]
    media = [_file_identity(row["audio_path"]) for row in selected if not row.get("confirmed_silence")]
    signature = hashlib.sha256(json.dumps({"rows": stable_rows, "media": media,
        "source": source, "end": boundary, "screens": screens,
        "style": getattr(session, "caption_style", {}), "renderer": CAPTION_RENDERER_VERSION},
        sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return {"segments": selected, "screen_texts": screens, "start": 0., "end": boundary,
            "duration": boundary, "input_hash": signature}


def prefix_metadata(session):
    try:
        prefix = ready_prefix(session)
        reason, duration = "", prefix["duration"]
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        prefix, reason, duration = None, "Chưa có đoạn đầu liên tục đủ nguồn, lời Việt và giọng đọc.", 0.
    busy = bool(getattr(session, "is_running", False) or getattr(session, "is_editing", False)
                or getattr(session, "_stop_draining", False)
                or any(owner is not None and not owner.done() for owner in
                       (getattr(session, "review_task", None), getattr(session, "export_task", None))))
    saved = getattr(session, "partial_output", None)
    current = False
    saved_prefix = None
    if isinstance(saved, dict):
        try:
            saved_prefix = ready_prefix(session, end_limit=saved["duration"])
            current = saved.get("input_hash") == saved_prefix["input_hash"]
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            pass
    valid = getattr(session, "_partial_validated_name", None)
    return {"partial_export_available": bool(prefix and not busy), "partial_available_seconds": duration,
        "partial_export_reason": "Dừng tác vụ và chờ lưu xong trước khi xuất phần đã dịch." if busy else reason,
        "partial_output": ({**saved, "scope": "ready_prefix", "is_partial": True,
            "output_video_url": f"/api/outputs/{saved['output_filename']}", "output_is_draft": True,
            "output_quality_label": "Bản xuất từng phần — cần kiểm tra",
            "review_issue_ids": draft_review_metadata(saved_prefix["segments"])["review_issue_ids"]}
            if current and valid == saved.get("output_filename") else None)}
