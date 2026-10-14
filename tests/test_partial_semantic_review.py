"""Offline independent review admission/publication; never advances real jobs."""
import asyncio
from copy import deepcopy
from pathlib import Path
import threading
from unittest.mock import Mock

import pytest

from config import settings
from core.streaming.pipeline import SegmentEditConflict, SegmentItem
from core.translation_review import AutomaticTranslationReviewer
from test_transcript_edit import session, client, wait_until


@pytest.fixture
def partial(session, monkeypatch):
    monkeypatch.setattr(settings, 'LLM_PROVIDER', 'opencode')
    session.video_path.write_bytes(b'isolated test source; injected reviewer, no real media claim')
    session.translation_mode = 'full'
    session.is_stopped = True
    session.error = 'Saved speech did not fit'
    session._restored_interrupted = True
    row = session.segments[0]
    row.needs_review = True
    row.verification = {'status':'unresolved', 'semantic_verified':False}
    failed = SegmentItem(1, 6, 7, 1)
    failed.text_zh, failed.final_vi = '失败', 'Câu chưa vừa'
    failed.status, failed.failed_stage, failed.error = 'FAILED', 'ALIGNING', 'Timing conflict'
    failed.verification = {'status':'verified', 'semantic_verified':True}
    session.segments[1] = failed
    manual = SegmentItem(2, 8, 11, 3)
    manual.text_zh, manual.final_vi = '手动', 'Người dùng đã sửa'
    manual.status, manual.verification = 'READY', {'status':'manual'}
    manual_audio = session.segments_dir / 'seg_2.wav'
    manual_audio.write_bytes(b'manual retained audio')
    manual.audio_path = str(manual_audio)
    manual.audio_url = '/api/streaming/audio/transcript-test/2'
    session.segments[2] = manual
    return session


def reviewer_result(targets, *, changed=False):
    return {'segments':{row.id:{**row.to_dict(),
        'final_vi':'Lời đã kiểm tra' if changed else row.final_vi,
        'needs_review':False, 'review_reason':None,
        'verification':{'status':'verified','semantic_verified':True}}
        for row in targets}, 'summary':{'checked':len(targets),'verified':len(targets)}}


def test_partial_review_uses_only_uncertain_playable_rows_and_keeps_failed_manual_context(partial, monkeypatch):
    sess = partial
    before = {sid:deepcopy(vars(row)) for sid,row in sess.segments.items() if sid != 0}
    audio = Path(sess.segments[0].audio_path).read_bytes()
    calls=[]
    def review(_, source, targets, screens, **options):
        calls.append(([row.id for row in targets], [row.id for row in options['context_segments']]))
        return reviewer_result(targets)
    monkeypatch.setattr(AutomaticTranslationReviewer, 'review', review)
    async def run():
        assert sess.get_progress()['can_review']
        async with client() as api:
            result=await api.post(f'/api/streaming/{sess.task_id}/review')
            assert result.status_code == 200
            assert result.json()['progress']['status'] == 'RUNNING'
            assert result.json()['progress']['review_scope'] == 'all'
            await sess.review_task
        progress=sess.get_progress()
        assert progress['status']=='FAILED' and progress['progress_pct'] is None
        assert progress['failed_segment_ids']==[1] and progress['can_retry']
        assert not progress['can_review'], 'No unresolved eligible row remains'
        assert not sess.output_filename
    asyncio.run(run())
    assert calls==[([0],[0,1,2])]
    assert all(vars(sess.segments[sid])==data for sid,data in before.items())
    assert Path(sess.segments[0].audio_path).read_bytes()==audio
    from core.streaming.session_store import _read
    saved=_read(sess.task_id)
    assert saved['state']=='FAILED'
    assert saved['segments'][1]['status']=='FAILED' and saved['segments'][1]['failed_stage']=='ALIGNING'
    assert saved['segments'][2]['verification']=={'status':'manual'}
    sess.tts_engine.synthesize.assert_not_called()
    assert not any(data.get('status')=='COMPLETED' or data.get('progress_pct')==100
                   for kind,data in sess.events if kind=='progress')


@pytest.mark.parametrize('condition', ['start_task','worker_task','review_task','_chunk_followup_task',
    'export_task','auto_export_task','_stop_draining','_persistence_capacity_failed','_restored_source_missing',
    '_startup_failed','missing_source','editing','asr_failed','waiting','no_targets'])
def test_partial_review_refuses_other_owners_or_non_speech_recovery(partial, condition):
    sess=partial
    async def run():
        future=None
        if condition.endswith('task'):
            future=asyncio.get_running_loop().create_future();setattr(sess,condition,future)
        elif condition.startswith('_'): setattr(sess,condition,True)
        elif condition=='missing_source': sess.video_path.unlink()
        elif condition=='editing': sess.edit_tasks.add(asyncio.current_task())
        elif condition=='asr_failed': sess.segments[1].failed_stage='ASR'
        elif condition=='waiting': sess.segments[1].status='WAITING'
        elif condition=='no_targets': sess.segments[0].needs_review=False;sess.segments[0].verification={'status':'verified'}
        try:
            assert not sess.get_progress()['can_review']
            with pytest.raises(SegmentEditConflict): await sess.start_automatic_review()
        finally:
            if future: future.cancel()
            sess.edit_tasks.clear()
    asyncio.run(run())


def test_partial_review_provider_failure_keeps_speech_and_can_retry_review(partial, monkeypatch):
    before={sid:deepcopy(vars(row)) for sid,row in partial.segments.items()}
    monkeypatch.setattr(AutomaticTranslationReviewer,'review',Mock(side_effect=RuntimeError('offline injected failure')))
    async def run():
        await partial.start_automatic_review();await partial.review_task
        progress=partial.get_progress()
        assert progress['status']=='FAILED' and progress['can_review'] and progress['can_retry']
        assert progress['review_summary']['status']=='failed'
    asyncio.run(run())
    assert all(vars(partial.segments[sid])==state for sid,state in before.items())


def test_new_stop_cancels_partial_review_without_publishing_late_response(partial, monkeypatch):
    entered,release=threading.Event(),threading.Event()
    before={sid:deepcopy(vars(row)) for sid,row in partial.segments.items()}
    partial.persist()
    def review(_,source,targets,screens,**options):
        assert not options['cancel_check'](), 'Old STOPPED must not cancel new explicit review'
        entered.set();assert release.wait(5)
        assert options['cancel_check'](), 'New stop must reach the reviewer'
        return reviewer_result(targets,changed=True)
    monkeypatch.setattr(AutomaticTranslationReviewer,'review',review)
    async def run():
        await partial.start_automatic_review();owner=partial.review_task
        await wait_until(entered.is_set)
        try:
            assert partial.get_progress()['can_stop']
            with pytest.raises(SegmentEditConflict): await partial.start_automatic_review()
            partial.stop()
        finally: release.set()
        with pytest.raises(asyncio.CancelledError): await owner
        assert partial.get_progress()['status']=='STOPPED'
        assert partial.get_progress()['can_review']
    asyncio.run(run())
    assert all(vars(partial.segments[sid])==state for sid,state in before.items())
    from core.streaming.session_store import _read
    saved=_read(partial.task_id)
    assert saved['state']=='STOPPED'
    assert saved['segments'][0]['verification']['status']=='unresolved'


def test_stale_partial_review_cannot_overwrite_a_new_manual_revision(partial, monkeypatch):
    def review(_,source,targets,screens,**options):
        partial.segments[0].revision+=1
        partial.segments[0].final_vi='Bản sửa mới nhất'
        partial.segments[0].verification={'status':'manual'}
        return reviewer_result(targets,changed=True)
    monkeypatch.setattr(AutomaticTranslationReviewer,'review',review)
    async def run():
        await partial.start_automatic_review();await partial.review_task
    asyncio.run(run())
    assert partial.segments[0].final_vi=='Bản sửa mới nhất'
    assert partial.segments[0].verification=={'status':'manual'}
    assert partial.segments[1].status=='FAILED'
    partial.tts_engine.synthesize.assert_not_called()


def test_partial_review_publication_failure_retains_old_row_and_sound(partial, monkeypatch):
    before=deepcopy(vars(partial.segments[0]))
    monkeypatch.setattr(AutomaticTranslationReviewer,'review',lambda _,source,targets,screens,**kw:reviewer_result(targets))
    monkeypatch.setattr(partial,'persist',Mock(side_effect=OSError('disk full')))
    monkeypatch.setattr(partial,'_persist_if_enabled',lambda:None)
    async def run():
        await partial.start_automatic_review();await partial.review_task
        assert partial.get_progress()['status']=='FAILED'
        assert partial.review_summary['status']=='failed'
    asyncio.run(run())
    assert vars(partial.segments[0])==before
    assert Path(partial.segments[0].audio_path).read_bytes()==b'old audio'


def test_new_tts_failure_during_partial_review_does_not_abort_next_eligible_row(partial, monkeypatch):
    later=deepcopy(partial.segments[0])
    later.id,later.start,later.end=3,12,15
    later_audio=partial.segments_dir/'seg_3.wav'
    later_audio.write_bytes(b'last retained audio')
    later.audio_path=str(later_audio)
    later.audio_url='/api/streaming/audio/transcript-test/3'
    partial.segments[3]=later
    partial.total_duration=15
    failed_before=deepcopy(vars(partial.segments[1]))
    def review(_,source,targets,screens,**options):
        result=reviewer_result(targets)
        result['segments'][0]['final_vi']='Lời được sửa cần giọng mới'
        return result
    monkeypatch.setattr(AutomaticTranslationReviewer,'review',review)
    partial.tts_engine.synthesize.side_effect=RuntimeError('offline injected speech failure')
    async def run():
        await partial.start_automatic_review();await partial.review_task
    asyncio.run(run())
    assert partial.segments[0].status=='FAILED'
    assert partial.segments[0].final_vi=='Lời được sửa cần giọng mới'
    assert partial.segments[0].verification['semantic_verified'] is True
    assert partial.segments[0].audio_path is None
    assert partial.segments[3].status=='READY' and not partial.segments[3].needs_review
    assert partial.segments[3].verification['semantic_verified'] is True
    assert Path(partial.segments[3].audio_path).read_bytes()==b'last retained audio'
    assert vars(partial.segments[1])==failed_before
    assert partial.get_progress()['failed_segment_ids']==[0,1]
    assert partial.get_progress()['status']=='FAILED'


@pytest.mark.parametrize('first_group_fails', [False, True])
def test_partial_review_checkpoints_groups_and_attempts_next_group_after_provider_failure(partial, monkeypatch, first_group_fails):
    from core.streaming.session_store import _read
    for sid in range(3,7):
        later=deepcopy(partial.segments[0])
        later.id,later.start,later.end=sid,12+(sid-3)*3,15+(sid-3)*3
        path=partial.segments_dir/f'seg_{sid}.wav'
        path.write_bytes(b'owned offline test audio')
        later.audio_path=str(path)
        later.audio_url=f'/api/streaming/audio/transcript-test/{sid}'
        partial.segments[sid]=later
    partial.total_duration=24
    partial.persist()
    calls=[]
    def review(_,source,targets,screens,**options):
        ids=[row.id for row in targets]
        calls.append(ids)
        assert [row.id for row in options['context_segments']]==[0,1,2,3,4,5,6]
        assert partial.get_progress()['review_scope']=='all'
        assert partial.get_progress()['review_summary']['status']=='running'
        if len(calls)==1 and first_group_fails:
            raise RuntimeError('injected first group timeout')
        if len(calls)==2:
            saved=_read(partial.task_id)
            first=[row for row in saved['segments'] if row['id'] in {0,3,4,5}]
            assert all(row['verification']['status']==('unresolved' if first_group_fails else 'verified') for row in first)
            assert next(row for row in saved['segments'] if row['id']==1)['status']=='FAILED'
        return reviewer_result(targets)
    monkeypatch.setattr(AutomaticTranslationReviewer,'review',review)
    async def run():
        await partial.start_automatic_review();await partial.review_task
    asyncio.run(run())
    assert calls==[[0,3,4,5],[6]]
    assert partial.segments[6].verification['status']=='verified'
    assert partial.get_progress()['status']=='FAILED'
    assert partial.review_summary['status']==('failed' if first_group_fails else 'completed')
    partial.tts_engine.synthesize.assert_not_called()
