"""Measured speech fitting shared by streaming, edits and batch exports."""
import math
import logging
import tempfile
import uuid
from pathlib import Path

from core.runtime_context import current_execution_context
from core.engines.alignment.speech_timing import take_tts_word_boundaries, trim_tts_padding
from core.engines.alignment.timing_aligner import SpeechBudgetError
from core.engines.translation.semantic_translator import PacingReviewRejected, pacing_candidate_key


def synthesize_natural_speech(*, text, source, duration, output_path, engine, aligner,
                              translator=None, voice=None, ref_audio=None, context=None, on_stage=None):
    """Never publish chopped or excessively accelerated speech.

    A rewrite is allowed only for automatic translation, with independent
    semantic verification. User edits pass translator=None to preserve exactly
    what the user typed. Temporary audio is replaced only after a valid fit.
    """
    if not math.isfinite(duration) or duration <= 0 or not str(text).strip():
        raise ValueError("Lời đọc hoặc thời lượng không hợp lệ.")
    current = str(text).strip()
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    verification = None
    logger = logging.getLogger("pipeline")
    speech_id = uuid.uuid4().hex[:8]
    raw_budget = duration * aligner.max_speed
    request_budget = raw_budget * .98
    measurements = []
    seen_candidates = set()
    with tempfile.TemporaryDirectory(prefix="speech-fit-", dir=output_path.parent) as temporary:
        raw, fitted = Path(temporary) / "raw.wav", Path(temporary) / "fitted.wav"
        for attempt in range(3):
            execution = current_execution_context()
            if execution.cancel_check and execution.cancel_check():
                import asyncio
                raise asyncio.CancelledError
            if on_stage:
                on_stage("TTS")
            engine.synthesize(text=current, output_path=raw, voice=voice, ref_audio=ref_audio)
            boundaries = take_tts_word_boundaries(engine, raw)
            trim_tts_padding(raw)
            measured = aligner.get_audio_duration(raw)
            if not math.isfinite(measured) or measured <= 0:
                raise RuntimeError("Không đọc được âm thanh từ TTS.")
            ratio = max(1.0, measured / duration)
            measurements.append({"text": current, "measured_seconds": round(measured, 4)})
            seen_candidates.add(pacing_candidate_key(current))
            logger.info("PACING_MEASURED run_id=%s speech_id=%s attempt=%d measured_seconds=%.4f slot_seconds=%.4f raw_budget_seconds=%.4f required_speed=%.4f max_speed=%.4f",
                        execution.run_id, speech_id, attempt + 1, measured, duration, raw_budget, ratio, aligner.max_speed)
            try:
                if on_stage:
                    on_stage("ALIGNING")
                if ratio > aligner.max_speed:
                    raise SpeechBudgetError("Câu vượt nhịp đọc tự nhiên.")
                ratio = aligner.apply_atempo(raw, fitted, ratio, fit_duration=duration)
            except SpeechBudgetError:
                if translator is None or attempt == 2:
                    logger.warning("PACING_FAILED run_id=%s speech_id=%s attempt=%d reason=%s",
                                   execution.run_id, speech_id, attempt + 1, "rewrite_disabled" if translator is None else "budget_exhausted")
                    raise SpeechBudgetError(
                        "Lời Việt vẫn quá dài để đọc tự nhiên trong câu này. "
                        "Hãy rút gọn lời hoặc chọn giọng khác; âm thanh chưa bị cắt hay ép tốc độ."
                    ) from None
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
                review_feedback = dict(timing_feedback)
                for review_attempt in range(3):
                    if execution.cancel_check and execution.cancel_check():
                        import asyncio
                        raise asyncio.CancelledError
                    try:
                        logger.info("PACING_REWRITE run_id=%s speech_id=%s attempt=%d proposal=%d request_budget_seconds=%.4f reduction_pct=%.1f",
                                    execution.run_id, speech_id, attempt + 1, review_attempt + 1,
                                    request_budget, timing_feedback["required_reduction_pct"])
                        proposed = translator.rewrite_for_pacing(
                            source, current, request_budget, context,
                            measured_duration=measured, feedback=review_feedback)
                        candidate = proposed.get("final_vi")
                        proof = proposed.get("pacing_verification")
                        if (not isinstance(candidate, str) or not candidate.strip()
                                or not isinstance(proof, dict) or proof.get("status") != "verified"
                                or proof.get("text") != candidate):
                            raise RuntimeError("Thiếu xác minh cho lời đọc đã rút gọn.")
                        if pacing_candidate_key(candidate) in seen_candidates:
                            raise PacingReviewRejected("AI lặp lại lời đã thử; cần cách diễn đạt ngắn hơn.",
                                candidate=candidate, reason="Lặp lời đã đo nhưng chưa vừa thời lượng.", code="duplicate")
                        break
                    except PacingReviewRejected as error:
                        # A rejected draft is not published or synthesized.
                        # Give the provider bounded fresh attempts; transport,
                        # credentials and unknown errors still fail immediately.
                        logger.warning("PACING_REJECTED run_id=%s speech_id=%s attempt=%d proposal=%d reason=%s",
                                       execution.run_id, speech_id, attempt + 1, review_attempt + 1, error.code)
                        seen_candidates.add(pacing_candidate_key(error.feedback["rejected_candidate"]))
                        if review_attempt == 2:
                            raise
                        review_feedback = {**timing_feedback, **error.feedback}
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
            return {"text": current, "tts_duration": measured, "speed_ratio": ratio,
                    "boundaries": boundaries, "pacing_verification": verification}
