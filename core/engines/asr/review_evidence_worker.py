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
    wav = Path(request["work_directory"]) / "speech.wav"
    recognizer, model = None, None
    try:
        if engine == "sensevoice":
            from core.engines.asr.sensevoice_engine import SenseVoiceEngine
            recognizer = SenseVoiceEngine(model_path=Path(request["sensevoice_model_dir"]))
            recognizer._ensure_loaded()
        elif engine == "faster-whisper-small":
            from faster_whisper import WhisperModel
            model = WhisperModel(request["whisper_model_path"], device="cpu", compute_type="int8",
                                 cpu_threads=4, num_workers=1, local_files_only=True)
        else:
            raise ValueError("Unknown local evidence recognizer")
        for row in rows:
            run_media(["ffmpeg", "-v", "error", "-nostdin", "-y", "-ss", str(row["start"]),
                       "-t", str(row["end"] - row["start"]), "-i", request["video_path"],
                       "-vn", "-ac", "1", "-ar", "16000", str(wav)])
            if recognizer is not None:
                import soundfile as sf
                samples, rate = sf.read(str(wav), dtype="float32")
                stream = recognizer.recognizer.create_stream()
                stream.accept_waveform(rate, samples)
                recognizer.recognizer.decode_stream(stream)
                text = stream.result.text.strip()
                del stream, samples
            else:
                decoded, _ = model.transcribe(str(wav), language="zh", task="transcribe",
                    beam_size=5, vad_filter=False, condition_on_previous_text=False)
                try:
                    text = " ".join(part.text.strip() for part in decoded).strip()
                finally:
                    close = getattr(decoded, "close", None)
                    if callable(close):
                        close()
            emit({"event": "evidence", "id": row["id"], "engine": engine, "text": text})
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
