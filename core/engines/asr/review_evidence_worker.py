"""One local evidence recognizer per disposable process, never provider calls."""
from __future__ import annotations

import contextlib
import json
from pathlib import Path
import sys


def collect_engine(request, emit):
    """Keep the previous two recognizers' exact independent decode settings."""
    from core.media_process import run_media
    engine = request["engine"]
    rows = request["rows"]
    # Explicit source-language metadata is supplied only by an owning caller.
    # Existing Chinese review must not auto-detect on subsecond fragments.
    language = request.get("source_language", "zh")
    if language not in {"zh", "ko", "ja", "en", "yue"}:
        raise ValueError("Unsupported evidence source language")
    wav = Path(request["work_directory"]) / "speech.wav"
    recognizer, model = None, None
    try:
        if engine == "sensevoice":
            from core.engines.asr.sensevoice_engine import SenseVoiceEngine
            # This review pipeline owns Chinese source dialogue. Automatic
            # language detection on subsecond crops was selecting Japanese or
            # English and creating a spurious disagreement with Chinese ASR.
            recognizer = SenseVoiceEngine(model_path=Path(request["sensevoice_model_dir"]), language=language)
            recognizer._ensure_loaded()
        elif engine in {"faster-whisper-small", "faster-whisper-medium", "faster-whisper-large-v3"}:
            from faster_whisper import WhisperModel
            model = WhisperModel(request["whisper_model_path"], device="cpu", compute_type="int8",
                                 cpu_threads=4, num_workers=1, local_files_only=True)
        else:
            raise ValueError("Unknown local evidence recognizer")
        contextual = request.get("contextual") is True
        from core.engines.asr.contextual_evidence import PADDING
        for row in rows:
            start = max(0, row["start"] - PADDING) if contextual else row["start"]
            end = row["end"] + PADDING if contextual else row["end"]
            timing = []
            run_media(["ffmpeg", "-v", "error", "-nostdin", "-y", "-ss", str(start),
                       "-t", str(end - start), "-i", request["video_path"],
                       "-vn", "-ac", "1", "-ar", "16000", str(wav)])
            if recognizer is not None:
                import soundfile as sf
                samples, rate = sf.read(str(wav), dtype="float32")
                stream = recognizer.recognizer.create_stream()
                stream.accept_waveform(rate, samples)
                recognizer.recognizer.decode_stream(stream)
                text = stream.result.text.strip()
                if contextual:
                    result = stream.result
                    if len(result.tokens) != len(result.timestamps):
                        raise ValueError("Contextual SenseVoice token/timestamp lengths differ")
                    timing = [{"text": token, "at": start + at}
                              for token, at in zip(result.tokens, result.timestamps)]
                del stream, samples
            else:
                options = {"word_timestamps": True} if contextual else {}
                decoded, _ = model.transcribe(str(wav), language=language, task="transcribe",
                    beam_size=5, vad_filter=False, condition_on_previous_text=False, **options)
                try:
                    texts = []
                    for part in decoded:
                        texts.append(part.text.strip())
                        if contextual:
                            timing.extend({"text": word.word, "start": start + word.start,
                                           "end": start + word.end, "probability": word.probability}
                                          for word in part.words or [])
                    text = " ".join(texts).strip()
                finally:
                    close = getattr(decoded, "close", None)
                    if callable(close):
                        close()
            record = {"event": "evidence", "id": row["id"], "engine": engine, "text": text}
            if "source_language" in request:
                record["source_language"] = language
            if contextual:
                record.update(crop_start=start, crop_end=end, timing=timing)
            emit(record)
            wav.unlink(missing_ok=True)
        emit({"event": "completed", "rows": len(rows), "engine": engine})
    finally:
        if recognizer is not None:
            recognizer.recognizer = None
        model = None
        wav.unlink(missing_ok=True)


def run(request_path, result_path):
    request = json.loads(Path(request_path).read_text(encoding="utf-8"))
    with contextlib.redirect_stdout(sys.stderr), Path(result_path).open("w", encoding="utf-8") as output:
        def emit(record):
            output.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
            output.flush()
        collect_engine(request, emit)


if __name__ == "__main__":
    run(*sys.argv[1:])
