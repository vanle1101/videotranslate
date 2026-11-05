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
import time
import traceback
from pathlib import Path

from config import settings

LOOKAHEAD_SECONDS = 8.0
MAX_SCAN_SECONDS = 56.0
VERSION = 1


async def _timed_stage(session, stage, function, *args, **kwargs):
    """Measure actual worker wall time while preserving cancellation behavior."""
    started = time.monotonic()
    try:
        return await function(*args, **kwargs)
    finally:
        profiler = getattr(session, "_stage_profiler", None)
        if profiler is not None:
            profiler.record_work(stage, max(0., time.monotonic() - started))


def source_failure_diagnostic(error, stage):
    """Keep useful local failure locations without provider text or transcripts."""
    stages = {"identity", "checkpoint_load", "audio_extract", "asr", "diarization", "segment",
              "background_extract", "background_suppress", "source_validate", "checkpoint_write"}
    codes = {
        "Video nguồn đã thay đổi khi đang nhận diện; không dùng lại lời cũ.": "source_identity_changed",
        "Video nguồn đã thay đổi; hãy tạo tác vụ mới để tránh ghép lời từ hai video.": "source_media_changed",
        "Nhận diện trả mốc nằm ngoài đoạn âm thanh đã gửi.": "asr_out_of_bounds",
        "Chưa tìm được điểm kết thúc lời thoại trong đoạn nhận diện có giới hạn; giữ phần trước để tiếp tục.": "source_boundary_missing",
        "Đoạn nhạc nền chưa tạo được tệp hợp lệ.": "background_missing",
    }
    frames = traceback.extract_tb(error.__traceback__)[-8:]
    trace = " -> ".join(f"{Path(frame.filename).name}:{frame.lineno}:{frame.name}" for frame in frames)
    from core.engines.asr.source_language import SourceLanguageMismatchError
    code = error.code if isinstance(error, SourceLanguageMismatchError) or getattr(error, "code", None) == "source_speech_unrecognized" else codes.get(str(error), "source_preparation_failed")
    return stage if stage in stages else "unknown", code, trace


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def merge_source_missing_spans(spans, *, offset=0.0, total_duration=None, interval_end=None):
    """Convert validated interval-local speech gaps into global source spans.

    The native worker reports seconds relative to the bounded audio crop.  Keep
    those locations durable after adding the crop cursor, and coalesce only
    overlapping gaps so the UI can show a short actionable list.
    """
    if not _number(offset) or offset < 0:
        raise ValueError("Mốc bắt đầu khoảng lời nói không hợp lệ.")
    if total_duration is not None and (not _number(total_duration) or total_duration <= 0):
        raise ValueError("Thời lượng nguồn không hợp lệ.")
    if not isinstance(spans, (list, tuple)):
        raise ValueError("Khoảng lời nói chưa nhận diện không hợp lệ.")
    if len(spans) > 1024:
        raise ValueError("Quá nhiều khoảng lời nói chưa nhận diện.")
    if interval_end is not None and (not _number(interval_end) or interval_end <= offset):
        raise ValueError("Mốc kết thúc đoạn nhận diện không hợp lệ.")
    result = []
    for span in spans:
        if (not isinstance(span, (list, tuple)) or len(span) != 2
                or not _number(span[0]) or not _number(span[1]) or span[0] < 0
                or span[1] <= span[0]):
            raise ValueError("Khoảng lời nói chưa nhận diện không hợp lệ.")
        start, end = offset + float(span[0]), offset + float(span[1])
        if (total_duration is not None and end > total_duration
                or interval_end is not None and end > interval_end):
            raise ValueError("Khoảng lời nói chưa nhận diện vượt thời lượng nguồn.")
        result.append((start, end))
    merged = []
    for start, end in sorted(result):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return [list(span) for span in merged]


def _source_identity(session):
    from core.streaming.source_revision import source_runtime_identity
    return {**_media_identity(session), "runtime": source_runtime_identity(session), "language_routing_version": 8}


def _media_identity(session):
    source = session.video_path.resolve(strict=True)
    stat = source.stat()
    return {"version": VERSION, "source": str(source), "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns, "ctime_ns": stat.st_ctime_ns, "file_id": stat.st_ino,
            "model": settings.WHISPER_MODEL_SIZE,
            "compute": settings.WHISPER_COMPUTE_TYPE, "suppression": settings.SUPPRESSION_MODE}


def ensure_source_identity(session):
    """Pin the media across intervals, promotion, retry and export."""
    current = _media_identity(session)
    media = {key: current[key] for key in ("source", "size", "mtime_ns", "ctime_ns", "file_id")}
    signature = hashlib.sha256(json.dumps(media, sort_keys=True).encode("utf-8")).hexdigest()
    saved = getattr(session, "_chunked_source_identity", "")
    if not saved:
        # Existing bounded sessions already own measured source checkpoints.
        # Do not adopt a replacement video when migrating their identity.
        first = _record_path(session, 0.0)
        if first.exists():
            if first.is_symlink() or first.stat().st_size > 4_000_000:
                raise ValueError("Checkpoint nguồn đầu tiên không hợp lệ.")
            original = json.loads(first.read_text(encoding="utf-8"))["identity"]
            if any(original.get(key) != media[key] for key in media if key in original):
                raise ValueError("Video nguồn đã thay đổi; hãy tạo tác vụ mới để tránh ghép lời từ hai video.")
        session._chunked_source_identity = signature
    elif saved != signature:
        raise ValueError("Video nguồn đã thay đổi; hãy tạo tác vụ mới để tránh ghép lời từ hai video.")
    return signature


def _digest(path):
    if hasattr(hashlib, "file_digest"):
        with Path(path).open("rb") as handle:
            return hashlib.file_digest(handle, "sha256").hexdigest()
    h = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while True:
            chunk = handle.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _record_path(session, start):
    return session.cache_dir / "source_preparation" / f"{round(start * 1000):012d}.json"


def _load(session, start, *, background_only=False):
    path = _record_path(session, start)
    try:
        if path.is_symlink() or path.stat().st_size > 4_000_000:
            return None
        record = json.loads(path.read_text(encoding="utf-8"))
        expected = _media_identity(session) if background_only else _source_identity(session)
        if not background_only and not expected["runtime"]["whisper_asset_revision_known"]:
            return None
        actual = {key: record["identity"].get(key) for key in expected} if background_only else record["identity"]
        if not background_only and isinstance(actual, dict) and isinstance(actual.get("runtime"), dict):
            # Older manifests included this optional enrichment toggle. Its
            # value never changed ASR words/timings, so ignore it when loading
            # those otherwise identical source packets.
            actual["runtime"].pop("diarization_enabled", None)
        if background_only:
            # The old BGM's measured timestamps, SHA-256 and suppression are
            # still validated. A changed recognition model does not invalidate
            # already-committed background bytes or require ASR of that prefix.
            for key in ("model", "compute"):
                expected.pop(key, None)
                actual.pop(key, None)
        if (actual != expected or record["start"] != start
                or not _number(record["end"]) or not start < record["end"] <= session.total_duration + .05
                or not isinstance(record["rows"], list)):
            return None
        previous = start
        if "source_intervals" in record:
            from core.streaming.source_coverage import validate_intervals
            coverage = validate_intervals(record["source_intervals"], total_duration=session.total_duration)
            if (not coverage or coverage[0]["start"] != start or coverage[-1]["end"] != record["end"]
                    or any(a["end"] != b["start"] for a, b in zip(coverage, coverage[1:]))):
                return None
        else:
            coverage = [{"start": start, "end": record["end"], "state": "prepared"}]
        for row in record["rows"]:
            if (not isinstance(row, dict) or not all(_number(row.get(key)) for key in ("start", "end", "duration"))
                    or not previous <= row["start"] < row["end"] <= record["end"] + .05
                    or abs(row["end"] - row["start"] - row["duration"]) > .02
                    or not isinstance(row.get("text_zh"), str)):
                return None
            if not any(part["state"] == "prepared" and part["start"] <= row["start"]
                       and row["end"] <= part["end"] for part in coverage):
                return None
            previous = row["end"]
        root = session.cache_dir.resolve()
        asset_names = ["raw_audio", "bgm"]
        if not background_only:
            asset_names.extend(name for name in ("vocal_evidence", "vocal_evidence_manifest") if name in record)
        for name in asset_names:
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


def _exclude_rows_over_unresolved_source(rows, intervals, diagnostics=()):
    """Keep only whole ASR rows covered by prepared source islands.

    A row that crosses a measured speech gap is retained as a diagnostic, not
    clipped, shifted, or published as source. Independent rows on either side
    can still advance the scan and translation pipeline.
    """
    prepared = [row for row in intervals if row.get("state") == "prepared"]
    unresolved = [row for row in intervals if row.get("state") == "unresolved"]
    kept, notes = [], list(diagnostics or ())
    for row in rows:
        if any(interval["start"] <= row["start"] and row["end"] <= interval["end"]
               for interval in prepared):
            kept.append(row)
            continue
        overlaps = [[interval["start"], interval["end"]] for interval in unresolved
                    if row["start"] < interval["end"] and row["end"] > interval["start"]]
        if not overlaps:
            raise ValueError("Recognized row lies outside source coverage")
        notes.append({
            "code": "recognized_row_overlaps_unresolved_source",
            "row_id": row.get("id"), "start": row["start"], "end": row["end"],
            "text_zh": row.get("text_zh", ""), "unresolved_spans": overlaps,
            "action": "excluded_without_timestamp_changes",
        })
    return kept, notes


def _asset(path):
    return {"path": str(path.resolve()), "size": path.stat().st_size, "sha256": _digest(path)}


def _save_native_error(session, start, end, identity, raw, error):
    """One bounded sidecar per interval; never a source checkpoint or gate input."""
    if getattr(error, "code", None) != "source_speech_unrecognized" or getattr(error, "diagnostic", None) is None:
        return
    from core.engines.asr.speech_gap_diagnostic import validate_gap_diagnostic
    path = session.cache_dir / "source_preparation" / f"{round(start * 1000):012d}_native_error.json"
    temporary = None
    try:
        diagnostic = validate_gap_diagnostic(error.diagnostic)
        if diagnostic["input_samples"] / diagnostic["sample_rate"] > end - start + .001:
            raise ValueError("Diagnostic exceeds input crop")
        vocal = raw.with_name(raw.stem + "_vocal_evidence.wav")
        record = {"version": 1, "code": error.code, "identity": identity,
                  "interval_start": start, "interval_end": end, "spans": error.spans,
                  "raw_audio": _asset(raw), "diagnostic": diagnostic}
        if vocal.is_file():
            record["vocal_evidence"] = _asset(vocal)
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".tmp", dir=path.parent,
                                         delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(record, handle, ensure_ascii=False, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    except (OSError, ValueError, TypeError):
        # Diagnostic retention must not replace the original source failure or
        # accidentally make it look like a transient write error worth retrying.
        import logging
        logging.getLogger("errors").warning("SOURCE_GAP_DIAGNOSTIC_NOT_SAVED interval_start=%s", start)
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def _language_path(session, start):
    return session.cache_dir / "source_preparation" / f"{round(start * 1000):012d}_language.json"


def _load_language(session, start, identity):
    from core.engines.asr.source_language import language_metadata
    from core.engines.asr.native_process import ASRProcessError
    path = _language_path(session, start)
    try:
        if path.is_symlink() or path.stat().st_size > 100_000:
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        if value["identity"] != identity or value["start"] != start:
            return None
        return language_metadata(value["metadata"])
    except (OSError, ValueError, KeyError, TypeError, ASRProcessError):
        return None


def _save_language(session, start, end, identity, metadata):
    """Retain a mismatch diagnosis, never fake a completed source interval."""
    path = _language_path(session, start)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".tmp", dir=path.parent,
                                         delete=False) as handle:
            temporary = Path(handle.name)
            json.dump({"identity": identity, "start": start, "end": end, "metadata": metadata}, handle,
                      ensure_ascii=False, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        if session.is_stopped:
            raise asyncio.CancelledError
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def owned_boundary(rows, start, scan_end, total_duration):
    """Hold back the final ASR row when it touches the extraction boundary."""
    if scan_end >= total_duration - .05 or not rows:
        return scan_end
    if rows[-1]["end"] >= scan_end - .35:
        return max(start, rows[-1]["start"])
    return scan_end


async def prepare_interval(session, start, nominal_end):
    session._source_preparation_active = True
    session._source_preparation_task = asyncio.current_task()
    try:
        return await _prepare_interval(session, start, nominal_end)
    finally:
        session._source_preparation_active = False
        session._source_preparation_task = None


async def _prepare_interval(session, start, nominal_end):
    """Prepare at most 56 seconds; restore the exact same interval on Retry."""
    session._source_preparation_stage = "identity"
    await _timed_stage(session, "source.identity", session._run_blocking,
                       ensure_source_identity, session)
    session._source_preparation_stage = "checkpoint_load"
    saved = await _timed_stage(session, "source.checkpoint_load", session._run_blocking,
                               _load, session, start)
    if saved is not None:
        await session.report_progress("prepare", "Đã dùng lại lời nhận diện của đoạn đã lưu", None,
                                      chunk_start=start, chunk_end=saved["end"])
        return saved
    session._source_preparation_stage = "identity"
    identity = await _timed_stage(session, "source.identity", session._run_blocking,
                                  _source_identity, session)
    from core.engines.asr.source_language import require_supported_source, source_row_language
    known_language = await session._run_blocking(_load_language, session, start, identity)
    if known_language is not None:
        session._source_preparation_stage = "asr"
        require_supported_source(known_language)
    scan_end = min(session.total_duration, nominal_end + LOOKAHEAD_SECONDS)
    root = session.cache_dir / "source_preparation"
    root.mkdir(parents=True, exist_ok=True)
    stem = f"{round(start * 1000):012d}"
    raw = root / f"{stem}_16k.wav"
    loop = asyncio.get_running_loop()
    while True:
        session._source_preparation_interval = (start, scan_end)
        session._source_preparation_stage = "audio_extract"
        await session.report_progress("prepare", "Đang chuẩn bị âm thanh của đoạn tiếp theo…", None,
                                      chunk_start=start, chunk_end=scan_end, total_seconds=session.total_duration)
        await _timed_stage(session, "source.audio_extract", session._run_ffmpeg,
            ["ffmpeg", "-v", "error", "-nostdin", "-y", "-ss", f"{start:.3f}",
             "-i", str(session.video_path), "-t", f"{scan_end - start:.3f}",
             "-vn", "-c:a", "pcm_s16le", "-ar", "16000", "-ac", "1", str(raw)])
        def asr_progress(relative_end):
            if not session.is_stopped:
                measured = min(scan_end, start + max(0, float(relative_end)))
                loop.call_soon_threadsafe(lambda: asyncio.create_task(session.report_progress(
                    "asr", f"Đang nhận diện đoạn {start:.0f}–{scan_end:.0f} giây", None,
                    chunk_start=start, chunk_end=scan_end, asr_completed_seconds=measured,
                    batch_progress_pct=round(100 * (measured - start) / (scan_end - start), 1))))
        session._source_preparation_stage = "asr"
        await session.report_progress("asr", "Đang nhận diện lời nói của đoạn tiếp theo…", None,
                                      chunk_start=start, chunk_end=scan_end)
        source_language = {}
        source_coverage = {}
        def detected_language(metadata):
            source_language.update(metadata)
            _save_language(session, start, scan_end, identity, metadata)
            require_supported_source(metadata)
        # Automatic detection uses this complete bounded context, never a tiny
        # per-word crop. Chinese subtitles do not establish spoken language.
        try:
            recognized = await _timed_stage(session, "source.asr", session._run_blocking,
                session.faster_whisper.transcribe, raw, language=None,
                progress_callback=asr_progress, metadata_callback=detected_language,
                coverage_callback=source_coverage.update)
        except Exception as error:
            from core.engines.asr.native_process import ASRProcessError
            _save_native_error(session, start, scan_end, identity, raw, error)
            # A VAD region clipped by this temporary crop can fail before
            # owned_boundary gets any rows. Give that tail real source context,
            # still within the same finite scan budget; never waive its gate.
            spans = getattr(error, "spans", None)
            duration = scan_end - start
            limit = min(session.total_duration, start + MAX_SCAN_SECONDS)
            clipped_tail = (isinstance(error, ASRProcessError)
                and getattr(error, "code", None) == "source_speech_unrecognized"
                and isinstance(spans, (list, tuple)) and 0 < len(spans) <= 1024
                and all(isinstance(span, (list, tuple)) and len(span) == 2
                    and _number(span[0]) and _number(span[1])
                    and 0 <= span[0] < span[1] <= duration + .001
                    and span[1] >= duration - .05 for span in spans))
            if not clipped_tail or scan_end >= limit - .05:
                raise
            scan_end = min(limit, scan_end + LOOKAHEAD_SECONDS)
            continue
        limit = min(session.total_duration, start + MAX_SCAN_SECONDS)
        if source_coverage.get("tail_unresolved") and scan_end < limit - .05:
            scan_end = min(limit, scan_end + LOOKAHEAD_SECONDS)
            continue
        if (source_language and not source_coverage.get("unresolved_windows")
                and not any(str(row.get("text_zh", "")).strip() for row in recognized)):
            from core.engines.asr.source_language import require_empty_source_is_speech_free
            await _timed_stage(session, "source.empty_audio_validation", session._run_blocking,
                               require_empty_source_is_speech_free, raw)
        shifted = []
        for value in recognized:
            if (not isinstance(value, dict) or not _number(value.get("start")) or not _number(value.get("end"))
                    or not 0 <= value["start"] < value["end"] <= scan_end - start + .05):
                raise ValueError("Nhận diện trả mốc nằm ngoài đoạn âm thanh đã gửi.")
            row = {**value, "start": round(value["start"] + start, 3), "end": round(value["end"] + start, 3)}
            if value.get("source_language_review"):
                from copy import deepcopy
                row["source_language_review"] = deepcopy(value["source_language_review"])
                row["source_language_review"]["interval_start"] = start
            for key in ("source_asr_start", "source_asr_end"):
                if _number(value.get(key)):
                    row[key] = round(value[key] + start, 3)
            if isinstance(value.get("words"), list):
                row["words"] = [{**word, "start": round(word["start"] + start, 3), "end": round(word["end"] + start, 3)}
                                for word in value["words"]]
            shifted.append(row)
        # Keep measured native text in the legacy text_zh compatibility field.
        # A routing label is not independent proof that ASR heard every word.
        for row in shifted:
            if source_language:
                local_metadata = ({"language": row["source_language"],
                    "probability": row["source_language_probability"]}
                    if "source_language" in row and "source_language_probability" in row else source_language)
                language = source_row_language(row.get("text_zh", ""), local_metadata)
                row["source_language"] = language
                row["source_language_probability"] = local_metadata["probability"]
                row["source_language_uncertain"] = (local_metadata["probability"] < .8
                    or language != local_metadata["language"] or row.get("source_audio_evidence") == "vocal_stem_unverified"
                    or bool(row.get("source_language_review")))
                if language != "zh" or row.get("source_audio_evidence") == "vocal_stem_unverified" or row.get("source_language_review"):
                    row["evidence_mode"] = "native-recovery-" + language
        # Long utterances already contain measured word timestamps. No uniform
        # source-time splitting is introduced by this bounded path.
        from core.streaming.speaker_source import annotate_source_rows
        session._source_preparation_stage = "diarization"
        await session.report_progress("prepare", "Đang phân biệt giọng nói trong đoạn…", None,
                                      chunk_start=start, chunk_end=scan_end)
        shifted = await _timed_stage(session, "source.diarization", annotate_source_rows,
                                     session, shifted, start, scan_end)
        session._source_preparation_stage = "segment"
        rows = await _timed_stage(session, "source.segment", session._run_blocking,
                                  session._grounded_visual_segments, shifted)
        end = owned_boundary(rows, start, scan_end, session.total_duration)
        unresolved = [[start + left, start + right]
                      for left, right in source_coverage.get("unresolved_windows", [])]
        # A failed final window has no proven utterance endpoint. Keep it for
        # the next overlapping crop instead of cutting at an arbitrary second.
        if source_coverage.get("tail_unresolved") and scan_end < session.total_duration:
            end = min(end, unresolved[-1][0])
        # Never split an unresolved measured window at an owned boundary.
        end = min([end, *(left for left, right in unresolved if left < end < right)])
        if end > start + .05:
            rows = [row for row in rows if row["end"] <= end + .001]
            from core.streaming.source_progress import packet_intervals
            coverage = packet_intervals(start, end, [span for span in unresolved if span[1] <= end])
            rows, gap_diagnostics = _exclude_rows_over_unresolved_source(
                rows, coverage, source_coverage.get("diagnostics", []))
            break
        if scan_end >= min(session.total_duration, start + MAX_SCAN_SECONDS) - .05:
            raise ValueError("Chưa tìm được điểm kết thúc lời thoại trong đoạn nhận diện có giới hạn; giữ phần trước để tiếp tục.")
        scan_end = min(session.total_duration, start + MAX_SCAN_SECONDS, scan_end + LOOKAHEAD_SECONDS)
    stereo = root / f"{stem}_stereo.wav"
    bgm = root / f"{stem}_bgm.ogg"
    try:
        session._source_preparation_stage = "background_extract"
        await session.report_progress("prepare", "Đang chuẩn bị nhạc nền của đoạn…", None,
                                      chunk_start=start, chunk_end=end)
        await _timed_stage(session, "source.background_extract", session._run_ffmpeg,
            ["ffmpeg", "-v", "error", "-nostdin", "-y", "-ss", f"{start:.3f}",
             "-i", str(session.video_path), "-t", f"{end - start:.3f}", "-vn",
             "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", str(stereo)])
        session._source_preparation_stage = "background_suppress"
        await session.report_progress("prepare", "Đang lọc thoại gốc và lưu nhạc nền của đoạn…", None,
                                      chunk_start=start, chunk_end=end)
        from core.streaming.prepared_background import background_provenance
        bg_provenance = background_provenance(session.vocal_suppressor)
        stats = await _timed_stage(session, "source.background_suppress", session._run_blocking,
            session.vocal_suppressor.process_file,
            input_audio_path=stereo, output_audio_path=bgm,
            forced_mode=None if settings.SUPPRESSION_MODE == "AUTO" else settings.SUPPRESSION_MODE,
            cancel_check=lambda: session.is_stopped)
        if not bgm.is_file() or not bgm.stat().st_size:
            raise ValueError("Đoạn nhạc nền chưa tạo được tệp hợp lệ.")
        session._source_preparation_stage = "source_validate"
        if identity != await _timed_stage(session, "source.identity", session._run_blocking,
                                          _source_identity, session):
            raise ValueError("Video nguồn đã thay đổi khi đang nhận diện; không dùng lại lời cũ.")
        record = {"identity": identity, "start": start, "end": end, "rows": rows,
                  "raw_audio": await _timed_stage(session, "source.checkpoint_asset", session._run_blocking, _asset, raw),
                  "bgm": await _timed_stage(session, "source.checkpoint_asset", session._run_blocking, _asset, bgm), "suppression_stats": stats,
                  "source_language": source_language,
                  "source_intervals": coverage,
                  "source_gap_diagnostics": gap_diagnostics,
                  "background_provenance": bg_provenance}
        vocal = raw.with_name(raw.stem + "_vocal_evidence.wav")
        if vocal.is_file():
            record["vocal_evidence"] = await _timed_stage(session, "source.checkpoint_asset", session._run_blocking, _asset, vocal)
            record["vocal_evidence_manifest"] = await _timed_stage(session, "source.checkpoint_asset", session._run_blocking, _asset, vocal.with_suffix(".json"))
        session._source_preparation_stage = "checkpoint_write"
        await _timed_stage(session, "source.checkpoint_write", session._run_blocking,
                           _save, session, record)
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
        record = new_record if abs(cursor - new_record["start"]) < .001 else await session._run_blocking(_load, session, cursor, background_only=True)
        if record is None or record["end"] <= cursor:
            raise ValueError("Thiếu đoạn nhạc nền đã lưu; chưa thể ghép bản xem trước.")
        records.append(record)
        cursor = record["end"]
    root = session.cache_dir / "source_preparation"
    listing = root / "background_concat.txt"
    pending = root / "background_pending.ogg"
    target = root / f"background_{round(cursor * 1000):012d}.ogg"
    try:
        # Opus containers include ~6.5 ms encoder pre-skip. Letting concat
        # derive each duration from the container accumulates that offset over
        # hundreds of intervals. Place packets at measured SOURCE offsets.
        listing.write_text("\n".join("file '" + Path(row["bgm"]["path"]).name + "'\n"
                            + f"duration {row['end'] - row['start']:.6f}" for row in records), encoding="utf-8")
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
