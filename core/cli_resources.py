"""RAM admission for owned CLI processes, independent of provider concurrency.

No background sampler: admission waiters sample live RAM and owned process RSS.
Reservations cover memory a newly admitted child has not allocated yet. Already
allocated RSS is reflected in OS available RAM and must not be counted twice.
"""
from contextlib import contextmanager
import logging
import math
import threading
import time


MIB = 1024 * 1024


def memory_snapshot():
    """Return (total, available), or unknown; never guess free memory."""
    try:
        import psutil
    except ImportError:
        return None
    try:
        memory = psutil.virtual_memory()
        return memory.total, memory.available
    except (psutil.Error, OSError, RuntimeError):
        return None


def owned_process(pid):
    try:
        import psutil
    except ImportError:
        return None
    try:
        return psutil.Process(pid) if type(pid) is int and pid > 0 else None
    except (psutil.Error, OSError, RuntimeError):
        return None


def process_rss(process):
    try:
        import psutil
    except ImportError:
        return 0
    try:
        if process is None or not process.is_running():
            return 0
        processes = [process, *process.children(recursive=True)]
        total = 0
        for child in processes:
            try:
                total += child.memory_info().rss
            except psutil.Error:
                continue
        return total
    except (psutil.Error, OSError, RuntimeError):
        return 0


class ResourceWaitTimeout(RuntimeError):
    pass


class _Lease:
    def __init__(self, owner, estimate, run_id):
        self.owner, self.estimate, self.process = owner, estimate, None
        self.run_id, self.retired = run_id, False
        self.popen = None

    def attach(self, process):
        # Popen succeeded; retain psutil's creation-time-aware identity rather
        # than later looking up a potentially recycled PID.
        with self.owner._condition:
            self.popen = process
            self.process = owned_process(process.pid)


class CLIResourceAdmission:
    def __init__(self, *, memory=memory_snapshot, rss=process_rss,
                 process_bytes=896 * MIB, reserve_bytes=2048 * MIB,
                 reserve_fraction=.10, poll_interval=.25):
        # Initial envelope covers the observed 400–750 MiB OpenCode children
        # plus startup variation. Larger observed RSS raises future envelopes.
        if (type(process_bytes) is not int or process_bytes <= 0
                or type(reserve_bytes) is not int or reserve_bytes < 0
                or not 0 <= reserve_fraction < 1 or not .01 <= poll_interval <= 1):
            raise ValueError("Invalid CLI memory admission policy")
        self.memory, self.rss = memory, rss
        self.process_bytes, self.reserve_bytes = process_bytes, reserve_bytes
        self.reserve_fraction, self.poll_interval = reserve_fraction, poll_interval
        self._condition = threading.Condition()
        self._leases = set()
        self._queue = []
        self._wait_reasons = {}

    def _reap_retired(self):
        for lease in tuple(self._leases):
            if not lease.retired:
                continue
            # Popen.poll reaps the owned child even if process inspection is
            # unavailable. An OS inspection failure must retain the reservation.
            try:
                alive = lease.popen is not None and lease.popen.poll() is None
            except OSError:
                alive = True
            if not alive:
                self._leases.discard(lease)

    def snapshot(self, run_id=None):
        with self._condition:
            self._reap_retired()
            reasons = [reason for (ticket, owner), reason in self._wait_reasons.items()
                       if run_id is None or owner == run_id]
            return {"resource_waiting_requests": len(reasons),
                    "cli_processes": sum(run_id is None or lease.run_id == run_id for lease in self._leases),
                    "resource_wait_reason": ("memory_unknown" if "memory_unknown" in reasons
                                             else "ram_headroom" if "ram_headroom" in reasons
                                             else "resource_fifo" if reasons else None)}

    def _budget(self):
        self._reap_retired()
        pending = 0
        observed = []
        for lease in self._leases:
            value = self.rss(lease.process)
            value = value if type(value) is int and value >= 0 else 0
            observed.append((lease, value))
            self.process_bytes = max(self.process_bytes, math.ceil(value * 1.15))
        for lease, value in observed:
            lease.estimate = max(lease.estimate, self.process_bytes)
            pending += max(0, lease.estimate - value)
        snapshot = self.memory()
        if (not isinstance(snapshot, tuple) or len(snapshot) != 2
                or any(type(n) is not int for n in snapshot)
                or not 0 <= snapshot[1] <= snapshot[0] or snapshot[0] <= 0):
            return None, pending, self.reserve_bytes
        total, available = snapshot
        return available, pending, max(self.reserve_bytes, math.ceil(total * self.reserve_fraction))

    @contextmanager
    def admit(self, *, check_cancelled=lambda: None, run_id="unscoped", timeout=30.):
        if not math.isfinite(timeout) or not 0 < timeout <= 300:
            raise ValueError("Invalid resource wait deadline")
        started = time.monotonic()
        ticket, lease, logged_wait = (object(), run_id), None, False
        logger = logging.getLogger("ai.resources")
        try:
            with self._condition:
                self._queue.append(ticket)
                while True:
                    check_cancelled()
                    available, pending, reserve = self._budget()
                    if (self._queue[0] is ticket and available is not None
                            and available - pending >= reserve + self.process_bytes):
                        lease = _Lease(self, self.process_bytes, run_id)
                        self._leases.add(lease)
                        self._queue.remove(ticket)
                        self._wait_reasons.pop(ticket, None)
                        self._condition.notify_all()
                        logger.info("CLI_RESOURCE_ADMITTED run_id=%s wait_ms=%d available_bytes=%s reserved_growth_bytes=%d child_budget_bytes=%d headroom_bytes=%d active_children=%d",
                            run_id, (time.monotonic() - started) * 1000, available, pending,
                            self.process_bytes, reserve, len(self._leases))
                        break
                    remaining = timeout - (time.monotonic() - started)
                    reason = ("memory_unknown" if available is None else "ram_headroom"
                              if available - pending < reserve + self.process_bytes else "resource_fifo")
                    self._wait_reasons[ticket] = reason
                    if remaining <= 0:
                        logger.warning("CLI_RESOURCE_TIMEOUT run_id=%s reason=%s wait_seconds=%.3f",
                            run_id, reason, timeout)
                        raise ResourceWaitTimeout(
                            "Chưa đo được RAM khả dụng để mở thêm Muse; giữ tiến độ và chờ thử lại."
                            if available is None else "Đang chờ lượt mở Muse; giữ tiến độ và chờ thử lại."
                            if reason == "resource_fifo" else "Đang thiếu RAM để mở thêm Muse; giữ tiến độ và chờ thử lại.")
                    if not logged_wait:
                        logger.info("CLI_RESOURCE_WAIT run_id=%s reason=%s available_bytes=%s reserved_growth_bytes=%d child_budget_bytes=%d headroom_bytes=%d active_children=%d",
                            run_id, reason,
                            available, pending, self.process_bytes, reserve, len(self._leases))
                        logged_wait = True
                    self._condition.wait(min(self.poll_interval, remaining))
            check_cancelled()
            yield lease
        finally:
            with self._condition:
                if ticket in self._queue:
                    self._queue.remove(ticket)
                self._wait_reasons.pop(ticket, None)
                if lease is not None:
                    # Normal communicate reaps the process. If OS cleanup
                    # failed, retain its reservation until it actually exits.
                    lease.retired = True
                    self._reap_retired()
                self._condition.notify_all()


opencode_resources = CLIResourceAdmission()
