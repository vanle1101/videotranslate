"""Bounded Gemini JSON requests with diagnostics safe for the application UI."""

from __future__ import annotations

import math
import os
from typing import Optional


class GeminiError(RuntimeError):
    """A sanitized Gemini configuration or request error."""


def _safe_error(exc: Exception) -> str:
    code = str(getattr(exc, "code", ""))
    details = str(getattr(exc, "details", "")).upper()
    if code in {"401", "403"} or (code == "400" and "API_KEY_INVALID" in details):
        return "Gemini từ chối API key hoặc quyền truy cập. Kiểm tra key và giới hạn của key trong Google AI Studio."
    if code == "429":
        return "Gemini đã hết hạn mức hoặc đang giới hạn tốc độ. Kiểm tra quota trong Google AI Studio rồi thử lại."
    if code == "404":
        return "Model Gemini không khả dụng với key này. Chọn model khác trong Cài đặt."
    if code == "503":
        return "Model Gemini đang quá tải hoặc tạm ngừng phục vụ. Đợi rồi thử lại, hoặc chọn model khác."
    if code == "400":
        return "Gemini không chấp nhận yêu cầu. Kiểm tra model và giảm số câu cần dịch rồi thử lại."
    return "Không nhận được bản dịch Gemini. Kiểm tra mạng hoặc thử lại với model khác."


class GeminiClient:
    def __init__(self, model: Optional[str] = None, timeout: float = 60.0):
        from config import settings

        self.model = model if model is not None else settings.GEMINI_MODEL
        if not isinstance(self.model, str) or not self.model.strip():
            raise GeminiError("Chưa chọn model Gemini trong Cài đặt.")
        self.model = self.model.strip()
        try:
            valid_timeout = not isinstance(timeout, bool) and math.isfinite(float(timeout)) and 0 < float(timeout) <= 300
        except (TypeError, ValueError):
            valid_timeout = False
        if not valid_timeout:
            raise GeminiError("Thời gian chờ Gemini phải lớn hơn 0 và tối đa 300 giây.")
        self.timeout = float(timeout)
        key = settings.GEMINI_API_KEY or os.getenv("GEMINI_API_KEY", "")
        self._api_key = key.strip() if isinstance(key, str) else ""

    def translate(self, prompt: str, system: Optional[str] = None) -> str:
        if not self._api_key:
            raise GeminiError("Chưa có API key Gemini. Nhập key trong Cài đặt.")
        if not isinstance(prompt, str) or not prompt.strip():
            raise GeminiError("Nội dung gửi Gemini đang trống.")
        client = None
        try:
            from google import genai

            client = genai.Client(
                api_key=self._api_key,
                http_options={"timeout": max(1, round(self.timeout * 1000)), "retry_options": {"attempts": 1}},
            )
            config = {"temperature": 0.1, "response_mime_type": "application/json", "max_output_tokens": 8192}
            if system:
                config["system_instruction"] = system
            response = client.models.generate_content(model=self.model, contents=prompt, config=config)
            candidates = response.candidates
            if not candidates:
                raise GeminiError("Gemini chưa trả nội dung bản dịch. Hãy thử lại.")
            reason = candidates[0].finish_reason
            reason = getattr(reason, "value", reason)
            if reason != "STOP":
                raise GeminiError("Gemini chưa hoàn thành bản dịch hoặc đã chặn nội dung. Hãy giảm số câu cần dịch hoặc thử lại.")
            text = response.text
            if not isinstance(text, str) or not text.strip():
                raise GeminiError("Gemini trả về bản dịch trống. Hãy thử lại.")
            return text.strip()
        except GeminiError:
            raise
        except Exception as exc:
            raise GeminiError(_safe_error(exc)) from None
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:
                    # Cleanup failures must not replace sanitized request errors.
                    pass
