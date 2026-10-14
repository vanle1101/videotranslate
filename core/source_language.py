"""Source language policy shared by HTTP, persisted jobs and ASR."""
from typing import Literal

SourceLanguage = Literal["auto", "zh", "en", "ja", "ko"]
SOURCE_LANGUAGES = ("auto", "zh", "en", "ja", "ko")


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
