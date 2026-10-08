"""Bounded source preparation for preview-first, resumable long videos.

The source video remains intact. Recognition sees a short lookahead, while only
complete measured rows enter the global timeline. A clipped tail is recognized
again from its original start in the next interval, never silently discarded.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import tempfile
from pathlib import Path

from config import settings

LOOKAHEAD_SECONDS = 8.0
MAX_SCAN_SECONDS = 56.0
VERSION = 1


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _source_identity(session):
    source = session.video_path.resolve(strict=True)
    stat = source.stat()
    return {"version": VERSION, "source": str(source), "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns, "model": settings.WHISPER_MODEL_SIZE,
            "compute": settings.WHISPER_COMPUTE_TYPE, "suppression": settings.SUPPRESSION_MODE}


def _digest(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _record_path(session, start):
    return session.cache_dir / "source_preparation" / f"{round(start * 1000):012d}.json"


def _load(session, start):
    path = _record_path(session, start)
    try:
        if path.is_symlink() or path.stat().st_size > 4_000_000:
            return None
        record = json.loads(path.read_text(encoding="utf-8"))
        if (record["identity"] != _source_identity(session) or record["start"] != start
                or not _number(record["end"]) or not start < record["end"] <= session.total_duration + .05
                or not isinstance(record["rows"], list)):
            return None
        previous = start
        for row in record["rows"]:
            if (not isinstance(row, dict) or not all(_number(row.get(key)) for key in ("start", "end", "duration"))
                    or not previous <= row["start"] < row["end"] <= record["end"] + .05
                    or abs(row["end"] - row["start"] - row["duration"]) > .02
                    or not isinstance(row.get("text_zh"), str)):
                return None
            previous = row["end"]
        root = session.cache_dir.resolve()
        for name in ("raw_audio", "bgm"):
            asset = record[name]
            media = Path(asset["path"])
            if (media.is_symlink() or not media.resolve().is_relative_to(root) or not media.is_file()
                    or media.stat().st_size != asset["size"] or asset["size"] <= 0
                    or _digest(media) != asset["sha256"]):
                return None
        return record
    except (OSError, KeyError, TypeError, ValueError):
        return None


def _save(session, record):
    path = _record_path(session, record["start"])
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".tmp", dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(record, handle, ensure_ascii=False, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        if session.is_stopped:
            raise asyncio.CancelledError
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _asset(path):
    return {"path": str(path.resolve()), "size": path.stat().st_size, "sha256": _digest(path)}


def owned_boundary(rows, start, scan_end, total_duration):
    """Hold back the final ASR row when it touches the extraction boundary."""
    if scan_end >= total_duration - .05 or not rows:
        return scan_end
    if rows[-1]["end"] >= scan_end - .35:
        return max(start, rows[-1]["start"])
    return scan_end


async def prepare_interval(session, start, nominal_end):
    """Prepare at most 56 seconds; restore the exact same interval on Retry."""
    saved = await session._run_blocking(_load, session, start)
    if saved is not None:
        await session.report_progress("prepare", "Đã dùng lại lời nhận diện của đoạn đã lưu", None,
                                      chunk_start=start, chunk_end=saved["end"])
        return saved
    identity = await session._run_blocking(_source_identity, session)
    scan_end = min(session.total_duration, nominal_end + LOOKAHEAD_SECONDS)
    root = session.cache_dir / "source_preparation"
    root.mkdir(parents=True, exist_ok=True)
    stem = f"{round(start * 1000):012d}"
    raw = root / f"{stem}_16k.wav"
    loop = asyncio.get_running_loop()
    while True:
        await session.report_progress("prepare", "Đang chuẩn bị âm thanh của đoạn tiếp theo…", None,
                                      chunk_start=start, chunk_end=scan_end, total_seconds=session.total_duration)
        await session._run_ffmpeg(["ffmpeg", "-v", "error", "-nostdin", "-y", "-ss", f"{start:.3f}",
                                  "-i", str(session.video_path), "-t", f"{scan_end - start:.3f}",
                                  "-vn", "-c:a", "pcm_s16le", "-ar", "16000", "-ac", "1", str(raw)])
        def asr_progress(relative_end):
            if not session.is_stopped:
                measured = min(scan_end, start + max(0, float(relative_end)))
                loop.call_soon_threadsafe(lambda: asyncio.create_task(session.report_progress(
                    "asr", f"Đang nhận diện đoạn {start:.0f}–{scan_end:.0f} giây", None,
                    chunk_start=start, chunk_end=scan_end, asr_completed_seconds=measured,
                    batch_progress_pct=round(100 * (measured - start) / (scan_end - start), 1))))
        recognized = await session._run_blocking(session.faster_whisper.transcribe, raw, language="zh",
                                                progress_callback=asr_progress)
        shifted = []
        for value in recognized:
            if (not isinstance(value, dict) or not _number(value.get("start")) or not _number(value.get("end"))
                    or not 0 <= value["start"] < value["end"] <= scan_end - start + .05):
                raise ValueError("Nhận diện trả mốc nằm ngoài đoạn âm thanh đã gửi.")
            row = {**value, "start": round(value["start"] + start, 3), "end": round(value["end"] + start, 3)}
            if isinstance(value.get("words"), list):
                row["words"] = [{**word, "start": round(word["start"] + start, 3), "end": round(word["end"] + start, 3)}
                                for word in value["words"]]
            shifted.append(row)
        # Long utterances already contain measured word timestamps. No uniform
        # source-time splitting is introduced by this bounded path.
        rows = await session._run_blocking(session._grounded_visual_segments, shifted)
        end = owned_boundary(rows, start, scan_end, session.total_duration)
        if end > start + .05:
            rows = [row for row in rows if row["end"] <= end + .001]
            break
        if scan_end >= min(session.total_duration, start + MAX_SCAN_SECONDS) - .05:
            raise ValueError("Chưa tìm được điểm kết thúc lời thoại trong đoạn nhận diện có giới hạn; giữ phần trước để tiếp tục.")
        scan_end = min(session.total_duration, start + MAX_SCAN_SECONDS, scan_end + LOOKAHEAD_SECONDS)
    stereo = root / f"{stem}_stereo.wav"
    bgm = root / f"{stem}_bgm.ogg"
    try:
        await session._run_ffmpeg(["ffmpeg", "-v", "error", "-nostdin", "-y", "-ss", f"{start:.3f}",
                                  "-i", str(session.video_path), "-t", f"{end - start:.3f}", "-vn",
                                  "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", str(stereo)])
        stats = await session._run_blocking(session.vocal_suppressor.process_file,
            input_audio_path=stereo, output_audio_path=bgm,
            forced_mode=None if settings.SUPPRESSION_MODE == "AUTO" else settings.SUPPRESSION_MODE,
            cancel_check=lambda: session.is_stopped)
        if not bgm.is_file() or not bgm.stat().st_size:
            raise ValueError("Đoạn nhạc nền chưa tạo được tệp hợp lệ.")
        if identity != await session._run_blocking(_source_identity, session):
            raise ValueError("Video nguồn đã thay đổi khi đang nhận diện; không dùng lại lời cũ.")
        record = {"identity": identity, "start": start, "end": end, "rows": rows,
                  "raw_audio": await session._run_blocking(_asset, raw),
                  "bgm": await session._run_blocking(_asset, bgm), "suppression_stats": stats}
        await session._run_blocking(_save, session, record)
        return record
    finally:
        stereo.unlink(missing_ok=True)


async def publish_background_prefix(session, new_record):
    """Remux ready background packets; never re-run ASR or suppression."""
    # A long run must not rehash every old source WAV for every new interval.
    # Reopening validates each immutable record once; then only the newly
    # prepared record enters this private, non-persisted cache.
    records = list(getattr(session, "_background_records", []))
    if records and records[-1]["end"] > new_record["start"] + .001:
        records = []
    cursor = records[-1]["end"] if records else 0.0
    while cursor < new_record["end"] - .05:
        record = new_record if abs(cursor - new_record["start"]) < .001 else await session._run_blocking(_load, session, cursor)
        if record is None or record["end"] <= cursor:
            raise ValueError("Thiếu đoạn nhạc nền đã lưu; chưa thể ghép bản xem trước.")
        records.append(record)
        cursor = record["end"]
    root = session.cache_dir / "source_preparation"
    listing = root / "background_concat.txt"
    pending = root / "background_pending.ogg"
    target = root / f"background_{round(cursor * 1000):012d}.ogg"
    try:
        listing.write_text("\n".join("file '" + Path(row["bgm"]["path"]).name + "'" for row in records), encoding="utf-8")
        await session._run_ffmpeg(["ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "concat", "-safe", "1",
                                  "-i", str(listing), "-c:a", "copy", str(pending)])
        if not pending.is_file() or not pending.stat().st_size:
            raise ValueError("Bản nhạc nền xem trước trống.")
        if session.is_stopped:
            raise asyncio.CancelledError
        pending.replace(target)
        session._background_records = records
        return target
    finally:
        listing.unlink(missing_ok=True)
        pending.unlink(missing_ok=True)
