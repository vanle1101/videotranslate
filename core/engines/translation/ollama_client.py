"""Local-only Ollama chat adapter for structured translation requests."""
from __future__ import annotations

import json
import socket
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


class OllamaClientError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False, code: str = "provider_failed",
                 metrics: dict | None = None):
        super().__init__(message)
        self.retryable = bool(retryable)
        self.code = code
        # Numeric server counters only; never retain the prompt or generated
        # text in an exception that may be written to diagnostics.
        self.metrics = {key: value for key, value in (metrics or {}).items()
                        if key in {"load_duration", "prompt_eval_count", "prompt_eval_duration",
                                   "eval_count", "eval_duration", "total_duration"}
                        and type(value) is int and value >= 0}


class OllamaClient:
    """Send one request to the local Ollama server; retry ownership stays upstream."""

    def __init__(self, model: str, base_url: str, timeout: float = 120.0):
        parsed = urlsplit(str(base_url).strip())
        host = (parsed.hostname or "").lower()
        if (parsed.scheme != "http" or host not in {"127.0.0.1", "localhost", "::1"}
                or parsed.username or parsed.password or parsed.path not in ("", "/")
                or parsed.query or parsed.fragment or parsed.port not in (None, 11434)):
            raise OllamaClientError("Ollama chỉ được cấu hình qua loopback trên máy này.", code="invalid_configuration")
        try:
            valid_timeout = 0 < float(timeout) <= 600
        except (TypeError, ValueError):
            valid_timeout = False
        if not valid_timeout:
            raise OllamaClientError("Thời gian chờ Ollama phải từ 1 đến 600 giây.", code="invalid_configuration")
        if not isinstance(model, str) or not model.strip() or len(model) > 160:
            raise OllamaClientError("Tên model Ollama không hợp lệ.", code="invalid_configuration")
        self.model = model.strip()
        self.base_url = str(base_url).strip().rstrip("/")
        self.timeout = float(timeout)
        # Ollama returns tokenizer and duration counters with every non-streaming
        # response. Keep only those numeric counters so callers can profile
        # throughput without exposing prompts or model output in diagnostics.
        self.last_metrics: dict[str, int] = {}

    @property
    def has_credentials(self) -> bool:
        # Ollama is a local service and does not require an API key.
        return True

    def verify_model(self) -> bool:
        request = Request(self.base_url + "/api/tags", headers={"Accept": "application/json"}, method="GET")
        try:
            with urlopen(request, timeout=min(self.timeout, 5.0)) as response:
                data = json.loads(response.read(1_000_001).decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, socket.timeout, UnicodeDecodeError,
                json.JSONDecodeError, OSError):
            return False
        models = data.get("models") if isinstance(data, dict) else None
        return isinstance(models, list) and any(
            isinstance(item, dict) and item.get("name") == self.model for item in models)

    def translate(self, prompt: str, system: str | None = None, max_tokens: int = 4096,
                  response_format: str | dict = "json", temperature: float = 0.2, **_options) -> str:
        self.last_metrics = {}
        if not isinstance(prompt, str) or not prompt.strip():
            raise OllamaClientError("Nội dung gửi Ollama đang trống.", code="invalid_request")
        if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or not 1 <= max_tokens <= 32768:
            raise OllamaClientError("Giới hạn trả lời Ollama không hợp lệ.", code="invalid_request")
        if isinstance(response_format, str):
            if response_format not in {"json", "text"}:
                raise OllamaClientError("Định dạng phản hồi Ollama không hợp lệ.", code="invalid_request")
            format_value = response_format
        elif isinstance(response_format, dict):
            try:
                encoded_schema = json.dumps(response_format, ensure_ascii=False)
            except (TypeError, ValueError):
                encoded_schema = ""
            if not encoded_schema or len(encoded_schema) > 64_000:
                raise OllamaClientError("Lược đồ phản hồi Ollama không hợp lệ.", code="invalid_request")
            format_value = response_format
        else:
            raise OllamaClientError("Định dạng phản hồi Ollama không hợp lệ.", code="invalid_request")
        if (isinstance(temperature, bool) or not isinstance(temperature, (int, float))
                or not 0 <= temperature <= 2):
            raise OllamaClientError("Nhiệt độ Ollama không hợp lệ.", code="invalid_request")
        messages = []
        if isinstance(system, str) and system.strip():
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        body = {"model": self.model, "messages": messages, "stream": False,
                # Keep CPU use bounded when a model spills layers out of VRAM.
                "options": {"num_predict": max_tokens, "temperature": float(temperature),
                            "num_thread": 2, "num_ctx": 2048}}
        if format_value != "text":
            body["format"] = format_value
        # Qwen3's default thinking mode spends tokens explaining translations.
        # Disable it for concise subtitle output while keeping older models' API
        # payloads unchanged.
        if self.model.split(":", 1)[0].startswith("qwen3"):
            body["think"] = False
        body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = Request(self.base_url + "/api/chat", data=body,
                          headers={"Content-Type": "application/json", "Accept": "application/json"},
                          method="POST")
        try:
            with urlopen(request, timeout=self.timeout) as response:
                if response.status != 200:
                    raise OllamaClientError("Ollama chưa trả được bản dịch.", retryable=response.status >= 500,
                                            code="provider_server" if response.status >= 500 else "provider_failed")
                raw = response.read(8_000_001)
        except HTTPError as error:
            status = int(error.code)
            raise OllamaClientError(
                "Ollama đang quá tải; sẽ thử lại." if status == 429 or status >= 500 else
                "Ollama từ chối yêu cầu; kiểm tra model local đã cài.",
                retryable=status == 429 or status >= 500,
                code="provider_rate_limited" if status == 429 else "provider_server" if status >= 500 else "provider_failed",
            ) from None
        except (TimeoutError, socket.timeout):
            raise OllamaClientError("Ollama phản hồi quá lâu; sẽ thử lại.", retryable=True,
                                    code="provider_timeout") from None
        except URLError as error:
            reason = getattr(error, "reason", None)
            if isinstance(reason, (TimeoutError, socket.timeout)):
                raise OllamaClientError("Ollama phản hồi quá lâu; sẽ thử lại.", retryable=True,
                                        code="provider_timeout") from None
            raise OllamaClientError("Không kết nối được Ollama local; sẽ thử lại.", retryable=True,
                                    code="provider_transport") from None
        if len(raw) > 8_000_000:
            raise OllamaClientError("Phản hồi Ollama vượt giới hạn kích thước.", code="provider_invalid_response")
        try:
            data = json.loads(raw.decode("utf-8"))
            message = data.get("message") if isinstance(data, dict) else None
            content = message.get("content") if isinstance(message, dict) else None
            done_reason = data.get("done_reason") if isinstance(data, dict) else None
            metrics = {}
            for key in ("load_duration", "prompt_eval_count", "prompt_eval_duration",
                        "eval_count", "eval_duration", "total_duration"):
                value = data.get(key) if isinstance(data, dict) else None
                if type(value) is int and value >= 0:
                    metrics[key] = value
            self.last_metrics = metrics
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            content = None
            done_reason = None
            self.last_metrics = {}
        if done_reason == "length":
            raise OllamaClientError("Ollama đã cắt phản hồi tại giới hạn token; cần chia nhỏ yêu cầu.",
                                    code="provider_incomplete", metrics=self.last_metrics)
        if not isinstance(content, str) or not content.strip():
            raise OllamaClientError("Ollama trả về phản hồi rỗng hoặc sai định dạng.",
                                    code="provider_invalid_response")
        return content.strip()
