"""Task identity and cooperative cancellation carried into blocking adapters."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import re
import threading
from typing import Callable, Optional


@dataclass(frozen=True)
class ExecutionContext:
    run_id: str = "unscoped"
    cancel_check: Optional[Callable[[], bool]] = field(default=None, repr=False, compare=False)


_execution: ContextVar[ExecutionContext] = ContextVar("studio_execution", default=ExecutionContext())
_cache_observer: ContextVar[Optional[threading.Event]] = ContextVar("studio_cache_observer", default=None)


@contextmanager
def observe_cache_usage():
    """Task-local marker shared only with workers that copy this context.

    An Event carries a cache hit back from a worker thread without mixing
    simultaneous jobs or retaining state after a failed/cancelled operation.
    Nested observations are deliberately isolated from their parent.
    """
    marker = threading.Event()
    token = _cache_observer.set(marker)
    try:
        yield marker
    finally:
        _cache_observer.reset(token)


def record_cache_usage():
    marker = _cache_observer.get()
    if marker is not None:
        marker.set()


def current_execution_context() -> ExecutionContext:
    return _execution.get()


@contextmanager
def execution_context(run_id: str, cancel_check=None):
    """Use around asyncio.to_thread or inside its callable; reset after work.

    asyncio.to_thread copies ContextVars. For run_in_executor, explicitly use
    contextvars.copy_context().run so this context reaches the worker thread.
    """
    safe_id = run_id if isinstance(run_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", run_id) else "unscoped"
    if cancel_check is not None and not callable(cancel_check):
        raise TypeError("cancel_check must be callable")
    token = _execution.set(ExecutionContext(safe_id, cancel_check))
    try:
        yield _execution.get()
    finally:
        _execution.reset(token)
