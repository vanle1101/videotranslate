"""Bounded translation owners, with independent sequential native/media stages."""
import asyncio
import json
import time
from copy import deepcopy

from core.translation_context import dialogue_context, neighbor_context
from core.streaming.native_recovery import native_language, review_native_rows
from core.multilingual_translation import is_untranslated_placeholder


def missing(row):
    return not row.final_vi.strip() or is_untranslated_placeholder(row.final_vi)


def _record_work(session, stage, started):
    profiler = getattr(session, "_stage_profiler", None)
    if profiler is not None:
        profiler.record_work(stage, max(0., time.monotonic() - started))


async def translate_nonvisual_rows(session, rows):
    from core.streaming.pipeline import _transient_failure, _naturalize_spoken_line

    rows = sorted(rows, key=lambda row: (row.start, row.id))
    targets = [row for row in rows if row.status == "WAITING" and missing(row)
               and not row.confirmed_silence and (row.verification or {}).get("status") != "manual"]
    if not targets:
        return

    async def check():
        await session.pause_event.wait()
        if session.is_stopped or not session.is_running:
            raise asyncio.CancelledError

    def owned(row, revision, source):
        return (session.segments.get(row.id) is row and row.revision == revision
                and row.text_zh == source and (row.verification or {}).get("status") != "manual")

    async def failure(row, error):
        row.failed_stage, row.status, row.error = row.status, "FAILED", str(error)
        session._persist_if_enabled()
        await session.emit("segment_update", row.to_dict())

    # ASR remains sequential and establishes immutable source/context before
    # independent AI requests start. Native recovered transcripts are retained.
    eligible = []
    for row in targets:
        await check()
        revision = row.revision
        try:
            if not native_language(row) and not row.asr_pretranscribed:
                await session._recognize_nonvisual_segment(row)
            if not row.text_zh.strip() and not row.confirmed_silence:
                session._raise_missing_source(row)
            eligible.append(row)
        except Exception as error:
            if getattr(session, "_persistence_capacity_failed", False):
                raise
            if session.segments.get(row.id) is not row or row.revision != revision or (row.verification or {}).get("status") == "manual":
                continue
            await failure(row, error)
    if (eligible and getattr(session.translator, "provider", None) == "ollama"
            and any(not native_language(row.to_dict()) for row in eligible)):
        # Detect a stopped local runner or missing model once before changing
        # any target row to TRANSLATING/FAILED. The provider boundary keeps the
        # same guard for other entry points and for later resumed work.
        await session._run_blocking(session.translator.ensure_provider_ready)
    references = deepcopy([row.to_dict() for row in session.segments.values()])
    jobs, speech = asyncio.Queue(), asyncio.Queue()
    pending = len(eligible)
    completed = asyncio.get_running_loop().create_future()
    timers = set()
    if not pending:
        completed.set_result(None)
    ollama_batching_available = (
        getattr(session.translator, "provider", None) == "ollama"
        and callable(getattr(session.translator, "_execute_3tier_translation", None)))
    batch_targets = ([row for row in eligible if not native_language(row.to_dict())]
                     if ollama_batching_available else [])
    batch_target_ids = {id(row) for row in batch_targets}
    regular_targets = [row for row in eligible if id(row) not in batch_target_ids]
    for row in regular_targets:
        jobs.put_nowait((row, row.revision, row.text_zh, deepcopy(row.to_dict()), 0))

    def terminal():
        nonlocal pending
        pending -= 1
        if not pending and not completed.done():
            completed.set_result(None)

    async def delayed(item, delay):
        row, revision, source, snapshot, attempt = item
        try:
            await session._wait_automatic_recovery(f"dịch câu {row.id + 1}", attempt, delay)
            jobs.put_nowait(item)
        except BaseException as error:
            if not completed.done():
                completed.set_exception(error)

    def observe(task):
        if task.cancelled():
            if not completed.done():
                completed.cancel()
        elif task.exception() is not None and not completed.done():
            completed.set_exception(task.exception())

    async def translate():
        while True:
            row, revision, source, snapshot, attempt = await jobs.get()
            context = neighbor_context(references, [snapshot], neighbors=3)
            # Retain measured speaker/address metadata in these original rows;
            # neighboring translations are references, never writable targets.
            local = dialogue_context(context, [snapshot], max_rows=7)
            for item in local:
                item["zh"] = item.get("text_zh", "")
                item["vi"] = item.get("final_vi", "")
            rescheduled = False
            try:
                while True:
                    await check()
                    if not owned(row, revision, source) or not missing(row):
                        break
                    row.status, row.failed_stage, row.error = "TRANSLATING", "TRANSLATING", None
                    await session._segment_progress("translate", f"Đang dịch câu {row.id + 1}")
                    await session.emit("segment_update", row.to_dict())
                    try:
                        if native_language(snapshot):
                            started = time.monotonic()
                            try:
                                result = await session._run_blocking(review_native_rows, [snapshot],
                                    context=context, cancel_check=lambda: session.is_stopped)
                            finally:
                                _record_work(session, "translation", started)
                            result = result["segments"][row.id]
                        elif source.strip():
                            started = time.monotonic()
                            try:
                                result = await session._run_blocking(session.translator.translate_single_segment,
                                    text_zh=source, duration=row.duration, rolling_context=deepcopy(local))
                            finally:
                                _record_work(session, "translation", started)
                        else:
                            result = {"final_vi": ""}
                        await check()
                        await session._wait_for_edit_publication()
                        if not owned(row, revision, source) or not missing(row):
                            break
                        text = result.get("final_vi", "")
                        if (source.strip() and (not isinstance(text, str) or not text.strip())) or is_untranslated_placeholder(text):
                            raise RuntimeError("Dịch thuật chưa tạo được lời Việt có căn cứ.")
                        with session._durable_speaker_review_publication(row):
                            if native_language(snapshot):
                                session._apply_review_metadata(row, result)
                            else:
                                row.literal_vi = result.get("literal_vi", text)
                                row.natural_vi = result.get("natural_vi", text)
                                row.final_vi = _naturalize_spoken_line(source, text)
                                row.needs_review = True
                                row.review_reason = result.get("review_reason") or "Bản dịch mới chưa được kiểm định độc lập; giữ bằng chứng nguồn để kiểm tra."
                                # Evidence about the source survives, but a
                                # verdict for previous wording cannot certify
                                # the newly published translation.
                                row.verification = {**deepcopy(row.verification or {}),
                                    "status": "pending", "semantic_verified": False}
                                for key in ("pacing", "multilingual_review",
                                            "address_verified", "meaning_verified", "roles_verified"):
                                    row.verification.pop(key, None)
                            row.status, row.failed_stage, row.error = "WAITING", "TTS", None
                            row._retry_synthesis = True
                        await session.emit("segment_update", row.to_dict())
                        speech.put_nowait((row, revision, row.final_vi))
                        break
                    except Exception as error:
                        if getattr(session, "_persistence_capacity_failed", False):
                            raise
                        if not owned(row, revision, source):
                            break
                        await failure(row, error)
                        attempt += 1
                        delay = session._recovery_delay(attempt, error) if _transient_failure(error) else None
                        if delay is None:
                            break
                        timer = asyncio.create_task(delayed((row, revision, source, snapshot, attempt), delay))
                        timers.add(timer)
                        timer.add_done_callback(timers.discard)
                        rescheduled = True
                        break
            except BaseException as error:
                if not completed.done():
                    completed.set_exception(error)
                raise
            finally:
                jobs.task_done()
                if not rescheduled:
                    terminal()

    async def synthesize():
        while True:
            item = await speech.get()
            try:
                if item is None:
                    return
                row, revision, text = item
                await check()
                if session.segments.get(row.id) is not row or row.revision != revision or row.final_vi != text or row.status != "WAITING":
                    continue
                try:
                    await session._synthesize_segment(row, allow_pacing=False)
                    if session.segments.get(row.id) is not row:
                        continue
                except Exception as error:
                    if getattr(session, "_persistence_capacity_failed", False):
                        raise
                    if session.segments.get(row.id) is not row or row.revision != revision or row.final_vi != text:
                        continue
                    await failure(row, error)
                    session._remember_transient_speech_failure(row, error)
            finally:
                speech.task_done()

    async def translate_ollama_batches():
        """Translate small consecutive source batches with one local runner."""
        translator = session.translator
        batch_size = max(1, min(16, int(getattr(
            translator, "MAX_OLLAMA_TRANSLATION_TARGETS", 16))))
        source_context = [translator._translation_source_row(row) for row in references]
        try:
            ordered = [(row, row.revision, row.text_zh, deepcopy(row.to_dict()), 0)
                       for row in sorted(batch_targets, key=lambda row: (row.start, row.id))]
            for offset in range(0, len(ordered), batch_size):
                items = ordered[offset:offset + batch_size]
                attempt = max((item[4] for item in items), default=0)
                while True:
                    await check()
                    active_items = [item for item in items
                                    if owned(item[0], item[1], item[2]) and missing(item[0])]
                    if not active_items:
                        break
                    for row, revision, source, snapshot, _ in active_items:
                        row.status, row.failed_stage, row.error = "TRANSLATING", "TRANSLATING", None
                        await session.emit("segment_update", row.to_dict())
                    first_id = active_items[0][0].id + 1
                    last_id = active_items[-1][0].id + 1
                    await session._segment_progress(
                        "translate", f"Đang dịch lô câu {first_id}–{last_id}")
                    try:
                        payload = [translator._translation_source_row(item[3])
                                   for item in active_items]
                        full_text = json.dumps(source_context, ensure_ascii=False)
                        context_info = translator._extract_context_and_glossary(full_text)
                        started = time.monotonic()
                        try:
                            result_map = await session._run_blocking(
                                translator._execute_3tier_translation, payload, context_info,
                                source_context=source_context)
                        finally:
                            _record_work(session, "translation", started)
                        row_errors = getattr(result_map, "errors", {})
                        for row, revision, source, snapshot, _ in active_items:
                            await check()
                            await session._wait_for_edit_publication()
                            if not owned(row, revision, source) or not missing(row):
                                continue
                            if row.id in row_errors:
                                await failure(row, row_errors[row.id])
                                continue
                            result = result_map.get(row.id)
                            text = result.get("final_vi", "") if isinstance(result, dict) else ""
                            if (not isinstance(text, str) or not text.strip()
                                    or is_untranslated_placeholder(text)):
                                await failure(row, RuntimeError("Dịch thuật chưa tạo được lời Việt có căn cứ."))
                                continue
                            with session._durable_speaker_review_publication(row):
                                row.literal_vi = result.get("literal_vi", text)
                                row.natural_vi = result.get("natural_vi", text)
                                row.final_vi = _naturalize_spoken_line(source, text)
                                row.needs_review = True
                                row.review_reason = result.get("review_reason") or "Bản dịch mới chưa được kiểm định độc lập; giữ bằng chứng nguồn để kiểm tra."
                                row.verification = {**deepcopy(row.verification or {}),
                                    "status": "pending", "semantic_verified": False}
                                for key in ("pacing", "multilingual_review", "address_verified",
                                            "meaning_verified", "roles_verified"):
                                    row.verification.pop(key, None)
                                row.status, row.failed_stage, row.error = "WAITING", "TTS", None
                                row._retry_synthesis = True
                            await session.emit("segment_update", row.to_dict())
                            speech.put_nowait((row, revision, row.final_vi))
                        break
                    except Exception as error:
                        if getattr(session, "_persistence_capacity_failed", False):
                            raise
                        for row, revision, source, snapshot, _ in active_items:
                            if owned(row, revision, source) and missing(row):
                                await failure(row, error)
                        attempt += 1
                        delay = session._recovery_delay(attempt, error) if _transient_failure(error) else None
                        if delay is None:
                            break
                        await session._wait_automatic_recovery(
                            f"dịch lô câu {first_id}–{last_id}", attempt, delay)
                for _item in items:
                    terminal()
        except BaseException as error:
            if not completed.done():
                completed.set_exception(error)
            raise

    consumer = asyncio.create_task(synthesize())
    workers = []
    if batch_targets:
        workers.append(asyncio.create_task(translate_ollama_batches()))
    if regular_targets:
        workers.extend(asyncio.create_task(translate())
                       for _ in range(min(session.translation_chunk_workers, len(regular_targets))))
    for worker in workers:
        worker.add_done_callback(observe)
    async def produce():
        await completed
        speech.put_nowait(None)
    producer = asyncio.create_task(produce())
    try:
        await asyncio.gather(producer, consumer)
    finally:
        owned_tasks = [producer, consumer, *workers, *timers]
        for task in owned_tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*owned_tasks, return_exceptions=True)
