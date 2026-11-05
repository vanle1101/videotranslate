"""Keep spoken language distinct from translated, burned-in subtitles."""
from __future__ import annotations

import math
import re

from core.engines.asr.native_process import ASRProcessError

# Whisper's supported language codes. Keep this local so validation never loads
# a recognizer/model merely to route a saved transcript.
SUPPORTED_SOURCE_LANGUAGES = frozenset("en zh de es ru ko fr ja pt tr pl ca nl ar sv it id hi fi vi he uk el ms cs ro da hu ta no th ur hr bg lt la mi ml cy sk te fa lv bn sr az sl kn et mk br eu is hy ne mn bs kk sq sw gl mr pa si km sn yo so af oc ka be tg sd gu am yi lo uz fo ht ps tk nn mt sa lb my bo tl mg as tt haw ln ha ba jw su yue".split())


def require_supported_source(metadata):
    metadata = language_metadata(metadata)
    if metadata["language"] not in SUPPORTED_SOURCE_LANGUAGES and metadata.get("speech_segments") != 0:
        raise ASRProcessError("Ngôn ngữ lời nói chưa được bộ nhận dạng hỗ trợ: " + metadata["language"])
    return metadata


def source_row_language(text, metadata):
    """Preserve strong per-row script evidence in a code-switching interval.

    Han text alone cannot distinguish Chinese from Japanese; use the measured
    interval language in that case. This is routing, never ASR certification.
    """
    metadata = require_supported_source(metadata)
    language = metadata["language"]
    if re.search(r"[\uac00-\ud7af\u1100-\u11ff\u3130-\u318f]", text):
        language = "ko"
    elif re.search(r"[\u3040-\u30ff]", text):
        language = "ja"
    elif re.search(r"[\u3400-\u9fff]", text) and language not in {"ja", "yue"}:
        language = "zh"
    return language


def require_empty_source_is_speech_free(audio_path):
    """An empty recognizer result is not evidence of silence.

    Use the installed local Silero VAD only for empty native results. Music can
    remain without dialogue; detected speech requires another source attempt,
    never a completed empty checkpoint.
    """
    import wave
    import numpy as np
    from faster_whisper.vad import get_speech_timestamps, VadOptions
    from core.runtime_context import current_execution_context
    from core.engines.asr.native_process import check_cancelled
    check = current_execution_context().cancel_check
    check_cancelled(check)
    try:
        with wave.open(str(audio_path), "rb") as wav:
            if wav.getframerate() != 16000 or wav.getnchannels() != 1 or wav.getsampwidth() != 2:
                raise ASRProcessError("Không kiểm chứng được đoạn nguồn trống vì định dạng audio không hợp lệ.")
            audio = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2").astype(np.float32) / 32768.
        if not len(audio):
            raise ASRProcessError("Đoạn audio nguồn trống; chưa thể xác nhận im lặng.")
        speech = get_speech_timestamps(audio, VadOptions(min_speech_duration_ms=100, min_silence_duration_ms=300))
        check_cancelled(check)
        if speech:
            error = ASRProcessError("Có hoạt động lời nói nhưng ASR chưa trả lời nguồn; cần nhận diện lại đoạn này.")
            error.code = "source_speech_unrecognized"
            raise error
    except (OSError, EOFError, wave.Error) as error:
        raise ASRProcessError("Không đọc được audio để kiểm chứng đoạn nguồn trống.") from error


def language_metadata(value):
    if not isinstance(value, dict):
        raise ASRProcessError("Bộ nhận giọng trả thông tin ngôn ngữ không hợp lệ.")
    language, probability = value.get("language"), value.get("probability")
    if (not isinstance(language, str) or re.fullmatch(r"[a-z]{2,3}", language) is None
            or type(probability) not in (int, float) or not math.isfinite(probability)
            or not 0 <= probability <= 1):
        raise ASRProcessError("Bộ nhận giọng trả thông tin ngôn ngữ không hợp lệ.")
    result = {"language": language, "probability": float(probability)}
    if "speech_segments" in value:
        count = value["speech_segments"]
        if type(count) is not int or count < 0:
            raise ASRProcessError("Bộ nhận giọng trả số câu nguồn không hợp lệ.")
        result["speech_segments"] = count
    return result


class SourceLanguageMismatchError(ValueError):
    code = "source_language_mismatch"
    retryable = False

    def __init__(self, metadata):
        self.metadata = language_metadata(metadata)
        names = {"ko": "tiếng Hàn", "ja": "tiếng Nhật", "en": "tiếng Anh"}
        language = self.metadata["language"]
        super().__init__(
            f"Âm thanh được nhận diện là {names.get(language, language)} "
            f"({self.metadata['probability']:.0%}), không phải tiếng Trung. "
            "Phụ đề Trung trên hình có thể là bản dịch; cần nhận diện lại lời gốc và đối chiếu "
            "phụ đề theo câu trước khi lồng tiếng. Không tự dùng mốc từ tiếng Trung cho âm thanh này.")


def require_chinese_source(metadata):
    metadata = language_metadata(metadata)
    if metadata["language"] != "zh" and metadata.get("speech_segments") != 0:
        raise SourceLanguageMismatchError(metadata)
    return metadata
