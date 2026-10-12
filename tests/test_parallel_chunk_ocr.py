"""Native OCR admission/cleanup contracts, with no media or model loading."""
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from core.screen_ocr import ChunkScreenOCR, ScreenOCR, ScreenOCRError


class TrackedAdmission:
    """Use a real bounded semaphore and measure its ownership transitions."""
    def __init__(self, capacity=2):
        self.semaphore = threading.BoundedSemaphore(capacity)
        self.lock = threading.Lock()
        self.waiting = threading.Event()
        self.active = 0
        self.peak = 0
        self.acquisitions = 0
        self.releases = 0
        self.before_release = None

    def acquire(self, timeout):
        acquired = self.semaphore.acquire(timeout=timeout)
        if acquired:
            with self.lock:
                self.active += 1
                self.peak = max(self.peak, self.active)
                self.acquisitions += 1
        else:
            self.waiting.set()
        return acquired

    def release(self):
        if self.before_release:
            self.before_release()
        with self.lock:
            self.active -= 1
            self.releases += 1
        self.semaphore.release()


@pytest.fixture
def admission(monkeypatch):
    gate = TrackedAdmission()
    monkeypatch.setattr(ChunkScreenOCR, "_admission", gate)
    return gate


def test_parallel_chunks_share_two_native_owners_with_one_cpu_thread_each(admission, monkeypatch):
    release = threading.Event()
    first_two_started = threading.Event()
    lock = threading.Lock()
    native_owners = 0
    native_peak = 0
    engines = []

    def infer(self, path, start, end, *, cancel_check=None):
        nonlocal native_owners, native_peak
        self._engine = object()
        with lock:
            engines.append(self._engine)
            native_owners += 1
            native_peak = max(native_peak, native_owners)
            if native_owners == 2:
                first_two_started.set()
        try:
            assert release.wait(2), "test did not release active OCR owners"
            return [{"start": start, "end": end}]
        finally:
            with lock:
                native_owners -= 1

    monkeypatch.setattr(ScreenOCR, "extract", infer)
    instances = [ChunkScreenOCR() for _ in range(5)]
    assert all(instance.cpu_threads == 1 for instance in instances)
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(instance.extract, Path("unused"), i, i + 1)
                   for i, instance in enumerate(instances)]
        try:
            assert first_two_started.wait(2)
            assert admission.waiting.wait(2)  # Other chunks are actually queued.
            assert admission.active == 2
        finally:
            release.set()
        results = [future.result(timeout=2) for future in futures]
    assert native_peak == admission.peak == 2
    assert native_owners == admission.active == 0
    assert admission.acquisitions == admission.releases == 5
    assert len({id(engine) for engine in engines}) == 5
    assert all(instance._engine is None for instance in instances)
    assert [rows[0]["start"] for rows in results] == list(range(5))


@pytest.mark.parametrize("result", [[], [{"text_zh": "真实结果"}]])
def test_success_and_empty_return_release_model_before_admission_slot(admission, monkeypatch, result):
    instance = ChunkScreenOCR()
    observed = []
    def infer(self, *args, **kwargs):
        self._engine = object()
        return result
    def before_release():
        observed.append(instance._engine)
    monkeypatch.setattr(ScreenOCR, "extract", infer)
    admission.before_release = before_release
    assert instance.extract(Path("unused"), 0, 1) is result
    assert observed == [None]  # A queued successor cannot inherit the ONNX owner.
    assert admission.active == 0 and admission.acquisitions == admission.releases == 1


@pytest.mark.parametrize("failure", [ScreenOCRError("decode failure"),
                                     RuntimeError("native inference failure"),
                                     MemoryError("model allocation exhausted")])
def test_failed_native_stage_releases_engine_and_slot_for_next_chunk(admission, monkeypatch, failure):
    failed = ChunkScreenOCR()
    next_chunk = ChunkScreenOCR()
    calls = []
    def infer(self, *args, **kwargs):
        self._engine = object()
        calls.append(self)
        if self is failed:
            raise failure
        return [{"text_zh": "下一段"}]
    monkeypatch.setattr(ScreenOCR, "extract", infer)
    with pytest.raises(type(failure), match=str(failure)):
        failed.extract(Path("unused"), 0, 1)
    assert failed._engine is None and admission.active == 0
    assert next_chunk.extract(Path("unused"), 1, 2) == [{"text_zh": "下一段"}]
    assert calls == [failed, next_chunk]
    assert next_chunk._engine is None
    assert admission.active == 0 and admission.acquisitions == admission.releases == 2


def test_cancelled_waiter_does_not_load_model_or_release_somebody_elses_slot(admission, monkeypatch):
    assert admission.acquire(timeout=0) and admission.acquire(timeout=0)
    called = []
    monkeypatch.setattr(ScreenOCR, "extract", lambda *args, **kwargs: called.append(1))
    cancelled = threading.Event()
    instance = ChunkScreenOCR()
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(instance.extract, Path("unused"), 0, 1,
                                 cancel_check=cancelled.is_set)
        try:
            assert admission.waiting.wait(2)
            cancelled.set()
            with pytest.raises(ScreenOCRError, match="hủy"):
                future.result(timeout=2)
            assert called == [] and instance._engine is None
            assert admission.active == 2
            assert admission.acquisitions == 2 and admission.releases == 0
        finally:
            admission.release()
            admission.release()
    assert admission.active == 0


def test_cancelled_immediately_after_admission_releases_without_native_inference(admission, monkeypatch):
    called = []
    monkeypatch.setattr(ScreenOCR, "extract", lambda *args, **kwargs: called.append(1))
    instance = ChunkScreenOCR()
    with pytest.raises(ScreenOCRError, match="hủy"):
        instance.extract(Path("unused"), 0, 1, cancel_check=lambda: True)
    assert called == [] and instance._engine is None
    assert admission.active == 0 and admission.acquisitions == admission.releases == 1


def test_cooperative_cancellation_during_inference_cleans_model_and_preserves_error(admission, monkeypatch):
    cancelled = threading.Event()
    started = threading.Event()
    instance = ChunkScreenOCR()
    def infer(self, *args, cancel_check=None, **kwargs):
        self._engine = object()
        started.set()
        assert cancelled.wait(2)
        self._check_cancelled(cancel_check)
        pytest.fail("cancelled inference cannot return successful OCR")
    monkeypatch.setattr(ScreenOCR, "extract", infer)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(instance.extract, Path("unused"), 0, 1,
                                 cancel_check=cancelled.is_set)
        try:
            assert started.wait(2)
        finally:
            cancelled.set()
        with pytest.raises(ScreenOCRError, match="hủy"):
            future.result(timeout=2)
    assert instance._engine is None
    assert admission.active == 0 and admission.acquisitions == admission.releases == 1


def test_instance_can_be_reused_after_error_without_reusing_released_engine(admission, monkeypatch):
    instance = ChunkScreenOCR()
    allocated = []
    def infer(self, *args, **kwargs):
        assert self._engine is None
        self._engine = object()
        allocated.append(self._engine)
        if len(allocated) == 1:
            raise ScreenOCRError("retry this chunk")
        return [{"text_zh": "恢复"}]
    monkeypatch.setattr(ScreenOCR, "extract", infer)
    with pytest.raises(ScreenOCRError, match="retry"):
        instance.extract(Path("unused"), 0, 1)
    assert instance.extract(Path("unused"), 0, 1) == [{"text_zh": "恢复"}]
    assert allocated[0] is not allocated[1]
    assert instance._engine is None
    assert admission.active == 0 and admission.acquisitions == admission.releases == 2
