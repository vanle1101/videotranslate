"""Real thread scheduling tests; callable I/O is controlled, not a provider."""
import asyncio
import contextvars
import threading

import pytest

from core.ai_workers import VideoAIExecutor
from core.runtime_context import current_execution_context


async def until(predicate):
    for _ in range(500):
        if predicate():
            return
        await asyncio.sleep(.005)
    raise AssertionError("workers did not reach expected state")


def no_threads(executor):
    assert not any(thread.name.startswith(executor.thread_prefix) for thread in threading.enumerate())


@pytest.mark.parametrize("limits", [(50,), (20, 20)])
def test_real_workers_are_independent_of_asyncio_default_executor(limits):
    async def scenario():
        from concurrent.futures import ThreadPoolExecutor
        asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=1))
        marker = contextvars.ContextVar("ai-marker", default="missing")
        marker.set("inherited")
        release = threading.Event()
        lock = threading.Lock()
        active = {}
        pools = [VideoAIExecutor(f"test-video-{i}", count) for i, count in enumerate(limits)]
        def operation(expected):
            assert marker.get() == "inherited"
            assert current_execution_context().run_id == expected
            with lock:
                active[expected] = active.get(expected, 0) + 1
            assert release.wait(8)
            return expected
        tasks = [asyncio.create_task(pool.run(operation, pool.run_id)) for pool in pools for _ in range(pool.max_workers)]
        try:
            await until(lambda: sum(active.values()) == sum(limits))
            assert [active[pool.run_id] for pool in pools] == list(limits)
        finally:
            release.set()
            await asyncio.gather(*tasks)
            for pool in pools:
                await pool.aclose()
                no_threads(pool)
        assert current_execution_context().run_id == "unscoped"
    asyncio.run(scenario())


def test_task_cancel_drains_transport_before_return_and_idle_pool_can_reopen():
    async def scenario():
        pool = VideoAIExecutor("test-cancel", 3)
        entered, exited = threading.Event(), threading.Event()
        def operation():
            entered.set()
            while not current_execution_context().cancel_check():
                threading.Event().wait(.005)
            exited.set()
            raise RuntimeError("transport cancelled")
        task = asyncio.create_task(pool.run(operation))
        await until(entered.is_set)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert exited.is_set()
        no_threads(pool)
        assert await pool.run(lambda: "retry") == "retry"
        no_threads(pool)
        await pool.aclose()
    asyncio.run(scenario())


def test_close_cancels_running_and_queued_without_invoking_queued_body():
    async def scenario():
        pool = VideoAIExecutor("test-close", 1)
        entered = threading.Event()
        calls = []
        def operation():
            calls.append(1)
            entered.set()
            while not current_execution_context().cancel_check():
                threading.Event().wait(.005)
            return "discarded after cancellation"
        tasks = [asyncio.create_task(pool.run(operation)) for _ in range(4)]
        await until(entered.is_set)
        await pool.aclose()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        assert len(calls) == 1
        assert all(isinstance(result, asyncio.CancelledError) for result in results)
        no_threads(pool)
        with pytest.raises(RuntimeError, match="closed"):
            await pool.run(lambda: "must not run")
    asyncio.run(scenario())


def test_worker_cancelled_error_does_not_spin_and_failure_closes_pool():
    async def scenario():
        pool = VideoAIExecutor("test-worker-failure", 1)
        def cancelled():
            raise asyncio.CancelledError
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(pool.run(cancelled), 2)
        no_threads(pool)
        def failed():
            raise ValueError("real failure")
        with pytest.raises(ValueError, match="real failure"):
            await pool.run(failed)
        no_threads(pool)
    asyncio.run(scenario())


def test_cancelled_close_still_drains_and_joins_threads():
    async def scenario():
        pool = VideoAIExecutor("test-close-drain", 1)
        entered, saw_cancel, release = threading.Event(), threading.Event(), threading.Event()
        def operation():
            entered.set()
            while not current_execution_context().cancel_check():
                threading.Event().wait(.005)
            saw_cancel.set()
            assert release.wait(5)
        task = asyncio.create_task(pool.run(operation))
        await until(entered.is_set)
        closing = asyncio.create_task(pool.aclose())
        await until(saw_cancel.is_set)
        closing.cancel()
        await asyncio.sleep(.01)
        assert not closing.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await closing
        with pytest.raises(asyncio.CancelledError):
            await task
        no_threads(pool)
    asyncio.run(scenario())
