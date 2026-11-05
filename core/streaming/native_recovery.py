"""Narrow translation routing for explicitly recovered native-language rows."""
from copy import deepcopy

from core.multilingual_translation import translate_and_review
from core.engines.asr.source_language import SUPPORTED_SOURCE_LANGUAGES


def native_ocr_evidence(engine, video_path, rows, start, end, *, cancel_check=None, detail_callback=None):
    """Read Chinese captions as timed references, never as native ASR truth."""
    from core.engines.asr.native_process import check_cancelled
    observed = []
    cursor = start
    while cursor < end - .001:
        check_cancelled(cancel_check)
        stop = min(end, cursor + 45.)
        if detail_callback:
            detail_callback({"visual_step": "ocr", "chunk_start": cursor, "chunk_end": stop})
        observed.extend(engine.screen_ocr.extract(video_path, cursor, stop, cancel_check=cancel_check))
        cursor = stop
    copies = []
    for item in rows:
        row = deepcopy(item if isinstance(item, dict) else item.to_dict())
        row["ocr_evidence"] = [{**screen, "kind": "reference"} for screen in observed
            if screen["start"] < row["end"] and screen["end"] > row["start"]][:24]
        copies.append(row)
    return copies


def native_language(row):
    mode = row.get("evidence_mode") if isinstance(row, dict) else getattr(row, "evidence_mode", None)
    if isinstance(mode, str) and mode.startswith("native-recovery-"):
        language = mode.removeprefix("native-recovery-")
        if language in SUPPORTED_SOURCE_LANGUAGES:
            return language
    language = row.get("source_language") if isinstance(row, dict) else getattr(row, "source_language", None)
    return language if language in SUPPORTED_SOURCE_LANGUAGES and language != "zh" else None


def native_context(rows):
    context = []
    for item in rows:
        row = deepcopy(item if isinstance(item, dict) else item.to_dict())
        language = native_language(row)
        if language is None and row.get("source_language") in SUPPORTED_SOURCE_LANGUAGES:
            language = row["source_language"]
        if language and row.get("text_zh", "").strip():
            row["source_language"] = language
            proof = row.get("verification") or {}
            row.setdefault("independent_audio_evidence", proof.get("audio_evidence", []))
            row.setdefault("ocr_evidence", proof.get("evidence", []))
            context.append(row)
    return sorted(context, key=lambda row: (row["start"], row["id"]))


def review_native_rows(rows, *, context=(), cancel_check=None, audit_only=False, client=None):
    """Combine a new semantic verdict with retained source evidence, never mint it."""
    rows = native_context(rows)
    references = native_context(context)
    output = {}
    summary = {"checked": 0, "verified": 0, "corrected": 0, "unresolved": 0}
    eligible = []
    from core.streaming.native_audit_retry import pending_native_audit
    for row in rows:
        if ((row.get("verification") or {}).get("status") == "manual"
                or (audit_only and not pending_native_audit(row))):
            output[row["id"]] = row
        else:
            eligible.append(row)
    from core.translation_batching import sentence_batches, neighbor_context
    for batch in sentence_batches(eligible, max_targets=1 if audit_only else 8):
        context_rows = neighbor_context(references, batch, neighbors=3)
        options = dict(language="mixed", context=context_rows, cancel_check=cancel_check,
                       keep_draft_on_review_failure=True, schema_repairs=2)
        if audit_only:
            options["audit_only"] = True
        if client is not None:
            options["client"] = client
        translated = translate_and_review(batch, **options)
        originals = {row["id"]: row for row in batch}
        for row in translated:
            old = originals[row["id"]]
            previous = deepcopy(old.get("verification") or {})
            if old.get("ocr_evidence"):
                previous["evidence"] = deepcopy(old["ocr_evidence"])
            semantic = row["multilingual_review"]
            supported = previous.get("source_supported") is True
            verified = supported and semantic.get("semantic_verified") is True
            changed = row["final_vi"] != old.get("final_vi", "")
            status = ("corrected" if changed else "verified") if verified else "unresolved"
            source_reason = previous.get("reason") or "Nguồn nhận giọng chưa được xác minh độc lập."
            reason = semantic["reason"] if supported else source_reason + " " + semantic["reason"]
            row["verification"] = {**previous, "status": status,
                "source_supported": supported, "semantic_verified": bool(semantic.get("semantic_verified")),
                "reason": reason, "multilingual_review": deepcopy(semantic)}
            row["needs_review"] = not verified
            row["review_reason"] = reason if not verified else None
            output[row["id"]] = row
            summary["checked"] += 1
            summary[status] += 1
    return {"segments": output, "summary": summary, "translation_sources": []}


def review_native_audits(rows, *, context=(), cancel_check=None, client=None):
    """Continue only validated pending semantic audits; never request a draft."""
    return review_native_rows(rows, context=context, cancel_check=cancel_check,
                              audit_only=True, client=client)
