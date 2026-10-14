"""Source language policy shared by HTTP, persisted jobs and ASR."""
from typing import Literal
import re

SourceLanguage = Literal["auto", "zh", "en", "ja", "ko"]
SOURCE_LANGUAGES = ("auto", "zh", "en", "ja", "ko")


def source_scripts(text):
    """Script signals for rejecting mismatched OCR, not a language detector."""
    return {name for name, pattern in (
        ("hangul", r"[\u1100-\u11ff\u3130-\u318f\uac00-\ud7af]"),
        ("kana", r"[\u3040-\u30ff\u31f0-\u31ff]"),
        ("han", r"[\u3400-\u4dbf\u4e00-\u9fff\U00020000-\U0002fa1f]"),
        ("latin", r"[A-Za-z\u00c0-\u024f]")) if re.search(pattern, text)}


def source_ocr_script_conflict(measured, candidate, source_language="auto"):
    """Incompatible scripts cannot be OCR evidence of spoken source words.

    Mixed utterances must retain their scripts. Adding a new-script name or
    dropping a language needs audio evidence. Matching scripts do not prove
    matching languages or meaning; callers must still apply ownership gates.
    """
    # Preserve the existing narrow spelling equivalences (e.g. 100m/100米).
    # Those are unit spellings, not a switch from Latin speech to Chinese.
    from core.chinese_text import comparable_chinese
    source, target = source_scripts(comparable_chinese(measured)), source_scripts(comparable_chinese(candidate))
    if source and target and source != target:
        return True
    return source_language in {"ko", "en"} and target == {"han"}


def validate_source_language(value):
    if value not in SOURCE_LANGUAGES:
        raise ValueError("Ngôn ngữ nguồn không được hỗ trợ.")
    return value


def whisper_language_options(language):
    """Auto detects on every decoding segment, not only the first 30 seconds."""
    validate_source_language(language)
    # A single default 30-second decode can stay in its initial language and
    # drop the second language entirely. Short decoding windows trigger fresh
    # language detection; previous-language text must not condition the next.
    return {"language": None, "multilingual": True, "chunk_length": 8,
            "condition_on_previous_text": False} if language == "auto" else {"language": language}
