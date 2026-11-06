"""Local, bounded recovery owner. No scheduler, Codex calls, or repeated prompts.

The supervisor owns one worker and restarts it at most twice after a crash.
Expected review/quota/media blockers exit without repeating provider requests.
The worker exposes the existing Studio backend independently of the desktop UI.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import datetime as dt
import hashlib
import html
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "workspace/audit/recovery_20261010"
SHORT_ID = "douyin_7692745161054506290"
SHORT_URL = "https://www.douyin.com/jingxuan?modal_id=7692745161054506290"
LONG_URL = "https://www.douyin.com/jingxuan?modal_id=7666381909177470271"
QA_SPEECH_SPEED_LIMITS = (.90, 1.15)
SERVICE_WAIT_CODES = {"provider_rate_limited", "provider_authentication", "provider_configuration",
                      "provider_model", "provider_circuit_open", "provider_queue_full"}
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def stamp():
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=7))).isoformat(timespec="seconds")


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.{uuid.uuid4().hex}.tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    replace_saved_status(temporary, path)


def replace_saved_status(temporary, path):
    # Windows readers (including PowerShell Get-Content) can briefly deny
    # delete-sharing. The prior checkpoint remains authoritative throughout
    # this bounded wait; persistent permission failures still raise with the
    # complete new temporary file retained for diagnosis/recovery.
    for attempt in range(7):
        try:
            os.replace(temporary, path)
            break
        except PermissionError as error:
            if getattr(error, "winerror", None) not in (5, 32, 33) or attempt == 6:
                raise
            time.sleep(.02 * 2**attempt)


def event(kind, **details):
    AUDIT.mkdir(parents=True, exist_ok=True)
    with (AUDIT / "events.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"at": stamp(), "pid": os.getpid(), "event": kind,
                                 **details}, ensure_ascii=False, allow_nan=False) + "\n")


def dashboard(data):
    # Refresh only reloads saved backend facts. It never advances a counter.
    pairs = [("Trạng thái", data.get("state")), ("Cập nhật", data.get("at")),
        ("Giai đoạn", data.get("stage")), ("Tổng tiến độ", data.get("overall_percent")),
        ("Tiến độ giai đoạn (backend)", data.get("backend_stage_percent")),
        ("Segment hiện tại", data.get("current_segments")),
        ("Hoàn tất segment / Tổng", f"{data.get('completed')} / {data.get('total')}"),
        ("Segment có file WAV", data.get("available_audio_segments")),
        ("Nội dung đã xác minh", data.get("verified_content_segments")),
        ("Failed / Retrying", f"{data.get('failed')} / {data.get('retrying')}"),
        ("Đã chạy (giây)", data.get("elapsed_seconds")), ("ETA (giây)", data.get("eta_seconds")),
        ("Tốc độ review (segment/giây)", data.get("review_segments_per_second")),
        ("Segment mới có WAV trong lượt", data.get("new_audio_segments_this_run")),
        ("Tốc độ tạo WAV (segment/giây)", data.get("speech_segments_per_second")),
        ("Lỗi gần nhất", data.get("latest_error")), ("Video dài", data.get("long_state"))]
    rows = "".join(f"<tr><th>{html.escape(label)}</th><td>{html.escape(str(value) if value is not None else 'Chưa đủ dữ liệu')}</td></tr>" for label, value in pairs)
    page = '<!doctype html><html lang="vi"><meta charset="utf-8"><meta http-equiv="refresh" content="10"><title>Tiến độ phục hồi local</title><style>body{font:16px system-ui;background:#101827;color:#edf3ff;max-width:1000px;margin:40px auto}table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:12px;border-bottom:1px solid #344054}th{width:30%}a{color:#8bc6ff}</style><h1>Tiến độ phục hồi local</h1><p>Số liệu backend đã lưu. Thời điểm cập nhật là dấu hiệu dữ liệu còn mới; tải lại trang không tạo tiến độ.</p><table>' + rows + '</table></html>'
    temporary = AUDIT / f"dashboard.html.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    temporary.write_text(page, encoding="utf-8")
    replace_saved_status(temporary, AUDIT / "dashboard.html")


class Ownership:
    """OS file lock is released on process death; a stale PID is never a lock."""
    def __init__(self, name="owner.lock"):
        self.name = name

    def __enter__(self):
        import msvcrt
        AUDIT.mkdir(parents=True, exist_ok=True)
        self.stream = (AUDIT / self.name).open("a+b")
        if os.fstat(self.stream.fileno()).st_size == 0:
            self.stream.write(b"0")
            self.stream.flush()
        self.stream.seek(0)
        try:
            msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            self.stream.close()
            raise RuntimeError("Another recovery supervisor already owns this project") from None
        return self

    def __exit__(self, *unused):
        import msvcrt
        self.stream.seek(0)
        msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
        self.stream.close()


def classify_error(error):
    """Classify the originating service; never infer that this is Codex 429."""
    code = getattr(error, "code", None)
    from core.engines.translation.opencode_client import (OpenCodeConfigurationError,
        OpenCodeModelError, OpenCodeTimeoutError)
    if isinstance(error, OpenCodeConfigurationError):
        code = "provider_configuration"
    elif isinstance(error, OpenCodeModelError):
        code = "provider_model"
    elif isinstance(error, OpenCodeTimeoutError):
        code = "provider_timeout"
    retry_after = getattr(error, "retry_after", None)
    current, seen = error, set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        candidate = ("provider_timeout" if isinstance(current, OpenCodeTimeoutError)
                     else getattr(current, "code", None))
        if candidate and (not code or candidate.startswith("provider_")
                          and candidate != "provider_failed"):
            code = candidate
        retry_after = getattr(current, "retry_after", None) or retry_after
        current = current.__cause__ or current.__context__
    text = str(error).casefold()
    waiting = code in SERVICE_WAIT_CODES or "quota" in text
    return {"service": "translation_review", "code": code or type(error).__name__,
            "waiting": waiting, "retry_after": retry_after}


def review_service_blocker(rows):
    # A second semantic/audio pass can preserve a draft with a diagnostic
    # instead of raising. That is still a service wait, never admission for
    # further requests from subsequent groups.
    for row in rows:
        diagnostic = (row.verification or {}).get("diagnostic") or {}
        if diagnostic.get("code") in SERVICE_WAIT_CODES:
            return {"service": "translation_review", "code": diagnostic["code"],
                    "retry_after": diagnostic.get("retry_after"), "segment_id": row.id,
                    "stage": diagnostic.get("stage"), "waiting": True}
    return None


def unresolved_runtime_diagnostics(rows):
    """Expose saved second-pass failures even when the reviewer kept a draft.

    Copy only safe category fields. A checkpoint without an error timestamp
    does not establish which historical failure happened last.
    """
    allowed = SERVICE_WAIT_CODES | {"provider_failed", "provider_timeout", "invalid_response",
        "asr_failed", "asr_timeout", "asr_unavailable", "runtime_unavailable"}
    records = []
    for row in rows:
        diagnostic = (row.verification or {}).get("diagnostic") or {}
        if row.needs_review and diagnostic.get("code") in allowed:
            stage = diagnostic.get("stage")
            record = {"segment_id": row.id, "code": diagnostic["code"],
                "service": "local_asr" if stage == "audio_evidence" else "translation_review",
                "basis": "unresolved_checkpoint_diagnostic"}
            if stage in {"audio_evidence", "audio_semantic_review", "semantic_request",
                         "semantic_second_pass", "address_context", "ocr_evidence"}:
                record["stage"] = stage
            retry_after = diagnostic.get("retry_after")
            if type(retry_after) in (int, float) and 0 <= retry_after <= 86400:
                record["retry_after"] = retry_after
            records.append(record)
    return records


def recover_ids(session):
    # Missing output first. Manual decisions retain ownership and are never
    # silently relabelled or treated as evidence of source silence.
    from core.translation_review import AutomaticTranslationReviewer
    ordered = sorted(session.segments.values(), key=lambda row: (bool(row.final_vi.strip()), row.start, row.id))
    current_sources = {str(row.id): getattr(row, 'text_zh', '') for row in ordered}
    def stale_context(row):
        # A persisted READY row can cite a source version corrected by a
        # later scoped review. The media checker binds exact source versions;
        # select this finite set for re-review without changing its verdict,
        # wording or WAV merely because the dependency is stale.
        audit = row.verification or {}
        snapshot = audit.get('address_context_sources')
        return bool(audit.get('address_stale_source_ids')) or (
            isinstance(snapshot, dict) and any(
                str(sid) not in current_sources or current_sources[str(sid)] != source
                for sid, source in snapshot.items()))
    return [row.id for row in ordered if (row.verification or {}).get("status") != "manual"
            and (row.needs_review or not row.final_vi.strip() and not row.confirmed_silence
                 or (row.verification or {}).get("status") in {None, "incomplete", "pending"}
                 or (row.verification or {}).get("review_gate_revision") != AutomaticTranslationReviewer.REVIEW_GATE_REVISION
                 or stale_context(row))]


def speech_repair_ids(session):
    """Select demonstrably invalid legacy pacing/timing, retaining healthy WAVs."""
    from core.streaming.audio_cache import resolve_dub_timing
    import math
    invalid, previous = set(), None
    for row in sorted(session.segments.values(), key=lambda item: (item.start, item.id)):
        if not row.final_vi.strip() or not row.audio_path:
            continue
        speed = getattr(row, "speed_ratio", 1.)
        if (not isinstance(speed, (int, float)) or not math.isfinite(speed)
                or not QA_SPEECH_SPEED_LIMITS[0] - 1e-9 <= speed <= QA_SPEECH_SPEED_LIMITS[1] + 1e-9):
            invalid.add(row.id)
        if not hasattr(row, "to_dict") or not hasattr(session, "_dub_audio_duration"):
            continue
        try:
            start, end = resolve_dub_timing(row.to_dict())
            duration = session._dub_audio_duration(row.audio_path)
            raw = getattr(row, "tts_duration", 0.)
            if duration > end - start + .001 or raw > 0 and speed > 0 and abs(duration - raw / speed) > .06:
                invalid.add(row.id)
            if previous and start < previous[1] - .001:
                invalid.update((previous[0], row.id))
            previous = (row.id, max(end, start + duration))
        except (OSError, ValueError, RuntimeError, ZeroDivisionError):
            invalid.add(row.id)
    return invalid


def configure_recovery_speech(session):
    """Produce the same cadence the independent media checker accepts.

    StreamingSession's ordinary 0.85--1.35 range is wider than this recovery's
    acceptance contract. Configure the producer before TTS, reflow or retries;
    retain complete speech and semantic pacing when a slot cannot fit.
    """
    session.aligner.min_speed, session.aligner.max_speed = QA_SPEECH_SPEED_LIMITS
    session._recovery_bidirectional_reflow = True
    event("RECOVERY_SPEECH_LIMITS", task_id=session.task_id,
          min_speed=session.aligner.min_speed, max_speed=session.aligner.max_speed)


def recover_legacy_timeline(session):
    """Publish a proven metadata-only repair before any new speech work."""
    from core.engines.alignment.dub_timing import plan_legacy_overlap_recovery
    # Lightweight fault-injection sessions have no production timing graph.
    # Real restored StreamingSession always supplies these methods.
    if not hasattr(session, '_dub_rows'):
        return
    rows = session._dub_rows()
    immutable = [row.id for row in session.segments.values()
                 if (row.verification or {}).get('status') == 'manual']
    plan = plan_legacy_overlap_recovery(rows, total_duration=session.total_duration,
                                       immutable_ids=immutable)
    if plan is None:
        raise RuntimeError('Cannot prove a complete bounded legacy timeline repair; checkpoint retained')
    if not plan:
        return
    before = [{'id': row['id'], 'dub_start': row.get('dub_start'), 'dub_end': row.get('dub_end')}
              for row in rows if row['id'] in plan]
    event('LEGACY_TIMELINE_REPAIR_PLANNED', task_id=session.task_id, before=before, plan=plan)
    # The production publisher shifts caption/word metadata with each slot;
    # no await can expose a partially published timeline to preview/export.
    session._publish_dub_plan(plan, None)
    session._invalidate_output()
    session._persist_if_enabled()
    event('LEGACY_TIMELINE_REPAIR_COMMITTED', task_id=session.task_id,
          segment_ids=list(plan), wav_bytes_changed=False, source_timestamps_changed=False)


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def archive_speech(sid, path):
    old = Path(path).resolve(strict=True)
    digest = file_sha256(old)
    target = AUDIT / "backups/audio" / f"{sid}-{digest[:16]}.wav"
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        temporary = target.with_suffix(".wav.copying")
        shutil.copy2(old, temporary)
        if file_sha256(temporary) != digest:
            raise RuntimeError("Cannot protect the prior WAV; refusing speech replacement")
        os.replace(temporary, target)
    if file_sha256(target) != digest:
        raise RuntimeError("Cannot protect the prior WAV; refusing speech replacement")
    return {"original": str(old), "backup": str(target), "sha256": digest}


def protect_existing_speech(session):
    """Protect every existing WAV before review can detach or retire its path."""
    inventory = []
    for row in session.segments.values():
        paths = ([row.audio_path] if row.audio_path else [])
        paths.extend(path for path in getattr(row, "superseded_audio_paths", [])
                     if Path(path).is_file() and path not in paths)
        for path in paths:
            inventory.append({"segment_id": row.id, **archive_speech(row.id, path)})
    write_json(AUDIT / "backups" / (session.task_id + "_audio_inventory.json"), inventory)
    event("EXISTING_SPEECH_PROTECTED", task_id=session.task_id, references=len(inventory))


def queue_protected_speech(session, row):
    """Archive the invalid prior WAV before scheduling production synthesis."""
    if row.audio_path:
        backup = archive_speech(row.id, row.audio_path)
        old = Path(backup["original"])
        event("INVALID_SPEECH_ARCHIVED", task_id=session.task_id, segment_id=row.id,
              **backup)
        retired = getattr(row, "superseded_audio_paths", [])
        if str(old) not in retired:
            retired.append(str(old))
        row.superseded_audio_paths = retired
    row.audio_path = row.audio_url = None
    row.status, row.failed_stage, row.error = "WAITING", "ALIGNING", None
    row._retry_synthesis = True
    session._persist_if_enabled()


class Monitor:
    def __init__(self, session, port):
        self.session, self.port = session, port
        self.started = time.monotonic()
        self.done, self.total = 0, 0
        self.latest_error = None
        self.failures = []
        self.state = "RECOVERING"
        self.short_pass = False
        self.long_state = "WAITING_FOR_SHORT_QA"
        self._speech_failure_baseline = {row.id: (row.status, getattr(row, 'error', None))
            for row in session.segments.values()}
        self._audio_baseline = {row.id: row.audio_path for row in session.segments.values()}

    def snapshot(self):
        import psutil
        session = self.session
        progress = session.get_progress()
        counts = Counter(row.status for row in session.segments.values())
        states = progress.get("segment_states", {})
        active = {row.id for row in session.segments.values()
                  if row.status in {"TTS", "ALIGNING", "TRANSLATING"}}
        active.update(getattr(session, "_reviewing_segment_ids", []))
        available_audio = sum(bool(row.audio_path) and Path(row.audio_path).is_file()
                              for row in session.segments.values())
        missing_audio_ids = [row.id for row in session.segments.values()
            if row.final_vi.strip() and not getattr(row, 'confirmed_silence', False)
            and (not row.audio_path or not Path(row.audio_path).is_file())]
        elapsed = time.monotonic() - self.started
        new_failures = []
        for row in session.segments.values():
            current = (row.status, getattr(row, 'error', None))
            previous = self._speech_failure_baseline.get(row.id)
            if (current != previous and row.status == 'FAILED' and current[1]
                    and getattr(row, 'failed_stage', None) in {'TTS', 'ALIGNING'}):
                new_failures.append(row.id)
            self._speech_failure_baseline[row.id] = current
        if new_failures:
            # Polling proves these failures were observed since the previous
            # sample, not the order of multiple failures inside that interval.
            self.latest_error = {'service': 'speech_recovery',
                'code': 'speech_stage_failed' if len(new_failures) == 1 else 'multiple_speech_stage_failures',
                'observed_at': stamp(), 'basis': 'backend_row_transition_since_previous_sample',
                **({'segment_id': new_failures[0],
                    'stage': session.segments[new_failures[0]].failed_stage} if len(new_failures) == 1
                   else {'segment_ids': sorted(new_failures)})}
            event('SPEECH_FAILURE_OBSERVED', task_id=session.task_id, **self.latest_error)
        new_audio = sum(bool(row.audio_path) and Path(row.audio_path).is_file()
            and row.audio_path != self._audio_baseline.get(row.id) for row in session.segments.values())
        verified = sum(not row.needs_review and (row.verification or {}).get("status") in
                       {"verified", "corrected", "manual"} for row in session.segments.values())
        diagnostics = unresolved_runtime_diagnostics(session.segments.values())
        reported_error = self.latest_error
        if reported_error is None and diagnostics:
            reported_error = diagnostics[0] if len(diagnostics) == 1 else {
                "code": "multiple_unresolved_runtime_diagnostics",
                "segment_ids": [item["segment_id"] for item in diagnostics],
                "basis": "checkpoint_has_no_error_order"}
        data = {"at": stamp(), "pid": os.getpid(), "backend": f"http://127.0.0.1:{self.port}",
                "state": self.state, "task_id": session.task_id, "stage": progress.get("stage"),
                "phase": progress.get("phase"), "overall_percent": 100 if self.state == "VERIFIED_COMPLETED" else None,
                "backend_stage_percent": progress.get("progress_pct"),
                "overall_percent_basis": "All stages including output QA must pass; no total percentage is inferred from one stage",
                "current_segments": sorted(active),
                "completed": states.get("COMPLETED", 0), "total": len(session.segments),
                "ready_segments": counts["READY"] + counts["PLAYED"],
                "available_audio_segments": available_audio,
                "missing_audio_segment_ids": sorted(missing_audio_ids),
                "failed": states.get("FAILED", counts["FAILED"]), "retrying": states.get("RETRY_PENDING", 0),
                "pending_review_targets": max(0, self.total - self.done),
                "reviewed_in_this_run": self.done, "review_targets": self.total,
                "verified_content_segments": verified, "review_failures": self.failures,
                "speech_speed_limits": {
                    "min": getattr(getattr(session, "aligner", None), "min_speed", None),
                    "max": getattr(getattr(session, "aligner", None), "max_speed", None)},
                "elapsed_seconds": round(elapsed, 3), "eta_seconds": progress.get("eta_seconds"),
                "review_segments_per_second": self.done / elapsed if elapsed and self.done else None,
                "new_audio_segments_this_run": new_audio,
                "speech_segments_per_second": new_audio / elapsed if elapsed >= 1 and new_audio else None,
                "latest_error": reported_error, "unresolved_runtime_diagnostics": diagnostics,
                "progress": progress,
                "resources": {"worker_rss_bytes": psutil.Process().memory_info().rss,
                              "available_ram_bytes": psutil.virtual_memory().available,
                              "free_disk_bytes": psutil.disk_usage(str(ROOT)).free},
                "short_pass": self.short_pass, "long_state": self.long_state}
        write_json(AUDIT / "progress.json", data)
        dashboard(data)
        with (AUDIT / "progress.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(data, ensure_ascii=False, allow_nan=False) + "\n")
        return data

    async def run(self):
        while True:
            self.snapshot()
            await asyncio.sleep(10)


async def recover_short(monitor, *, review_ids=None, repair_speech_ids=None, speech_scope_ids=None):
    from core.runtime_errors import redacted_detail, redacted_traceback
    session = monitor.session
    if session.is_running or session.is_editing or not session.video_path.is_file():
        raise RuntimeError("Recovery requires an idle saved session and an existing source")
    scope = None if speech_scope_ids is None else set(speech_scope_ids)
    if speech_scope_ids is not None and (
            review_ids != [] or not speech_scope_ids
            or any(type(sid) is not int or sid not in session.segments for sid in speech_scope_ids)
            or len(scope) != len(speech_scope_ids)
            or any(not session.segments[sid].final_vi.strip()
                   or (session.segments[sid].verification or {}).get('status') == 'manual'
                   for sid in speech_scope_ids)
            or not set(repair_speech_ids or []).issubset(scope)):
        raise ValueError('Speech scope requires speech-only and unique existing nonmanual speech IDs containing all repairs')
    if repair_speech_ids is not None and (
            any(type(sid) is not int or sid not in session.segments for sid in repair_speech_ids)
            or len(set(repair_speech_ids)) != len(repair_speech_ids)
            or any(not session.segments[sid].final_vi.strip()
                   or (session.segments[sid].verification or {}).get('status') == 'manual'
                   for sid in repair_speech_ids)):
        raise ValueError('Speech repair IDs must be unique existing nonmanual rows with Vietnamese speech')
    pending = recover_ids(session)
    if review_ids is not None:
        if (any(type(sid) is not int or sid not in session.segments for sid in review_ids)
                or len(set(review_ids)) != len(review_ids)):
            raise ValueError("Targeted recovery IDs must be unique existing segment IDs")
        selected = set(review_ids)
        pending = [sid for sid in pending if sid in selected]
        event("TARGETED_RECOVERY", task_id=session.task_id, requested_ids=list(review_ids),
              pending_ids=pending)
        if review_ids and scope is None:
            # A source-review repair is not fresh evidence for unrelated old
            # pacing failures. Keep its speech recovery in the requested set.
            scope = selected | set(repair_speech_ids or [])
            event("TARGETED_REVIEW_SPEECH_SCOPE", task_id=session.task_id, segment_ids=sorted(scope))
    configure_recovery_speech(session)
    # Production review/synthesis may retire superseded files. A manifest
    # backup alone cannot recover their bytes, including otherwise healthy
    # speech whose wording changes during this review.
    protect_existing_speech(session)
    recover_legacy_timeline(session)
    session._ensure_tts_engine()
    repair_audio = speech_repair_ids(session)
    repair_audio.update(repair_speech_ids or [])
    if scope is not None:
        repair_audio.intersection_update(scope)
        event('TARGETED_SPEECH_SCOPE', task_id=session.task_id, segment_ids=sorted(scope))
    # A current semantic verdict can retain an invalid legacy WAV. Such rows
    # need speech repair even when they do not require another provider review.
    for sid in repair_audio.difference(pending):
        queue_protected_speech(session, session.segments[sid])
    monitor.total = len(pending)
    session.auto_export_result = False
    session._run_started_at, session._run_finished_at = time.monotonic(), None
    session._run_ready_baseline = sum(row.status in {"READY", "PLAYED"} for row in session.segments.values())
    session.is_stopped, session.is_running, session.error = False, True, None
    session.pause_event.set()
    session.review_summary["status"] = "running"
    deferred = []
    # Capture this finite set once. No unbounded loop over unresolved content.
    try:
        for offset in range(0, len(pending), session.VISUAL_REVIEW_GROUP_SIZE):
            await session.pause_event.wait()
            if session.is_stopped:
                raise asyncio.CancelledError
            ids = pending[offset:offset + session.VISUAL_REVIEW_GROUP_SIZE]
            event("REVIEW_GROUP_START", task_id=session.task_id, segment_ids=ids)
            try:
                await session._review_translations(regenerate_audio=True, force_review=False,
                    segment_ids=set(ids), defer_audio=True)
                monitor.done += len(ids)
                session._persist_if_enabled()
                event("REVIEW_GROUP_COMMIT", task_id=session.task_id, segment_ids=ids)
                diagnostics = unresolved_runtime_diagnostics(session.segments[sid] for sid in ids)
                if diagnostics:
                    monitor.latest_error = {**diagnostics[-1], "observed_at": stamp()}
                    event("REVIEW_DIAGNOSTIC_SAVED", task_id=session.task_id, diagnostics=diagnostics)
                blocker = review_service_blocker(session.segments[sid] for sid in ids)
                if blocker:
                    monitor.latest_error = blocker
                    monitor.failures.append(blocker)
                    monitor.state = "WAITING_FOR_SERVICE"
                    session.review_summary["status"] = "incomplete"
                    session._persist_if_enabled()
                    event("REVIEW_SERVICE_WAIT", task_id=session.task_id, **blocker)
                    return False
            except asyncio.CancelledError:
                raise
            except Exception as error:
                classification = classify_error(error)
                detail = redacted_detail(error)
                trace = redacted_traceback(error)
                monitor.latest_error = {**classification, "detail": detail, "segment_ids": ids}
                monitor.failures.append(monitor.latest_error)
                event("REVIEW_GROUP_FAILED", task_id=session.task_id,
                      **monitor.latest_error, traceback=trace)
                logging.getLogger("errors").error("RECOVERY_REVIEW_FAILED run_id=%s ids=%s traceback=%s",
                    session.task_id, ids, trace)
                session.review_summary["status"] = "incomplete"
                session._persist_if_enabled()
                if classification["waiting"] or getattr(session, "_persistence_capacity_failed", False):
                    monitor.state = "WAITING_FOR_SERVICE" if classification["waiting"] else "BLOCKED"
                    return False
                continue
            # Valid old WAVs stay outside this set. Only publish missing or
            # wording-changed speech through the production timing/cache path.
            for sid in ids:
                row = session.segments[sid]
                if sid in repair_audio and row.audio_path:
                    queue_protected_speech(session, row)
                if row.final_vi.strip() and not row.audio_path:
                    row.status, row.failed_stage = "WAITING", "TTS"
                    row._retry_synthesis = True
            deferred.extend(await session._resume_speech_rows(
                (session.segments[sid] for sid in ids), defer_pacing=True))
            session._persist_if_enabled()
            monitor.snapshot()
        # Repair any independent, accepted speech lost in a previous crash.
        for row in session.segments.values():
            if (scope is None or row.id in scope) and row.final_vi.strip() and not row.audio_path and not row.needs_review:
                row.status, row.failed_stage = "WAITING", "TTS"
                row._retry_synthesis = True
        deferred_ids = {entry[0].id for entry in deferred}
        deferred.extend(await session._resume_speech_rows(
            (row for row in session.segments.values() if row.id not in deferred_ids
             and (scope is None or row.id in scope)), defer_pacing=True))
        await session._resume_deferred_speech_rows(deferred)
        session.review_summary["status"] = "incomplete" if monitor.failures else "completed"
        session._refresh_review_counts()
        from core.streaming.output_gate import final_output_metadata
        gate = final_output_metadata(session.segments.values())
        failed = [row.id for row in session.segments.values() if row.status == "FAILED"]
        if gate["final_output_blocked"] or failed or any(row.needs_review for row in session.segments.values()) or monitor.failures:
            monitor.state = "BLOCKED_CONTENT_OR_SPEECH"
            event("SHORT_BLOCKED", missing_speech_ids=gate["missing_speech_ids"],
                  failed_ids=failed, needs_review_ids=[row.id for row in session.segments.values() if row.needs_review])
            return False
        return True
    finally:
        session.is_running = False
        session._run_finished_at = time.monotonic()
        session._persist_if_enabled()
        monitor.snapshot()


async def verify_media(session, label, receipt=None):
    manifest = ROOT / "workspace/projects" / (session.task_id + ".json")
    output = ROOT / "workspace/outputs" / session.output_filename
    command = [sys.executable, "-B", str(ROOT / "reports/autonomous_audit/verify_output.py"),
        "--manifest", str(manifest), "--output", str(output), "--video-label", label,
        "--qa-root", str(AUDIT / "qa")]
    if receipt:
        command.extend(["--perceptual-evidence", str(Path(receipt).resolve(strict=True))])
    result = await asyncio.to_thread(subprocess.run, command, cwd=ROOT,
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1300)
    (AUDIT / (label.lower() + "_qa.log")).write_text(result.stdout + result.stderr, encoding="utf-8")
    event(label + "_QA_RESULT", exit_code=result.returncode, output=str(output))
    return result.returncode == 0


async def run_long(monitor, *, short_pass, receipt=None):
    if short_pass is not True:
        raise RuntimeError("Long-video admission requires actual short-video QA PASS")
    import main
    from core.downloader import VideoDownloader
    from core.streaming.session_store import list_saved_sessions, restore_saved_session, _read
    from core.streaming.pipeline import get_streaming_session
    identity = VideoDownloader.source_identity(LONG_URL)
    saved = await asyncio.to_thread(list_saved_sessions)
    session = None
    for row in saved:
        record = await asyncio.to_thread(_read, row["task_id"])
        source = record["session"].get("source_url")
        if source and VideoDownloader.source_identity(source) == identity:
            session = restore_saved_session(row["task_id"], event_callback=lambda kind, payload:
                main.broadcast_session_event(row["task_id"], kind, payload))
            break
    if session is None:
        result = await main.start_streaming_url(main.StreamUrlRequest(url=LONG_URL,
            visual_translation=True, translation_mode="full", tts_engine="edge-tts"))
        session = get_streaming_session(result["task_id"])
        configure_recovery_speech(session)
        session.auto_export_result = False
        await asyncio.sleep(0)  # allow the backend's already-created source owner to reserve
    else:
        configure_recovery_speech(session)
        if session.can_retry:
            await session.retry_failed_synthesis()
        elif not session.initialized:
            raise RuntimeError("Existing long checkpoint cannot resume; refusing a duplicate job")
    session.auto_export_result = False
    monitor.session, monitor.started = session, time.monotonic()
    monitor.state, monitor.done, monitor.total = "PROCESSING_LONG", 0, 0
    monitor.long_state = "RUNNING"
    event("LONG_ADMITTED", task_id=session.task_id, restored=any(row["task_id"] == session.task_id for row in saved))
    # Runtime bounds are independent of Codex turn duration. Existing workers
    # apply their per-service timeouts and checkpoint/retry contracts.
    deadline = time.monotonic() + 24 * 3600
    while session.is_running or any(owner is not None and not owner.done()
            for owner in (session.start_task, session.worker_task, session.review_task)):
        if time.monotonic() >= deadline:
            session.pause()
            session.persist()
            monitor.state = "WAITING_RUNTIME_LIMIT"
            monitor.long_state = monitor.state
            return False
        await asyncio.sleep(10)
    if session.error or session.review_summary.get("status") != "completed" or any(
            row.needs_review or row.status not in {"READY", "PLAYED"} for row in session.segments.values()):
        monitor.state = "BLOCKED_LONG"
        monitor.long_state = monitor.state
        session.persist()
        event("LONG_BLOCKED", task_id=session.task_id, stage=session.get_progress().get("stage"))
        return False
    session.auto_export_result = False
    await main.export_hq(main.ExportHQRequest(task_id=session.task_id))
    session.persist()
    passed = await verify_media(session, "LONG", receipt)
    monitor.state = "VERIFIED_COMPLETED" if passed else "LONG_QA_REQUIRED"
    monitor.long_state = monitor.state
    return passed


async def worker(args):
    import uvicorn
    import main
    from core.streaming.session_store import restore_saved_session
    from core.services.service_manager import ServiceManager
    session = restore_saved_session(SHORT_ID,
        event_callback=lambda kind, payload: main.broadcast_session_event(SHORT_ID, kind, payload))
    if session is None:
        raise RuntimeError("The short-video checkpoint is missing or invalid; refusing to create a duplicate")
    port = ServiceManager.find_free_port()
    server = uvicorn.Server(uvicorn.Config(main.app, host="127.0.0.1", port=port,
        log_config=None, log_level="warning", access_log=False, loop="none"))
    serving = asyncio.create_task(server.serve())
    monitor = Monitor(session, port)
    polling = asyncio.create_task(monitor.run())
    event("WORKER_RESTORED", segment_count=len(session.segments),
          existing_audio_count=sum(bool(row.audio_path) for row in session.segments.values()), port=port)
    manifest = ROOT / "workspace/projects" / (SHORT_ID + ".json")
    if args.restart_probe and not (AUDIT / "restart_probe.json").exists():
        # Crash after validating the real checkpoint, before any processing or
        # source write. The supervisor must re-open the identical bytes.
        write_json(AUDIT / "restart_probe.json", {"sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
                   "segment_count": len(session.segments), "first_pid": os.getpid()})
        event("CHECKPOINT_RESTART_PROBE")
        os._exit(99)
    probe_path = AUDIT / "restart_probe.json"
    if probe_path.exists():
        probe = json.loads(probe_path.read_text(encoding="utf-8"))
        if not probe.get("verified"):
            verified = probe["sha256"] == hashlib.sha256(manifest.read_bytes()).hexdigest()
            event("CHECKPOINT_RESTART_VERIFIED", verified=verified, previous_pid=probe["first_pid"])
            if not verified:
                raise RuntimeError("Checkpoint changed during the restart probe")
            write_json(probe_path, {**probe, "verified": True, "restored_pid": os.getpid(), "at": stamp()})
    try:
        selected = [] if getattr(args, "speech_only", False) else getattr(args, "review_ids", None)
        ready = await recover_short(monitor, review_ids=selected,
            repair_speech_ids=getattr(args, 'repair_speech_ids', None),
            speech_scope_ids=getattr(args, 'speech_scope_ids', None))
        if ready:
            monitor.state = "RENDERING"
            session.is_running = False
            await main.export_hq(main.ExportHQRequest(task_id=SHORT_ID))
            session.persist()
            monitor.state = "QA_REQUIRED"
            # The independent checker requires perceptual review. No unsigned
            # receipt is manufactured by this runtime to admit the long video.
            short_pass = await verify_media(session, "SHORT", args.short_perceptual_evidence)
            monitor.short_pass = short_pass
            monitor.state = "SHORT_QA_PASS" if short_pass else "QA_REQUIRED"
            if short_pass and await run_long(monitor, short_pass=True, receipt=args.long_perceptual_evidence):
                return 0
        return 20
    except asyncio.CancelledError:
        monitor.state = "STOPPED"
        event("RECOVERY_STOPPED", task_id=monitor.session.task_id)
        # An explicit stop is terminal for this owner. It must never trigger
        # the crash supervisor to resume provider work behind the user's back.
        return 20
    except Exception as error:
        from core.runtime_errors import redacted_detail, redacted_traceback
        monitor.state = "WORKER_FAILED"
        monitor.latest_error = {"code": type(error).__name__, "detail": redacted_detail(error)}
        event("WORKER_FAILED", task_id=monitor.session.task_id,
              **monitor.latest_error, traceback=redacted_traceback(error))
        raise
    finally:
        monitor.snapshot()
        polling.cancel()
        await asyncio.gather(polling, return_exceptions=True)
        server.should_exit = True
        await serving
        session._release_runtime()


def supervisor(args):
    import psutil
    with Ownership():
        for process in ([] if args.lifecycle_probe else psutil.process_iter(["pid", "cmdline"])):
            command = " ".join(process.info["cmdline"] or [])
            if str(ROOT).casefold() in command.casefold() and "desktop_app.py" in command:
                raise RuntimeError("Desktop still owns saved projects; close the idle desktop before recovery")
        event("SUPERVISOR_STARTED", parent_pid=os.getppid(), max_crash_restarts=2)
        for attempt in range(3):
            command = [sys.executable, "-B", str(Path(__file__).resolve()), "--worker"]
            if args.restart_probe:
                command.append("--restart-probe")
            if args.lifecycle_probe:
                command.extend(["--lifecycle-probe", args.lifecycle_probe])
            if getattr(args, "speech_only", False):
                command.append("--speech-only")
            elif getattr(args, "review_ids", None) is not None:
                command.extend(["--review-ids", ",".join(map(str, args.review_ids))])
            if getattr(args, 'repair_speech_ids', None):
                command.extend(['--repair-speech-ids', ','.join(map(str, args.repair_speech_ids))])
            if getattr(args, 'speech_scope_ids', None):
                command.extend(['--speech-scope-ids', ','.join(map(str, args.speech_scope_ids))])
            for name in ("short_perceptual_evidence", "long_perceptual_evidence"):
                if getattr(args, name, None):
                    command.extend(["--" + name.replace("_", "-"), getattr(args, name)])
            with (AUDIT / "worker_stdout.log").open("a", encoding="utf-8") as out, (AUDIT / "worker_stderr.log").open("a", encoding="utf-8") as err:
                child = subprocess.Popen(command, cwd=ROOT, stdout=out, stderr=err)
                write_json(AUDIT / "supervisor.json", {"at": stamp(), "pid": os.getpid(),
                    "worker_pid": child.pid, "attempt": attempt + 1, "state": "RUNNING"})
                code = child.wait()
            event("WORKER_EXITED", worker_pid=child.pid, exit_code=code, attempt=attempt + 1)
            if code in {0, 20}:
                write_json(AUDIT / "supervisor.json", {"at": stamp(), "pid": os.getpid(),
                    "state": "WAITING" if code == 20 else "COMPLETED", "exit_code": code})
                return code
            time.sleep(min(30, 2 ** (attempt + 1)))
        event("CRASH_RESTART_LIMIT_REACHED")
        write_json(AUDIT / "supervisor.json", {"at": stamp(), "pid": os.getpid(),
            "state": "BLOCKED_CRASH_LIMIT", "exit_code": 21, "attempts": 3})
        return 21


def lifecycle_probe():
    """Exercise this supervisor with a copied real checkpoint, without jobs.

    The probe owns only the explicitly supplied test directory. It never calls
    a provider, changes a live checkpoint, or claims that either video passed.
    """
    from core.streaming.session_store import _decode_project, _validate
    checkpoint = AUDIT / "checkpoint.json"
    raw = checkpoint.read_bytes()
    record = _decode_project(raw.decode("utf-8"))
    _validate(record, record["task_id"])
    digest = hashlib.sha256(raw).hexdigest()
    saved = AUDIT / "probe_checkpoint.json"
    if not saved.exists():
        write_json(saved, {"sha256": digest, "task_id": record["task_id"], "segment_count": len(record["segments"]),
                           "first_pid": os.getpid()})
        event("PROBE_CRASH_AFTER_VALIDATED_CHECKPOINT", sha256=digest)
        os._exit(99)
    before = json.loads(saved.read_text(encoding="utf-8"))
    if before["sha256"] != digest:
        raise RuntimeError("Probe checkpoint corrupt or changed")
    event("PROBE_CHECKPOINT_RESTORED", sha256=digest, segment_count=len(record["segments"]), previous_pid=before["first_pid"])
    for sample in range(10):
        write_json(AUDIT / "probe_progress.json", {"kind": "LIFECYCLE_PROBE_ONLY", "pid": os.getpid(),
                   "sample": sample + 1, "segment_count": len(record["segments"]), "checkpoint_sha256": digest,
                   "video_qa_pass": False})
        time.sleep(.2)
    return 0


def parse_segment_ids(value):
    try:
        result = [int(piece) for piece in value.split(",")]
        if any(sid < 0 for sid in result) or len(set(result)) != len(result):
            raise ValueError
        return result
    except ValueError:
        raise argparse.ArgumentTypeError("Use unique nonnegative segment IDs separated by commas") from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--restart-probe", action="store_true")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--review-ids", type=parse_segment_ids,
        help="Review only these existing pending IDs; retain every other row and output gate")
    selection.add_argument("--speech-only", action="store_true",
        help="Resume cached speech/pacing without repeating provider content review")
    parser.add_argument('--repair-speech-ids', type=parse_segment_ids,
        help='Repair only specified speech drafts after a diagnosed fix; all content/QA gates remain')
    parser.add_argument('--speech-scope-ids', type=parse_segment_ids,
        help='With speech-only, process only these speech rows; retain unrelated failed rows and every gate')
    parser.add_argument("--short-perceptual-evidence")
    parser.add_argument("--long-perceptual-evidence")
    parser.add_argument("--lifecycle-probe", help="Test-only directory containing a copied checkpoint.json; never process video")
    args = parser.parse_args()
    if args.speech_scope_ids is not None and not args.speech_only:
        parser.error('--speech-scope-ids requires --speech-only')
    if args.lifecycle_probe:
        global AUDIT
        AUDIT = Path(args.lifecycle_probe).resolve(strict=True)
        if AUDIT == ROOT or ROOT.is_relative_to(AUDIT) or AUDIT == ROOT / "workspace/projects":
            raise ValueError("The lifecycle probe must own a separate test directory")
    if args.worker:
        if args.lifecycle_probe:
            with Ownership("worker.lock"):
                return lifecycle_probe()
        from core.services.backend_runtime import create_backend_loop
        loop = create_backend_loop()
        asyncio.set_event_loop(loop)
        with Ownership("worker.lock"):
            try:
                return loop.run_until_complete(worker(args))
            finally:
                loop.run_until_complete(loop.shutdown_asyncgens())
                loop.run_until_complete(loop.shutdown_default_executor())
                loop.close()
    return supervisor(args)


if __name__ == "__main__":
    raise SystemExit(main())
