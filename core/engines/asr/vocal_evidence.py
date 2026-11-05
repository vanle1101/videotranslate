"""Content-scoped, atomic vocal evidence cache; caller owns the native slot."""
import json
import math
from pathlib import Path
import tempfile
import wave

from core.engines.asr.native_process import ASRProcessError, check_cancelled, iter_native_records
from core.review_checkpoint import file_digest

MODEL = "model_bs_roformer_ep_317_sdr_12.9755.ckpt"


def normalize_spans(spans, duration):
    """Validate and coalesce the unresolved speech spans used for local evidence."""
    if not isinstance(spans, (list, tuple)) or not spans:
        raise ValueError("Vocal evidence requires at least one unresolved speech span")
    normalized = []
    for span in spans:
        if (not isinstance(span, (list, tuple)) or len(span) != 2
                or any(type(value) not in (int, float) or not math.isfinite(value) for value in span)):
            raise ValueError("Unresolved speech spans are invalid")
        start, end = map(float, span)
        if not 0 <= start < end <= duration:
            raise ValueError("Unresolved speech span is outside the source audio")
        if normalized and start <= normalized[-1][1]:
            normalized[-1][1] = max(normalized[-1][1], end)
        else:
            normalized.append([start, end])
    return normalized


def validate_pcm(path, source):
    """Published evidence is mono PCM16 at16kHz with the same sample duration."""
    # Main source extraction is PCM WAV. Independent stereo/other-rate WAV
    # callers retain the same duration without resampling in the parent.
    with wave.open(str(source), "rb") as original, wave.open(str(path), "rb") as evidence:
        expected = round(original.getnframes() * 16000 / original.getframerate())
        if (evidence.getnchannels(), evidence.getsampwidth(), evidence.getframerate(), evidence.getcomptype()) != (1, 2, 16000, "NONE"):
            raise ASRProcessError("Bằng chứng giọng nguồn không đúng định dạng PCM.")
        if expected <= 0 or abs(evidence.getnframes() - expected) > 1:
            raise ASRProcessError("Thời lượng bằng chứng giọng nguồn không khớp audio gốc.")
        if len(evidence.readframes(evidence.getnframes())) != evidence.getnframes() * 2:
            raise ASRProcessError("Bằng chứng giọng nguồn thiếu dữ liệu PCM.")


def prepare_vocal_evidence(source, *, timeout, cancel_check=None, spans=None,
                           crop_padding=1.5, minimum_window_seconds=2.6):
    from config import settings
    from core.engines.separator.roformer_engine import BSRoFormerSeparator
    source = Path(source)
    check = lambda: check_cancelled(cancel_check)
    model = settings.BASE_DIR / "workspace/models/audio_separator" / MODEL
    if not model.is_file():
        raise ASRProcessError("Thiếu model tách giọng để kiểm tra lời nói bị nhạc che.")
    selected_spans = None
    if spans is not None:
        if (type(crop_padding) not in (int, float) or not math.isfinite(crop_padding)
                or not 0 <= crop_padding <= 12
                or type(minimum_window_seconds) not in (int, float)
                or not math.isfinite(minimum_window_seconds)
                or not 2.6 <= minimum_window_seconds <= 16):
            raise ValueError("Vocal evidence crop bounds are invalid")
        with wave.open(str(source), "rb") as audio:
            duration = audio.getnframes() / float(audio.getframerate())
        selected_spans = normalize_spans(spans, duration)
    identity = {"version": 3, "source_sha256": file_digest(source, check), "model": MODEL,
                "model_sha256": file_digest(model, check), "segment_size": BSRoFormerSeparator.SEGMENT_SIZE,
                "step_seconds": float(getattr(settings, "ROFORMER_STEP_SECONDS", BSRoFormerSeparator.STEP_SECONDS)),
                "spans": selected_spans,
                "crop_padding": float(crop_padding) if selected_spans is not None else None,
                "minimum_window_seconds": float(minimum_window_seconds) if selected_spans is not None else None,
                "model_config_sha256": file_digest(model.with_suffix(".yaml"), check),
                "separator_code": file_digest(Path(__file__).parents[1] / "separator/roformer_engine.py", check),
                "worker_code": file_digest(Path(__file__).with_name("vocal_evidence_worker.py"), check),
                "cache_code": file_digest(Path(__file__), check)}
    target = source.with_name(source.stem + "_vocal_evidence.wav")
    manifest = target.with_suffix(".json")
    check_cancelled(cancel_check)
    try:
        saved = json.loads(manifest.read_text(encoding="utf8"))
        if saved["identity"] == identity and saved["sha256"] == file_digest(target, check):
            validate_pcm(target, source)
            return target
    except (OSError, ValueError, KeyError):
        pass
    with tempfile.TemporaryDirectory(prefix="vocal-evidence-", dir=source.parent) as directory:
        partial = Path(directory) / "vocals.wav"
        records = iter_native_records("core.engines.asr.vocal_evidence_worker",
            {"audio_path": str(source.resolve()), "output_path": str(partial.resolve()), "model": MODEL,
             "spans": selected_spans, "crop_padding": crop_padding,
             "minimum_window_seconds": minimum_window_seconds},
            timeout=timeout, cancel_check=cancel_check, prefix="vocal-worker-",
            threads=max(1, int(getattr(settings, "ASR_CPU_THREADS", 2))))
        try:
            events = list(records)  # Exhaustion reaps separator before Whisper loads.
        finally:
            records.close()
        check_cancelled(cancel_check)
        if events != [{"event": "completed"}] or not partial.is_file() or partial.stat().st_size <= 44:
            raise ASRProcessError("Chưa tạo được bằng chứng giọng nguồn hoàn chỉnh.")
        digest = file_digest(partial, check)
        validate_pcm(partial, source)
        if file_digest(source, check) != identity["source_sha256"]:
            raise ASRProcessError("Nguồn thay đổi trong khi tách bằng chứng lời nói.")
        partial.replace(target)
        temporary = Path(directory) / "manifest.json"
        temporary.write_text(json.dumps({"identity": identity, "sha256": digest}), encoding="utf8")
        temporary.replace(manifest)
    return target
