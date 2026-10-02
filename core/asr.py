import os
from pathlib import Path
from typing import List, Dict, Any
from config import settings

class ChineseASR:
    def __init__(self, model_size: str = None, device: str = None):
        self.model_size = model_size or settings.WHISPER_MODEL_SIZE
        self.device = device or settings.DEVICE
        self.model = None

    def _load_model(self):
        if self.model is None:
            from faster_whisper import WhisperModel
            import torch

            # Verify if CUDA is really usable without OOM
            use_device = self.device
            compute_type = "float16"

            if use_device == "cuda" and not torch.cuda.is_available():
                use_device = "cpu"
                compute_type = "int8"
            elif use_device == "cuda":
                # For GTX 1050 Ti (4GB), int8_float16 or float16 is very stable
                compute_type = "int8_float16"

            print(f"[*] Loading Faster-Whisper ({self.model_size}) on {use_device} ({compute_type})...")
            try:
                self.model = WhisperModel(self.model_size, device=use_device, compute_type=compute_type)
            except Exception as e:
                print(f"[!] Warning: CUDA initialization failed ({e}), falling back to CPU int8.")
                self.model = WhisperModel(self.model_size, device="cpu", compute_type="int8")

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
