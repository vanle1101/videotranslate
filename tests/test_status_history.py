from types import SimpleNamespace

import pytest

from core.streaming.status_history import (
    MAX_ENTRIES, record_snapshot, record_status, snapshot_metadata, validate_history,
)


def test_work_transitions_keep_wall_time_and_ignore_frame_and_sentence_noise():
    session = SimpleNamespace()
    assert record_status(session, "RUNNING", "tts", "Đang tạo giọng câu 1", timestamp=100)
    assert not record_status(session, "RUNNING", "tts", "Đang tạo giọng câu 2", timestamp=101)
    assert session.status_changed_at == 100
    assert record_status(session, "PAUSED", "tts", "Người dùng tạm dừng", timestamp=102)
    assert record_status(session, "RUNNING", "tts", "Tiếp tục", timestamp=103)
    assert record_status(session, "FAILED", "failed", "Không nhận được âm thanh", timestamp=104)
    assert not record_status(session, "FAILED", "failed", "Không nhận được âm thanh", timestamp=110)
    for _ in range(100):
        assert snapshot_metadata(session)["status_changed_at"] == 104
    assert len(session.status_history) == 4
    copy = snapshot_metadata(session)
    copy["status_history"][0]["message"] = "changed"
    assert session.status_history[0]["message"] != "changed"


def test_failed_event_retains_actual_causes_without_duplicates():
    session = SimpleNamespace(segments={
        0: SimpleNamespace(status="FAILED", error="OpenCode hết thời gian chờ"),
        1: SimpleNamespace(status="FAILED", error="OpenCode hết thời gian chờ"),
        2: SimpleNamespace(status="FAILED", error="Âm thanh dài hơn khoảng thoại"),
    })
    data = {"status": "FAILED", "phase": "failed", "stage": "3 câu chưa xong"}
    assert record_snapshot(session, data)
    assert session.status_history[-1]["message"].count("OpenCode hết thời gian chờ") == 1
    assert "Âm thanh dài hơn khoảng thoại" in session.status_history[-1]["message"]
    assert not record_snapshot(session, data)


def test_journal_is_bounded_and_rejects_invalid_timestamp():
    session = SimpleNamespace()
    for n in range(MAX_ENTRIES + 8):
        record_status(session, "RUNNING", str(n), "Đang xử lý", timestamp=100 + n)
    assert len(session.status_history) == MAX_ENTRIES
    assert session.status_history[0]["timestamp"] == 108
    for value in (True, float("nan"), -1):
        with pytest.raises(ValueError):
            record_status(session, "FAILED", "failed", "error", timestamp=value)
    validate_history(session.status_history, session.status_changed_at)
