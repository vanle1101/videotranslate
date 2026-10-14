"""Draft policy integration; mocked rendering is not real MP4 acceptance."""
import asyncio
import json
import pytest
from fastapi import HTTPException
import main
from core.streaming.output_gate import final_output_metadata, draft_review_metadata
from test_auto_review_export import session


@pytest.mark.parametrize('status',[None,'failed','incomplete','completed'])
def test_uncertainty_exports_as_draft_and_keeps_sidecar_findings(session,status):
    item, exporter=session
    item.review_summary={'status':status}
    item.segments[0].verification={'status':'incomplete'}
    result=asyncio.run(main.export_hq(main.ExportHQRequest(task_id=item.task_id)))
    assert result['output_is_draft'] is True
    assert result['output_quality_label']=='Bản nháp — cần kiểm tra'
    assert result['review_report'][0]['verification']['status']=='incomplete'
    assert item.segments[0].needs_review is True
    report=json.loads((main.settings.OUTPUT_DIR/'reviewed.review.json').read_text(encoding='utf-8'))
    assert report['output_is_draft'] is True
    assert report['segments'][0]['needs_review'] is True
    exporter.export.assert_called_once()


@pytest.mark.parametrize('condition',['running_review','runtime_error','not_ready','missing_translation','source_incomplete'])
def test_draft_permission_does_not_bypass_runtime_and_source_gates(session,monkeypatch,condition):
    item,exporter=session
    item.review_summary={'status':'incomplete'}
    if condition=='running_review': item.review_summary={'status':'running'}
    elif condition=='runtime_error': item.error='Media failure'
    elif condition=='not_ready': item.segments[0].status='TTS'
    elif condition=='missing_translation': item.segments[0].final_vi=''
    else:
        from core.streaming import chunked_source
        item._chunked_source_started=True
        item._source_prepared_seconds=.5
        monkeypatch.setattr(chunked_source,'ensure_source_identity',lambda session:None)
    with pytest.raises(HTTPException) as error:
        asyncio.run(main.export_hq(main.ExportHQRequest(task_id=item.task_id)))
    assert error.value.status_code==409
    exporter.export.assert_not_called()


def test_missing_speech_remains_blocked_even_when_uncertainty_is_only_editorial():
    row={'id':1,'text_zh':'hello','final_vi':'','needs_review':True}
    metadata=final_output_metadata([row],{'status':'incomplete'})
    assert metadata['final_output_blocked'] is True
    assert metadata['missing_speech_ids']==[1]
    assert metadata['output_is_draft'] is True


def test_failed_review_without_row_flags_still_labels_export_as_draft(session):
    item,_=session
    item.segments[0].needs_review=False
    item.segments[0].verification={'status':'verified'}
    item.review_summary={'status':'failed'}
    result=asyncio.run(main.export_hq(main.ExportHQRequest(task_id=item.task_id)))
    assert result['output_is_draft'] is True
    assert 'Bản nháp' in result['review_warning']


def test_clear_completed_audit_never_claims_certain_semantic_truth():
    result=draft_review_metadata([{'id':0,'needs_review':False,'verification':{'status':'verified'}}],{'status':'completed'})
    assert result['output_is_draft'] is False
    assert result['output_quality_label']=='Đã rà tự động'
