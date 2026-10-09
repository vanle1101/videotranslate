"""Bounded synchronous provider admission, retries and validated response cache.

No permanent worker/process is started: the admitted caller owns its transport
and must implement its deadline/cancellation. Queue and circuit failures are
explicit retryable outcomes, never empty/placeholder provider responses.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import hashlib
import json
import logging
import math
from pathlib import Path
import random
import re
import threading
import time
from typing import Any, Callable, Mapping, Optional
import uuid


class AIExecutionError(RuntimeError):
    """An admission failure; the caller should preserve its checkpoint."""


class AIQueueFullError(AIExecutionError):
    pass


class AIQueueTimeoutError(AIExecutionError):
    pass


class AICircuitOpenError(AIExecutionError):
    def __init__(self, retry_after: float):
        self.retry_after = max(0.0, retry_after)
        super().__init__("Muse đang tạm nghỉ sau nhiều lượt lỗi; phần chưa xong sẽ được thử lại.")


class AIExecutionCancelledError(AIExecutionError):
    pass


@dataclass(frozen=True)
class AIExecutionPolicy:
    concurrency: int = 1
    queue_limit: int = 24
    queue_timeout: float = 30.0
    circuit_failures: int = 4
    circuit_cooldown: float = 30.0
    retry_base: float = 1.0
    retry_cap: float = 8.0
    retry_jitter: float = 0.25
    cache_ttl: float = 7 * 24 * 3600.0
    cache_entries: int = 1024
    cache_bytes: int = 16 * 1024 * 1024

    def __post_init__(self):
        for name in ("concurrency", "queue_limit", "circuit_failures", "cache_entries", "cache_bytes"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        for name in ("queue_timeout", "circuit_cooldown", "retry_base", "retry_cap", "cache_ttl"):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be positive and finite")
        if not math.isfinite(self.retry_jitter) or not 0 <= self.retry_jitter <= 1:
            raise ValueError("retry_jitter must be between zero and one")


def response_cache_key(identity: Mapping[str, Any]) -> str:
    """Only a digest reaches disk/logs; credentials must not be in identity."""
    serial = json.dumps(dict(identity), sort_keys=True, ensure_ascii=False,
                        separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(serial.encode("utf-8")).hexdigest()


class ValidatedResponseCache:
    """Atomic, bounded, expiring cache; every read repeats caller validation.

    The response contains source-derived content. Prompts/keys/raw failure
    responses are never written here. Invalid semantic/schema outputs are not
    successful entries, even if the provider transport returned successfully.
    """

    VERSION = 1

    def __init__(self, root: Optional[Path], policy: AIExecutionPolicy):
        self.root = Path(root) if root is not None else None
        self.policy = policy
        self._lock = threading.RLock()

    def _path(self, key):
        if not re.fullmatch(r"[a-f0-9]{64}", key):
            raise ValueError("Invalid cache digest")
        return self.root / (key + ".json") if self.root is not None else None

    def get(self, key: str, validate: Callable[[str], Any]) -> Optional[str]:
        path = self._path(key)
        if path is None:
            return None
        with self._lock:
            try:
                if path.stat().st_size > self.policy.cache_bytes:
                    return None
                payload = json.loads(path.read_text(encoding="utf-8"))
                if (payload.get("version") != self.VERSION or payload.get("key") != key
                        or not isinstance(payload.get("created"), (float, int))
                        or isinstance(payload.get("created"), bool)
                        or not 0 <= time.time() - payload["created"] <= self.policy.cache_ttl):
                    return None
                answer = payload.get("response")
                if not isinstance(answer, str) or not answer.strip():
                    return None
                if validate(answer) is False:
                    return None
                return answer
            except Exception:
                return None

    def put(self, key: str, response: str, validate: Callable[[str], Any]) -> bool:
        path = self._path(key)
        if path is None:
            return False
        # The caller gets its validation error. A disk failure cannot turn a
        # successful real response into fake output, nor discard its result.
        if not isinstance(response, str) or not response.strip() or validate(response) is False:
            return False
        payload = json.dumps({"version": self.VERSION, "key": key, "created": time.time(),
                              "response": response}, ensure_ascii=False, allow_nan=False)
        if len(payload.encode("utf-8")) > self.policy.cache_bytes:
            return False
        temporary = path.with_suffix("." + uuid.uuid4().hex + ".tmp")
        with self._lock:
            try:
                self.root.mkdir(parents=True, exist_ok=True)
                temporary.write_text(payload, encoding="utf-8")
                temporary.replace(path)
                self._prune()
                return True
            except OSError:
                logging.getLogger("ai.execution").warning("AI_CACHE_WRITE_FAILED code=cache_io")
                return False
            finally:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def _prune(self):
        # Only this cache's digest-named entries are eligible. Do not touch
        # source media, manifests, sibling stage caches or unknown user files.
        entries = []
        for path in self.root.glob("*.json"):
            if not re.fullmatch(r"[a-f0-9]{64}\.json", path.name):
                continue
            try:
                stat = path.stat()
                entries.append((stat.st_mtime, stat.st_size, path))
            except OSError:
                continue
        entries.sort(reverse=True)
        total = 0
        for index, (modified, size, path) in enumerate(entries):
            total += size
            if (index >= self.policy.cache_entries or total > self.policy.cache_bytes
                    or time.time() - modified > self.policy.cache_ttl):
                path.unlink(missing_ok=True)


class AIExecutionLayer:
    """FIFO admission across clients; circuit and cache are provider scoped.

    A retry holds its admitted slot (including backoff), so callers cannot
    bypass the provider's backpressure by each starting their own retry loop.
    Queue wait, backoff and half-open admission all remain cancellable.
    """

    def __init__(self, policy: Optional[AIExecutionPolicy] = None, *, cache_root=None):
        self.policy = policy or AIExecutionPolicy()
        self.cache = ValidatedResponseCache(cache_root, self.policy)
        self._condition = threading.Condition()
        self._queue = deque()
        self._active = 0
        self._active_keys = set()
        self._failures = 0
        self._open_until = 0.0
        self._half_open = False
        self._requests = self._retries = self._cache_hits = 0

    @staticmethod
    def _cancel(cancel_check):
        if cancel_check is not None and cancel_check():
            raise AIExecutionCancelledError("Đã hủy yêu cầu AI đang chờ.")

    def _admit(self, cancel_check, key):
        ticket = object()
        deadline = time.monotonic() + self.policy.queue_timeout
        with self._condition:
            self._cancel(cancel_check)
            self._check_circuit()
            if len(self._queue) >= self.policy.queue_limit:
                raise AIQueueFullError("Hàng đợi Muse đã đầy; phần chưa xong sẽ được thử lại.")
            self._queue.append(ticket)
            try:
                while True:
                    self._cancel(cancel_check)
                    self._check_circuit()
                    if (self._queue[0] is ticket and self._active < self.policy.concurrency
                            and not self._half_open and key not in self._active_keys):
                        self._queue.popleft()
                        self._active += 1
                        self._active_keys.add(key)
                        if self._open_until:
                            self._half_open = True
                        self._condition.notify_all()
                        return
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise AIQueueTimeoutError("Hết thời gian chờ lượt Muse; phần chưa xong sẽ được thử lại.")
                    self._condition.wait(min(0.1, remaining))
            finally:
                if ticket in self._queue:
                    self._queue.remove(ticket)
                    self._condition.notify_all()

    def _check_circuit(self):
        if self._open_until and time.monotonic() < self._open_until:
            raise AICircuitOpenError(self._open_until - time.monotonic())

    def _success(self):
        with self._condition:
            self._failures = 0
            self._open_until = 0.0
            self._half_open = False

    def _failure(self):
        with self._condition:
            self._failures += 1
            if self._half_open or self._failures >= self.policy.circuit_failures:
                self._open_until = time.monotonic() + self.policy.circuit_cooldown
            self._half_open = False
            self._condition.notify_all()

    def _release(self, key):
        with self._condition:
            self._active -= 1
            self._active_keys.discard(key)
            # Cancellation/configuration of a half-open probe does not prove
            # the service recovered. Preserve cooldown for the next probe.
            if self._half_open:
                self._half_open = False
                self._open_until = time.monotonic() + self.policy.circuit_cooldown
            self._condition.notify_all()

    def retry_delay(self, retry_index: int) -> float:
        base = min(self.policy.retry_cap, self.policy.retry_base * 2 ** retry_index)
        return max(0, base * (1 + self.policy.retry_jitter * (2 * random.random() - 1)))

    def execute(self, operation: Callable[[int], str], *, identity: Mapping[str, Any],
                max_retries: int = 0, cancel_check=None,
                retryable: Callable[[Exception], bool] = lambda error: False,
                wait_retry: Optional[Callable[[float], None]] = None,
                on_retry: Optional[Callable[[int, Exception], None]] = None,
                validate: Optional[Callable[[str], Any]] = None,
                use_cache: bool = True,
                on_cache_hit: Optional[Callable[[], None]] = None) -> str:
        self._cancel(cancel_check)
        max_retries = max(0, min(int(max_retries), 2))
        key = response_cache_key(identity)
        cache_enabled = use_cache and validate is not None
        if cache_enabled:
            cached = self.cache.get(key, validate)
            if cached is not None:
                self._cancel(cancel_check)
                with self._condition:
                    self._cache_hits += 1
                if on_cache_hit:
                    on_cache_hit()
                return cached
        self._admit(cancel_check, key)
        try:
            # Same-key requests queued behind a successful request use its
            # validated result rather than starting another provider request.
            if cache_enabled:
                cached = self.cache.get(key, validate)
                if cached is not None:
                    self._cancel(cancel_check)
                    with self._condition:
                        self._cache_hits += 1
                    if on_cache_hit:
                        on_cache_hit()
                    return cached
            for attempt in range(max_retries + 1):
                self._cancel(cancel_check)
                self._check_circuit()
                with self._condition:
                    self._requests += 1
                try:
                    result = operation(attempt + 1)
                    self._cancel(cancel_check)
                    if not isinstance(result, str) or not result.strip():
                        raise ValueError("AI response is empty")
                    if validate is not None and validate(result) is False:
                        raise ValueError("AI response did not pass validation")
                except Exception as error:
                    if not retryable(error):
                        raise
                    self._failure()
                    if attempt >= max_retries:
                        raise
                    # A breaker opened on this failure: do not spin through
                    # retries or sleep for the whole cooldown in this caller.
                    self._check_circuit()
                    with self._condition:
                        self._retries += 1
                    delay = self.retry_delay(attempt)
                    if wait_retry is None:
                        self._wait(delay, cancel_check)
                    else:
                        wait_retry(delay)
                    self._cancel(cancel_check)
                    if on_retry:
                        on_retry(attempt + 2, error)
                    continue
                self._success()
                if cache_enabled:
                    self.cache.put(key, result, validate)
                self._cancel(cancel_check)
                return result
        finally:
            self._release(key)
        raise AIExecutionError("AI execution exhausted without a response")

    def _wait(self, seconds, cancel_check):
        deadline = time.monotonic() + seconds
        while True:
            self._cancel(cancel_check)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(0.1, remaining))

    def snapshot(self):
        with self._condition:
            return {"active_requests": self._active, "queued_requests": len(self._queue),
                    "requests": self._requests, "retry_requests": self._retries,
                    "cache_hits": self._cache_hits, "consecutive_failures": self._failures,
                    "circuit_retry_after": max(0, self._open_until - time.monotonic()),
                    "half_open": self._half_open}
