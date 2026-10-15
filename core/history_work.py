"""Cooperative ownership for read-only history threads and media probes."""
import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
import threading
import time


class HistoryReadCancelled(RuntimeError):
    pass


_scope = ContextVar("history_read_scope", default=None)
_active = set()
_active_lock = threading.Lock()
_stopping = threading.Event()
HISTORY_READ_TIMEOUT = 30.0


def cancel_history_reads():
    _stopping.set()
    with _active_lock:
        for event in _active:
            event.set()


def reset_history_reads():
    _stopping.clear()


def check_history_read():
    scope = _scope.get()
    if scope and (scope[0].is_set() or time.monotonic() >= scope[1]):
        raise HistoryReadCancelled("Đã dừng đọc lịch sử; tải lại khi ứng dụng sẵn sàng.")


def history_read_active():
    return _scope.get() is not None


@contextmanager
def history_read_scope(cancel, timeout=HISTORY_READ_TIMEOUT):
    token = _scope.set((cancel, time.monotonic() + timeout))
    try:
        check_history_read()
        yield
    finally:
        _scope.reset(token)


async def read_history(function, *args, request=None, **kwargs):
    """Cancel AND join the actual thread before releasing request ownership."""
    cancel = threading.Event()
    with _active_lock:
        if _stopping.is_set():
            raise HistoryReadCancelled("Studio đang dừng; chưa thể đọc lịch sử.")
        _active.add(cancel)
    def read():
        with history_read_scope(cancel, timeout=HISTORY_READ_TIMEOUT):
            result = function(*args, **kwargs)
            check_history_read()
            return result
    worker = asyncio.create_task(asyncio.to_thread(read))
    try:
        while not worker.done():
            await asyncio.wait({worker}, timeout=.1)
            if not worker.done() and request is not None and await request.is_disconnected():
                cancel.set()
        return await worker
    finally:
        cancel.set()
        # asyncio.to_thread cancellation alone leaves its executor thread alive.
        # Checkpoints/native probes cooperate, then the exact worker is reaped.
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
            except Exception:
                # A filesystem/probe failure can race cancellation. It is
                # retrieved below; never leak ownership or replace the
                # original request cancellation while draining its worker.
                break
        with _active_lock:
            _active.discard(cancel)
        if worker.done() and not worker.cancelled():
            worker.exception()  # Retrieve a raced failure during cancellation.
