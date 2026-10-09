"""Audio identity enrichment, independently resumable from source/translation/TTS.

Existing transcript IDs, timestamps, manual edits and healthy WAVs never change.
Automatic voice labels are hypotheses, not proof of character relationships.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
import logging
from pathlib import Path

from config import settings


def _speaker_adapter(cache_dir, source_id):
    from core.engines.asr.speaker_diarization import BoundedSpeakerDiarizer
    from core.engines.asr.speaker_registry import digest
    from core.engines.asr import diarization_assets
    from importlib.metadata import version
    from core.review_checkpoint import file_digest
    module_root = Path(diarization_assets.__file__).parent
    revision = digest({"models": [diarization_assets.SEGMENTATION_SHA256, diarization_assets.EMBEDDING_SHA256],
        "runtime": version("sherpa-onnx"), "threads": settings.DIARIZATION_CPU_THREADS,
        "implementation": {name: file_digest(module_root / name) for name in
            ("diarization_assets.py", "diarization_worker.py", "speaker_diarization.py", "speaker_registry.py")}})
    return BoundedSpeakerDiarizer(cache_dir / "speakers" / (revision + ".sqlite"), source_id)


async def annotate_source_rows(session, rows, start, end):
    if not settings.DIARIZATION_ENABLED or not rows:
        return deepcopy(rows)
    from core.streaming.chunked_source import ensure_source_identity
    source_id = await session._run_blocking(ensure_source_identity, session)
    loop = asyncio.get_running_loop()
    active = True
    async def publish(value):
        if active and not session.is_stopped:
            await session.report_progress("prepare", "Đang phân biệt giọng nói trong đoạn…", None,
                speaker_completed_frames=value["processed"], speaker_total_frames=value["total"],
                chunk_start=start, chunk_end=end)
    def progress(value):
        if active and not session.is_stopped:
            loop.call_soon_threadsafe(lambda: asyncio.create_task(publish(value)))
    try:
        adapter = _speaker_adapter(session.cache_dir, source_id)
        result = await session._run_blocking(adapter.diarize, session.video_path,
            max(0., start - 2.), min(session.total_duration, end + 2.),
            owned_start=start, owned_end=end, cancel_check=lambda: session.is_stopped,
            progress_callback=progress)
        return adapter.annotate(rows, result)
    except asyncio.CancelledError:
        raise
    except Exception as error:
        if session.is_stopped:
            raise asyncio.CancelledError from None
        # This optional evidence stage cannot erase valid ASR or hold unrelated
        # translation/TTS. It has its own bounded worker and retries on Resume.
        warning = "Chưa phân biệt được giọng nói của đoạn; giữ lời nguồn và các vai chưa chắc chắn để rà lại."
        if warning not in session.warnings:
            session.warnings.append(warning)
        logging.getLogger("errors").warning(
            "[%s] SPEAKER_EVIDENCE_PENDING source_start=%s source_end=%s error_type=%s",
            session.task_id, start, end, type(error).__name__)
        return deepcopy(rows)
    finally:
        active = False


async def recover_speaker_evidence(session):
    """Enrich old retained rows in bounded windows; never re-run successful ASR."""
    if not settings.DIARIZATION_ENABLED or not session._chunked_source_started:
        return
    rows = sorted((row for row in session.segments.values()
        if session._published_row(row) and not row.speaker_evidence
        and (row.verification or {}).get("status") != "manual"), key=lambda row: (row.start, row.id))
    offset = 0
    while offset < len(rows):
        await session.pause_event.wait()
        if session.is_stopped:
            raise asyncio.CancelledError
        start = rows[offset].start
        batch = []
        while offset < len(rows) and rows[offset].end <= start + 56:
            batch.append(rows[offset])
            offset += 1
        if not batch:
            # An existing legacy utterance longer than the bounded model input
            # stays uncertain; never allocate its entire audio or change IDs.
            offset += 1
            continue
        revisions = {row.id: row.revision for row in batch}
        enriched = await annotate_source_rows(session, [row.to_dict() for row in batch], start, batch[-1].end)
        for value in enriched:
            row = session.segments[value["id"]]
            if row.revision != revisions[row.id] or (row.verification or {}).get("status") == "manual":
                continue
            if not isinstance(value.get("speaker_evidence"), dict):
                continue
            row.speaker_id = value.get("speaker_id")
            row.speaker_evidence = deepcopy(value["speaker_evidence"])
            row.speaker_diagnostics = deepcopy(value.get("speaker_diagnostics"))
            # A previously empty draft can use the new evidence in a fresh
            # review. Spoken negative verdicts remain negative; no blanket
            # re-audit or forced semantic approval is caused by this migration.
            audit = row.verification or {}
            if row.text_zh.strip() and not row.final_vi.strip() and audit.get("status") == "unresolved":
                row.verification = {**audit, "status": "incomplete", "semantic_verified": False}
            await session.emit("segment_update", row.to_dict())
        session._persist_if_enabled()
