"""Bounded chunk execution with exclusive ownership and ordered publication.

Workers may finish out of order, but only the contiguous timeline prefix is
published. Use the runner as an async context manager so an early consumer exit
also cancels and joins its workers. Provider concurrency remains a separate cap.
"""
from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Generic, Hashable, Iterable, TypeVar


T = TypeVar("T")


@dataclass(frozen=True)
class ChunkWork(Generic[T]):
    """An immutable ownership interval and its isolated async operation.

    ``start``/``end`` describe the owned half-open timeline, not extra read-only
    context. Context overlap is allowed inside ``run``; owned output is not.
    """

    start: float
    end: float
    source_ids: tuple[Hashable, ...]
    run: Callable[[], Awaitable[T]] = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        for name in ("start", "end"):
            value = getattr(self, name)
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value)):
                raise ValueError(f"Chunk {name} must be a finite number")
        if self.start < 0 or self.end <= self.start:
            raise ValueError("Chunk must own a positive duration starting at or after zero")
        if not callable(self.run):
            raise TypeError("Chunk run must be an async callable")
        object.__setattr__(self, "source_ids", tuple(self.source_ids))
        try:
            unique = set(self.source_ids)
        except TypeError as exc:
            raise ValueError("Chunk source IDs must be hashable") from exc
        if len(unique) != len(self.source_ids):
            raise ValueError("A source ID may only be owned once in a chunk")


@dataclass(frozen=True)
class ChunkOutcome(Generic[T]):
    work: ChunkWork[T]
    value: T | None = None
    error: Exception | None = None

    @property
    def succeeded(self) -> bool:
        return self.error is None


def effective_chunk_workers(requested: int, max_ram_workers: int) -> int:
    """Apply the resource admission cap without changing provider rate limits."""
    for value in (requested, max_ram_workers):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError("Worker limits must be positive integers")
    return min(requested, max_ram_workers)


class OrderedChunkRunner(Generic[T]):
    """Run isolated chunks concurrently and yield their timeline-ordered results.

    All ownership is validated before the first operation in each batch starts.
    Claims survive subsequent batches, including failed outcomes: retry failed
    stages outside this runner (or in a fresh run), never as duplicate work.
    Completed out-of-order results count toward the bounded in-flight window.
    An unset ``admission_event`` pauses new admission, not active operations.
    ``is_current`` returning false cancels all workers and prevents publication.
    """

    def __init__(self, max_workers: int, *, admission_event: asyncio.Event | None = None,
                 is_current: Callable[[], bool] | None = None,
                 return_errors: bool = True) -> None:
        self.max_workers = effective_chunk_workers(max_workers, max_workers)
        self.admission_event = admission_event
        self.is_current = is_current
        self.return_errors = return_errors
        self._claimed_ids: set[Hashable] = set()
        self._claimed_end = 0.0
        self._tasks: set[asyncio.Task] = set()
        self._busy = False
        self._closed = False

    async def __aenter__(self):
        self._check_current()
        return self

    async def __aexit__(self, _exc_type, _exc, _traceback):
        await self.close()

    def _check_current(self) -> None:
        if self._closed or (self.is_current is not None and not self.is_current()):
            raise asyncio.CancelledError("Chunk run stopped or superseded")

    def _claim(self, work: Iterable[ChunkWork[T]]) -> list[ChunkWork[T]]:
        ordered = sorted(work, key=lambda item: (item.start, item.end))
        claimed_ids: set[Hashable] = set()
        end = self._claimed_end
        for item in ordered:
            if item.start < end:
                raise ValueError("Chunk ownership overlaps or goes backwards in the timeline")
            ids = set(item.source_ids)
            if ids.intersection(self._claimed_ids) or ids.intersection(claimed_ids):
                raise ValueError("A source ID cannot be owned by multiple chunks")
            claimed_ids.update(ids)
            end = item.end
        self._claimed_ids.update(claimed_ids)
        self._claimed_end = end
        return ordered

    async def _wait_admission(self) -> None:
        self._check_current()
        event = self.admission_event
        while event is not None and not event.is_set():
            try:
                await asyncio.wait_for(event.wait(), timeout=0.1)
            except asyncio.TimeoutError:
                pass
            self._check_current()
        self._check_current()

    async def _execute(self, work: ChunkWork[T]) -> ChunkOutcome[T]:
        self._check_current()
        try:
            value = await work.run()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return ChunkOutcome(work=work, error=exc)
        return ChunkOutcome(work=work, value=value)

    async def _cancel_and_join(self) -> None:
        tasks = tuple(self._tasks)
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.difference_update(tasks)

    async def close(self) -> None:
        self._closed = True
        await self._cancel_and_join()

    async def run(self, work: Iterable[ChunkWork[T]]):
        """Yield success/failure outcomes, with at most ``max_workers`` buffered."""
        if self._busy:
            raise RuntimeError("A chunk runner cannot consume two batches at once")
        self._check_current()
        self._busy = True
        try:
            ordered = self._claim(work)
            pending: dict[int, asyncio.Task] = {}
            next_index = 0
            for index in range(len(ordered)):
                while next_index < min(len(ordered), index + self.max_workers):
                    # Pause still lets already admitted outcomes be checkpointed.
                    if (pending and self.admission_event is not None
                            and not self.admission_event.is_set()):
                        break
                    await self._wait_admission()
                    task = asyncio.create_task(self._execute(ordered[next_index]))
                    self._tasks.add(task)
                    pending[next_index] = task
                    next_index += 1
                task = pending.pop(index)
                while not task.done():
                    await asyncio.wait((task,), timeout=0.1)
                    self._check_current()
                outcome = task.result()  # Cancellation must never become a failure outcome.
                self._tasks.discard(task)
                self._check_current()
                if outcome.error is not None and not self.return_errors:
                    raise outcome.error
                yield outcome
        finally:
            await self._cancel_and_join()
            self._busy = False
