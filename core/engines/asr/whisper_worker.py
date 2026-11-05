"""One real Whisper inference; process exit releases MKL/ONNX native caches."""
from __future__ import annotations

import contextlib
import json
from pathlib import Path
import sys


def _cuda_out_of_memory(error):
    # CTranslate2 reports allocation failures as RuntimeError, including while
    # iterating generate(). Classify here, not by parsing parent stderr logs.
    if not isinstance(error, RuntimeError):
        return False
    detail = str(error).lower()
    return "cuda" in detail and ("out of memory" in detail or "cuda_error_out_of_memory" in detail)


def run(request_path, result_path):
    request = json.loads(Path(request_path).read_text(encoding="utf-8"))
    from config import settings
    from core.asr import load_whisper_model
    settings.WHISPER_COMPUTE_TYPE = request["compute_type"]
    settings.ASR_CPU_THREADS = request["cpu_threads"]
    # Native model startup may print diagnostics. It never writes the strict
    # result channel, including when launched by the desktop pythonw process.
    with contextlib.redirect_stdout(sys.stderr), Path(result_path).open("w", encoding="utf-8") as output:
        try:
            model = load_whisper_model(request["model_size"], request["device"])
            options = dict(request["options"])
            local_detection = options.pop("utterance_language_detection", False)
            vocal_path = options.pop("vocal_evidence_path", None)
            vocal_spans = options.pop("vocal_evidence_spans", None)
            metadata = None
            coverage = {}
            capture_coverage = request.get("capture_coverage", False)
            if capture_coverage and not (local_detection and options.get("language") is None):
                raise ValueError("Partial source coverage requires local language detection")
            if local_detection and options.get("language") is None:
                from faster_whisper.audio import decode_audio
                from core.engines.asr.utterance_language import transcribe_local_speech
                from core.engines.asr.language_disagreement import LocalLanguageReader
                segments, metadata = transcribe_local_speech(model,
                    decode_audio(request["audio_path"], sampling_rate=16000), options,
                    vocal_audio=decode_audio(vocal_path, sampling_rate=16000) if vocal_path else None,
                    vocal_spans=vocal_spans,
                    language_reader=LocalLanguageReader(),
                    **({"coverage_callback": coverage.update} if capture_coverage else {}))
            else:
                segments, info = model.transcribe(request["audio_path"], **options)
            count = 0
            for segment in segments:
                record = {"event": "segment", "segment": {"start": float(segment.start),
                    "end": float(segment.end), "text": segment.text,
                    "words": [{"word": word.word, "start": float(word.start), "end": float(word.end)}
                              for word in (segment.words or [])]}}
                if getattr(segment, "source_language", None) is not None:
                    record["segment"].update(source_language=segment.source_language,
                        source_language_probability=segment.source_language_probability)
                    if getattr(segment, "source_audio_evidence", None):
                        record["segment"]["source_audio_evidence"] = segment.source_audio_evidence
                    if getattr(segment, "source_language_review", None):
                        record["segment"]["source_language_review"] = segment.source_language_review
                output.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
                output.flush()
                count += 1
            if request.get("capture_language"):
                if metadata is not None:
                    info = metadata()
                output.write(json.dumps({"event": "language", "language": info.language,
                    "probability": float(info.language_probability)}, allow_nan=False) + "\n")
            output.write(json.dumps({"event": ("partial_completed" if coverage.get("unresolved_windows")
                                                else "completed"), "segments": count,
                                    **({"coverage": coverage} if capture_coverage else {})}, allow_nan=False) + "\n")
            output.flush()
        except RuntimeError as error:
            from core.engines.asr.utterance_language import UnrecognizedSpeechError
            if isinstance(error, UnrecognizedSpeechError):
                output.write(json.dumps({"event": "error", "code": "source_speech_unrecognized",
                    "spans": error.spans, **({"diagnostic": error.diagnostic}
                                            if error.diagnostic is not None else {})}, allow_nan=False) + "\n")
                output.flush()
                return
            if request["device"] != "cuda" or not _cuda_out_of_memory(error):
                raise
            output.write(json.dumps({"event": "error", "code": "cuda_out_of_memory", "device": "cuda"}) + "\n")
            output.flush()


if __name__ == "__main__":
    run(*sys.argv[1:])
