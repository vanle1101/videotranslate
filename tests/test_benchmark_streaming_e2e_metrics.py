from scripts.benchmark_streaming_e2e import _summarize_request_intervals


def test_request_timing_separates_cumulative_latency_from_concurrent_wall_time():
    result = _summarize_request_intervals([
        {"started_elapsed_seconds": 0., "finished_elapsed_seconds": 2.},
        {"started_elapsed_seconds": 1., "finished_elapsed_seconds": 3.},
        {"started_elapsed_seconds": 3., "finished_elapsed_seconds": 4.},
    ])

    assert result == {
        "concurrent_wall_seconds": 4.,
        "peak_in_flight": 2,
        "mean_in_flight": 1.25,
    }
