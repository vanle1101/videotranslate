"""Small monotonic phase profiler for a single streaming session run."""
from __future__ import annotations

import math
import threading


class StageProfiler:
    """Accumulate active wall time by progress phase without changing UI state."""

    def __init__(self):
        self.reset()

    def reset(self, *, started_at=None, initial_phase=None, source_stage=None, preserve=False):
        self.seconds = dict(getattr(self, "seconds", {})) if preserve else {}
        self.work_seconds = dict(getattr(self, "work_seconds", {})) if preserve else {}
        self.work_counts = dict(getattr(self, "work_counts", {})) if preserve else {}
        if not hasattr(self, "_work_lock"):
            self._work_lock = threading.Lock()
        self.active = (self._key(initial_phase, source_stage)
                       if started_at is not None and initial_phase else None)
        self.last_at = started_at
        self.finished_at = None

    def record_work(self, stage, seconds, *, units=1):
        """Record measured stage wall time, separately from the visible phase.

        Work can overlap across the translation, speech and source workers, so
        these values are cumulative per-stage durations and must not be summed
        to infer end-to-end elapsed time.
        """
        if (not isinstance(stage, str) or not stage.strip()
                or type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds < 0
                or type(units) is not int or units < 0):
            return
        with self._work_lock:
            self.work_seconds[stage] = self.work_seconds.get(stage, 0.) + float(seconds)
            self.work_counts[stage] = self.work_counts.get(stage, 0) + units

    def work_snapshot(self):
        with self._work_lock:
            return {"seconds": {key: round(value, 3)
                                for key, value in sorted(self.work_seconds.items())},
                    "units": dict(sorted(self.work_counts.items()))}

    @staticmethod
    def _key(phase, source_stage=None):
        if phase in {"prepare", "asr"} and isinstance(source_stage, str) and source_stage:
            return "source." + source_stage
        return str(phase)

    def advance(self, phase, *, now, source_stage=None):
        if self.finished_at is not None:
            return
        if self.active is None or self.last_at is None:
            self.active = self._key(phase, source_stage)
            self.last_at = now
            return
        self.seconds[self.active] = self.seconds.get(self.active, 0.) + max(0., now - self.last_at)
        self.active = self._key(phase, source_stage)
        self.last_at = now

    def finish(self, *, now):
        if self.finished_at is not None:
            return
        if self.active is not None and self.last_at is not None:
            self.seconds[self.active] = self.seconds.get(self.active, 0.) + max(0., now - self.last_at)
        self.active = None
        self.last_at = None
        self.finished_at = now

    def snapshot(self, *, now):
        result = dict(self.seconds)
        if self.active is not None and self.last_at is not None:
            result[self.active] = result.get(self.active, 0.) + max(0., now - self.last_at)
        return {key: round(value, 3) for key, value in sorted(result.items())}
