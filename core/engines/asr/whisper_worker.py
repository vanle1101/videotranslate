"""One real Whisper inference; process exit releases MKL/ONNX native caches."""
from __future__ import annotations

import contextlib
import json
from pathlib import Path
import sys


def run(request_path, result_path):
    request = json.loads(Path(request_path).read_text(encoding="utf-8"))
    from config import settings
    from core.asr import load_whisper_model
    settings.WHISPER_COMPUTE_TYPE = request["compute_type"]
    settings.ASR_CPU_THREADS = request["cpu_threads"]
    # Native model startup may print diagnostics. It never writes the strict
    # result channel, including when launched by the desktop pythonw process.
    with contextlib.redirect_stdout(sys.stderr):
        model = load_whisper_model(request["model_size"], request["device"])
        segments, _ = model.transcribe(request["audio_path"], **request["options"])
        with Path(result_path).open("w", encoding="utf-8") as output:
            count = 0
            for segment in segments:
                record = {"event": "segment", "segment": {"start": float(segment.start),
                    "end": float(segment.end), "text": segment.text,
                    "words": [{"word": word.word, "start": float(word.start), "end": float(word.end)}
                              for word in (segment.words or [])]}}
                output.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
                output.flush()
                count += 1
            output.write(json.dumps({"event": "completed", "segments": count}) + "\n")
            output.flush()


if __name__ == "__main__":
    run(*sys.argv[1:])
