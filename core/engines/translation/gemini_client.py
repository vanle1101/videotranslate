"""Bounded Gemini JSON requests with diagnostics safe for the application UI."""

from __future__ import annotations

import math
import os
import logging
from typing import Optional


class GeminiError(RuntimeError):
    """A sanitized Gemini configuration or request error."""


class GeminiIncompleteError(GeminiError):
    """The selected model returned a bounded, incomplete generation."""

    def __init__(self, reason: str):
        self.reason = reason
        descriptions = {
            "MAX_TOKENS": "Gemini đạt giới hạn độ dài phản hồi (MAX_TOKENS). Đoạn video cần được rút ngắn.",
            "SAFETY": "Gemini chặn phân tích theo bộ lọc nội dung (SAFETY).",
            "RECITATION": "Gemini dừng phân tích theo bộ lọc trích dẫn (RECITATION).",
        }
        super().__init__(descriptions.get(reason, "Gemini dừng trước khi hoàn thành phân tích khung hình."))


def _safe_error(exc: Exception) -> str:
    if "timeout" in type(exc).__name__.lower():
        return "Gemini hết thời gian chờ phản hồi. Hãy thử lại hoặc chọn model nhanh hơn."
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
            code = getattr(exc, "code", None)
            safe_code = code if isinstance(code, int) and 100 <= code <= 599 else None
            logging.getLogger("errors").warning("Gemini text request failed (%s, HTTP %s)",
                                                type(exc).__name__, safe_code or "unknown")
            raise GeminiError(_safe_error(exc)) from None
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:
                    # Cleanup failures must not replace sanitized request errors.
                    pass

    def analyze_media(self, media: bytes, prompt: str, mime_type: str = "video/mp4",
                      additional_media: Optional[list[tuple[bytes, str]]] = None) -> str:
        """Send one bounded inline media part to Gemini (no Files API uploads)."""
        if not self._api_key:
            raise GeminiError("Chưa có API key Gemini. Nhập key trong Cài đặt.")
        if not isinstance(media, (bytes, bytearray)) or not media:
            raise GeminiError("Nội dung video gửi Gemini đang trống.")
        additional_media = additional_media or []
        if len(media) > 14_000_000 or mime_type not in {"video/mp4", "audio/wav", "audio/mpeg"}:
            raise GeminiError("Định dạng hoặc dung lượng media gửi Gemini không hợp lệ.")
        if any(not isinstance(blob, (bytes, bytearray)) or not blob or len(blob) > 2_000_000
               or kind not in {"image/jpeg", "image/png"} for blob, kind in additional_media):
            raise GeminiError("Ảnh tham chiếu gửi Gemini không hợp lệ hoặc vượt dung lượng.")
        if len(additional_media) > 4 or len(media) + sum(len(blob) for blob, _ in additional_media) > 14_000_000:
            raise GeminiError("Tổng dung lượng media gửi Gemini vượt giới hạn.")
        if not isinstance(prompt, str) or not prompt.strip():
            raise GeminiError("Yêu cầu phân tích video đang trống.")
        client = None
        try:
            from google import genai
            from google.genai import types
            client = genai.Client(
                api_key=self._api_key,
                http_options={"timeout": max(1, round(self.timeout * 1000)), "retry_options": {"attempts": 1}},
            )
            parts = [types.Part.from_bytes(data=bytes(media), mime_type=mime_type)]
            if mime_type == "video/mp4":
                parts[0].video_metadata = types.VideoMetadata(fps=3)
            parts.extend(types.Part.from_bytes(data=bytes(blob), mime_type=kind)
                         for blob, kind in additional_media)
            config = {"temperature": 0.1, "response_mime_type": "application/json", "max_output_tokens": 16384}
            if self.model.startswith("gemini-2.5"):
                config["thinking_config"] = {"thinking_budget": 1024}
            response = client.models.generate_content(
                model=self.model,
                contents=[prompt, *parts],
                config=config,
            )
            candidates = getattr(response, "candidates", None) or []
            if not candidates:
                raise GeminiError("Gemini chưa trả phân tích khung hình.")
            reason = getattr(candidates[0].finish_reason, "value", candidates[0].finish_reason)
            if reason != "STOP":
                allowed = {"MAX_TOKENS", "SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "OTHER"}
                raise GeminiIncompleteError(reason if reason in allowed else "OTHER")
            text = getattr(response, "text", None)
            if not isinstance(text, str) or not text.strip():
                raise GeminiError("Gemini trả về phân tích khung hình trống.")
            return text.strip()
        except GeminiError:
            raise
        except Exception as exc:
            code = getattr(exc, "code", None)
            safe_code = code if isinstance(code, int) and 100 <= code <= 599 else None
            logging.getLogger("errors").warning("Gemini media request failed (%s, HTTP %s)",
                                                type(exc).__name__, safe_code or "unknown")
            raise GeminiError(_safe_error(exc)) from None
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass
