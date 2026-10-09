"""Strict provider JSON contracts and bounded, redacted local diagnostics.

Schema checks describe syntax and types; caller validators still own source,
meaning, timing and address evidence. A schema-valid reply is not proof of truth.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import logging
import math
import os
from pathlib import Path
import re
import tempfile
import threading
import uuid


class StructuredResponseError(RuntimeError):
    def __init__(self, message, *, code="invalid_content", path="$", line=None, column=None):
        super().__init__(message)
        self.code = code
        self.path = path
        self.line = line
        self.column = column


def parse_object(raw, *, error_type=StructuredResponseError):
    """Never accept duplicate keys or non-JSON NaN/Infinity values."""
    if isinstance(raw, dict):
        return deepcopy(raw)
    if not isinstance(raw, str):
        raise error_type("Phản hồi AI phải là văn bản JSON hoặc đối tượng.", code="invalid_type")
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    if len(text) > 2_000_000:
        raise error_type("Phản hồi AI vượt giới hạn dữ liệu.", code="invalid_content")

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise error_type("JSON chứa trường trùng lặp.", code="invalid_content")
            result[key] = value
        return result

    def invalid_constant(value):
        raise error_type("JSON chứa giá trị số không hữu hạn.", code="invalid_type")

    try:
        data = json.loads(text, object_pairs_hook=unique_object, parse_constant=invalid_constant)
    except json.JSONDecodeError as error:
        raise error_type(
            f"AI trả về JSON không hợp lệ tại dòng {error.lineno}, cột {error.colno}.",
            code="invalid_json", line=error.lineno, column=error.colno) from error
    if not isinstance(data, dict):
        raise error_type("Bộ dịch phải trả về một đối tượng JSON.", code="invalid_type")
    return data


def validate_schema(value, schema, *, path="$", error_type=StructuredResponseError):
    """Validate the JSON Schema subset used by these explicit provider contracts."""
    kind = schema.get("type")
    valid = {
        "object": lambda: isinstance(value, dict),
        "array": lambda: isinstance(value, list),
        "string": lambda: isinstance(value, str),
        "boolean": lambda: type(value) is bool,
        "integer": lambda: type(value) is int,
        "number": lambda: type(value) in (int, float) and math.isfinite(value),
    }
    if kind in valid and not valid[kind]():
        raise error_type(f"Phản hồi AI sai kiểu dữ liệu tại {path}.", code="invalid_type", path=path)
    if "enum" in schema and value not in schema["enum"]:
        raise error_type(f"Phản hồi AI có giá trị không hợp lệ tại {path}.", code="invalid_content", path=path)
    if kind == "object":
        for key in schema.get("required", []):
            if key not in value:
                raise error_type(f"Phản hồi AI thiếu trường {path}.{key}.",
                                 code="missing_field", path=f"{path}.{key}")
        for key, child in schema.get("properties", {}).items():
            if key in value:
                validate_schema(value[key], child, path=f"{path}.{key}", error_type=error_type)
    elif kind == "array":
        if len(value) > schema.get("maxItems", 10000):
            raise error_type(f"Phản hồi AI có quá nhiều hàng tại {path}.", code="invalid_content", path=path)
        for index, child in enumerate(value):
            validate_schema(child, schema.get("items", {}), path=f"{path}[{index}]", error_type=error_type)
    elif kind == "string" and (len(value) > schema.get("maxLength", 100000) or "\x00" in value):
        raise error_type(f"Phản hồi AI có chuỗi không hợp lệ tại {path}.", code="invalid_content", path=path)


def _object(properties, required):
    return {"type": "object", "properties": properties, "required": required}


TEXT = {"type": "string", "maxLength": 2000}
BOOL = {"type": "boolean"}
NUMBER = {"type": "number"}
ID = {"type": "integer"}
SPEECH_PROPERTIES = {"id": ID, "start": NUMBER, "end": NUMBER,
    **{key: TEXT for key in ("text_zh", "literal_vi", "natural_vi", "final_vi", "review_reason")},
    "needs_review": BOOL}
TRANSLATION_SCHEMA = _object({
    "segments": {"type": "array", "items": _object(SPEECH_PROPERTIES, list(SPEECH_PROPERTIES))},
    "screen_texts": {"type": "array", "items": _object({"id": TEXT, "text_vi": TEXT,
        "kind": {"type": "string", "enum": ["title", "subtitle", "ignore"]},
        "needs_review": BOOL, "review_reason": TEXT}, ["id", "text_vi", "kind", "needs_review", "review_reason"])},
    "summary": {"type": "string", "maxLength": 3000}}, ["segments", "screen_texts", "summary"])
SOURCE_SCHEMA = _object({"segments": {"type": "array", "items": _object({
    "id": ID, "text_zh": TEXT, "evidence_ids": {"type": "array", "items": TEXT},
    "needs_review": BOOL, "review_reason": TEXT}, ["id", "text_zh", "evidence_ids", "needs_review", "review_reason"])}}, ["segments"])
EVIDENCE = {"type": "array", "items": _object({"id": ID, "quote": TEXT}, ["id", "quote"])}
ADDRESS_SCHEMA = _object({"address_context": {"type": "array", "items": _object({
    "id": ID, "self_address": TEXT, "listener_address": TEXT, "uncertain": BOOL,
    "self_uncertain": BOOL, "listener_uncertain": BOOL, "reason": TEXT, "evidence": EVIDENCE,
    "turn_check": _object({"ambiguous_roles": {"type": "array", "items": {
        "type": "string", "enum": ["self", "listener"]}}, "reason": TEXT, "evidence": EVIDENCE},
        ["ambiguous_roles", "reason", "evidence"])},
    ["id", "self_address", "listener_address", "uncertain", "reason", "evidence", "turn_check"])}}, ["address_context"])


_diagnostic_lock = threading.Lock()
_SECRET_KEY = r"(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret|authorization)"


def _redact(value, secrets=()):
    from config import settings
    text = str(value)
    # Remove known configured credentials even when they appear inside a
    # malformed JSON string. Never persist credentials or authentication headers.
    candidates = dict(os.environ)
    candidates.update(vars(settings))
    for secret in secrets:
        if isinstance(secret, str) and secret:
            text = text.replace(secret, "[REDACTED]")
    for name, secret in candidates.items():
        if re.search(r"KEY|TOKEN|PASSWORD|SECRET|AUTH", name, re.I) and isinstance(secret, str) and len(secret) >= 6:
            text = text.replace(secret, "[REDACTED]")
    text = re.sub(r"(?i)(Bearer\s+)[^\s\"'<>]+", r"\1[REDACTED]", text)
    text = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}\b", "[REDACTED]", text)
    text = re.sub(rf"(?i)([\"']?{_SECRET_KEY}[\"']?\s*[:=]\s*[\"']?)[^\"'\s,&}}]+", r"\1[REDACTED]", text)
    text = re.sub(rf"(?i)([?&]{_SECRET_KEY}=)[^&#\s\"'<>]+", r"\1[REDACTED]", text)
    return text


def schema_attempts(stage_repairs):
    """A configurable global repair cap can only lower each bounded stage."""
    from config import settings
    cap = getattr(settings, "OPENCODE_SCHEMA_REPAIR_ATTEMPTS", 2)
    if type(cap) is not int or not 0 <= cap <= 4:
        raise StructuredResponseError("Giới hạn sửa JSON không hợp lệ.", code="invalid_configuration")
    return 1 + min(cap, stage_repairs)


def retain_diagnostic(raw, *, schema_id, task_kind, attempt, error=None, directory=None, secrets=()):
    """Atomic local retention capped at 64 records / 8 MiB / 128 KiB each."""
    from config import settings
    from core.runtime_context import current_execution_context
    root = Path(directory) if directory is not None else Path(settings.WORKSPACE_DIR) / "cache" / "ai_diagnostics"
    temporary = None
    try:
        context = current_execution_context()
        raw_text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False, allow_nan=False)
        redacted = _redact(raw_text, secrets).encode("utf-8")
        record = {"version": 1, "time": datetime.now(timezone.utc).isoformat(),
            "run_id": _redact(context.run_id), "schema_id": _redact(schema_id),
            "task_kind": _redact(task_kind), "attempt": attempt,
            "status": "rejected" if error else "validated",
            "code": getattr(error, "code", "invalid_content") if error else None,
            "path": getattr(error, "path", None), "line": getattr(error, "line", None),
            "column": getattr(error, "column", None),
            "validation": _redact(str(error), secrets) if error else None,
            "raw_response": redacted[:128_000].decode("utf-8", errors="ignore"),
            "truncated": len(redacted) > 128_000}
        with _diagnostic_lock:
            root.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=root,
                    prefix="response_", suffix=".tmp", delete=False) as handle:
                temporary = Path(handle.name)
                json.dump(record, handle, ensure_ascii=False, allow_nan=False)
                handle.flush()
                os.fsync(handle.fileno())
            destination = root / f"response_{uuid.uuid4().hex}.json"
            os.replace(temporary, destination)
            records = sorted((item for item in root.glob("response_*.json")
                if re.fullmatch(r"response_[0-9a-f]{32}\.json", item.name) and not item.is_symlink()),
                key=lambda item: item.stat().st_mtime_ns, reverse=True)
            total = 0
            for index, item in enumerate(records):
                total += item.stat().st_size
                if index >= 64 or total > 8_000_000:
                    item.unlink(missing_ok=True)
        return destination
    except (OSError, ValueError, TypeError):
        logging.getLogger("ai").warning("AI_DIAGNOSTIC_WRITE_FAILED")
        return None
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                logging.getLogger("ai").warning("AI_DIAGNOSTIC_CLEANUP_FAILED")


def request_structured(client, prompt, validator, *, schema_id, task_kind,
                       max_tokens, attempt=1, diagnostics=None):
    """Only the official adapter opts into validated-response caching.

    Plugin/test adapters retain their existing translate signature and still
    undergo the same validation. Invalid responses are retained then raised;
    caller-owned, bounded repair requests must generate a fresh full response.
    """
    from core.engines.translation.opencode_client import OpenCodeZenClient, OpenCodeClientError
    if diagnostics is None:
        # Injected test/plugin adapters do not write production diagnostic data.
        diagnostics = type(client) is OpenCodeZenClient
    secrets = (getattr(client, "_api_key", None),) if type(client) is OpenCodeZenClient else ()
    checked_raw = checked_result = None
    checked = False

    def validate(raw):
        nonlocal checked_raw, checked_result, checked
        if checked and raw == checked_raw:
            return checked_result
        try:
            result = validator(raw)
        except (StructuredResponseError, ValueError) as error:
            if diagnostics:
                retain_diagnostic(raw, schema_id=schema_id, task_kind=task_kind, attempt=attempt,
                                  error=error, secrets=secrets)
            error.raw_response = raw
            raise
        if diagnostics:
            retain_diagnostic(raw, schema_id=schema_id, task_kind=task_kind, attempt=attempt, secrets=secrets)
        checked_raw, checked_result, checked = raw, result, True
        return result

    kwargs = {"max_tokens": max_tokens}
    if type(client) is OpenCodeZenClient:
        kwargs.update(response_validator=validate, schema_id=schema_id, task_kind=task_kind)
    try:
        raw = client.translate(prompt, **kwargs)
    except OpenCodeClientError as error:
        # The adapter wraps rejected schema output separately from transport.
        # Restore the original validator exception so bounded schema repair
        # remains distinct from rate-limit/network retries.
        if (type(error).__name__ == "OpenCodeResponseValidationError"
                and isinstance(error.__cause__, (StructuredResponseError, ValueError))):
            raise error.__cause__
        raise
    if not checked or raw != checked_raw:
        validate(raw)
    return raw, checked_result
