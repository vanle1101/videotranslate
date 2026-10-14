"""Per-video thread ownership for synchronous AI I/O, independent of CPU pools.

The transport must cooperate with current_execution_context().cancel_check.
Python cannot forcibly terminate a thread: cancellation drains owned work before
returning, so media/session cleanup cannot race an unfinished request.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import contextvars
import threading

from core.runtime_context import execution_context


class VideoAIExecutor:
    """One instance per session, used from its single asyncio event loop.

    Workers start lazily. The final outstanding call joins all workers; a later
    call recreates the pool. aclose permanently rejects new work and signals
    cancellation to both queued and running calls before draining them.
    """

    def __init__(self, run_id: str, max_workers: int):
        if isinstance(max_workers, bool) or not isinstance(max_workers, int) or not 1 <= max_workers <= 50:
            raise ValueError("AI workers must be between 1 and 50")
        self.run_id = run_id
        self.max_workers = max_workers
        self.thread_prefix = f"studio-ai-{run_id}"
        self._pool = None
        self._loop = None
        self._pending = set()
        self._closing = threading.Event()

    def _bind_loop(self):
        loop = asyncio.get_running_loop()
        if self._loop is not None and self._loop is not loop:
            raise RuntimeError("VideoAIExecutor cannot be shared across event loops")
        self._loop = loop
        return loop

    async def run(self, function, *args, cancel_check=None, **kwargs):
        loop = self._bind_loop()
        if self._closing.is_set():
            raise RuntimeError("VideoAIExecutor is closed")
        cancellation = threading.Event()

        def cancelled():
            return cancellation.is_set() or self._closing.is_set() or bool(cancel_check and cancel_check())

        if cancelled():
            raise asyncio.CancelledError

        def invoke():
            if cancelled():
                raise asyncio.CancelledError
            with execution_context(self.run_id, cancelled):
                return function(*args, **kwargs)

        if self._pool is None:
            self._pool = ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix=self.thread_prefix)
        future = loop.run_in_executor(self._pool, contextvars.copy_context().run, invoke)
        self._pending.add(future)
        was_cancelled = False
        try:
            while True:
                try:
                    result = await asyncio.shield(future)
                    break
                except asyncio.CancelledError:
                    cancellation.set()
                    if future.done():
                        # Retrieve an exception if cancellation raced completion.
                        if not future.cancelled():
                            future.exception()
                        raise
                    was_cancelled = True
                except Exception:
                    if was_cancelled or cancelled():
                        raise asyncio.CancelledError from None
                    raise
            if was_cancelled or cancelled():
                raise asyncio.CancelledError
            return result
        finally:
            self._pending.discard(future)
            self._close_idle()

    def _close_idle(self):
        if not self._pending and self._pool is not None:
            pool, self._pool = self._pool, None
            # Every owned future has completed; this joins only idle workers.
            pool.shutdown(wait=True)

    async def aclose(self):
        self._bind_loop()
        self._closing.set()
        was_cancelled = False
        # Do not cancel the wrapper futures: that would hide still-running
        # native calls. Their context cancellation reaps transport processes.
        while self._pending:
            drain = asyncio.gather(*tuple(self._pending), return_exceptions=True)
            while not drain.done():
                try:
                    await asyncio.shield(drain)
                except asyncio.CancelledError:
                    was_cancelled = True
            drain.result()
            try:
                await asyncio.sleep(0)
            except asyncio.CancelledError:
                was_cancelled = True
        self._close_idle()
        if was_cancelled:
            raise asyncio.CancelledError

    async def __aenter__(self):
        self._bind_loop()
        return self

    async def __aexit__(self, *exc):
        await self.aclose()
