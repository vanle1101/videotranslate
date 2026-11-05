"""Bounded, checkpointed recovery for interior ASR coverage gaps."""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import tempfile
import time
import wave
from pathlib import Path

from config import settings

CONTEXT_SECONDS = 1.5
ASR_TIMEOUT_SECONDS = 90.0


def _digest(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _find_packet(session, target):
    from core.streaming.chunked_source import _load
    root = session.cache_dir / "source_preparation"
    for path in sorted(root.glob("[0-9]" * 12 + ".json")):
        try:
            packet_start = int(path.stem) / 1000.0
            raw = json.loads(path.read_text(encoding="utf-8"))
            intervals = raw.get("source_intervals", [])
            if not any(row.get("state") == "unresolved"
                       and row.get("start") == target[0] and row.get("end") == target[1]
                       for row in intervals):
                continue
            packet = _load(session, packet_start, background_only=True)
            if packet is not None:
                return packet
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
    raise ValueError("Không tìm thấy checkpoint âm thanh gốc của khoảng nguồn chưa xác minh.")


def _write_context_clip(raw_audio, target, packet_start, packet_end, output):
    context_start = max(packet_start, target[0] - CONTEXT_SECONDS)
    context_end = min(packet_end, target[1] + CONTEXT_SECONDS)
    if context_end <= target[1] or context_start > target[0]:
        raise ValueError("Checkpoint âm thanh không có đủ ngữ cảnh quanh khoảng nguồn.")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with wave.open(str(raw_audio), "rb") as source:
            rate, channels, width = source.getframerate(), source.getnchannels(), source.getsampwidth()
            if rate != 16000 or channels != 1 or width != 2 or source.getcomptype() != "NONE":
                raise ValueError("Checkpoint ASR không phải PCM mono 16 kHz hợp lệ.")
            first = max(0, int(math.floor((context_start - packet_start) * rate)))
            stop = min(source.getnframes(), int(math.ceil((context_end - packet_start) * rate)))
            if stop <= first or stop - first > 12 * rate:
                raise ValueError("Khoảng phục hồi vượt giới hạn 12 giây.")
            source.setpos(first)
            frames = source.readframes(stop - first)
        if len(frames) != (stop - first) * channels * width:
            raise ValueError("Checkpoint ASR bị thiếu audio ở cuối đoạn ngữ cảnh.")
        with tempfile.NamedTemporaryFile(mode="wb", suffix=".wav.tmp", dir=output.parent,
                                         delete=False) as handle:
            temporary = Path(handle.name)
        with wave.open(str(temporary), "wb") as clip:
            clip.setparams((channels, width, rate, 0, "NONE", "not compressed"))
            clip.writeframes(frames)
        temporary.replace(output)
        return context_start, context_end
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _candidate_rows(raw_rows, context, interval, language, probability):
    start, end = interval
    context_start, context_end = context
    rows = []
    for row in raw_rows:
        if not isinstance(row, dict):
            continue
        left, right = row.get("start"), row.get("end")
        text = row.get("text_zh")
        if (type(left) not in (int, float) or type(right) not in (int, float)
                or not math.isfinite(left) or not math.isfinite(right)
                or not isinstance(text, str) or not text.strip()):
            continue
        left, right = context_start + float(left), context_start + float(right)
        # Never clip a context sentence to make it appear covered. The normal
        # ASR owner must produce timestamps wholly inside the unresolved packet.
        if not start - .001 <= left < right <= end + .001:
            continue
        words = []
        for word in row.get("words", []):
            if not isinstance(word, dict):
                continue
            word_start, word_end = word.get("start"), word.get("end")
            token, confidence = word.get("word"), word.get("probability")
            if (not isinstance(token, str) or not token.strip()
                    or type(word_start) not in (int, float) or type(word_end) not in (int, float)
                    or not math.isfinite(word_start) or not math.isfinite(word_end)
                    or not 0 <= word_start < word_end <= context_end - context_start + .05
                    or context_start + word_start < left - .001
                    or context_start + word_end > right + .001
                    or confidence is not None and (type(confidence) not in (int, float)
                        or not math.isfinite(confidence) or not 0 <= confidence <= 1)):
                continue
            words.append({"word": token, "start": round(context_start + word_start, 3),
                          "end": round(context_start + word_end, 3),
                          "probability": float(confidence) if confidence is not None else None})
        if not words:
            continue
        rows.append({"start": round(left, 3), "end": round(right, 3), "duration": round(right - left, 3),
            "text_zh": text.strip(), "emotion": "<|NEUTRAL|>", "words": words,
            "source_language": language, "source_language_probability": probability,
            "source_audio_evidence": "original_audio", "source_language_uncertain": probability < .8,
            "source_asr_start": round(left, 3), "source_asr_end": round(right, 3)})
    rows.sort(key=lambda row: (row["start"], row["end"]))
    if any(right["start"] < left["end"] - .02 for left, right in zip(rows, rows[1:])):
        return []
    return rows


def _validate_coverage(packet, interval, candidate_rows):
    diagnostics = packet.get("source_gap_diagnostics")
    if not isinstance(diagnostics, list):
        return False, [], [], "Checkpoint thiếu chẩn đoán VAD gốc."
    matching = [item for item in diagnostics if isinstance(item, dict)
                and isinstance(item.get("window"), list)
                and len(item["window"]) == 2
                and packet["start"] + item["window"][0] < interval[1]
                and packet["start"] + item["window"][1] > interval[0]]
    if len(matching) != 1:
        return False, [], [], "Không xác định được duy nhất chẩn đoán VAD của khoảng này."
    diagnostic = matching[0]
    if (diagnostic.get("timing_origin") != "input_audio"
            or diagnostic.get("sample_rate") != 16000
            or diagnostic.get("omitted", {}).get("original_regions", 0) != 0):
        return False, [], [], "Danh sách VAD nguồn bị thiếu hoặc không cùng timeline với audio gốc."
    regions = []
    for region in diagnostic.get("original_regions", []):
        if (not isinstance(region, (list, tuple)) or len(region) != 2
                or any(type(value) not in (int, float) or not math.isfinite(value) for value in region)):
            return False, [], [], "Chẩn đoán chứa mốc VAD không hợp lệ."
        left, right = packet["start"] + region[0], packet["start"] + region[1]
        left, right = max(interval[0], left), min(interval[1], right)
        if left < right:
            regions.append([round(left, 3), round(right, 3)])
    gaps = []
    for item in diagnostic.get("gaps", []):
        span = item.get("span") if isinstance(item, dict) else None
        if (not isinstance(span, (list, tuple)) or len(span) != 2
                or any(type(value) not in (int, float) or not math.isfinite(value) for value in span)):
            return False, regions, [], "Chẩn đoán thiếu mốc lời nói VAD chưa được nhận dạng."
        left, right = packet["start"] + span[0], packet["start"] + span[1]
        left, right = max(interval[0], left), min(interval[1], right)
        if left < right:
            gaps.append([round(left, 3), round(right, 3)])
    if not regions or not gaps:
        return False, regions, gaps, "Không có VAD gap cụ thể nào để xác minh bằng transcript."
    for left, right in regions:
        duration = right - left
        if not any(min(right, row["end"]) - max(left, row["start"]) >= min(.08, duration * .5)
                   for row in candidate_rows):
            return False, regions, gaps, f"Chưa có transcript bertimestamp phủ VAD {left:.3f}–{right:.3f}s."
    for left, right in gaps:
        measured = [word for row in candidate_rows for word in row["words"]
                    if word["probability"] is not None and word["probability"] >= .70
                    and word["start"] < right and word["end"] > left]
        if not measured:
            return False, regions, gaps, f"ASR chưa tạo từ có độ tin cậy đủ cao trong gap {left:.3f}–{right:.3f}s."
    return True, regions, gaps, "ASR gốc đã tạo transcript và timestamp phủ toàn bộ vùng VAD, gồm gap đã đo."


def _repair_proof(session, interval, context, clip, candidate_rows, regions, *, resolved, reason):
    relative = clip.resolve().relative_to(settings.WORKSPACE_DIR.resolve()).as_posix()
    return {"start": float(interval[0]), "end": float(interval[1]),
        "classification": "SPEECH" if resolved else "UNCERTAIN",
        "status": "resolved" if resolved else "uncertain", "attempts": 1, "reason": reason[:1000],
        "context_start": float(context[0]), "context_end": float(context[1]),
        "audio_path": relative, "audio_sha256": _digest(clip),
        "candidate_rows": [{"start": row["start"], "end": row["end"], "text_zh": row["text_zh"],
            "words": list(row["words"])} for row in candidate_rows[:64]],
        "vad_regions": [list(row) for row in regions[:256]], "updated_at": time.time()}


def _prepare_source_ledger_update(session, packet, interval, candidate_rows):
    """Build a repair update without mutating session state or doing I/O."""
    from core.streaming.source_coverage import replace_unresolved_interval, verified_prefix, scanned_frontier
    replacements = [{"start": interval[0], "end": interval[1], "state": "prepared",
                     "missing_speech_spans": [], "error_code": None}]
    existing = packet.get("rows", [])
    for candidate in candidate_rows:
        if any(candidate["start"] < row["end"] - .001
               and candidate["end"] > row["start"] + .001 for row in existing):
            raise ValueError("ASR repair overlaps a sentence already stored in its source checkpoint")
        if any(candidate["start"] < row.end - .001 and candidate["end"] > row.start + .001
               for row in session.segments.values()):
            raise ValueError("ASR repair overlaps a sentence already published in the project")
    ledger = replace_unresolved_interval(session._source_coverage_intervals,
        start=interval[0], end=interval[1], replacements=replacements,
        total_duration=session.total_duration)
    packet["source_intervals"] = replace_unresolved_interval(packet.get("source_intervals", []),
        start=interval[0], end=interval[1], replacements=replacements,
        total_duration=session.total_duration)
    packet["rows"] = sorted([*existing, *candidate_rows], key=lambda row: (row["start"], row["end"]))

    from core.streaming.pipeline import SegmentItem, SOURCE_METADATA_FIELDS
    known = {(round(row.start, 3), round(row.end, 3), "".join(ch for ch in row.text_zh if ch.isalnum()))
             for row in session.segments.values()}
    source_rows = session._grounded_visual_segments(candidate_rows)
    next_id = max(session.segments, default=-1) + 1
    new_segments = []
    for source in source_rows:
        signature = (round(source["start"], 3), round(source["end"], 3),
                     "".join(ch for ch in source["text_zh"] if ch.isalnum()))
        if signature in known:
            continue
        row = SegmentItem(next_id, source["start"], source["end"], source["duration"])
        row.text_zh = row.asr_text = source["text_zh"]
        row.asr_pretranscribed = True
        row.source_method = "audio"
        for name in SOURCE_METADATA_FIELDS:
            if name in source:
                setattr(row, name, source[name])
        new_segments.append(row)
        known.add(signature)
        next_id += 1
    return {"ledger": ledger, "new_segments": new_segments,
            "prepared_seconds": verified_prefix(ledger, total_duration=session.total_duration),
            "scanned_seconds": scanned_frontier(ledger, total_duration=session.total_duration)}


def _apply_source_ledger_update(session, update):
    """Publish the already checkpointed repair on the owning event loop."""
    ledger = update["ledger"]
    session._source_coverage_intervals = ledger
    session._source_prepared_seconds = update["prepared_seconds"]
    session._source_scanned_seconds = update["scanned_seconds"]
    session._source_missing_speech_spans = [list(span) for row in ledger if row["state"] == "unresolved"
                                              for span in row["missing_speech_spans"]]
    if session._source_missing_speech_spans:
        session._source_failure_code = "source_speech_unrecognized"
        session._source_failure_start = session._source_missing_speech_spans[0][0]
    else:
        session._source_failure_code = session._source_failure_start = None
    for row in update["new_segments"]:
        session.segments[row.id] = row
    session._persist_if_enabled()


async def _commit_source_ledger_update(session, packet, interval, candidate_rows, repair_history):
    """Write the immutable source packet off-loop, then publish session state."""
    from core.streaming.chunked_source import _save
    update = _prepare_source_ledger_update(session, packet, interval, candidate_rows)
    await session._run_blocking(_save, session, packet)
    session._source_gap_repair_history = repair_history
    _apply_source_ledger_update(session, update)


async def repair_unresolved_source_interval(session, interval):
    """Try one focused ASR recovery and durably preserve evidence/outcome.

    Returns True only when every measured VAD region, including each omitted
    speech trigger, has a timestamped transcript. An uncertain attempt remains
    unresolved and never certifies silence.
    """
    from core.streaming.chunked_source import _save

    interval = (float(interval["start"]), float(interval["end"]))
    packet = await session._run_blocking(_find_packet, session, interval)
    attempts = list(getattr(session, "_source_gap_repair_history", []))
    prior = next((item for item in packet.get("source_gap_repairs", [])
                  if item.get("start") == interval[0] and item.get("end") == interval[1]), None)
    if prior and prior.get("status") == "resolved":
        language = packet.get("source_language") or {}
        candidates = [{"start": row["start"], "end": row["end"], "duration": row["end"] - row["start"],
            "text_zh": row["text_zh"], "words": row["words"],
            "source_language": language.get("language", "zh"),
            "source_language_probability": language.get("probability", .99), "source_audio_evidence": "original_audio",
            "source_asr_start": row["start"], "source_asr_end": row["end"]}
            for row in prior["candidate_rows"]]
        if not any(item.get("start") == interval[0] and item.get("end") == interval[1]
                   for item in attempts):
            attempts.append(prior)
        await _commit_source_ledger_update(session, packet, interval, candidates, attempts)
        return True
    if prior:
        if not any(item.get("start") == interval[0] and item.get("end") == interval[1]
                   for item in attempts):
            attempts.append(prior)
            session._source_gap_repair_history = attempts
            session._persist_if_enabled()
        return False

    diagnostic = next((item for item in packet.get("source_gap_diagnostics", [])
                       if isinstance(item, dict) and isinstance(item.get("window"), list)
                       and packet["start"] + item["window"][0] < interval[1]
                       and packet["start"] + item["window"][1] > interval[0]), {})
    language = packet.get("source_language") or {}
    source_language = language.get("language")
    probability = language.get("probability", 0.)
    if source_language not in {"zh", "en", "ja", "ko", "yue"} or probability < .8:
        raise ValueError("Checkpoint không có ngôn ngữ nguồn đủ tin cậy cho ASR tập trung.")

    stem = f"{round(interval[0] * 1000):012d}_{round(interval[1] * 1000):012d}"
    clip = session.cache_dir / "source_preparation" / f"repair_{stem}_context.wav"
    context = await session._run_blocking(_write_context_clip, Path(packet["raw_audio"]["path"]),
        interval, packet["start"], packet["end"], clip)
    try:
        raw_candidates = await session._run_blocking(session.faster_whisper.transcribe_focused,
            clip, language=source_language, timeout_seconds=ASR_TIMEOUT_SECONDS)
    except Exception as error:
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise
        raw_candidates = []
        asr_error = type(error).__name__[:120]
    else:
        asr_error = None
    candidates = _candidate_rows(raw_candidates, context, interval, source_language, float(probability))
    resolved, regions, _gaps, reason = _validate_coverage(packet, interval, candidates)
    if asr_error:
        resolved = False
        reason = f"ASR phục hồi có giới hạn kết thúc bằng lỗi {asr_error}; không có transcript mới được chấp nhận."
    proof = _repair_proof(session, interval, context, clip, candidates, regions,
                          resolved=resolved, reason=reason)
    packet.setdefault("source_gap_repairs", [])
    packet["source_gap_repairs"] = [item for item in packet["source_gap_repairs"]
        if (item.get("start"), item.get("end")) != interval]
    packet["source_gap_repairs"].append(proof)

    attempts = [item for item in attempts
                if (item.get("start"), item.get("end")) != interval]
    attempts.append(proof)
    if resolved:
        await _commit_source_ledger_update(session, packet, interval, candidates, attempts)
        logging.getLogger("pipeline").info(
            "SOURCE_GAP_REPAIRED run_id=%s interval=%s candidate_rows=%s evidence_sha256=%s",
            session.task_id, interval, len(candidates), proof["audio_sha256"])
    else:
        await session._run_blocking(_save, session, packet)
        session._source_gap_repair_history = attempts
        session._persist_if_enabled()
        logging.getLogger("pipeline").warning(
            "SOURCE_GAP_UNCERTAIN run_id=%s interval=%s reason=%s candidates=%s evidence_sha256=%s",
            session.task_id, interval, reason, len(candidates), proof["audio_sha256"])
    return resolved
