from pathlib import Path
from typing import List, Dict, Any, Optional
import importlib.util
import math
from config import settings
from core.engines.asr.base import ASREngine
from core.dialogue_segments import split_dialogue_segments
from core.runtime_context import current_execution_context
import asyncio

class FasterWhisperFallbackEngine(ASREngine):
    """
    Fallback ASR engine using Faster-Whisper.
    Used when SenseVoice is unavailable or requested by user.
    """
    def __init__(self, model_size: Optional[str] = None):
        self.model_size = model_size or settings.WHISPER_MODEL_SIZE
        self.model = None

    @property
    def name(self) -> str:
        return f"Faster-Whisper ({self.model_size}) [Fallback]"

    @property
    def is_available(self) -> bool:
        try:
            # Availability checks run in the desktop/backend process. Importing
            # faster_whisper here also loads native CTranslate2; on affected
            # Windows/CUDA setups that extension can terminate pythonw instead
            # of raising an exception. Keep native inference inside its worker.
            return importlib.util.find_spec("faster_whisper") is not None
        except (ImportError, ModuleNotFoundError, ValueError):
            return False

    def _ensure_loaded(self):
        if self.model is None:
            from core.engines.asr.whisper_process import SubprocessWhisperModel
            self.model = SubprocessWhisperModel(self.model_size)

    def get_info(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "engine": "faster-whisper",
            "is_available": self.is_available,
            "is_fallback": True
        }

    def transcribe(
        self,
        audio_path: Path,
        language: Optional[str] = "zh",
        progress_callback: Optional[callable] = None,
        metadata_callback: Optional[callable] = None,
        coverage_callback: Optional[callable] = None,
    ) -> List[Dict[str, Any]]:
        if coverage_callback is not None and (not callable(coverage_callback) or language is not None):
            raise ValueError("Partial source coverage requires automatic language detection")
        context = current_execution_context()
        def check_cancelled():
            if context.cancel_check and context.cancel_check():
                raise asyncio.CancelledError("Speech recognition cancelled")
        check_cancelled()
        self._ensure_loaded()
        check_cancelled()
        coverage = {}
        segments_gen, _ = self.model.transcribe(
            str(audio_path),
            language=language,
            # Automatic detection must be refreshed while decoding: one initial
            # language for an entire interval transliterates code-switched speech.
            # Explicit evidence calls retain their requested language and context.
            **({"multilingual": True, "condition_on_previous_text": False,
                "utterance_language_detection": True}
               if language is None else {}),
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 300},
            beam_size=5,
            word_timestamps=True,
            **({"metadata_callback": metadata_callback} if metadata_callback is not None else {}),
            **({"coverage_callback": coverage.update} if coverage_callback is not None else {}),
        )

        results = []
        try:
            for idx, seg in enumerate(segments_gen):
                check_cancelled()
                words = []
                for word in (getattr(seg, "words", None) or []):
                    word_text = getattr(word, "word", "")
                    word_start = getattr(word, "start", None)
                    word_end = getattr(word, "end", None)
                    if not isinstance(word_text, str):
                        continue
                    if not isinstance(word_start, (int, float)) or not isinstance(word_end, (int, float)):
                        continue
                    words.append({"word": word_text, "start": round(float(word_start), 3),
                                  "end": round(float(word_end), 3)})
                results.append({
                    "id": idx,
                    "start": round(seg.start, 2),
                    "end": round(seg.end, 2),
                    "duration": round(seg.end - seg.start, 2),
                    "text_zh": seg.text.strip(),
                    "text": seg.text.strip(),
                    "words": words,
                    "emotion": "<|NEUTRAL|>",
                    "speaker": getattr(seg, "speaker", None)
                })
                if getattr(seg, "source_language", None) is not None:
                    results[-1].update(source_language=seg.source_language,
                        source_language_probability=seg.source_language_probability)
                if getattr(seg, "source_audio_evidence", None):
                    results[-1]["source_audio_evidence"] = seg.source_audio_evidence
                if getattr(seg, "source_language_review", None):
                    results[-1]["source_language_review"] = seg.source_language_review
                if progress_callback:
                    progress_callback(float(seg.end))
        finally:
            close = getattr(segments_gen, "close", None)
            if callable(close):
                close()
        check_cancelled()
        # Return complete utterances instead of Whisper's sometimes long VAD
        # chunks.  The helper uses only measured word timestamps and leaves a
        # row intact when Whisper did not provide usable word timing.
        result = split_dialogue_segments(results)
        if coverage_callback is not None:
            from core.engines.asr.partial_coverage import input_duration, validate_coverage
            checked = validate_coverage(coverage, input_duration(audio_path), result)
            check_cancelled()
            coverage_callback(checked)
        return result

    def transcribe_focused(self, audio_path: Path, *, language: str = "zh",
                           timeout_seconds: float = 90.0) -> List[Dict[str, Any]]:
        """Run one bounded, original-audio hypothesis without VAD cropping.

        This recovery path returns timestamped candidates for caller
        validation; it never certifies a source interval by itself.
        """
        if language not in {"zh", "en", "ja", "ko", "yue"}:
            raise ValueError("Focused ASR language is not supported")
        if (type(timeout_seconds) not in (int, float)
                or not math.isfinite(timeout_seconds) or not 1 <= timeout_seconds <= 600):
            raise ValueError("Focused ASR timeout must be between 1 and 600 seconds")
        context = current_execution_context()
        if context.cancel_check and context.cancel_check():
            raise asyncio.CancelledError("Speech recognition cancelled")
        self._ensure_loaded()
        generated, _ = self.model.transcribe(
            str(audio_path), language=language, beam_size=5, word_timestamps=True,
            vad_filter=False, multilingual=True, condition_on_previous_text=False,
            timeout_seconds=float(timeout_seconds))
        results = []
        try:
            for segment in generated:
                if context.cancel_check and context.cancel_check():
                    raise asyncio.CancelledError("Speech recognition cancelled")
                text = str(getattr(segment, "text", "") or "").strip()
                start, end = getattr(segment, "start", None), getattr(segment, "end", None)
                if not text or type(start) not in (int, float) or type(end) not in (int, float):
                    continue
                words = []
                for word in getattr(segment, "words", None) or []:
                    word_text = getattr(word, "word", "")
                    word_start, word_end = getattr(word, "start", None), getattr(word, "end", None)
                    if (not isinstance(word_text, str) or type(word_start) not in (int, float)
                            or type(word_end) not in (int, float)):
                        continue
                    item = {"word": word_text, "start": round(float(word_start), 3),
                            "end": round(float(word_end), 3)}
                    probability = getattr(word, "probability", None)
                    if type(probability) in (int, float) and math.isfinite(probability):
                        item["probability"] = float(probability)
                    words.append(item)
                item = {"start": round(float(start), 3), "end": round(float(end), 3),
                        "text_zh": text, "words": words}
                for name in ("avg_logprob", "no_speech_prob"):
                    score = getattr(segment, name, None)
                    if type(score) in (int, float) and math.isfinite(score):
                        item[name] = float(score)
                results.append(item)
        finally:
            close = getattr(generated, "close", None)
            if callable(close):
                close()
        return results
