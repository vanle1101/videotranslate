import time
from pathlib import Path
from typing import List, Dict, Any, Optional
from config import settings
from core.engines.asr.base import ASREngine

class SenseVoiceEngine(ASREngine):
    """
    FunAudioLLM/SenseVoice ASR Engine.
    High-accuracy Chinese speech recognition with emotions, sound events,
    and automatic punctuation and timestamps.
    """
    def __init__(self, model_path: Optional[Path] = None):
        self.model_dir = model_path or (settings.BASE_DIR / "workspace" / "models" / "sensevoice_onnx")
        self.model_file = self.model_dir / "model.int8.onnx"
        self.tokens_file = self.model_dir / "tokens.txt"
        self.recognizer = None

    @property
    def name(self) -> str:
        return "FunAudioLLM/SenseVoice"

    @property
    def is_available(self) -> bool:
        return self.model_file.exists() and self.tokens_file.exists()

    def _ensure_loaded(self):
        if self.recognizer is None:
            if not self.is_available:
                raise FileNotFoundError(f"SenseVoice model files not found in {self.model_dir}")
            
            print(f"[*] Initializing SenseVoice from {self.model_dir.name}...")
            import sherpa_onnx
            self.recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
                model=str(self.model_file),
                tokens=str(self.tokens_file),
                num_threads=4,
                use_itn=True
            )
            print("[+] SenseVoice initialized successfully.")

    def get_info(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "engine": "SenseVoiceSmall",
            "is_available": self.is_available,
            "model_path": str(self.model_file),
            "supports_emotion": True,
            "supports_events": True
        }

    def transcribe(
        self,
        audio_path: Path,
        language: str = "zh",
        progress_callback: Optional[callable] = None
    ) -> List[Dict[str, Any]]:
        """
        Transcribes audio into internal segment format:
        {
            "id": 0,
            "start": float,
            "end": float,
            "duration": float,
            "text_zh": str,
            "emotion": str,
            "speaker": None
        }
        """
        self._ensure_loaded()
        import soundfile as sf
        import subprocess

        if progress_callback:
            progress_callback(10, f"SenseVoice: Đang đọc và chuẩn hóa âm thanh {audio_path.name}...")

        # Convert to 16kHz mono WAV for optimal SenseVoice inference
        temp_16k = settings.TEMP_DIR / f"sensevoice_input_{int(time.time())}.wav"
        cmd = [
            "ffmpeg", "-y", "-i", str(audio_path),
            "-ar", "16000", "-ac", "1",
            str(temp_16k)
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

        samples, sample_rate = sf.read(str(temp_16k))
        duration = len(samples) / sample_rate

        if progress_callback:
            progress_callback(40, f"SenseVoice: Đang nhận dạng tiếng Trung và cảm xúc...")

        t0 = time.time()
        stream = self.recognizer.create_stream()
        stream.accept_waveform(sample_rate, samples)
        self.recognizer.decode_stream(stream)
        res = stream.result
        elapsed = time.time() - t0

        raw_text = res.text.strip()
        emotion = getattr(res, "emotion", "<|NEUTRAL|>")
        timestamps = getattr(res, "timestamps", [])

        # Clean temp
        if temp_16k.exists():
            temp_16k.unlink()
        try:
            print(f"[+] SenseVoice decoded in {elapsed:.2f}s: {raw_text} (Emotion: {emotion})")
        except Exception:
            pass
        if not raw_text:
            return []

        # Segment formatting
        # Split sentences by Chinese punctuation (。！？；)
        import re
        sentence_parts = re.split(r'([。！？；])', raw_text)
        sentences = []
        for i in range(0, len(sentence_parts) - 1, 2):
            sentences.append(sentence_parts[i] + sentence_parts[i+1])
        if len(sentence_parts) % 2 == 1 and sentence_parts[-1].strip():
            sentences.append(sentence_parts[-1].strip())

        if not sentences:
            sentences = [raw_text]

        # Allocate start and end times across sentences
        segments = []
        time_per_sentence = duration / len(sentences)
        current_time = 0.0

        for idx, sent in enumerate(sentences):
            sent_clean = sent.strip()
            if not sent_clean:
                continue
            seg_start = round(current_time, 2)
            seg_end = round(min(duration, current_time + time_per_sentence), 2)
            segments.append({
                "id": idx,
                "start": seg_start,
                "end": seg_end,
                "duration": round(seg_end - seg_start, 2),
                "text_zh": sent_clean,
                "text": sent_clean, # for backward compatibility
                "emotion": emotion,
                "speaker": None
            })
            current_time = seg_end

        if progress_callback:
            progress_callback(100, f"SenseVoice hoàn tất ({len(segments)} segments)")

        return segments
