"""Task identity and cooperative cancellation carried into blocking adapters."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import re
from typing import Callable, Optional


@dataclass(frozen=True)
class ExecutionContext:
    run_id: str = "unscoped"
    cancel_check: Optional[Callable[[], bool]] = field(default=None, repr=False, compare=False)


_execution: ContextVar[ExecutionContext] = ContextVar("studio_execution", default=ExecutionContext())


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
