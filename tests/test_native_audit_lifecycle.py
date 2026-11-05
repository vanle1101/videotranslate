import asyncio
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock
import main

from core.engines.translation.opencode_client import OpenCodeTimeoutError
from core.multilingual_translation import VERSION
from core.streaming.native_audit_retry import make_pending_audit, pending_native_audit
from core.streaming import native_audit_lifecycle as lifecycle
from core.streaming.session_store import restore_saved_session
from test_session_store import persisted


def install(session, deadline=0):
    row = session.segments[0]
    row.source_language = 'ko'
    row.literal_vi = row.natural_vi = row.final_vi
    draft = row.to_dict()
    draft.update(needs_review=True, review_reason='Pending')
    marker = make_pending_audit(draft, draft, [], version=VERSION, model='test',
                                error=OpenCodeTimeoutError('timeout'), now=0)
    marker['next_attempt_at'] = deadline
    row.verification = {'status':'unresolved', 'source_supported':False,
                        'multilingual_review':{'pending_audit':marker}}
    row.needs_review = True
    session.run_intent = 'running'
    session.is_stopped = session.is_running = False
    return row


def success(rows, **kwargs):
    row = deepcopy(rows[0])
    row['verification'] = {'status':'unresolved', 'source_supported':False,
                           'semantic_verified':True, 'multilingual_review':{'semantic_verified':True}}
    row['needs_review'], row['review_reason'] = True, 'Source still unresolved'
    return {'segments':{row['id']:row}}


def test_pending_actual_manifest_roundtrip_and_draft_export_gate(persisted):
    row = install(persisted, 1300)
    marker = pending_native_audit(row)
    persisted.persist()
    restored = restore_saved_session(persisted.task_id)
    assert pending_native_audit(restored.segments[0]) == marker
    assert restored.run_intent == 'running'
    assert not restored._final_output_metadata()['final_output_blocked']


def test_success_keeps_wav_and_source_uncertainty_with_single_owner(persisted, monkeypatch):
    row = install(persisted)
    audio = Path(row.audio_path).read_bytes()
    monkeypatch.setattr(lifecycle, 'audit_existing_native_rows', success)
    async def run():
        task = lifecycle.ensure_native_audit_retry(persisted)
        assert lifecycle.ensure_native_audit_retry(persisted) is task
        await task
    asyncio.run(run())
    assert Path(row.audio_path).read_bytes() == audio and row.status == 'READY'
    assert row.verification['source_supported'] is False and row.needs_review
    assert not lifecycle.has_pending(persisted) and persisted.run_intent == 'idle'


def test_user_edit_wins_over_inflight_audit(persisted, monkeypatch):
    row = install(persisted)
    async def work(function, rows, **kwargs):
        row.final_vi = 'Lời sửa tay.'
        row.revision += 1
        row.verification = {'status':'manual'}
        return success(rows)
    monkeypatch.setattr(persisted, '_run_blocking', work)
    asyncio.run(lifecycle._run(persisted))
    assert row.final_vi == 'Lời sửa tay.' and row.verification == {'status':'manual'}


def test_pause_stop_and_shutdown_preserve_pending_deadline(persisted, monkeypatch):
    row = install(persisted, 1300)
    now = [1000.]
    monkeypatch.setattr(lifecycle.time, 'time', lambda:now[0])
    provider = Mock(side_effect=AssertionError('No early request'))
    monkeypatch.setattr(lifecycle, 'audit_existing_native_rows', provider)
    persisted.is_paused = True
    persisted.run_intent = 'paused'
    persisted.pause_event.clear()
    async def sleep(delay):
        now[0] += delay
        persisted.is_stopped = True
    monkeypatch.setattr(lifecycle.asyncio, 'sleep', sleep)
    asyncio.run(lifecycle._run(persisted))
    assert pending_native_audit(row)['next_attempt_at'] == 1300
    provider.assert_not_called()


def test_startup_completed_media_resumes_audit_without_synthesis(persisted, monkeypatch):
    row = install(persisted)
    persisted.persist()
    monkeypatch.setattr(lifecycle, 'audit_existing_native_rows', success)
    from core.streaming.retry_lifecycle import resume_interrupted_sessions
    async def run():
        owners = await resume_interrupted_sessions()
        assert len(owners) == 1
        await asyncio.gather(*owners)
    asyncio.run(run())
    restored = restore_saved_session(persisted.task_id)
    assert not lifecycle.has_pending(restored)
    assert restored.segments[0].final_vi == row.final_vi


def test_repeated_pending_audits_respect_each_deadline(persisted, monkeypatch):
    row = install(persisted, 1001)
    now, calls = [1000.], []
    monkeypatch.setattr(lifecycle.time, 'time', lambda:now[0])
    async def sleep(delay): now[0] += delay
    monkeypatch.setattr(lifecycle.asyncio, 'sleep', sleep)
    async def work(function, rows, **kwargs):
        calls.append(now[0])
        if len(calls) == 8: return success(rows)
        result = deepcopy(rows[0])
        marker = result['verification']['multilingual_review']['pending_audit']
        marker['attempt'] += 1
        marker['next_attempt_at'] = now[0]+300
        return {'segments':{result['id']:result}}
    monkeypatch.setattr(persisted, '_run_blocking', work)
    asyncio.run(lifecycle._run(persisted))
    assert len(calls)==8 and calls[0]>=1001
    assert all(b-a>=300 for a,b in zip(calls,calls[1:]))
    assert row.status=='READY' and not lifecycle.has_pending(persisted)

def test_api_pause_resume_stop_controls_independent_audit(persisted, monkeypatch):
    from core.streaming.pipeline import active_streaming_sessions
    install(persisted, 1e12)
    async def run():
        active_streaming_sessions[persisted.task_id] = persisted
        owner = lifecycle.ensure_native_audit_retry(persisted)
        assert persisted.get_progress()['can_pause']
        await main.pause_task(persisted.task_id)
        assert persisted.get_progress()['can_resume']
        await main.resume_task(persisted.task_id)
        assert persisted.get_progress()['can_stop']
        await main.stop_task(persisted.task_id)
        assert owner.done() and persisted.run_intent == 'stopped'
    asyncio.run(run())

def test_source_replaced_between_restart_retires_pending_without_request(persisted, monkeypatch):
    row = install(persisted)
    persisted.persist()
    persisted.video_path.write_bytes(b'replacement-source')
    provider = Mock(side_effect=AssertionError('stale source'))
    monkeypatch.setattr(lifecycle, 'audit_existing_native_rows', provider)
    asyncio.run(lifecycle._run(persisted))
    provider.assert_not_called()
    assert not lifecycle.has_pending(persisted) and row.needs_review

def test_paused_restart_explicit_resume_preserves_remaining_wait(persisted, monkeypatch):
    from core.streaming.pipeline import active_streaming_sessions
    now = [1000.]
    monkeypatch.setattr(lifecycle.time, 'time', lambda:now[0])
    install(persisted, 1300)
    persisted.pause()
    persisted.shutdown()
    now[0] = 9000.
    restored = restore_saved_session(persisted.task_id)
    assert pending_native_audit(restored.segments[0])['remaining_seconds']==300
    provider = Mock(side_effect=AssertionError('cooldown not elapsed'))
    monkeypatch.setattr(lifecycle, 'audit_existing_native_rows', provider)
    async def run():
        active_streaming_sessions[restored.task_id] = restored
        await main.resume_task(restored.task_id)
        assert pending_native_audit(restored.segments[0])['next_attempt_at']==9300
        restored.stop()
        await asyncio.gather(restored.native_audit_task, return_exceptions=True)
    asyncio.run(run())
    provider.assert_not_called()


def test_model_changed_inflight_retires_verdict(persisted, monkeypatch):
    from config import settings
    row = install(persisted)
    async def work(function, rows, **kwargs):
        monkeypatch.setattr(settings, 'OPENCODE_MODEL', 'other-model')
        return success(rows)
    monkeypatch.setattr(persisted, '_run_blocking', work)
    asyncio.run(lifecycle._run(persisted))
    assert not lifecycle.has_pending(persisted)
    assert row.verification.get('semantic_verified') is not True
