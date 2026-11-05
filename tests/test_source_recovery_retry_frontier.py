from core.streaming.pipeline import SegmentItem, StreamingPipelineSession


def saved_gap_session(tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    session = StreamingPipelineSession("source-retry-frontier", source)
    session.initialized = True
    session._startup_failed = True
    session._chunked_source_started = True
    session.error = "Chưa nhận diện được lời nói tại 0.18–6.25s."
    session._source_coverage_intervals = [{"start": .18, "end": 6.252, "state": "unresolved",
        "missing_speech_spans": [[.18, 6.252]], "error_code": "source_speech_unrecognized"}]
    session._chunk_jobs = [{"id": "0-10", "start": 0.0, "end": 10.0, "state": "COMPLETED"}]
    session._source_gap_repair_history = [{"start": .18, "end": 6.252, "status": "uncertain", "attempts": 1}]
    return session


def test_repeated_continue_is_disabled_after_same_source_repair_was_uncertain(tmp_path):
    session = saved_gap_session(tmp_path)

    assert session._source_recovery_exhausted()
    assert not session.can_retry


def test_missing_source_repair_proof_remains_retryable(tmp_path):
    session = saved_gap_session(tmp_path)
    session._source_gap_repair_history = []

    assert not session._source_recovery_exhausted()
    assert session.can_retry


def test_unfinished_source_chunks_remain_retryable(tmp_path):
    session = saved_gap_session(tmp_path)
    session._chunk_jobs.append({"id": "10-12", "start": 10.0, "end": 12.0, "state": "RETRY_PENDING"})

    assert not session._source_recovery_exhausted()
    assert session.can_retry


def test_failed_tts_row_remains_retryable_after_source_repair_exhaustion(tmp_path):
    session = saved_gap_session(tmp_path)
    row = SegmentItem(0, 7.0, 8.0, 1.0)
    row.status = "FAILED"
    row.failed_stage = "TTS"
    session.segments[row.id] = row

    # The source repair is exhausted, but an independent failed speech stage
    # still has actionable retry work and must not be hidden.
    assert session.can_retry


def test_uncertain_source_status_names_the_draft_and_suppresses_continue(tmp_path):
    session = saved_gap_session(tmp_path)
    session.output_filename = "draft.mp4"
    session._source_missing_speech_spans = [[.18, 6.252]]
    session._source_prepared_seconds = .18
    session._source_scanned_seconds = 10.0
    session.total_duration = 10.0
    session.visual_translation = False
    session._visual_completed_seconds = 0.0
    session._visual_scanned_seconds = 10.0
    session._chunk_jobs = [{"id": "0-10", "start": 0.0, "end": 10.0, "state": "COMPLETED"}]

    progress = session.get_progress()

    assert progress["can_retry"] is False
    assert "không lặp lại cùng lượt ASR" in progress["stage"]
    assert "MP4 bản nháp vẫn được giữ" in progress["stage"]
