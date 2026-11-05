"""Actionable export errors with bounded, credential-redacted diagnostics."""
import errno
import logging
import re
import traceback

from config import settings


def redacted_detail(value):
    text = str(value)
    for name in ("OPENCODE_API_KEY", "OPENROUTER_API_KEY", "GEMINI_API_KEY",
                 "DEEPSEEK_API_KEY", "OPENAI_API_KEY"):
        secret = getattr(settings, name, "")
        if isinstance(secret, str) and secret:
            text = text.replace(secret, "[redacted]")
    text = re.sub(r"(?i)(authorization\s*[:=]\s*(?:bearer\s+)?)[^\s,;]+", r"\1[redacted]", text)
    text = re.sub(r"(?i)((?:api[_-]?key|access_token|refresh_token|password|token|cookie)\s*[:=]\s*)[^\s,;]+", r"\1[redacted]", text)
    text = re.sub(r"\b(?:sk-[A-Za-z0-9_-]{16,}|AIza[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,})\b", "[redacted]", text)
    text = re.sub(r"(?i)\b[^\s]*secret[-_][^\s]+", "[redacted]", text)
    text = re.sub(r"(https?://[^\s?]+)\?[^\s]+", r"\1?[redacted]", text)
    return text[-6000:]


def redacted_traceback(error, *, include_messages=True):
    """Keep every frame and chained cause without logging credentials or locals."""
    if not include_messages:
        # Provider bodies can contain arbitrary prompt text that cannot be
        # reliably recognized as a credential. Preserve every frame, exception
        # type and cause boundary without echoing those strings or source lines.
        seen = set()
        def frames(current):
            if id(current) in seen:
                return ""
            seen.add(id(current))
            before = ""
            if current.__cause__ is not None:
                before = frames(current.__cause__) + "\nThe above exception was the direct cause of the following exception:\n\n"
            elif current.__context__ is not None and not current.__suppress_context__:
                before = frames(current.__context__) + "\nDuring handling of the above exception, another exception occurred:\n\n"
            stack = "Traceback (most recent call last):\n" if current.__traceback__ else ""
            for frame, number in traceback.walk_tb(current.__traceback__):
                stack += f'  File {frame.f_code.co_filename!r}, line {number}, in {frame.f_code.co_name}\n'
            kind = type(current)
            return before + stack + f"{kind.__module__}.{kind.__qualname__}: [provider detail withheld]\n"
        return "".join(redacted_detail(line) for line in frames(error).splitlines(keepends=True))
    return "".join(redacted_detail(line) for block in traceback.format_exception(
        type(error), error, error.__traceback__) for line in block.splitlines(keepends=True))


def export_failure(error, export_id, stage):
    lowered = str(error).casefold()
    if isinstance(error, FileNotFoundError):
        category, action = "missing_file", "Thiếu video nguồn, âm thanh hoặc FFmpeg. Kiểm tra file trong Lịch sử và Diagnostics."
    elif getattr(error, "errno", None) == errno.ENOSPC or any(s in lowered for s in ("no space left", "disk full", "not enough space")):
        category, action = "disk_full", "Ổ đĩa không đủ chỗ trống. Giải phóng dung lượng rồi xuất lại."
    elif isinstance(error, PermissionError) or any(s in lowered for s in ("permission denied", "being used by another process")):
        category, action = "file_access", "Không ghi được video. Đóng trình phát đang giữ file và kiểm tra quyền ghi thư mục kết quả."
    elif "quá nhiều vùng" in lowered:
        category, action = "blur_regions", "Video có quá nhiều vùng làm mờ. Tắt làm mờ sub gốc rồi thử xuất lại."
    elif "ffmpeg" in lowered:
        category, action = "media_process", "FFmpeg không xử lý được hình hoặc âm thanh. Xem log Lỗi với mã tác vụ này trước khi thử lại."
    else:
        category, action = "export_failed", "Chưa xuất được video. Xem log Lỗi rồi bấm Xuất video để thử lại."
    logging.getLogger("errors").error(
        "EXPORT_FAILED run_id=%s category=%s stage=%s error_type=%s detail=%s traceback=%s",
        export_id, category, redacted_detail(stage), type(error).__name__,
        redacted_detail(error), redacted_traceback(error))
    return f"{action} Bản dịch và giọng đọc vẫn được giữ. Mã: {export_id}."
