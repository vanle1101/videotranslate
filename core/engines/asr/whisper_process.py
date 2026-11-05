"""Disposable Whisper workers: native allocators never outlive an ASR call.

The selected model/precision and decoding arguments are preserved. Only one
worker may decode at once; queueing, decoding and cancellation are bounded.
"""
from __future__ import annotations

import math
import logging
from copy import deepcopy
from pathlib import Path
import subprocess
from types import SimpleNamespace
import tempfile
import time
import wave

from config import settings
from core.runtime_context import current_execution_context
from core.engines.asr.native_process import (
    ASRProcessError, WORKER_SLOT as _WORKER_SLOT, positive_deadline,
    check_cancelled, native_worker_slot, iter_native_records,
)


class WhisperCudaMemoryError(ASRProcessError):
    """Typed child failure eligible for one CPU retry, never generic ASR errors."""


def _same_recovered_utterance(left, right):
    """Recognize the same already-published turn across CPU/GPU timing jitter."""
    def signature(value):
        return "".join(char.casefold() for char in str(value or "") if char.isalnum())
    if not signature(left.text) or signature(left.text) != signature(right.text):
        return False
    if abs(left.start - right.start) > .25 or abs(left.end - right.end) > .25:
        return False
    overlap = max(0., min(left.end, right.end) - max(left.start, right.start))
    shorter = min(left.end - left.start, right.end - right.start)
    return shorter > 0 and overlap / shorter >= .8


def _merge_focused_recovery(rows, coverage, recovered_rows, recovered_coverage):
    """Merge recovered turns while keeping every unrecognized speech region blocked.

    An empty ASR crop used to reserve its entire VAD window as missing. A
    bounded retry that recovered most utterances then lost every recovered row
    because one short VAD trigger was still unresolved. When the diagnostic
    contains original-audio VAD regions, retain independently recovered turns
    and keep only residual regions in the source-coverage gate.
    """
    if not coverage or not coverage.get("unresolved_windows"):
        return rows, coverage
    remaining_windows, remaining_missing, remaining_diagnostics, recovered = [], [], [], []
    resolved_any = False
    retry_gaps = recovered_coverage.get("unresolved_windows", []) if recovered_coverage else []
    retry_missing = recovered_coverage.get("missing_speech_spans", []) if recovered_coverage else []
    if not retry_missing:
        retry_missing = retry_gaps
    original_windows = coverage["unresolved_windows"]
    original_missing = coverage["missing_speech_spans"]
    for index, window in enumerate(original_windows):
        spans = [span for span in original_missing
                 if window[0] <= span[0] and span[1] <= window[1]]
        diagnostic = coverage["diagnostics"][index]
        sample_rate = diagnostic["sample_rate"]
        duration = diagnostic["input_samples"] / sample_rate
        # The CPU retry intentionally decodes a 600 ms context on both sides
        # of the unresolved window. Keep a complete utterance that begins in
        # that measured context; cutting it at the VAD edge loses its first
        # words and leaves the same source window blocked on every resume.
        retry_crop = (max(0., window[0] - .6), min(duration, window[1] + .6))
        proof_spans = spans
        # The diagnostic includes the complete original-audio VAD inventory
        # even when the worker generated it locally instead of receiving it
        # from its caller. Treat those regions as speech candidates only: any
        # candidate without a recovered transcript remains unresolved below.
        original_regions_complete = bool(diagnostic.get("original_regions")) and (
            diagnostic.get("omitted", {}).get("original_regions") == 0)
        whole_empty_window = (len(spans) == 1 and spans[0][0] <= window[0] + .001
                              and spans[0][1] >= window[1] - .001)
        if original_regions_complete:
            regions = []
            for left, right in diagnostic.get("original_regions", []):
                clipped = [max(float(left), window[0]), min(float(right), window[1])]
                if clipped[0] < clipped[1]:
                    regions.append(clipped)
            regions.sort()
            merged_regions = []
            for left, right in regions:
                if merged_regions and left <= merged_regions[-1][1]:
                    merged_regions[-1][1] = max(merged_regions[-1][1], right)
                else:
                    merged_regions.append([left, right])
            if merged_regions:
                # A nonempty ASR crop can yield useful rows and still report a
                # small uncovered VAD region. The worker withholds every row
                # in that crop to keep its coverage atomic, so retry rows for
                # the already-covered speech islands must be proven and
                # restored alongside the remaining gap. The explicit missing
                # spans below continue to block any VAD island they intersect.
                proof_spans = list(merged_regions)
                if not whole_empty_window:
                    proof_spans.extend([list(span) for span in spans if not any(
                        left <= span[0] and span[1] <= right for left, right in merged_regions)])
                proof_spans.sort(key=lambda span: (span[0], span[1]))
                normalized_proof_spans = []
                for left, right in proof_spans:
                    if normalized_proof_spans and left <= normalized_proof_spans[-1][1]:
                        normalized_proof_spans[-1][1] = max(normalized_proof_spans[-1][1], right)
                    else:
                        normalized_proof_spans.append([left, right])
                proof_spans = normalized_proof_spans
        candidates = [row for row in recovered_rows if row.text.strip()
                      if row.start >= retry_crop[0] - .05 and row.end <= retry_crop[1] + .05
                      and any(row.start < span[1] and row.end > span[0] for span in proof_spans)]
        proof_rows, additions = [], []
        for candidate in candidates:
            overlaps = [old for old in rows if candidate.start < old.end and candidate.end > old.start]
            duplicates = [old for old in overlaps if _same_recovered_utterance(candidate, old)]
            if len(overlaps) == 1 and len(duplicates) == 1:
                # The CPU retry independently heard an already-published turn.
                # Use its existing source time once; never append a duplicate.
                proof_rows.append(duplicates[0])
            elif not overlaps:
                proof_rows.append(candidate)
                additions.append(candidate)

        def span_covered(span, evidence_rows):
            start, end = span
            intervals = sorted((max(start, row.start), min(end, row.end)) for row in evidence_rows
                               if row.start < end and row.end > start)
            total, stop = 0., None
            for left, right in intervals:
                if stop is None:
                    total, stop = right - left, right
                elif left <= stop:
                    total += max(0., right - stop)
                    stop = max(stop, right)
                else:
                    total += right - left
                    stop = right
            return total >= (end - start) * .5

        blocking_spans = list(retry_missing)
        if not whole_empty_window:
            blocking_spans.extend(spans)
        blocking_uncovered = [span for span in blocking_spans
                              if not span_covered(span, proof_rows)]
        remaining_indices = {span_index for span_index, span in enumerate(proof_spans)
            if not span_covered(span, proof_rows)
            or any(left < span[1] and right > span[0] for left, right in blocking_uncovered)}
        if whole_empty_window and not original_regions_complete:
            # Without a complete VAD inventory, recovered rows cannot prove
            # that the rest of an empty crop contains no additional speech.
            remaining_indices.update(range(len(proof_spans)))
        # A recovered row is indivisible. If its measured interval crosses an
        # unresolved speech region, keep that whole row out and preserve every
        # VAD region it touches as unresolved; never trim a transcript row.
        accepted_additions = list(additions)
        while True:
            rejected = [row for row in accepted_additions if any(
                row.start < proof_spans[span_index][1] and row.end > proof_spans[span_index][0]
                for span_index in remaining_indices)]
            if not rejected:
                break
            before = len(remaining_indices)
            for row in rejected:
                remaining_indices.update(span_index for span_index, span in enumerate(proof_spans)
                    if row.start < span[1] and row.end > span[0])
            rejected_ids = {id(row) for row in rejected}
            accepted_additions = [row for row in accepted_additions if id(row) not in rejected_ids]
            proof_rows = [row for row in proof_rows if id(row) not in rejected_ids]
            if len(remaining_indices) == before:
                break

        unresolved_spans = [proof_spans[span_index] for span_index in sorted(remaining_indices)]
        logging.getLogger("pipeline").info(
            "WHISPER_FOCUSED_GAP_PROOF run_id=%s window=%s proof_spans=%s candidates=%s proof_rows=%s accepted=%s unresolved=%s complete_vad=%s",
            current_execution_context().run_id, window, len(proof_spans), len(candidates),
            [(round(row.start, 3), round(row.end, 3)) for row in proof_rows],
            [(round(row.start, 3), round(row.end, 3)) for row in accepted_additions],
            unresolved_spans, original_regions_complete)
        if not unresolved_spans:
            resolved_any = True
            recovered.extend(accepted_additions)
        elif accepted_additions:
            resolved_any = True
            recovered.extend(accepted_additions)
            for left, right in unresolved_spans:
                remaining_windows.append([left, right])
                remaining_missing.append([left, right])
                narrowed = deepcopy(diagnostic)
                narrowed["window"] = [left, right]
                origins = [name for name, values in (
                    ("original_audio", diagnostic.get("original_regions", [])),
                    ("vocal_stem", diagnostic.get("vocal_regions", [])))
                    if any(start < right and end > left for start, end in values)]
                narrowed["gaps"] = [{"span": [left, right], "vad_origins": origins}]
                remaining_diagnostics.append(narrowed)
        else:
            remaining_windows.append(window)
            remaining_missing.extend(spans)
            remaining_diagnostics.append(diagnostic)

    if not resolved_any:
        return rows, coverage
    order = sorted(range(len(remaining_windows)), key=lambda item: tuple(remaining_windows[item]))
    remaining_windows = [remaining_windows[item] for item in order]
    remaining_missing = [remaining_missing[item] for item in order]
    remaining_diagnostics = [remaining_diagnostics[item] for item in order]
    duration = (remaining_diagnostics[0]["input_samples"] / remaining_diagnostics[0]["sample_rate"]
                if remaining_diagnostics else 0.)
    merged_coverage = {"unresolved_windows": remaining_windows,
        "missing_speech_spans": remaining_missing, "diagnostics": remaining_diagnostics,
        "tail_unresolved": bool(remaining_windows and duration
                                and remaining_windows[-1][1] >= duration - .05)}
    return sorted([*rows, *recovered], key=lambda row: (row.start, row.end)), merged_coverage


def _decode_segment(record):
    """Validate child output before it becomes a source checkpoint."""
    if not isinstance(record, dict) or record.get("event") != "segment":
        raise ASRProcessError("Bộ nhận giọng trả dữ liệu không đúng cấu trúc.")
    values = record.get("segment")
    if (not isinstance(values, dict) or not isinstance(values.get("text"), str)
            or any(type(values.get(key)) not in (int, float) or not math.isfinite(values[key])
                   for key in ("start", "end"))
            or not 0 <= values["start"] < values["end"]
            or not isinstance(values.get("words"), list)):
        raise ASRProcessError("Bộ nhận giọng trả lời thoại hoặc mốc thời gian không hợp lệ.")
    words = []
    for word in values["words"]:
        if (not isinstance(word, dict) or not isinstance(word.get("word"), str)
                or any(type(word.get(key)) not in (int, float) or not math.isfinite(word[key])
                       for key in ("start", "end"))
                or not 0 <= word["start"] <= word["end"]):
            raise ASRProcessError("Bộ nhận giọng trả mốc từ không hợp lệ.")
        probability = word.get("probability")
        if (probability is not None and (type(probability) not in (int, float)
                or not math.isfinite(probability) or not 0 <= probability <= 1)):
            raise ASRProcessError("Bộ nhận giọng trả độ tin cậy từ không hợp lệ.")
        words.append(SimpleNamespace(**word))
    scores = {}
    for name in ("avg_logprob", "no_speech_prob"):
        score = values.get(name)
        if score is not None:
            if type(score) not in (int, float) or not math.isfinite(score):
                raise ASRProcessError("Bộ nhận giọng trả điểm câu không hợp lệ.")
            scores[name] = score
    language = {}
    if "source_language" in values or "source_language_probability" in values:
        from core.engines.asr.source_language import require_supported_source
        metadata = require_supported_source({"language": values.get("source_language"),
            "probability": values.get("source_language_probability")})
        language = {"source_language": metadata["language"],
                    "source_language_probability": metadata["probability"]}
    if "source_audio_evidence" in values:
        if values["source_audio_evidence"] not in {"original_audio", "vocal_stem_unverified"}:
            raise ASRProcessError("Bằng chứng giọng nguồn không hợp lệ.")
        language["source_audio_evidence"] = values["source_audio_evidence"]
    if "source_language_review" in values:
        from core.engines.asr.language_disagreement import validate_language_review
        try:
            language["source_language_review"] = validate_language_review(values["source_language_review"])
        except (ValueError, TypeError) as error:
            raise ASRProcessError("Bằng chứng bất đồng ngôn ngữ không hợp lệ.") from error
    return SimpleNamespace(start=values["start"], end=values["end"], text=values["text"],
                           words=words, speaker=None, **scores, **language)


class SubprocessWhisperModel:
    """Small model-compatible handle; no CTranslate2/ONNX model lives here."""

    def __init__(self, model_size, device=None):
        self.model_size = str(model_size)
        self.device = device or settings.DEVICE

    def transcribe(self, audio_path, *, status_callback=None, metadata_callback=None,
                   coverage_callback=None, timeout_seconds=None, **options):
        # Match Whisper's lazy iterator contract. The child is acquired when
        # iteration begins and reaped before this iterator finishes or closes.
        if coverage_callback is not None and (not callable(coverage_callback)
                or not options.get("utterance_language_detection") or options.get("language") is not None):
            raise ValueError("Partial source coverage requires local automatic language detection")
        return self._segments(audio_path, options, status_callback, metadata_callback,
                              coverage_callback, timeout_seconds=timeout_seconds), None

    def _segments(self, audio_path, options, status_callback=None, metadata_callback=None,
                  coverage_callback=None, *, timeout_seconds=None):
        context = current_execution_context()
        check = context.cancel_check
        check_cancelled(check)
        source = Path(audio_path).resolve(strict=True)
        if timeout_seconds is None:
            timeout = positive_deadline("ASR_PROCESS_TIMEOUT", 600)
        elif (type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds)
                or not 1 <= timeout_seconds <= 600):
            raise ValueError("Focused ASR timeout must be between 1 and 600 seconds")
        else:
            timeout = float(timeout_seconds)
        with native_worker_slot(check):
            local_detection = options.get("utterance_language_detection") and options.get("language") is None
            strategy = str(getattr(settings, "ASR_VOCAL_EVIDENCE_STRATEGY", "always"))
            lazy_evidence = local_detection and strategy == "on_gap" and coverage_callback is not None
            run_options = options
            if local_detection and strategy != "never" and not lazy_evidence:
                from core.engines.asr.vocal_evidence import prepare_vocal_evidence
                vocal_timeout = positive_deadline("ASR_VOCAL_EVIDENCE_TIMEOUT", 3600)
                vocal = prepare_vocal_evidence(source, timeout=vocal_timeout, cancel_check=check)
                run_options = {**run_options, "vocal_evidence_path": str(vocal)}

            def run_worker(worker_options, *, worker_metadata=None, worker_coverage=None):
                try:
                    # Lazy native generation can OOM after yielding earlier rows.
                    # Publish only a complete call, never GPU rows plus CPU copies.
                    return list(self._run_worker(source, worker_options, timeout, check,
                        metadata_callback=worker_metadata, coverage_callback=worker_coverage))
                except WhisperCudaMemoryError:
                    if self.device != "cuda":
                        raise
                    check_cancelled(check)
                    message = "GPU thiếu bộ nhớ; đang tự nhận diện lại đoạn này bằng CPU int8, giữ nguyên model và phần đã lưu."
                    logging.getLogger("pipeline").warning(
                        "WHISPER_CPU_FALLBACK run_id=%s reason=cuda_out_of_memory model=%s %s",
                        context.run_id, self.model_size, message)
                    if status_callback:
                        status_callback(message)
                    # The failed child has been reaped by _run_worker. Keep its
                    # partial rows private and retry with the same evidence.
                    rows = list(self._run_worker(source, worker_options, timeout, check,
                        device="cpu", compute_type="int8", metadata_callback=worker_metadata,
                        coverage_callback=worker_coverage))
                    logging.getLogger("pipeline").info(
                        "WHISPER_CPU_FALLBACK_COMPLETED run_id=%s model=%s segments=%s",
                        context.run_id, self.model_size, len(rows))
                    return rows

            if lazy_evidence:
                coverage_result, metadata_result = {}, {}
                metadata_handler = metadata_result.update if metadata_callback is not None else None
                rows = run_worker(run_options, worker_metadata=metadata_handler,
                                  worker_coverage=coverage_result.update)
                if coverage_result.get("unresolved_windows"):
                    localized = self._retry_unresolved_on_cpu(
                        source, options, coverage_result, metadata_result, timeout, check)
                    if localized:
                        previous = {id(row) for row in rows}
                        rows, coverage_result = _merge_focused_recovery(
                            rows, coverage_result, localized, {})
                        published = [row for row in rows if id(row) not in previous]
                        logging.getLogger("pipeline").info(
                            "WHISPER_GAP_CPU_RETRY_MERGED run_id=%s candidate_rows=%s published_rows=%s published_intervals=%s remaining_windows=%s",
                            context.run_id, len(localized), len(published),
                            [(round(row.start, 3), round(row.end, 3)) for row in published],
                            coverage_result.get("unresolved_windows", []))
                    if coverage_result.get("unresolved_windows"):
                        from core.engines.asr.vocal_evidence import prepare_vocal_evidence
                        logging.getLogger("pipeline").info(
                            "WHISPER_VOCAL_EVIDENCE_RETRY run_id=%s unresolved_windows=%s",
                            context.run_id, len(coverage_result["unresolved_windows"]))
                        missing = coverage_result["missing_speech_spans"]
                        vocal = prepare_vocal_evidence(source, timeout=positive_deadline(
                            "ASR_VOCAL_EVIDENCE_TIMEOUT", 3600), cancel_check=check, spans=missing)
                        retry_coverage = {}
                        recovered = run_worker({**options, "vocal_evidence_path": str(vocal),
                                                "vocal_evidence_spans": missing},
                            worker_coverage=retry_coverage.update)
                        previous = {id(row) for row in rows}
                        rows, coverage_result = _merge_focused_recovery(
                            rows, coverage_result, recovered, retry_coverage)
                        published = [row for row in rows if id(row) not in previous]
                        logging.getLogger("pipeline").info(
                            "WHISPER_VOCAL_EVIDENCE_MERGED run_id=%s candidate_rows=%s published_rows=%s published_intervals=%s remaining_windows=%s",
                            context.run_id, len(recovered), len(published),
                            [(round(row.start, 3), round(row.end, 3)) for row in published],
                            coverage_result.get("unresolved_windows", []))
                        if coverage_result.get("unresolved_windows"):
                            logging.getLogger("pipeline").warning(
                                "WHISPER_FOCUSED_GAPS_REMAIN run_id=%s unresolved_windows=%s; preserving original recognized rows",
                                context.run_id, len(coverage_result["unresolved_windows"]))
                    else:
                        logging.getLogger("pipeline").info(
                            "WHISPER_VOCAL_EVIDENCE_SKIPPED run_id=%s reason=cpu_gap_retry_completed_coverage",
                            context.run_id)
                else:
                    logging.getLogger("pipeline").info(
                        "WHISPER_VOCAL_EVIDENCE_SKIPPED run_id=%s reason=original_audio_coverage_complete",
                        context.run_id)
                check_cancelled(check)
                if metadata_callback is not None:
                    metadata_callback(metadata_result)
                if coverage_callback is not None:
                    coverage_callback(coverage_result)
            else:
                rows = run_worker(run_options, worker_metadata=metadata_callback,
                                  worker_coverage=coverage_callback)
            for row in rows:
                check_cancelled(check)
                yield row

    def _retry_unresolved_on_cpu(self, source, options, coverage, metadata, timeout, check):
        """Retry a few short, measured speech gaps on CPU before vocal separation.

        The retry is deliberately constrained by language confidence, gap count,
        duration and CPU threads. It only returns text with original audio timing;
        unresolved coverage remains blocking unless the normal merge validator
        proves each missing speech window has a non-overlapping transcript.
        """
        if self.device != "cuda" or not isinstance(coverage, dict):
            return []
        windows = coverage.get("unresolved_windows")
        if (not isinstance(windows, list) or not 1 <= len(windows) <= 2
                or any(not isinstance(item, (list, tuple)) or len(item) != 2 for item in windows)):
            return []
        try:
            from core.engines.asr.source_language import require_supported_source
            language = require_supported_source(metadata)
            if language["probability"] < .8:
                return []
            spans = [(float(left), float(right)) for left, right in windows]
            if (any(not 0 <= left < right or right - left > 12 for left, right in spans)
                    or sum(right - left for left, right in spans) > 18):
                return []
        except (TypeError, ValueError, OverflowError):
            return []

        recovered = []
        started = time.monotonic()
        try:
            with wave.open(str(source), "rb") as audio:
                if (audio.getcomptype() != "NONE" or audio.getframerate() != 16000
                        or audio.getnchannels() != 1 or audio.getsampwidth() != 2
                        or audio.getnframes() <= 0):
                    return []
                rate, frame_count = audio.getframerate(), audio.getnframes()
                duration = frame_count / rate
                for left, right in spans:
                    check_cancelled(check)
                    crop_left = max(0., left - .6)
                    crop_right = min(duration, right + .6)
                    first = max(0, int(math.floor(crop_left * rate)))
                    stop = min(frame_count, int(math.ceil(crop_right * rate)))
                    if stop <= first:
                        continue
                    audio.setpos(first)
                    frames = audio.readframes(stop - first)
                    temporary = None
                    try:
                        with tempfile.NamedTemporaryFile(prefix="whisper-gap-cpu-", suffix=".wav",
                                                         dir=source.parent, delete=False) as handle:
                            temporary = Path(handle.name)
                        with wave.open(str(temporary), "wb") as crop:
                            crop.setparams((1, 2, rate, 0, "NONE", "not compressed"))
                            crop.writeframes(frames)
                        local_options = {key: value for key, value in options.items()
                                         if key not in {"utterance_language_detection",
                                                        "vocal_evidence_path", "vocal_evidence_spans"}}
                        local_options.update(language=language["language"], vad_filter=False,
                                             multilingual=True, condition_on_previous_text=False)
                        rows = list(self._run_worker(temporary, local_options, timeout, check,
                            device="cpu", compute_type="int8", cpu_threads=2))
                        offset = first / rate
                        for row in rows:
                            if not row.text.strip():
                                continue
                            row.start += offset
                            row.end += offset
                            row.words = [SimpleNamespace(word=word.word,
                                start=word.start + offset, end=word.end + offset)
                                for word in row.words]
                            recovered.append(row)
                    finally:
                        if temporary is not None:
                            temporary.unlink(missing_ok=True)
        except (ASRProcessError, OSError, EOFError, wave.Error, TimeoutError) as error:
            logging.getLogger("pipeline").warning(
                "WHISPER_GAP_CPU_RETRY_FAILED run_id=%s error_type=%s",
                current_execution_context().run_id, type(error).__name__)
            return []
        logging.getLogger("pipeline").info(
            "WHISPER_GAP_CPU_RETRY_COMPLETED run_id=%s language=%s windows=%s rows=%s cpu_threads=2 elapsed_seconds=%.2f",
            current_execution_context().run_id, language["language"], len(spans), len(recovered),
            time.monotonic() - started)
        return recovered

    def _run_worker(self, source, options, timeout, check, *, device=None, compute_type=None,
                    metadata_callback=None, coverage_callback=None, cpu_threads=None):
        duration = None
        if coverage_callback is not None:
            from core.engines.asr.partial_coverage import input_duration
            duration = input_duration(source)
        threads = max(1, int(getattr(settings, "ASR_CPU_THREADS", 4)))
        if cpu_threads is not None:
            if type(cpu_threads) is not int or not 1 <= cpu_threads <= 2:
                raise ValueError("A focused CPU recovery may use at most two threads")
            threads = min(threads, cpu_threads)
        request = {"audio_path": str(source), "model_size": self.model_size,
                   "device": device or self.device,
                   "compute_type": compute_type or settings.WHISPER_COMPUTE_TYPE,
                   "cpu_threads": threads, "options": options, "capture_language": metadata_callback is not None,
                   "capture_coverage": coverage_callback is not None}
        records = iter_native_records("core.engines.asr.whisper_worker", request, timeout=timeout,
                                      cancel_check=check, prefix="whisper-worker-", threads=threads)
        count, speech_count, complete, metadata = 0, 0, False, None
        coverage, accepted = None, []
        try:
            for record in records:
                if complete:
                    raise ASRProcessError("Bộ nhận giọng trả dữ liệu sau khi kết thúc.")
                if record.get("event") in {"completed", "partial_completed"}:
                    if type(record.get("segments")) is not int or record["segments"] != count:
                        raise ASRProcessError("Kết quả nhận giọng thiếu hoặc trùng câu.")
                    partial = record["event"] == "partial_completed"
                    if coverage_callback is not None:
                        from core.engines.asr.partial_coverage import validate_coverage
                        try:
                            coverage = validate_coverage(record.get("coverage"), duration, accepted)
                        except (ValueError, TypeError, KeyError) as invalid:
                            raise ASRProcessError("Khoảng nguồn chưa nhận diện không hợp lệ.") from invalid
                        if partial != bool(coverage["unresolved_windows"]):
                            raise ASRProcessError("Xác nhận hoàn tất không khớp phần nguồn còn thiếu.")
                    elif partial or "coverage" in record:
                        raise ASRProcessError("Bộ nhận giọng trả nguồn chưa đầy đủ ngoài yêu cầu.")
                    complete = True
                elif record.get("event") == "language":
                    from core.engines.asr.source_language import language_metadata
                    if metadata_callback is None or metadata is not None:
                        raise ASRProcessError("Bộ nhận giọng trả thông tin ngôn ngữ ngoài yêu cầu.")
                    metadata = language_metadata(record)
                elif record == {"event": "error", "code": "cuda_out_of_memory", "device": "cuda"}:
                    if request["device"] != "cuda":
                        raise ASRProcessError("Bộ nhận giọng trả lỗi thiết bị không đúng yêu cầu.")
                    raise WhisperCudaMemoryError("GPU không đủ bộ nhớ để nhận diện đoạn này.")
                elif record.get("event") == "error" and record.get("code") == "source_speech_unrecognized":
                    spans = record.get("spans", [])
                    if (not isinstance(spans, list) or any(not isinstance(span, (list, tuple)) or len(span) != 2
                            or any(type(value) not in (int, float) or not math.isfinite(value) for value in span)
                            or not 0 <= span[0] < span[1] for span in spans)):
                        raise ASRProcessError("Bộ nhận giọng trả khoảng lời nói chưa nhận diện không hợp lệ.")
                    error = ASRProcessError("Có hoạt động lời nói nhưng ASR chưa trả lời nguồn; cần nhận diện lại đoạn này.")
                    error.code = "source_speech_unrecognized"
                    error.spans = spans
                    if "diagnostic" in record:
                        from core.engines.asr.speech_gap_diagnostic import validate_gap_diagnostic
                        try:
                            error.diagnostic = validate_gap_diagnostic(record["diagnostic"])
                        except (ValueError, TypeError) as invalid:
                            raise ASRProcessError("Bằng chứng VAD của khoảng lời nói không hợp lệ.") from invalid
                    raise error
                else:
                    segment = _decode_segment(record)
                    count += 1
                    speech_count += bool(segment.text.strip())
                    if coverage_callback is not None:
                        accepted.append(segment)
                    yield segment
            if not complete:
                raise ASRProcessError("Bộ nhận giọng thiếu xác nhận hoàn tất.")
            check_cancelled(check)
            if metadata_callback is not None:
                if metadata is None:
                    raise ASRProcessError("Bộ nhận giọng chưa xác nhận ngôn ngữ nguồn.")
                metadata_callback({**metadata, "speech_segments": speech_count})
            if coverage_callback is not None:
                check_cancelled(check)
                coverage_callback(coverage)
        finally:
            records.close()
