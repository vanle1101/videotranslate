"""Disposable RoFormer evidence worker; never keep its GPU alongside ASR."""
import contextlib
import json
import math
import os
from pathlib import Path
import sys


def _configure_cpu_backend(torch_module, *, force_cpu):
    """Keep this disposable evidence worker off a CUDA runtime when requested."""
    if force_cpu:
        # audio-separator selects its device through this probe. On machines
        # with a broken/hidden CUDA device, torch may report availability but
        # fail get_device_properties("cuda"); the RoFormer then aborts before
        # it can use its CPU attention implementation.
        torch_module.cuda.is_available = lambda: False


def _partial_windows(spans, duration, *, padding=1.5, minimum=2.6):
    """Expand uncertain speech spans with context, then merge nearby crops."""
    if (type(padding) not in (int, float) or not math.isfinite(padding) or not 0 <= padding <= 12
            or type(minimum) not in (int, float) or not math.isfinite(minimum) or not 2.6 <= minimum <= 16):
        raise ValueError("Partial vocal evidence crop bounds are invalid")
    if not isinstance(spans, list) or not spans:
        raise ValueError("Partial vocal evidence requires unresolved spans")
    windows = []
    for span in spans:
        if (not isinstance(span, (list, tuple)) or len(span) != 2
                or any(type(value) not in (int, float) or not math.isfinite(value) for value in span)):
            raise ValueError("Unresolved speech spans are invalid")
        start, end = map(float, span)
        if not 0 <= start < end <= duration:
            raise ValueError("Unresolved speech span is outside the source audio")
        left, right = max(0.0, start - padding), min(duration, end + padding)
        missing = max(0.0, minimum - (right - left))
        if missing:
            left = max(0.0, left - missing / 2)
            right = min(duration, right + missing / 2)
            if right - left < minimum:
                if left <= 0:
                    right = min(duration, minimum)
                else:
                    left = max(0.0, duration - minimum)
        if windows and left <= windows[-1][1]:
            windows[-1][1] = max(windows[-1][1], right)
        else:
            windows.append([left, right])
    return windows


def _make_partial_evidence(request, separator, output, run_media):
    import numpy as np
    import soundfile as sf

    source = Path(request["audio_path"])
    audio, sample_rate = sf.read(str(source), dtype="float32", always_2d=True)
    if sample_rate != 16000 or audio.shape[1] != 1 or not len(audio):
        raise ValueError("Partial vocal evidence requires mono 16 kHz audio")
    duration = len(audio) / float(sample_rate)
    windows = _partial_windows(request.get("spans"), duration,
        padding=request.get("crop_padding", 1.5), minimum=request.get("minimum_window_seconds", 2.6))
    output_audio = audio.copy()
    work_directory = Path(request["work_directory"])
    work_directory.mkdir(parents=True, exist_ok=True)
    for index, (start, end) in enumerate(windows):
        first, last = round(start * sample_rate), round(end * sample_rate)
        crop = audio[first:last]
        crop_path = work_directory / f"crop_{index:03d}.wav"
        sf.write(str(crop_path), crop, sample_rate, subtype="PCM_16")
        stems = work_directory / f"stems_{index:03d}"
        vocal, _ = separator.separate(crop_path, stems)
        vocal_16k = work_directory / f"vocal_{index:03d}_16k.wav"
        run_media(["ffmpeg", "-v", "error", "-nostdin", "-y", "-i", str(vocal),
                   "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(vocal_16k)])
        isolated, rate = sf.read(str(vocal_16k), dtype="float32", always_2d=True)
        if rate != sample_rate or isolated.shape[1] != 1:
            raise ValueError("RoFormer evidence crop has an unsupported audio format")
        expected = last - first
        if abs(len(isolated) - expected) > 1:
            raise ValueError("RoFormer evidence crop duration does not match its source")
        if len(isolated) < expected:
            isolated = np.pad(isolated, ((0, expected - len(isolated)), (0, 0)))
        isolated = isolated[:expected]

        # Blend only at crop boundaries. The padded context keeps the actual
        # unresolved speech away from those edges.
        fade = min(round(.1 * sample_rate), expected // 2)
        if fade:
            ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)[:, None]
            isolated[:fade] = audio[first:first + fade] * (1 - ramp) + isolated[:fade] * ramp
            isolated[-fade:] = audio[last - fade:last] * ramp[::-1] + isolated[-fade:] * (1 - ramp[::-1])
        output_audio[first:last] = isolated
    sf.write(str(output), np.clip(output_audio, -1.0, 1.0), sample_rate, subtype="PCM_16")


def run(request_path, result_path):
    request = json.loads(Path(request_path).read_text(encoding="utf8"))
    with contextlib.redirect_stdout(sys.stderr):
        from config import settings
        if (str(getattr(settings, "DEVICE", "")).strip().lower() == "cuda"
                and os.environ.get("CUDA_VISIBLE_DEVICES", "").strip() == "-1"):
            os.environ.pop("CUDA_VISIBLE_DEVICES", None)
        import torch
        force_cpu = (str(getattr(settings, "DEVICE", "")).strip().lower() == "cpu"
                     or os.environ.get("STUDIO_FORCE_CPU") == "1")
        _configure_cpu_backend(torch, force_cpu=force_cpu)
        from core.engines.separator.roformer_engine import BSRoFormerSeparator
        from core.media_process import run_media
        output = Path(request["output_path"])
        separator = BSRoFormerSeparator(request["model"])
        if request.get("spans") is not None:
            _make_partial_evidence(request, separator, output, run_media)
        else:
            vocal, _ = separator.separate(
                Path(request["audio_path"]), Path(request["work_directory"]) / "stems")
            run_media(["ffmpeg", "-v", "error", "-nostdin", "-y", "-i", str(vocal),
                       "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(output)])
    Path(result_path).write_text(json.dumps({"event": "completed"}) + "\n", encoding="utf8")


if __name__ == "__main__":
    run(*sys.argv[1:])
