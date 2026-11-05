import time
import tempfile
from pathlib import Path
from typing import List, Dict, Any, Optional
from config import settings
from core.engines.asr.base import ASREngine
from core.engines.asr.source_language import SUPPORTED_SOURCE_LANGUAGES
from core.engines.asr.native_process import ASRProcessError


def _detected_language(result):
    value = getattr(result, "lang", None)
    if isinstance(value, str):
        value = value.strip().lower()
        if value.startswith("<|") and value.endswith("|>"):
            value = value[2:-2].strip()
    if not isinstance(value, str) or value not in SUPPORTED_SOURCE_LANGUAGES:
        error = ASRProcessError("SenseVoice chưa xác định được ngôn ngữ lời nguồn được hỗ trợ; cần nhận diện lại đoạn này.")
        error.code = "source_language_unrecognized"
        raise error
    return value

class SenseVoiceEngine(ASREngine):
    """
    FunAudioLLM/SenseVoice ASR Engine.
    Multilingual speech recognition with emotions and sound events.
    Sentence times are approximate allocations, not measured alignment.
    """
    def __init__(self, model_path: Optional[Path] = None, *, language: str = ""):
        self.model_dir = model_path or (settings.BASE_DIR / "workspace" / "models" / "sensevoice_onnx")
        self.model_file = self.model_dir / "model.int8.onnx"
        self.tokens_file = self.model_dir / "tokens.txt"
        self.recognizer = None
        self.language = language

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
            try:
                cpu_threads = max(1, min(2, int(settings.ASR_CPU_THREADS)))
            except (TypeError, ValueError, OverflowError):
                cpu_threads = 2
            self.recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
                model=str(self.model_file),
                tokens=str(self.tokens_file),
                num_threads=cpu_threads,
                use_itn=True,
                language=self.language,
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

        # Each inference owns its PCM even when multiple sessions start in the
        # same second. Clean it after conversion, decoding or callback errors.
        with tempfile.TemporaryDirectory(prefix="sensevoice-", dir=settings.TEMP_DIR) as directory:
            temp_16k = Path(directory) / "input.wav"
            cmd = [
                "ffmpeg", "-y", "-i", str(audio_path),
                "-ar", "16000", "-ac", "1",
                str(temp_16k)
            ]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           check=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

            samples, sample_rate = sf.read(str(temp_16k))
            duration = len(samples) / sample_rate

            if progress_callback:
                progress_callback(40, "SenseVoice: Đang nhận dạng lời nói và cảm xúc...")

            t0 = time.time()
            stream = self.recognizer.create_stream()
            stream.accept_waveform(sample_rate, samples)
            self.recognizer.decode_stream(stream)
            res = stream.result
            elapsed = time.time() - t0

            raw_text = res.text.strip()
            emotion = getattr(res, "emotion", "<|NEUTRAL|>")
            # The legacy transcribe(language='zh') argument is not a measured
            # label. The recognizer is configured by self.language (auto by
            # default); route the transcript using its actual decode result.
            detected_language = _detected_language(res) if raw_text else None
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
                "source_language": detected_language,
                # SenseVoice exposes a label, not a calibrated posterior or
                # independent verification of this source transcript.
                "source_language_probability": 0.,
                "source_language_uncertain": True,
                "speaker": None
            })
            current_time = seg_end

        if progress_callback:
            progress_callback(100, f"SenseVoice hoàn tất ({len(segments)} segments)")

        return segments
