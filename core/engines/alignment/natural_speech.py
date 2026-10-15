"""Measured speech fitting shared by streaming, edits and batch exports."""
import math
import logging
import tempfile
import uuid
import hashlib
import json
from dataclasses import replace
from pathlib import Path

from core.runtime_context import current_execution_context
from core.engines.alignment.speech_timing import (
    audio_activity_span, compact_tts_pauses, compact_tts_edge_padding, retime_tts_word_boundaries,
    take_tts_word_boundaries, trim_tts_padding,
)
from core.engines.alignment.timing_aligner import SpeechBudgetError
from core.engines.translation.semantic_translator import (
    PacingReviewRejected, pacing_candidate_key, accumulate_pacing_rejection,
)
from core.engines.alignment.speech_cache import (
    synthesis_cache_identity, load_speech_cache, store_speech_cache,
    raw_synthesis_cache_identity, load_raw_speech_cache, store_raw_speech_cache,
)
from core.engines.alignment import pacing_checkpoint


def synthesize_natural_speech(*, text, source, duration, output_path, engine, aligner,
                              translator=None, voice=None, ref_audio=None, context=None, on_stage=None,
                              max_duration=None, allow_bidirectional_reflow=False,
                              max_duration_limit=None, pacing_verification=None,
                              source_language=None, max_pacing_proposals=5, allow_review_draft=False):
    """Never publish chopped or excessively accelerated speech.

    A rewrite is allowed only for automatic translation, with independent
    semantic verification. User edits pass translator=None to preserve exactly
    what the user typed. Temporary audio is replaced only after a valid fit.
    """
    if not math.isfinite(duration) or duration <= 0 or not str(text).strip():
        raise ValueError("Lời đọc hoặc thời lượng không hợp lệ.")
    if type(max_pacing_proposals) is not int or not 1 <= max_pacing_proposals <= 5:
        raise ValueError("Mỗi đợt căn lời chỉ được thử từ 1 đến 5 đề xuất.")
    if max_duration is None:
        max_duration = duration
    if max_duration_limit is not None:
        if (isinstance(max_duration_limit, bool) or not isinstance(max_duration_limit, (int, float))
                or not math.isfinite(max_duration_limit)
                or max_duration_limit < duration
                or max_duration_limit > duration + 1.35 + 1e-9):
            raise ValueError("Giới hạn căn lời thoại không hợp lệ.")
    reflow_limit = .700001 if allow_bidirectional_reflow else .350001
    if max_duration_limit is not None:
        reflow_limit = max(reflow_limit, float(max_duration_limit) - duration + .000001)
    if (not math.isfinite(max_duration) or not duration <= max_duration <= duration + reflow_limit
            or max_duration_limit is not None and max_duration > max_duration_limit + 1e-9):
        raise ValueError("Khoảng căn lời thoại không hợp lệ.")
    current = str(text).strip()
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # A rescue retry may supply an independently verified rewrite from a
    # previous, measured attempt. Carry that proof through fitting without
    # asking the semantic translator for a different sentence.
    verification = (pacing_verification if isinstance(pacing_verification, dict)
                    and (pacing_verification.get("status") == "verified" or allow_review_draft
                         and pacing_verification.get("status") == "draft"
                         and pacing_verification.get("semantic_verified") is True)
                    and pacing_verification.get("text") == current else None)
    logger = logging.getLogger("pipeline")
    speech_id = uuid.uuid4().hex[:8]
    execution = current_execution_context()
    cache_identity = None
    try:
        # Adaptive timing depends on neighboring audio. Its final checkpoint is
        # persisted by the session; never reuse a fixed-slot speech cache hit.
        if max_duration == duration and not allow_review_draft:
            cache_identity = synthesis_cache_identity(
                source=source, text=current, duration=duration, voice=voice, engine=engine,
                aligner=aligner, translator=translator, context=context, ref_audio=ref_audio)
            if cache_identity is not None:
                policy = json.dumps([cache_identity.key, source_language, max_pacing_proposals], ensure_ascii=True)
                cache_identity = replace(cache_identity, key=hashlib.sha256(policy.encode()).hexdigest())
    except (OSError, ValueError, TypeError, KeyError):
        logger.warning("PACING_CACHE_UNAVAILABLE run_id=%s speech_id=%s", execution.run_id, speech_id)
    if cache_identity is not None:
        cached = load_speech_cache(cache_identity, output_path, cancel_check=execution.cancel_check)
        if cached is not None:
            from core.runtime_context import record_cache_usage
            record_cache_usage()
            logger.info("PACING_CACHE_HIT run_id=%s speech_id=%s", execution.run_id, speech_id)
            return cached
    raw_budget = duration * aligner.max_speed
    request_budget = raw_budget * .98
    history_key = None
    try:
        history_key = pacing_checkpoint.identity(source=source, text=current, duration=duration,
            voice=voice, engine=engine, aligner=aligner, translator=translator, context=context,
            ref_audio=ref_audio, max_duration=max_duration, max_duration_limit=max_duration_limit,
            allow_bidirectional_reflow=allow_bidirectional_reflow, source_language=source_language,
            max_pacing_proposals=max_pacing_proposals, allow_review_draft=allow_review_draft)
    except (OSError, ValueError, TypeError, KeyError):
        logger.warning("PACING_HISTORY_IDENTITY_UNAVAILABLE run_id=%s", execution.run_id)
    history = pacing_checkpoint.load(history_key, cancel_check=execution.cancel_check)
    logger.info("PACING_HISTORY run_id=%s speech_id=%s scope=%s measured=%d rejected=%d slot_seconds=%.4f capacity_seconds=%.4f proposal_limit=%d",
                execution.run_id, speech_id, history_key or "unavailable",
                len(history["measured"]), len(history["feedback"].get("rejected_candidates", [])),
                duration, max_duration, max_pacing_proposals)
    # Historical proof is diagnostic only. An explicitly capable adapter may
    # revalidate one exact alternative, but only fresh gates permit synthesis.
    historical_measured = history["measured"]
    measurements = [{"text": row["text"], "measured_seconds": row["measured_seconds"]}
                    for row in historical_measured]
    measured_candidates = []
    seen_candidates = {pacing_candidate_key(row["text"]) for row in historical_measured}
    rejection_feedback = history["feedback"]
    seen_candidates.update(pacing_candidate_key(row["text"])
                           for row in rejection_feedback.get("rejected_candidates", []))
    historical_retry = None
    if (translator is not None
            and getattr(type(translator), "supports_pacing_revalidation", False) is True
            and callable(getattr(type(translator), "revalidate_pacing_candidate", None))):
        rejected = {pacing_candidate_key(row["text"])
                    for row in rejection_feedback.get("rejected_candidates", [])
                    if row.get("code") != "duplicate"}
        eligible = [row for row in historical_measured
                    if pacing_candidate_key(row["text"]) != pacing_candidate_key(current)
                    and pacing_candidate_key(row["text"]) not in rejected
                    and isinstance(row.get("pacing_verification"), dict)
                    and (row["pacing_verification"].get("status") == "verified"
                         or allow_review_draft and row["pacing_verification"].get("status") == "draft")]
        if eligible:
            historical_retry = min(eligible, key=lambda row: row["measured_seconds"])

    def checkpoint_failure():
        # The original is always measured again; deduplicate that observation
        # while keeping newly measured alternatives and their evidence bounded.
        merged = {}
        for row in [*historical_measured, *measured_candidates]:
            merged[pacing_candidate_key(row["text"])] = row
        pacing_checkpoint.store(history_key, list(merged.values()), rejection_feedback,
                                cancel_check=execution.cancel_check)
    proposals_used = 0
    original_duration = duration
    with tempfile.TemporaryDirectory(prefix="speech-fit-", dir=output_path.parent) as temporary:
        raw, fitted = Path(temporary) / "raw.wav", Path(temporary) / "fitted.wav"
        # One original waveform plus at most five proposed wordings per round.
        # Duplicate responses consume a request slot but are never synthesized.
        for attempt in range(max_pacing_proposals + 1):
            duration = original_duration
            raw_budget = duration * aligner.max_speed
            execution = current_execution_context()
            if execution.cancel_check and execution.cancel_check():
                import asyncio
                raise asyncio.CancelledError
            if on_stage:
                on_stage("TTS")
            # A failed acoustic-only first pass still owns useful normal-rate
            # speech. Reuse that untouched PCM across pacing/model/slot changes,
            # never an already trimmed/accelerated or independently rewritten
            # result. The final fit and semantic checks below remain mandatory.
            raw_identity = None
            try:
                raw_identity = raw_synthesis_cache_identity(text=current, voice=voice,
                    engine=engine, ref_audio=ref_audio)
            except (OSError, ValueError, TypeError, KeyError):
                logger.warning("TTS_RAW_CACHE_UNAVAILABLE run_id=%s speech_id=%s", execution.run_id, speech_id)
            raw_cached = (load_raw_speech_cache(raw_identity, raw, cancel_check=execution.cancel_check)
                          if raw_identity is not None else None)
            if raw_cached is None:
                engine.synthesize(text=current, output_path=raw, voice=voice, ref_audio=ref_audio)
                boundaries = take_tts_word_boundaries(engine, raw)
                if raw_identity is not None:
                    saved = store_raw_speech_cache(raw_identity, raw, boundaries,
                        cancel_check=execution.cancel_check)
                    logger.info("TTS_RAW_CACHE_STORED run_id=%s speech_id=%s saved=%s", execution.run_id, speech_id, saved)
            else:
                from core.runtime_context import record_cache_usage
                record_cache_usage()
                boundaries = raw_cached["boundaries"]
                logger.info("TTS_RAW_CACHE_HIT run_id=%s speech_id=%s", execution.run_id, speech_id)
            trim_offset = trim_tts_padding(raw)
            activity = audio_activity_span(raw)
            boundaries = retime_tts_word_boundaries(boundaries, trim_offset=trim_offset,
                speech_onset=activity[0] if activity else None)
            measured = aligner.get_audio_duration(raw)
            if not math.isfinite(measured) or measured <= 0:
                raise RuntimeError("Không đọc được âm thanh từ TTS.")
            ratio = max(1.0, measured / duration)
            measurements.append({"text": current, "measured_seconds": round(measured, 4)})
            measured_candidates.append({"text": current, "measured_seconds": measured,
                                        "pacing_verification": verification})
            seen_candidates.add(pacing_candidate_key(current))
            logger.info("PACING_MEASURED run_id=%s speech_id=%s attempt=%d measured_seconds=%.4f slot_seconds=%.4f raw_budget_seconds=%.4f required_speed=%.4f max_speed=%.4f",
                        execution.run_id, speech_id, attempt + 1, measured, duration, raw_budget, ratio, aligner.max_speed)
            try:
                if on_stage:
                    on_stage("ALIGNING")
                for fit_attempt in range(3):
                    try:
                        # Subtracting source timestamps can put an exact
                        # ceiling ratio a few floating-point ulps over it.
                        # Clamp only that arithmetic noise, never audible time.
                        if ratio > aligner.max_speed and math.isclose(
                                ratio, aligner.max_speed, rel_tol=0, abs_tol=1e-12):
                            ratio = aligner.max_speed
                        if ratio > aligner.max_speed:
                            raise SpeechBudgetError("Câu vượt nhịp đọc tự nhiên.")
                        ratio = aligner.apply_atempo(raw, fitted, ratio, fit_duration=duration)
                        break
                    except SpeechBudgetError:
                        if fit_attempt == 1 and max_duration > duration + .0001:
                            # The caller has proved this extra space exists in
                            # the dialogue timeline. Request only what the full
                            # waveform needs, keeping the same speed ceiling.
                            duration = min(max_duration, measured / aligner.max_speed + .012)
                            raw_budget = duration * aligner.max_speed
                            request_budget = raw_budget * .98
                            ratio = max(1.0, measured / duration)
                            logger.info("PACING_BUDGET_EXTENDED run_id=%s speech_id=%s slot_seconds=%.4f",
                                        execution.run_id, speech_id, duration)
                            continue
                        if fit_attempt:
                            raise
                        # First preserve the exact wording and every spoken
                        # sample by reclaiming excess synthesis pauses. Only
                        # an actual fit failure activates this path; normal
                        # speech stays unchanged. Leave 2% for atempo rounding.
                        cuts = compact_tts_pauses(raw, raw_budget * .98)
                        boundaries = retime_tts_word_boundaries(
                            boundaries, removed_intervals=cuts)
                        edge_cuts = compact_tts_edge_padding(raw, raw_budget * .98)
                        # Edge cuts refer to the already compacted waveform.
                        # Apply each map in order, in its own coordinate space.
                        boundaries = retime_tts_word_boundaries(
                            boundaries, removed_intervals=edge_cuts)
                        if not cuts and not edge_cuts and max_duration <= duration:
                            raise
                        before = measured
                        measured = aligner.get_audio_duration(raw)
                        if not math.isfinite(measured) or measured <= 0:
                            raise RuntimeError("Không đọc được âm thanh từ TTS.")
                        ratio = max(1.0, measured / duration)
                        measurements[-1]["measured_seconds"] = round(measured, 4)
                        measured_candidates[-1]["measured_seconds"] = measured
                        logger.info("PACING_COMPACTED run_id=%s speech_id=%s attempt=%d before_seconds=%.4f measured_seconds=%.4f removed_seconds=%.4f pauses=%d",
                                    execution.run_id, speech_id, attempt + 1, before, measured,
                                    sum(right - left for left, right in cuts + edge_cuts), len(cuts) + len(edge_cuts))
            except SpeechBudgetError:
                checkpoint_failure()
                if translator is None or proposals_used >= max_pacing_proposals:
                    logger.warning("PACING_FAILED run_id=%s speech_id=%s attempt=%d reason=%s",
                                   execution.run_id, speech_id, attempt + 1, "rewrite_disabled" if translator is None else "budget_exhausted")
                    failure = SpeechBudgetError(
                        "Lời Việt vẫn quá dài để đọc tự nhiên trong câu này. "
                        "Hãy rút gọn lời hoặc chọn giọng khác; âm thanh chưa bị cắt hay ép tốc độ."
                    )
                    best = min(measured_candidates, key=lambda item: item["measured_seconds"])
                    failure.required_dub_duration = best["measured_seconds"] / aligner.max_speed + .012
                    failure.candidate_text = best["text"]
                    failure.pacing_verification = best["pacing_verification"]
                    # Keep the complete candidate record available to callers
                    # that need to explain or retry this measured attempt.
                    failure.measured_candidate = dict(best)
                    failure.pacing_proposals_used = proposals_used
                    failure.pacing_rejection_feedback = rejection_feedback
                    raise failure from None
                # Leave a little room for encoder/atempo block rounding.
                if on_stage:
                    on_stage("REWRITING")
                # Calibrate an overoptimistic provider using the last actual
                # voice measurement. Tighten the requested target, never the
                # semantic verifier or the physical playback speed limit.
                if attempt:
                    request_budget *= min(.90, raw_budget * .98 / measured)
                timing_feedback = {
                    "raw_budget_seconds": round(raw_budget, 4),
                    "request_budget_seconds": round(request_budget, 4),
                    "required_reduction_pct": round(max(0, 1 - request_budget / measured) * 100, 1),
                    "measured_candidates": list(measurements),
                }
                review_feedback = {**rejection_feedback, **timing_feedback}
                for review_attempt in range(max_pacing_proposals - proposals_used):
                    if execution.cancel_check and execution.cancel_check():
                        import asyncio
                        raise asyncio.CancelledError
                    try:
                        proposals_used += 1
                        logger.info("PACING_REWRITE run_id=%s speech_id=%s attempt=%d proposal=%d request_budget_seconds=%.4f reduction_pct=%.1f",
                                    execution.run_id, speech_id, attempt + 1, proposals_used,
                                    request_budget, timing_feedback["required_reduction_pct"])
                        proposal_options = {}
                        if source_language is not None:
                            proposal_options["source_language"] = source_language
                        if allow_review_draft:
                            proposal_options["allow_review_draft"] = True
                        revalidating = historical_retry
                        historical_retry = None  # At most one attempt, also on rejection.
                        if revalidating is not None:
                            logger.info("PACING_REVALIDATE_HISTORY run_id=%s speech_id=%s proposal=%d",
                                        execution.run_id, speech_id, proposals_used)
                            old_proof = revalidating["pacing_verification"]
                            proposed = translator.revalidate_pacing_candidate(
                                source, current, revalidating["text"], context,
                                needs_review=old_proof.get("status") == "draft",
                                review_reason=old_proof.get("review_reason", ""), **proposal_options)
                        else:
                            proposed = translator.rewrite_for_pacing(
                                source, current, request_budget, context,
                                measured_duration=measured, feedback=review_feedback, **proposal_options)
                        candidate = proposed.get("final_vi")
                        proof = proposed.get("pacing_verification")
                        if (not isinstance(candidate, str) or not candidate.strip()
                                or not isinstance(proof, dict)
                                or not (proof.get("status") == "verified" or allow_review_draft
                                        and proof.get("status") == "draft" and proof.get("semantic_verified") is True)
                                or proof.get("text") != candidate):
                            raise RuntimeError("Thiếu xác minh cho lời đọc đã rút gọn.")
                        if revalidating is not None and candidate != revalidating["text"]:
                            raise RuntimeError("Kiểm định đã đổi lời đọc lịch sử; chưa chấp nhận kết quả.")
                        if revalidating is None and pacing_candidate_key(candidate) in seen_candidates:
                            raise PacingReviewRejected("AI lặp lại lời đã thử; cần cách diễn đạt ngắn hơn.",
                                candidate=candidate, reason="Lặp lời đã đo nhưng chưa vừa thời lượng.", code="duplicate")
                        break
                    except PacingReviewRejected as error:
                        # A rejected draft is not published or synthesized.
                        # Give the provider bounded fresh attempts; transport,
                        # credentials and unknown errors still fail immediately.
                        logger.warning("PACING_REJECTED run_id=%s speech_id=%s attempt=%d proposal=%d reason=%s",
                                       execution.run_id, speech_id, attempt + 1, proposals_used, error.code)
                        seen_candidates.add(pacing_candidate_key(error.feedback["rejected_candidate"]))
                        rejection_feedback = accumulate_pacing_rejection(rejection_feedback, error)
                        checkpoint_failure()
                        if proposals_used >= max_pacing_proposals:
                            best = min(measured_candidates, key=lambda item: item["measured_seconds"])
                            error.required_dub_duration = best["measured_seconds"] / aligner.max_speed + .012
                            error.candidate_text = best["text"]
                            error.pacing_verification = best["pacing_verification"]
                            error.measured_candidate = dict(best)
                            error.pacing_proposals_used = proposals_used
                            error.pacing_rejection_feedback = rejection_feedback
                            raise
                        review_feedback = {**rejection_feedback, **timing_feedback}
                current, verification = candidate.strip(), proof
                continue
            if not fitted.is_file() or fitted.stat().st_size <= 0:
                raise RuntimeError("Không tạo được giọng đọc hợp lệ.")
            # Cancel before publication even when TTS itself cannot interrupt
            # its provider request immediately.
            if execution.cancel_check and execution.cancel_check():
                import asyncio
                raise asyncio.CancelledError
            fitted.replace(output_path)
            logger.info("PACING_READY run_id=%s speech_id=%s attempt=%d speed=%.4f rewritten=%s",
                        execution.run_id, speech_id, attempt + 1, ratio, verification is not None)
            result = {"text": current, "tts_duration": measured, "speed_ratio": ratio,
                      "boundaries": boundaries, "pacing_verification": verification,
                      "pacing_proposals_used": proposals_used}
            if verification is not None and verification.get("status") == "draft":
                result.update(needs_review=True, review_reason=verification["review_reason"])
            if cache_identity is not None:
                saved = store_speech_cache(cache_identity, output_path, result, cancel_check=execution.cancel_check)
                logger.info("PACING_CACHE_STORED run_id=%s speech_id=%s saved=%s", execution.run_id, speech_id, saved)
            return result
