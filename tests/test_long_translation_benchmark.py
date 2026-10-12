"""Benchmark safety/planning contracts; no provider invocation or media writes."""
import pytest

from config import settings
from scripts import check_long_translation_concurrency as benchmark


def test_minute_ownership_extends_through_crossing_and_overlapping_utterances():
    rows = [{"id": 10, "start": 0.5, "end": 1.2},
            {"id": 11, "start": 59., "end": 61.},
            {"id": 12, "start": 60.5, "end": 62.},
            {"id": 13, "start": 80., "end": 82.}]
    chunks = benchmark.chunk_plan(rows, 130.)
    assert [(start, end) for start, end, _ in chunks] == [(0., 62.), (62., 122.), (122., 130.)]
    assert [[row["id"] for row in owned] for _, _, owned in chunks] == [[10, 11, 12], [13], []]


def test_capacity_uses_current_resource_function_and_restores_settings(monkeypatch):
    before = settings.TRANSLATION_CHUNK_WORKERS, settings.OPENCODE_CONCURRENCY
    observed = []
    def capacity(_):
        observed.append((settings.TRANSLATION_CHUNK_WORKERS, settings.OPENCODE_CONCURRENCY))
        return 1
    monkeypatch.setattr(benchmark.StreamingPipelineSession, "_parallel_chunk_capacity", capacity)
    assert benchmark.effective_capacity(20) == 1
    assert observed == [(20, 20)]
    assert (settings.TRANSLATION_CHUNK_WORKERS, settings.OPENCODE_CONCURRENCY) == before


def test_capacity_error_still_restores_settings(monkeypatch):
    before = settings.TRANSLATION_CHUNK_WORKERS, settings.OPENCODE_CONCURRENCY
    def fail(_):
        raise OSError("resource unavailable")
    monkeypatch.setattr(benchmark.StreamingPipelineSession, "_parallel_chunk_capacity", fail)
    with pytest.raises(OSError):
        benchmark.effective_capacity(20)
    assert (settings.TRANSLATION_CHUNK_WORKERS, settings.OPENCODE_CONCURRENCY) == before


def test_real_flag_required_before_reading_any_project_or_provider(monkeypatch):
    monkeypatch.setattr(benchmark.sys, "argv", ["benchmark"])
    monkeypatch.setattr(benchmark, "project_hashes", lambda: pytest.fail("must not read project without opt-in"))
    with pytest.raises(SystemExit) as error:
        benchmark.main()
    assert error.value.code == 2
