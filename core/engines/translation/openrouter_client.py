"""Free-only OpenRouter chat client using the user's saved OpenCode login."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
from typing import Optional

import requests


CHAT_COMPLETIONS_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_FREE_MODEL = "inclusionai/ling-3.0-flash-sante:free"


class OpenRouterClientError(RuntimeError):
    """A sanitized error safe for the application UI."""


class OpenRouterConfigurationError(OpenRouterClientError):
    pass


class OpenRouterRequestError(OpenRouterClientError):
    pass


def resolve_openrouter_key() -> Optional[str]:
    """Read only OpenRouter credentials; never expose or rewrite saved keys."""
    from config import settings

    for value in (getattr(settings, "OPENROUTER_API_KEY", ""), os.getenv("OPENROUTER_API_KEY", "")):
        if isinstance(value, str) and value.strip():
            return value.strip()
    data_home = os.getenv("XDG_DATA_HOME")
    root = Path(data_home).expanduser() if data_home else Path.home() / ".local" / "share"
    try:
        with (root / "opencode" / "auth.json").open(encoding="utf-8") as stream:
            auth = json.load(stream)
        if isinstance(auth, dict):
            for provider in ("OpenRouter-Free", "openrouter"):
                entry = auth.get(provider)
                if isinstance(entry, dict) and entry.get("type") == "api":
                    key = entry.get("key")
                    if isinstance(key, str) and key.strip():
                        return key.strip()
    except (OSError, ValueError, TypeError):
        pass
    return None


def _safe_http_error(status: int) -> str:
    if status in (401, 403):
        return "OpenRouter từ chối quyền truy cập. Hãy kiểm tra kết nối OpenRouter-Free trong OpenCode."
    if status == 402:
        return "OpenRouter từ chối hạn mức tài khoản. Ứng dụng không chuyển sang model trả phí."
    if status == 429:
        return "OpenRouter đang giới hạn lượt miễn phí. Hãy đợi rồi thử lại."
    if status in (400, 404):
        return "Model OpenRouter miễn phí chưa khả dụng hoặc không chấp nhận yêu cầu. Hãy đổi model :free."
    if status >= 500:
        return "Máy chủ OpenRouter chưa sẵn sàng. Hãy thử lại sau."
    return "OpenRouter chưa trả được bản dịch. Hãy kiểm tra kết nối và thử lại."


class OpenRouterFreeClient:
    def __init__(self, model: str = DEFAULT_FREE_MODEL, timeout: float = 60.0):
        if not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+:free", model.strip()):
            raise OpenRouterConfigurationError("Chỉ chấp nhận model OpenRouter có hậu tố :free.")
        try:
            valid_timeout = math.isfinite(float(timeout)) and 0 < float(timeout) <= 300
        except (TypeError, ValueError):
            valid_timeout = False
        if not valid_timeout:
            raise OpenRouterConfigurationError("Thời gian chờ OpenRouter phải từ 1 đến 300 giây.")
        self.model = model.strip()
        self.timeout = float(timeout)
        self._api_key = resolve_openrouter_key()

    @property
    def has_credentials(self) -> bool:
        return bool(self._api_key)

    def translate(self, prompt: str, system: Optional[str] = None, max_tokens: int = 8192) -> str:
        if not self._api_key:
            raise OpenRouterConfigurationError("Chưa có API key OpenRouter. Hãy kết nối OpenRouter-Free trong OpenCode.")
        if not isinstance(prompt, str) or not prompt.strip():
            raise OpenRouterConfigurationError("Nội dung gửi OpenRouter đang trống.")
        if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens < 1024:
            raise OpenRouterConfigurationError("Giới hạn trả lời OpenRouter phải ít nhất 1024 token.")
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        try:
            response = requests.post(
                CHAT_COMPLETIONS_URL,
                headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
                json={"model": self.model, "messages": messages, "max_tokens": max_tokens,
                      "reasoning": {"enabled": False}},
                timeout=self.timeout,
                allow_redirects=False,
            )
        except requests.Timeout:
            raise OpenRouterRequestError("OpenRouter phản hồi quá chậm. Hãy thử lại hoặc tăng thời gian chờ.") from None
        except requests.RequestException:
            raise OpenRouterRequestError("Không kết nối được OpenRouter. Hãy kiểm tra Internet và thử lại.") from None
        try:
            if response.status_code != 200:
                raise OpenRouterRequestError(_safe_http_error(response.status_code))
            # requests can otherwise guess an incompatible charset for Vietnamese.
            response.encoding = "utf-8"
            try:
                data = response.json()
            except (ValueError, TypeError):
                raise OpenRouterRequestError("OpenRouter trả về dữ liệu không hợp lệ. Hãy thử lại.") from None
            choices = data.get("choices") if isinstance(data, dict) else None
            if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
                raise OpenRouterRequestError("OpenRouter chưa trả nội dung bản dịch. Hãy thử lại.")
            choice = choices[0]
            if choice.get("finish_reason") in ("length", "content_filter", "error"):
                raise OpenRouterRequestError("OpenRouter chưa hoàn thành bản dịch. Hãy giảm số câu hoặc thử lại.")
            message = choice.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            if not isinstance(content, str) or not content.strip():
                raise OpenRouterRequestError("OpenRouter trả về bản dịch trống. Hãy thử lại.")
            return content.strip()
        finally:
            response.close()
