"""A semantically unresolved draft is publishable only with complete media."""
from copy import deepcopy
from types import SimpleNamespace
import json
import logging
import threading

import pytest

from core.media_preview import PreviewManager
from core.streaming.export import HQExporter
from core.streaming.output_gate import final_output_metadata


def row(**changes):
    return {"id": 41, "start": 0., "end": 1., "final_vi": "Xin chào.",
            "text_zh": "你好", "audio_path": "voice.wav", "needs_review": True,
            "verification": {"status": "unresolved", "semantic_verified": False}, **changes}


@pytest.mark.parametrize("runtime_object", [False, True])
def test_full_media_with_unresolved_meaning_is_exportable_draft(runtime_object):
    item = row()
    before = deepcopy(item)
    result = final_output_metadata([SimpleNamespace(**item) if runtime_object else item],
                                   valid_audio=lambda item: True, source_complete=True, total_duration=2.)
    assert result["media_complete"] is True and result["final_output_blocked"] is False
    assert result["output_is_draft"] is True
    assert result["output_quality_label"] == "Bản nháp — cần kiểm tra"
    assert result["output_review_segment_ids"] == [41]
    assert result["content_review_state"] == "REVIEW_REQUIRED"
    assert item == before


def test_unverified_source_coverage_is_a_draft_even_without_materialized_review_rows():
    result = final_output_metadata([], valid_audio=lambda _item: True,
                                   source_complete=False, total_duration=2.)
    assert result["media_complete"] is False
    assert result["final_output_blocked"] is True
    assert result["output_is_draft"] is True
    assert result["source_coverage_unverified"] is True
    assert result["output_quality_label"] == "Bản nháp — source coverage chưa hoàn tất"


def test_legacy_editorial_metadata_never_claims_media_is_complete():
    result = final_output_metadata([row()])
    assert result["media_complete"] is None
    assert result["final_output_blocked"] is False


@pytest.mark.parametrize("cause", ["text", "audio", "source", "overlap", "outside", "partial", "invalid_duration"])
def test_each_media_gap_blocks_full_export_even_for_draft(cause):
    rows = [row()]
    if cause == "text":
        rows[0]["final_vi"] = ""
    elif cause == "overlap":
        rows.append(row(id=42, start=.9, end=1.8))
    elif cause == "outside":
        rows[0]["end"] = 2.1
    elif cause == "partial":
        rows[0]["omit_voice"] = True
    result = final_output_metadata(rows, valid_audio=lambda item: cause != "audio",
                                   source_complete=cause != "source",
                                   total_duration=float("nan") if cause == "invalid_duration" else 2.)
    assert result["media_complete"] is False and result["final_output_blocked"] is True
    assert result["output_is_draft"] is True


def test_stale_boolean_cannot_hide_explicit_unresolved_verification():
    result = final_output_metadata([row(needs_review=False)])
    assert result["output_is_draft"] and result["output_review_segment_ids"] == [41]


def test_missing_source_text_is_rejected_before_reusing_export_mix(tmp_path, monkeypatch):
    monkeypatch.setattr("core.streaming.export.settings.TEMP_DIR", tmp_path)
    monkeypatch.setattr("core.streaming.export.load_audio_cache",
                        lambda *args: pytest.fail("A media gap must fail before cache lookup"))
    with pytest.raises(ValueError, match="chưa có lời Việt"):
        HQExporter().export("missing-text", tmp_path / "source.mp4", [row(final_vi="")], 2.)


@pytest.mark.parametrize("duration", ["nan", "inf", "-inf", "0"])
def test_preview_rejects_invalid_source_duration_before_transcode(tmp_path, monkeypatch, duration):
    calls = []
    def media(command, **kwargs):
        calls.append(command)
        return json.dumps({"format": {"duration": duration}})
    monkeypatch.setattr("core.media_preview.run_media", media)
    manager = PreviewManager()
    job = {"output": tmp_path / "owned.webm", "cancel": threading.Event()}
    manager._convert(tmp_path / "source.mp4", job)
    assert job["status"] == "FAILED"
    assert len(calls) == 1


def test_preview_user_cancellation_is_not_logged_as_failure(tmp_path, monkeypatch, caplog):
    event = threading.Event()
    def media(*args, **kwargs):
        event.set()
        raise RuntimeError("cancelled")
    monkeypatch.setattr("core.media_preview.run_media", media)
    manager = PreviewManager()
    job = {"output": tmp_path / "owned.webm", "cancel": event}
    with caplog.at_level(logging.INFO, logger="app"):
        manager._convert(tmp_path / "source.mp4", job)
    assert job["status"] == "CANCELLED" and job["error_code"] == "cancelled"
    assert "cancelled by request" in caplog.text
    assert not any(record.levelno >= logging.WARNING for record in caplog.records)
