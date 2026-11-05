"""Bounded recovery fault injection; no provider requests or live-project writes."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from scripts import recovery_runner as runner
from core.engines.translation.opencode_client import OpenCodeRequestError, OpenCodeTimeoutError
from core.translation_review import AutomaticTranslationReviewer


@pytest.fixture
def recovery(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "AUDIT", tmp_path / "audit")
    source = tmp_path / "source.mp4"
    source.write_bytes(b"fixture")
    old = tmp_path / "accepted.wav"
    old.write_bytes(b"old accepted audio")
    rows = {sid: SimpleNamespace(id=sid, start=float(sid), final_vi="Lời đã giữ." if sid else "",
        confirmed_silence=False, verification={"status": "verified", "review_gate_revision": AutomaticTranslationReviewer.REVIEW_GATE_REVISION} if sid else {"status": "unresolved"},
        needs_review=sid == 0, audio_path=str(old) if sid else None,
        status="READY", failed_stage=None) for sid in range(5)}
    session = SimpleNamespace(task_id="fixture", segments=rows, video_path=source,
        aligner=SimpleNamespace(min_speed=.85, max_speed=1.35),
        is_running=False, is_editing=False, is_stopped=False, error=None,
        auto_export_result=True, _ensure_tts_engine=Mock(), pause_event=asyncio.Event(),
        review_summary={"status": "failed"}, VISUAL_REVIEW_GROUP_SIZE=1,
        _persist_if_enabled=Mock(), _refresh_review_counts=Mock(),
        _resume_speech_rows=AsyncMock(return_value=[]), _resume_deferred_speech_rows=AsyncMock())
    monitor = SimpleNamespace(session=session, total=0, done=0, failures=[],
        latest_error=None, state="RECOVERING", snapshot=Mock())
    return monitor, old


@pytest.mark.parametrize('updated_source', ['我是你女儿小满', '你闭嘴。', None])
def test_recovery_rechecks_accepted_speech_after_cited_source_changes(recovery, updated_source):
    monitor, old = recovery
    session = monitor.session
    for row in session.segments.values():
        row.text_zh = '未改动'
    row = session.segments[1]
    row.verification.update(address_context_sources={'2': '你闭嘴'})
    if updated_source is None:
        del session.segments[2]
    else:
        session.segments[2].text_zh = updated_source
    before = {sid: dict(value.verification) for sid, value in session.segments.items()}
    assert 1 in runner.recover_ids(session)
    assert session.segments[1].audio_path == str(old) and old.read_bytes() == b'old accepted audio'
    assert {sid: value.verification for sid, value in session.segments.items()} == before


def test_recovery_retains_matching_context_and_manual_ownership(recovery):
    monitor, old = recovery
    session = monitor.session
    for row in session.segments.values():
        row.text_zh = '未改动'
    session.segments[1].verification.update(address_context_sources={'2': '未改动'})
    session.segments[3].verification.update(status='manual', address_context_sources={'2': '旧来源'})
    assert runner.recover_ids(session) == [0]
    assert session.segments[1].audio_path == str(old)


def test_targeted_recovery_reviews_stale_accepted_row_once_and_keeps_other_audio(recovery):
    monitor, old = recovery
    session = monitor.session
    for row in session.segments.values():
        row.text_zh = '现有来源'
    session.segments[1].verification.update(address_context_sources={'2': '旧来源'})
    session._review_translations = AsyncMock()
    assert asyncio.run(runner.recover_short(monitor, review_ids=[1])) is False
    session._review_translations.assert_awaited_once()
    assert session._review_translations.await_args.kwargs['segment_ids'] == {1}
    assert all(row.audio_path == str(old) for sid, row in session.segments.items() if sid)
    assert old.read_bytes() == b'old accepted audio'


def test_review_failure_preserves_accepted_audio_and_never_exports_or_retries_same_group(recovery):
    monitor, old = recovery
    session = monitor.session
    session._review_translations = AsyncMock(side_effect=ValueError("invalid citation"))
    assert asyncio.run(runner.recover_short(monitor)) is False
    assert old.read_bytes() == b"old accepted audio"
    assert all(row.audio_path == str(old) for sid, row in session.segments.items() if sid)
    assert session._review_translations.await_count == 1
    assert not session.is_running and not session.auto_export_result
    assert session.review_summary["status"] == "incomplete"
    assert monitor.state == "BLOCKED_CONTENT_OR_SPEECH"
    assert 'traceback' in (runner.AUDIT / "events.jsonl").read_text(encoding="utf-8")


def test_rate_limit_checkpoints_and_stops_without_admitting_later_requests(recovery):
    monitor, old = recovery
    session = monitor.session
    session.segments[1].needs_review = True
    error = OpenCodeRequestError("Translation service HTTP 429", code="provider_rate_limited", retryable=True)
    error.retry_after = 120
    session._review_translations = AsyncMock(side_effect=error)
    assert asyncio.run(runner.recover_short(monitor)) is False
    assert session._review_translations.await_count == 1
    assert monitor.state == "WAITING_FOR_SERVICE"
    assert monitor.latest_error["service"] == "translation_review"
    assert monitor.latest_error["retry_after"] == 120
    assert session._persist_if_enabled.called and old.exists()


@pytest.mark.parametrize("wrapped", [False, True])
def test_owned_provider_timeout_is_distinct_from_rate_limit(wrapped):
    timeout = OpenCodeTimeoutError("Owned CLI deadline exceeded")
    error = timeout
    if wrapped:
        error = OpenCodeRequestError("Review failed")
        error.__cause__ = timeout
    classified = runner.classify_error(error)
    assert classified["service"] == "translation_review"
    assert classified["code"] == "provider_timeout"
    assert classified["waiting"] is False
    assert classified["retry_after"] is None


def test_rate_limit_saved_by_second_pass_does_not_admit_more_requests(recovery):
    monitor, old = recovery
    session = monitor.session
    session.segments[1].needs_review = True
    async def review(**options):
        session.segments[0].verification = {"status": "incomplete", "diagnostic": {
            "code": "provider_rate_limited", "stage": "semantic_second_pass", "retry_after": 120}}
    session._review_translations = AsyncMock(side_effect=review)
    assert asyncio.run(runner.recover_short(monitor)) is False
    assert monitor.state == "WAITING_FOR_SERVICE"
    assert monitor.latest_error["retry_after"] == 120
    assert session._review_translations.await_count == 1
    session._resume_speech_rows.assert_not_awaited()
    assert session._persist_if_enabled.called and old.exists()


def test_validation_failure_does_not_stop_independent_group_and_new_speech(recovery):
    monitor, old = recovery
    session = monitor.session
    session.segments[1].needs_review = True
    session.segments[1].audio_path = None
    async def review(**options):
        if options["segment_ids"] == {0}:
            raise ValueError("bad citation")
        session.segments[1].needs_review = False
    async def speech(rows, **options):
        for row in rows:
            if row.status == "WAITING":
                row.audio_path = str(old)
                row.status = "READY"
        return []
    session._review_translations = AsyncMock(side_effect=review)
    session._resume_speech_rows = AsyncMock(side_effect=speech)
    assert asyncio.run(runner.recover_short(monitor)) is False
    assert session._review_translations.await_count == 2
    assert session.segments[1].audio_path == str(old)
    assert monitor.done == 1 and old.read_bytes() == b"old accepted audio"


def test_verified_audio_needs_no_provider_request(recovery):
    monitor, old = recovery
    session = monitor.session
    session.segments[0].needs_review = False
    session.segments[0].final_vi = "Đủ lời."
    session.segments[0].verification = {"status": "verified", "review_gate_revision": AutomaticTranslationReviewer.REVIEW_GATE_REVISION}
    session.segments[0].audio_path = str(old)
    session._review_translations = AsyncMock()
    assert asyncio.run(runner.recover_short(monitor)) is True
    session._review_translations.assert_not_awaited()


def test_recovery_configures_qa_cadence_before_any_review_or_speech(recovery):
    monitor, old = recovery
    session = monitor.session
    async def review(**options):
        assert session.aligner.min_speed == .90
        assert session.aligner.max_speed == 1.15
        assert session._recovery_bidirectional_reflow is True
    async def speech(rows, **options):
        assert session.aligner.min_speed == .90
        assert session.aligner.max_speed == 1.15
        return []
    session._review_translations = AsyncMock(side_effect=review)
    session._resume_speech_rows = AsyncMock(side_effect=speech)
    assert asyncio.run(runner.recover_short(monitor)) is False
    assert old.read_bytes() == b'old accepted audio'


@pytest.mark.parametrize('speed,invalid', [(1.15, False), (1.2, True), (1.35, True), (.85, True), (.9, False)])
def test_qa_cadence_selects_only_wavs_that_exceed_the_acceptance_contract(recovery, speed, invalid):
    monitor, old = recovery
    row = monitor.session.segments[1]
    row.speed_ratio = speed
    selected = runner.speech_repair_ids(monitor.session)
    assert (1 in selected) is invalid
    assert old.read_bytes() == b'old accepted audio'


def test_targeted_review_does_not_repeat_other_rows_or_hide_unresolved_content(recovery):
    monitor, old = recovery
    session = monitor.session
    session.segments[1].needs_review = True
    session._review_translations = AsyncMock()
    assert asyncio.run(runner.recover_short(monitor, review_ids=[1])) is False
    assert session._review_translations.await_count == 1
    assert session._review_translations.await_args.kwargs['segment_ids'] == {1}
    assert session.segments[0].needs_review
    assert monitor.state == 'BLOCKED_CONTENT_OR_SPEECH'
    assert old.read_bytes() == b'old accepted audio'


def test_targeted_source_review_does_not_retry_unrelated_rejected_pacing(recovery):
    monitor, old = recovery
    session = monitor.session
    chosen, rejected, healthy = session.segments[0], session.segments[1], session.segments[2]
    chosen.final_vi = 'Lời nguồn mới được đối chiếu.'
    chosen.status = 'FAILED'
    rejected.status, rejected.failed_stage, rejected.audio_path = 'FAILED', 'ALIGNING', None
    rejected.error = 'No new pacing evidence.'
    healthy.speed_ratio = 1.4
    session._review_translations = AsyncMock()
    def speech(rows, **options):
        ids = [row.id for row in rows]
        assert set(ids).issubset({0})
        chosen.audio_path, chosen.status = str(old), 'READY'
        return []
    session._resume_speech_rows = AsyncMock(side_effect=speech)
    assert asyncio.run(runner.recover_short(monitor, review_ids=[0])) is False
    assert rejected.status == 'FAILED' and rejected.audio_path is None
    assert rejected.error == 'No new pacing evidence.'
    assert healthy.status == 'READY' and healthy.audio_path == str(old)
    assert chosen.needs_review and monitor.state == 'BLOCKED_CONTENT_OR_SPEECH'


@pytest.mark.parametrize('ids', [[99], [0, 0], [True]])
def test_targeted_recovery_rejects_unknown_duplicate_or_boolean_ids_before_writes(recovery, ids):
    monitor, old = recovery
    session = monitor.session
    session._review_translations = AsyncMock()
    with pytest.raises(ValueError, match='existing segment IDs'):
        asyncio.run(runner.recover_short(monitor, review_ids=ids))
    session._review_translations.assert_not_awaited()
    session._persist_if_enabled.assert_not_called()
    assert not monitor.session.is_running


def test_speech_only_recovery_never_reviews_or_accepts_unresolved_content(recovery):
    monitor, old = recovery
    session = monitor.session
    session._review_translations = AsyncMock()
    assert asyncio.run(runner.recover_short(monitor, review_ids=[])) is False
    session._review_translations.assert_not_awaited()
    assert session.segments[0].needs_review
    assert monitor.total == 0


def test_targeted_draft_speech_repair_never_clears_content_or_repeats_review(recovery):
    monitor, old = recovery
    row = monitor.session.segments[0]
    row.final_vi = 'Bản nháp có lời nguồn.'
    row.status, row.failed_stage = 'FAILED', 'TTS'
    monitor.session._review_translations = AsyncMock()
    async def speech(rows, **options):
        for candidate in rows:
            if candidate.id == 0:
                assert candidate.status == 'WAITING'
                candidate.audio_path, candidate.status = str(old), 'READY'
        return []
    monitor.session._resume_speech_rows = AsyncMock(side_effect=speech)
    assert asyncio.run(runner.recover_short(monitor, review_ids=[], repair_speech_ids=[0])) is False
    assert row.needs_review and row.verification['status'] == 'unresolved'
    monitor.session._review_translations.assert_not_awaited()


def test_speech_scope_does_not_repeat_unrelated_failed_pacing_or_queue_healthy_rows(recovery):
    monitor, old = recovery
    session = monitor.session
    selected, failed = session.segments[0], session.segments[1]
    selected.final_vi = 'Bản nháp đủ thời lượng.'
    selected.status = 'FAILED'
    failed.audio_path, failed.status, failed.failed_stage = None, 'FAILED', 'ALIGNING'
    failed.error = 'Previously rejected pacing; no new evidence.'
    healthy = session.segments[2]
    healthy.speed_ratio = 1.4  # Outside the scope even if legacy repair selects it.
    async def speech(rows, **options):
        candidates = list(rows)
        assert [row.id for row in candidates] == [0]
        selected.audio_path, selected.status = str(old), 'READY'
        return []
    session._resume_speech_rows = AsyncMock(side_effect=speech)
    session._review_translations = AsyncMock()
    assert asyncio.run(runner.recover_short(monitor, review_ids=[],
        repair_speech_ids=[0], speech_scope_ids=[0])) is False
    assert failed.status == 'FAILED' and failed.audio_path is None
    assert failed.error == 'Previously rejected pacing; no new evidence.'
    assert healthy.status == 'READY' and healthy.audio_path == str(old)
    assert selected.needs_review and selected.verification['status'] == 'unresolved'
    session._review_translations.assert_not_awaited()
    session._resume_speech_rows.assert_awaited_once()


@pytest.mark.parametrize('scope,review,repair', [([99], [], []), ([True], [], []),
    ([1, 1], [], []), ([], [], []), ([0], [], []), ([1], None, []), ([1], [], [2])])
def test_invalid_speech_scope_fails_before_protecting_or_mutating_checkpoint(recovery, scope, review, repair):
    monitor, old = recovery
    with pytest.raises(ValueError, match='Speech scope'):
        asyncio.run(runner.recover_short(monitor, review_ids=review,
            repair_speech_ids=repair, speech_scope_ids=scope))
    monitor.session._persist_if_enabled.assert_not_called()
    monitor.session._ensure_tts_engine.assert_not_called()
    assert not runner.AUDIT.exists()
    assert old.read_bytes() == b'old accepted audio'


def test_supervisor_preserves_exact_speech_scope_without_crash_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, 'AUDIT', tmp_path)
    launched = []
    def launch(command, **options):
        launched.append(command)
        return SimpleNamespace(pid=123, wait=lambda: 20)
    monkeypatch.setattr(runner.subprocess, 'Popen', launch)
    args = SimpleNamespace(lifecycle_probe=str(tmp_path), restart_probe=False,
        speech_only=True, repair_speech_ids=[27, 40], speech_scope_ids=[27, 40])
    assert runner.supervisor(args) == 20
    assert len(launched) == 1
    command = launched[0]
    assert '--speech-only' in command
    assert command[command.index('--speech-scope-ids') + 1] == '27,40'
    assert command[command.index('--repair-speech-ids') + 1] == '27,40'


@pytest.mark.parametrize('ids', [[99], [0, 0], [True], [0]])
def test_invalid_speech_repair_selection_fails_before_writes(recovery, ids):
    monitor, old = recovery
    with pytest.raises(ValueError, match='Speech repair IDs'):
        asyncio.run(runner.recover_short(monitor, review_ids=[], repair_speech_ids=ids))
    monitor.session._persist_if_enabled.assert_not_called()
    assert not monitor.session.is_running


@pytest.mark.parametrize('value', ['', '0,0', '-1', '0,x'])
def test_cli_target_parser_rejects_invalid_values(value):
    import argparse
    with pytest.raises(argparse.ArgumentTypeError):
        runner.parse_segment_ids(value)


def test_owner_lock_rejects_duplicate_and_releases_after_exit(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "AUDIT", tmp_path)
    with runner.Ownership():
        with pytest.raises(RuntimeError, match="already owns"):
            with runner.Ownership():
                pass
    with runner.Ownership():
        pass


def test_long_job_is_never_admitted_before_short_qa_pass(recovery):
    monitor, unused = recovery
    with pytest.raises(RuntimeError, match="actual short-video QA PASS"):
        asyncio.run(runner.run_long(monitor, short_pass=False))
    assert monitor.session.task_id == "fixture"


def test_overfast_legacy_wav_is_archived_and_only_that_row_is_repaired(recovery):
    monitor, old = recovery
    session = monitor.session
    session.segments[0].needs_review = False
    session.segments[0].final_vi = "Câu đã được kiểm tra."
    session.segments[0].verification = {"status": "verified", "review_gate_revision": AutomaticTranslationReviewer.REVIEW_GATE_REVISION}
    session.segments[0].audio_path = str(old)
    session.segments[0].speed_ratio = 1.4
    session._review_translations = AsyncMock()
    generated = old.with_name("replacement.wav")
    async def speech(rows, **options):
        for row in rows:
            if row.status == "WAITING":
                assert row.id == 0
                generated.write_bytes(b"new complete speech")
                row.audio_path, row.status, row.speed_ratio = str(generated), "READY", 1.0
        return []
    session._resume_speech_rows = AsyncMock(side_effect=speech)
    assert asyncio.run(runner.recover_short(monitor)) is True
    session._review_translations.assert_not_awaited()
    backups = list((runner.AUDIT / "backups/audio").glob("*.wav"))
    assert len(backups) == len(session.segments)
    assert all(path.read_bytes() == b"old accepted audio" for path in backups)
    assert old.read_bytes() == b"old accepted audio"
    assert session.segments[1].audio_path == str(old)


def test_review_that_retires_previous_audio_keeps_a_recoverable_backup(recovery):
    import json
    monitor, old = recovery
    session = monitor.session
    session.segments[1].needs_review = True
    async def review(**options):
        # Production publication may delete a superseded WAV after a changed
        # translation. Verify that its bytes were protected before this call.
        inventory = json.loads((runner.AUDIT / "backups/fixture_audio_inventory.json").read_text(encoding="utf-8"))
        protected = next(item for item in inventory if item["segment_id"] == 1)
        assert runner.Path(protected["backup"]).read_bytes() == b"old accepted audio"
        old.unlink(missing_ok=True)
        for row in session.segments.values():
            row.audio_path = None
        session.segments[1].needs_review = False
    session._review_translations = AsyncMock(side_effect=review)
    assert asyncio.run(runner.recover_short(monitor)) is False
    assert not old.exists()
    backups = list((runner.AUDIT / "backups/audio").glob("*.wav"))
    assert len(backups) == 4
    assert all(path.read_bytes() == b"old accepted audio" for path in backups)


def test_unverified_backup_stops_before_review_or_synthesis(recovery, monkeypatch):
    monitor, old = recovery
    session = monitor.session
    session._review_translations = AsyncMock()
    def corrupt_copy(source, destination):
        runner.Path(destination).write_bytes(b"incomplete backup")
    monkeypatch.setattr(runner.shutil, "copy2", corrupt_copy)
    with pytest.raises(RuntimeError, match="Cannot protect"):
        asyncio.run(runner.recover_short(monitor))
    session._review_translations.assert_not_awaited()
    session._resume_speech_rows.assert_not_awaited()
    session._ensure_tts_engine.assert_not_called()
    assert old.read_bytes() == b"old accepted audio"
    assert not session.is_running


def test_monitor_reports_measured_segment_states_without_claiming_total_completion(recovery):
    import json
    source, old = recovery
    session = source.session
    session._reviewing_segment_ids = [0]
    session.segments[1].status = "TTS"
    session.get_progress = Mock(return_value={"progress_pct": 100,
        "segment_states": {"COMPLETED": 2, "FAILED": 1, "RETRY_PENDING": 1}})
    monitor = runner.Monitor(session, 12345)
    monitor.total, monitor.done = 198, 0
    snapshot = monitor.snapshot()
    assert snapshot["completed"] == 2 and snapshot["available_audio_segments"] == 4
    assert snapshot["failed"] == 1 and snapshot["retrying"] == 1
    assert snapshot["pending_review_targets"] == 198
    assert snapshot["current_segments"] == [0, 1]
    assert snapshot["backend_stage_percent"] == 100 and snapshot["overall_percent"] is None
    assert json.loads((runner.AUDIT / "progress.json").read_text(encoding="utf-8")) == snapshot


def test_monitor_surfaces_retained_second_pass_failure_without_raw_provider_body(recovery):
    source, old = recovery
    session = source.session
    session.segments[0].verification = {'status': 'incomplete', 'diagnostic': {
        'code': 'provider_failed', 'stage': 'audio_semantic_review',
        'detail': 'private-body-do-not-copy', 'api_key': 'private-key-do-not-copy'}}
    session.get_progress = Mock(return_value={})
    monitor = runner.Monitor(session, 12345)
    snapshot = monitor.snapshot()
    assert snapshot['latest_error']['code'] == 'provider_failed'
    assert snapshot['latest_error']['service'] == 'translation_review'
    assert snapshot['unresolved_runtime_diagnostics'][0]['segment_id'] == 0
    assert 'private-body-do-not-copy' not in str(snapshot)
    assert 'private-key-do-not-copy' not in str(snapshot)
    session.segments[0].needs_review = False
    assert monitor.snapshot()['latest_error'] is None


def test_multiple_historical_diagnostics_do_not_invent_error_order(recovery):
    source, old = recovery
    for sid in (0, 1):
        row = source.session.segments[sid]
        row.needs_review = True
        row.verification = {'diagnostic': {'code': 'asr_timeout', 'stage': 'audio_evidence'}}
    source.session.get_progress = Mock(return_value={})
    snapshot = runner.Monitor(source.session, 12345).snapshot()
    assert snapshot['latest_error']['code'] == 'multiple_unresolved_runtime_diagnostics'
    assert snapshot['latest_error']['basis'] == 'checkpoint_has_no_error_order'
    assert all(item['service'] == 'local_asr' for item in snapshot['unresolved_runtime_diagnostics'])


def test_monitor_reports_new_speech_failure_after_historical_review_failure_without_private_body(recovery):
    source, old = recovery
    session = source.session
    session.get_progress = Mock(return_value={})
    session.segments[0].verification = {'diagnostic': {'code': 'provider_failed', 'stage': 'audio_semantic_review'}}
    monitor = runner.Monitor(session, 12345)
    assert monitor.snapshot()['latest_error']['segment_id'] == 0
    row = session.segments[1]
    row.status, row.failed_stage, row.error = 'FAILED', 'ALIGNING', 'private-provider-response'
    snapshot = monitor.snapshot()
    assert snapshot['latest_error']['segment_id'] == 1
    assert snapshot['latest_error']['service'] == 'speech_recovery'
    assert snapshot['latest_error']['code'] == 'speech_stage_failed'
    assert 'private-provider-response' not in str(snapshot)
    assert monitor.snapshot()['latest_error'] == snapshot['latest_error']


def test_monitor_new_audio_speed_uses_actual_file_changes_not_elapsed_time_alone(recovery, monkeypatch):
    source, old = recovery
    session = source.session
    session.get_progress = Mock(return_value={})
    monitor = runner.Monitor(session, 12345)
    monkeypatch.setattr(runner.time, 'monotonic', lambda: monitor.started + 10.)
    assert monitor.snapshot()['speech_segments_per_second'] is None
    row = session.segments[0]
    row.audio_path = str(old)
    snapshot = monitor.snapshot()
    assert snapshot['new_audio_segments_this_run'] == 1
    assert snapshot['speech_segments_per_second'] == .1
    row.audio_path = str(old.with_name('missing.wav'))
    assert monitor.snapshot()['new_audio_segments_this_run'] == 0


def test_crash_budget_records_terminal_state_instead_of_a_stale_running_pid(tmp_path, monkeypatch):
    import json
    monkeypatch.setattr(runner, "AUDIT", tmp_path)
    monkeypatch.setattr(runner.time, "sleep", lambda seconds: None)
    attempts = []
    def crash(*args, **options):
        attempts.append(args)
        return SimpleNamespace(pid=100 + len(attempts), wait=lambda: 1)
    monkeypatch.setattr(runner.subprocess, "Popen", crash)
    args = SimpleNamespace(lifecycle_probe=str(tmp_path), restart_probe=False,
                           short_perceptual_evidence=None, long_perceptual_evidence=None)
    assert runner.supervisor(args) == 21
    assert len(attempts) == 3
    state = json.loads((tmp_path / "supervisor.json").read_text(encoding="utf-8"))
    assert state["state"] == "BLOCKED_CRASH_LIMIT" and state["attempts"] == 3
    assert "worker_pid" not in state


def test_explicit_stop_returns_expected_wait_exit_without_crash_restart(recovery, monkeypatch):
    import json
    import uvicorn
    import core.streaming.session_store as store
    from core.services.service_manager import ServiceManager
    monitor, old = recovery
    monitor.short_pass = False
    session = monitor.session
    session._release_runtime = Mock()
    monkeypatch.setattr(store, "restore_saved_session", lambda *args, **kwargs: session)
    monkeypatch.setattr(ServiceManager, "find_free_port", lambda: 12345)
    monkeypatch.setattr(runner, "Monitor", lambda *args: monitor)
    monitor.run = AsyncMock()
    server = SimpleNamespace(should_exit=False)
    async def serve():
        while not server.should_exit:
            await asyncio.sleep(.01)
    server.serve = serve
    monkeypatch.setattr(uvicorn, "Server", lambda config: server)
    monkeypatch.setattr(runner, "recover_short", AsyncMock(side_effect=asyncio.CancelledError))
    args = SimpleNamespace(restart_probe=False, short_perceptual_evidence=None,
                           long_perceptual_evidence=None)
    assert asyncio.run(runner.worker(args)) == 20
    assert monitor.state == "STOPPED"
    session._release_runtime.assert_called_once()
    events = [json.loads(line) for line in (runner.AUDIT / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(row["event"] == "RECOVERY_STOPPED" for row in events)


def test_real_supervisor_survives_launcher_exit_and_restores_copied_checkpoint(tmp_path):
    import hashlib
    import json
    import os
    import subprocess
    import sys
    import time
    source = runner.ROOT / "workspace/audit/recovery_20261010/backups/projects" / (runner.SHORT_ID + ".json")
    if not source.exists():
        pytest.skip("A saved real-checkpoint copy is required for this local lifecycle acceptance")
    copied = tmp_path / "checkpoint.json"
    copied.write_bytes(source.read_bytes())
    digest = hashlib.sha256(copied.read_bytes()).hexdigest()
    script = str(runner.ROOT / "scripts/recovery_runner.py")
    # The launcher exits before its detached supervisor does any useful work.
    launcher = 'import subprocess,sys; p=subprocess.Popen([sys.executable,"-B",sys.argv[1],"--lifecycle-probe",sys.argv[2]],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,"DETACHED_PROCESS",0)|getattr(subprocess,"CREATE_NEW_PROCESS_GROUP",0)); print(p.pid)'
    launched = subprocess.run([sys.executable, "-B", "-c", launcher, script, str(tmp_path)],
        capture_output=True, text=True, timeout=10, cwd=runner.ROOT)
    assert launched.returncode == 0
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            if (tmp_path / "supervisor.json").exists():
                state = json.loads((tmp_path / "supervisor.json").read_text(encoding="utf-8"))
                if state["state"] == "COMPLETED":
                    break
        except (OSError, json.JSONDecodeError):
            # Windows readers can briefly collide with atomic replacement;
            # absence of one sample must not be interpreted as a dead worker.
            pass
        time.sleep(.1)
    else:
        pytest.fail("Detached checkpoint probe did not complete within 30 seconds")
    events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    restored = next(row for row in events if row["event"] == "PROBE_CHECKPOINT_RESTORED")
    assert restored["sha256"] == digest and restored["segment_count"] == 198
    assert restored["pid"] != restored["previous_pid"]
    assert sum(row["event"] == "WORKER_EXITED" for row in events) == 2
    progress = json.loads((tmp_path / "probe_progress.json").read_text(encoding="utf-8"))
    assert progress["sample"] == 10 and progress["video_qa_pass"] is False
    assert hashlib.sha256(copied.read_bytes()).hexdigest() == digest
def test_atomic_status_write_retries_transient_windows_reader_lock(tmp_path, monkeypatch):
    import scripts.recovery_runner as runner
    import json
    target = tmp_path / "progress.json"
    target.write_text('{"state":"old"}', encoding="utf-8")
    original_replace = runner.os.replace
    attempts = []
    def locked_replace(source, destination):
        attempts.append(source)
        if len(attempts) <= 2:
            assert json.loads(target.read_text()) == {"state": "old"}
            error = PermissionError("Windows reader denied delete-sharing")
            error.winerror = 32
            raise error
        original_replace(source, destination)
    monkeypatch.setattr(runner.os, "replace", locked_replace)
    monkeypatch.setattr(runner.time, "sleep", lambda value: None)
    runner.write_json(target, {"state": "new"})
    assert len(attempts) == 3
    assert json.loads(target.read_text()) == {"state": "new"}
    assert not list(tmp_path.glob("*.tmp"))


def test_atomic_status_write_preserves_old_file_and_new_temporary_on_permanent_lock(tmp_path, monkeypatch):
    import scripts.recovery_runner as runner
    import json
    import pytest
    target = tmp_path / "progress.json"
    target.write_text('{"state":"old"}', encoding="utf-8")
    attempts = []
    def locked_replace(source, destination):
        attempts.append(source)
        error = PermissionError("Windows reader denied delete-sharing")
        error.winerror = 5
        raise error
    monkeypatch.setattr(runner.os, "replace", locked_replace)
    monkeypatch.setattr(runner.time, "sleep", lambda value: None)
    with pytest.raises(PermissionError):
        runner.write_json(target, {"state": "new"})
    assert len(attempts) == 7
    assert json.loads(target.read_text()) == {"state": "old"}
    assert json.loads(attempts[-1].read_text()) == {"state": "new"}
