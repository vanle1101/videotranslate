"""Conservative spelling equivalence for measured Chinese ASR/OCR.

This is not fuzzy matching: negation, digits and word order are retained. Windows
provides its script converter; unsupported hosts keep the original characters.
"""
from functools import lru_cache
import os
import re
import unicodedata


@lru_cache(maxsize=4096)
def simplified_text(value):
    text = unicodedata.normalize("NFKC", value)
    if os.name != "nt" or not text:
        return text
    try:
        import ctypes
        from ctypes import wintypes
        convert = ctypes.WinDLL("kernel32", use_last_error=True).LCMapStringEx
        convert.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.LPCWSTR,
                            ctypes.c_int, wintypes.LPWSTR, ctypes.c_int,
                            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ssize_t]
        convert.restype = ctypes.c_int
        # -1 requests a NUL-terminated string, including UTF-16 surrogate pairs.
        count = convert("zh-CN", 0x02000000, text, -1, None, 0, None, None, 0)
        if count <= 0:
            return text
        buffer = ctypes.create_unicode_buffer(count)
        if convert("zh-CN", 0x02000000, text, -1, buffer, count, None, None, 0) <= 0:
            return text
        return buffer.value
    except (OSError, AttributeError, ValueError):
        return text


def comparable_chinese(value):
    if not isinstance(value, str):
        return ""
    text = simplified_text(value).replace("−", "-")
    # SI metres only after a numeral. Do not equate m/s, mm, ms, capital M,
    # or arbitrary Latin letters in names/handles with Chinese words.
    text = re.sub(r"(?<=\d)\s*m(?![A-Za-z0-9/])", "米", text)
    # In the fixed phrase 'school record', 纪录/記錄 are standard written
    # variants. Do not globally replace 纪录 (a record) with 记录 (to record).
    text = text.replace("校纪录", "校记录")
    kept = []
    for index, char in enumerate(text):
        if char.isalnum():
            kept.append(char)
        elif char == "." and index > 0 and index + 1 < len(text) and text[index - 1].isdigit() and text[index + 1].isdigit():
            kept.append(char)
        elif char in "+-" and index + 1 < len(text) and text[index + 1].isdigit():
            kept.append(char)
        elif char == "%" and index > 0 and text[index - 1].isdigit():
            kept.append(char)
        elif char in "/:" and index > 0 and index + 1 < len(text) and text[index - 1].isalnum() and text[index + 1].isalnum():
            kept.append(char)
    return "".join(kept)


def comparable_audio_chinese(value):
    """Allow exact spelling variants of a standalone 0–99 spoken integer.

    Actual independent ASR returned '19。' and '十九' for the same age. Only a
    whole numeric utterance is normalized: names, dates, units, signs, decimals
    and longer phrases keep their original comparison, without fuzzy matching.
    OCR substring ownership deliberately continues using comparable_chinese.
    """
    text = comparable_chinese(value)
    digits = {char: index for index, char in enumerate('零一二三四五六七八九')}
    digits['〇'] = 0
    if len(text) == 1 and text in digits:
        return str(digits[text])
    if re.fullmatch(r'[一二三四五六七八九]?十[一二三四五六七八九]?', text):
        tens, ones = text.split('十')
        return str((digits[tens] if tens else 1) * 10 + (digits[ones] if ones else 0))
    return text
