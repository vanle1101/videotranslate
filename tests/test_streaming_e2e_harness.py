from types import SimpleNamespace

from scripts.benchmark_streaming_e2e import _final_output_pending


def test_benchmark_waits_for_scheduled_export_after_source_warning():
    session = SimpleNamespace(
        automatic_output={"status": "running", "reason": "source_coverage_draft"},
        auto_export_task=SimpleNamespace(done=lambda: False),
        review_task=None,
    )

    assert _final_output_pending(session)


def test_benchmark_stops_after_automatic_output_is_exhausted():
    session = SimpleNamespace(
        automatic_output={"status": "blocked", "reason": "partial_output"},
        auto_export_task=None,
        review_task=None,
    )

    assert not _final_output_pending(session)
