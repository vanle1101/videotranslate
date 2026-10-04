from pathlib import Path
from typing import List, Dict, Any
from config import settings


def load_whisper_model(model_size: str, device: str = None):
    """Load the configured CTranslate2 model without requiring PyTorch."""
    from faster_whisper import WhisperModel

    device = device or settings.DEVICE
    compute_type = getattr(settings, "WHISPER_COMPUTE_TYPE", "int8")
    threads = max(1, int(getattr(settings, "ASR_CPU_THREADS", 4)))
    local_model = settings.BASE_DIR / "workspace" / "models" / f"faster-whisper-{model_size}"
    model_source = str(local_model) if (local_model / "config.json").is_file() else model_size
    kwargs = {"cpu_threads": threads, "num_workers": 1}
    print(f"[*] Loading Faster-Whisper ({model_size}) on {device} ({compute_type})...")
    try:
        return WhisperModel(model_source, device=device, compute_type=compute_type, **kwargs)
    except (RuntimeError, ValueError) as exc:
        if device != "cuda":
            raise
        print(f"[!] CUDA unavailable ({exc}); using CPU int8.")
        return WhisperModel(model_source, device="cpu", compute_type="int8", **kwargs)


class ChineseASR:
    def __init__(self, model_size: str = None, device: str = None):
        self.model_size = model_size or settings.WHISPER_MODEL_SIZE
        self.device = device or settings.DEVICE
        self.model = None

    def _load_model(self):
        if self.model is None:
            self.model = load_whisper_model(self.model_size, self.device)

    def transcribe(self, audio_path: str) -> List[Dict[str, Any]]:
        """
        Transcribe Chinese audio into timestamped segments.
        Returns:
            List of dicts: [
                {"id": 0, "start": 0.5, "end": 2.8, "text": "大家好，今天给大家分享一个好东西。"}
            ]
        """
        self._load_model()
        print(f"[*] Transcribing Chinese speech from: {Path(audio_path).name}...")

        # Language set to Chinese ('zh')
        segments, info = self.model.transcribe(
            str(audio_path),
            language="zh",
            task="transcribe",
            vad_filter=True, # Voice Activity Detection removes silence
            vad_parameters=dict(min_silence_duration_ms=400),
            beam_size=5,
            word_timestamps=True
        )

        result_segments = []
        seg_id = 0
        for segment in segments:
            text = segment.text.strip()
            if not text:
                continue
            result_segments.append({
                "id": seg_id,
                "start": round(segment.start, 2),
                "end": round(segment.end, 2),
                "duration": round(segment.end - segment.start, 2),
                "text": text,
                "words": [
                    {
                        "word": w.word,
                        "start": round(w.start, 2),
                        "end": round(w.end, 2)
                    } for w in (segment.words or [])
                ]
            })
            seg_id += 1

        print(f"[+] Transcription complete: {len(result_segments)} segments detected.")
        return result_segments
