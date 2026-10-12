"""Ownership, backpressure and cancellation regression tests without providers."""
import asyncio
from dataclasses import FrozenInstanceError

import pytest

from core.streaming.parallel_chunks import (
    ChunkWork, OrderedChunkRunner, effective_chunk_workers,
)


async def collect(runner, work):
    return [outcome async for outcome in runner.run(work)]


def make_work(index, operation, *, start=None, end=None, source_ids=None):
    return ChunkWork(index if start is None else start,
                     index + 1 if end is None else end,
                     (index,) if source_ids is None else source_ids, operation)


def test_multiple_workers_execute_simultaneously_but_publish_in_timeline_order():
    async def scenario():
        started = asyncio.Event()
        releases = [asyncio.Event() for _ in range(3)]
        completed = [asyncio.Event() for _ in range(3)]
        active = 0
        peak = 0
        completion_order = []
        publication_order = []

        async def execute(index):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            if active == 3:
                started.set()
            try:
                await releases[index].wait()
                completion_order.append(index)
                completed[index].set()
                return index
            finally:
                active -= 1

        work = [make_work(i, lambda i=i: execute(i)) for i in (2, 0, 1)]
        async with OrderedChunkRunner(3) as runner:
            async def consume():
                async for outcome in runner.run(work):
                    publication_order.append(outcome.value)
            consumer = asyncio.create_task(consume())
            await asyncio.wait_for(started.wait(), 1)
            for i in (2, 1):
                releases[i].set()
                await asyncio.wait_for(completed[i].wait(), 1)
                assert publication_order == []
            releases[0].set()
            await asyncio.wait_for(consumer, 1)
        assert peak == 3 and active == 0
        assert completion_order == [2, 1, 0]
        assert publication_order == [0, 1, 2]
    asyncio.run(scenario())


def test_active_and_completed_unpublished_window_is_bounded():
    async def scenario():
        admitted = []
        first_release = asyncio.Event()
        second_done = asyncio.Event()
        async def execute(index):
            admitted.append(index)
            if index == 0:
                await first_release.wait()
            elif index == 1:
                second_done.set()
            return index
        async with OrderedChunkRunner(2) as runner:
            consumer = asyncio.create_task(collect(
                runner, [make_work(i, lambda i=i: execute(i)) for i in range(8)]))
            await asyncio.wait_for(second_done.wait(), 1)
            assert admitted == [0, 1]  # The slow prefix limits RAM even if peers finish.
            first_release.set()
            results = await asyncio.wait_for(consumer, 1)
        assert admitted == list(range(8))
        assert [o.value for o in results] == list(range(8))
    asyncio.run(scenario())


@pytest.mark.parametrize("invalid", ["same_id", "overlap"])
def test_entire_batch_is_validated_before_any_operation_starts(invalid):
    async def scenario():
        calls = []
        async def execute():
            calls.append(1)
        first = make_work(0, execute)
        second = (make_work(1, execute, source_ids=(0,)) if invalid == "same_id"
                  else make_work(1, execute, start=.5, end=2))
        async with OrderedChunkRunner(2) as runner:
            with pytest.raises(ValueError):
                await collect(runner, [first, second])
        assert calls == []
    asyncio.run(scenario())


def test_claims_persist_across_waves_and_prevent_duplicate_execution():
    async def scenario():
        calls = []
        async def execute():
            calls.append(1)
            return "real stage result"
        async with OrderedChunkRunner(2) as runner:
            await collect(runner, [make_work(0, execute)])
            with pytest.raises(ValueError, match="source ID"):
                await collect(runner, [make_work(1, execute, source_ids=(0,))])
            with pytest.raises(ValueError, match="timeline"):
                await collect(runner, [make_work(2, execute, start=.2, end=.8)])
            results = await collect(runner, [make_work(1, execute)])
        assert calls == [1, 1] and results[0].succeeded
    asyncio.run(scenario())


def test_one_stage_failure_is_an_outcome_and_does_not_cancel_independent_peers():
    async def scenario():
        calls = []
        async def execute(index):
            calls.append(index)
            if index == 1:
                raise RuntimeError("provider timeout: recover this chunk independently")
            return index
        async with OrderedChunkRunner(3) as runner:
            results = await collect(runner, [make_work(i, lambda i=i: execute(i))
                                              for i in range(4)])
        assert calls == [0, 1, 2, 3]
        assert [o.succeeded for o in results] == [True, False, True, True]
        assert isinstance(results[1].error, RuntimeError)
        assert results[1].value is None
        assert results[-1].value == 3
    asyncio.run(scenario())


def test_fail_fast_cancels_and_joins_other_owned_workers():
    async def scenario():
        peer_started = asyncio.Event()
        peer_cancelled = asyncio.Event()
        async def failed():
            await peer_started.wait()
            raise ValueError("critical storage failure")
        async def peer():
            peer_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                peer_cancelled.set()
        async with OrderedChunkRunner(2, return_errors=False) as runner:
            with pytest.raises(ValueError, match="storage"):
                await collect(runner, [make_work(0, failed), make_work(1, peer)])
            assert peer_cancelled.is_set() and not runner._tasks
    asyncio.run(scenario())


def test_cancelled_worker_is_not_converted_into_failed_or_successful_output():
    async def scenario():
        async def execute():
            raise asyncio.CancelledError()
        async with OrderedChunkRunner(1) as runner:
            with pytest.raises(asyncio.CancelledError):
                await collect(runner, [make_work(0, execute)])
            assert not runner._tasks
    asyncio.run(scenario())


def test_consumer_cancellation_cancels_and_joins_all_workers():
    async def scenario():
        started = asyncio.Event()
        active = 0
        async def execute():
            nonlocal active
            active += 1
            if active == 2:
                started.set()
            try:
                await asyncio.Event().wait()
            finally:
                active -= 1
        async with OrderedChunkRunner(2) as runner:
            consumer = asyncio.create_task(collect(
                runner, [make_work(i, execute) for i in range(5)]))
            await asyncio.wait_for(started.wait(), 1)
            consumer.cancel()
            with pytest.raises(asyncio.CancelledError):
                await consumer
            assert active == 0 and not runner._tasks
    asyncio.run(scenario())


def test_stale_run_guard_stops_workers_and_never_publishes_old_results():
    async def scenario():
        current = True
        started = asyncio.Event()
        cancelled = asyncio.Event()
        published = []
        async def execute():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        async with OrderedChunkRunner(1, is_current=lambda: current) as runner:
            async def consume():
                async for outcome in runner.run([make_work(0, execute)]):
                    published.append(outcome)
            consumer = asyncio.create_task(consume())
            await asyncio.wait_for(started.wait(), 1)
            current = False
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(consumer, 1)
        assert cancelled.is_set() and published == []
    asyncio.run(scenario())


def test_pause_stops_new_admission_but_publishes_already_running_results():
    async def scenario():
        admission = asyncio.Event()
        admission.set()
        admitted = []
        paused_results = asyncio.Event()
        second_release = asyncio.Event()
        async def execute(index):
            admitted.append(index)
            if index == 1:
                await second_release.wait()
            return index
        async with OrderedChunkRunner(2, admission_event=admission) as runner:
            results = []
            async def consume():
                async for outcome in runner.run([make_work(i, lambda i=i: execute(i))
                                                for i in range(3)]):
                    results.append(outcome.value)
                    if outcome.value == 0:
                        admission.clear()
                        second_release.set()
                    if outcome.value == 1:
                        paused_results.set()
            consumer = asyncio.create_task(consume())
            await asyncio.wait_for(paused_results.wait(), 1)
            assert admitted == [0, 1] and results == [0, 1]
            admission.set()
            await asyncio.wait_for(consumer, 1)
        assert admitted == [0, 1, 2] and results == [0, 1, 2]
    asyncio.run(scenario())


def test_stop_callback_works_while_admission_is_paused():
    async def scenario():
        current = True
        admission = asyncio.Event()
        called = []
        async def execute():
            called.append(1)
        async with OrderedChunkRunner(1, admission_event=admission,
                                      is_current=lambda: current) as runner:
            consumer = asyncio.create_task(collect(runner, [make_work(0, execute)]))
            await asyncio.sleep(0)
            current = False
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(consumer, 1)
        assert called == []
    asyncio.run(scenario())


@pytest.mark.parametrize("consumer_action", ["break", "raise"])
def test_context_exit_cleans_up_when_consumer_stops_at_first_result(consumer_action):
    async def scenario():
        peer_started = asyncio.Event()
        peer_done = asyncio.Event()
        async def first():
            await peer_started.wait()
            return 0
        async def peer():
            peer_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                peer_done.set()
        async def consume():
            async with OrderedChunkRunner(2) as runner:
                async for _ in runner.run([make_work(0, first), make_work(1, peer)]):
                    if consumer_action == "raise":
                        raise OSError("checkpoint save failed")
                    break
            return runner
        if consumer_action == "raise":
            with pytest.raises(OSError, match="checkpoint"):
                await consume()
        else:
            runner = await consume()
            assert not runner._tasks
        assert peer_done.is_set()
    asyncio.run(scenario())


def test_work_ownership_is_immutable_and_resource_cap_is_explicit():
    async def execute():
        return 1
    ids = [1, 2]
    work = ChunkWork(0, 1, ids, execute)
    ids.append(3)
    assert work.source_ids == (1, 2)
    with pytest.raises(FrozenInstanceError):
        work.start = 10
    assert effective_chunk_workers(5, 3) == 3
    assert effective_chunk_workers(5, 8) == 5
    for invalid in (0, -1, True, 1.5):
        with pytest.raises(ValueError):
            effective_chunk_workers(invalid, 2)
    with pytest.raises(ValueError):
        ChunkWork(0, 1, (1, 1), execute)
    for start, end in ((False, 1), (0, float("nan")), (1, 1), (-1, 1)):
        with pytest.raises(ValueError):
            ChunkWork(start, end, (), execute)
