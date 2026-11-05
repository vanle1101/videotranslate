import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from core.streaming import native_recovery
from core.streaming.pipeline import SegmentItem, SegmentEditConflict
from test_dub_pipeline import session
from test_review_pacing_recovery import reviewed_session


def native_row(supported=False, manual=False):
    return {"id": 0, "start": 0., "end": 2., "text_zh": "우리 친구잖아?",
        "final_vi": "Cũ.", "evidence_mode": "native-recovery-ko",
        "verification": {"status": "manual" if manual else "unresolved", "source_supported": supported,
            "reason": "Hai lượt nhận giọng chưa thống nhất.", "source_readings": ["first", "second"]}}


@pytest.mark.parametrize("supported", [False, True])
def test_semantic_review_keeps_original_source_support(monkeypatch, supported):
    original = native_row(supported)
    before = deepcopy(original)
    def translate(rows, **kwargs):
        assert kwargs["language"] == "mixed"
        assert rows[0]["source_language"] == "ko"
        return [{**rows[0], "final_vi": "Chúng ta là bạn mà?", "multilingual_review": {
            "semantic_verified": True, "reason": "Quan hệ bạn bè."}}]
    monkeypatch.setattr(native_recovery, "translate_and_review", translate)
    result = native_recovery.review_native_rows([original])
    row = result["segments"][0]
    assert original == before
    assert row["verification"]["source_readings"] == ["first", "second"]
    assert row["verification"]["source_supported"] is supported
    assert row["needs_review"] is not supported
    assert row["verification"]["status"] == ("corrected" if supported else "unresolved")
    assert result["summary"]["checked"] == 1


def test_manual_native_rows_are_never_translated(monkeypatch):
    monkeypatch.setattr(native_recovery, "translate_and_review", lambda *a, **k: pytest.fail("manual overwritten"))
    row = native_row(manual=True)
    result = native_recovery.review_native_rows([row])
    assert result["segments"][0]["final_vi"] == "Cũ."
    assert result["summary"]["checked"] == 0


def test_saved_native_translation_only_synthesizes(session, monkeypatch):
    row = session.segments[6]
    row.evidence_mode = "native-recovery-ko"
    session.visual_translation = False
    speech = AsyncMock()
    monkeypatch.setattr(session, "_synthesize_segment", speech)
    monkeypatch.setattr("core.streaming.pipeline.review_native_rows", lambda *a, **k: pytest.fail("retranslated saved VI"))
    asyncio.run(session._process_segment(row))
    speech.assert_awaited_once_with(row)


@pytest.mark.parametrize("draft", ["", "[không rõ nghĩa]"])
def test_missing_native_translation_is_published_then_synthesized(session, monkeypatch, draft):
    row = session.segments[6]
    row.evidence_mode, row.text_zh, row.final_vi = "native-recovery-ko", "우리 친구잖아?", draft
    row.verification = {"status": "unresolved", "source_supported": False}
    old_revision = row.revision
    def review(rows, **kwargs):
        assert rows[0].text_zh == "우리 친구잖아?"
        return {"segments": {6: {"text_zh": "우리 친구잖아?", "final_vi": "Chúng ta là bạn mà?",
            "needs_review": True, "verification": {"status": "unresolved", "source_supported": False}}}}
    speech = AsyncMock()
    monkeypatch.setattr("core.streaming.pipeline.review_native_rows", review)
    monkeypatch.setattr(session, "_synthesize_segment", speech)
    asyncio.run(session._process_segment(row))
    assert row.final_vi == "Chúng ta là bạn mà?"
    assert row.needs_review is True
    assert row.revision == old_revision
    speech.assert_awaited_once_with(row)


def test_native_manual_placeholder_is_preserved_but_never_synthesized(session, monkeypatch):
    row = session.segments[6]
    row.evidence_mode, row.final_vi = "native-recovery-ko", "[không rõ nghĩa]"
    row.verification = {"status": "manual", "source_supported": False}
    monkeypatch.setattr("core.streaming.pipeline.review_native_rows", lambda *a, **k: pytest.fail("manual overwritten"))
    monkeypatch.setattr(session, "_ensure_tts_engine", lambda: pytest.fail("placeholder reached TTS"))
    with pytest.raises(RuntimeError, match="ghi chú"):
        asyncio.run(session._process_segment(row))
    assert row.final_vi == "[không rõ nghĩa]" and row.verification["status"] == "manual"


def test_edit_cannot_publish_uncertainty_note_as_spoken_translation(session):
    with pytest.raises(ValueError):
        asyncio.run(session.edit_segment(6, "[không rõ nghĩa]"))


def test_concurrent_edit_wins_over_native_translation(session, monkeypatch):
    row = session.segments[6]
    row.evidence_mode, row.text_zh, row.final_vi = "native-recovery-ko", "우리 친구잖아?", ""
    def review(rows, **kwargs):
        row.revision += 1
        row.final_vi = "Bản người dùng."
        return {"segments": {6: {"final_vi": "Bản AI cũ."}}}
    monkeypatch.setattr("core.streaming.pipeline.review_native_rows", review)
    with pytest.raises(SegmentEditConflict):
        asyncio.run(session._process_segment(row))
    assert row.final_vi == "Bản người dùng."


def test_full_review_routes_native_rows_without_chinese_reviewer(reviewed_session, monkeypatch):
    from core.translation_review import AutomaticTranslationReviewer
    current = reviewed_session
    for row in current.segments.values():
        row.evidence_mode = "native-recovery-ko"
        row.text_zh = "우리 친구잖아?"
    def review(rows, **kwargs):
        return {"segments": {row.id: {**row.to_dict(), "final_vi": "Chúng ta là bạn mà?",
            "needs_review": True, "verification": {"status": "unresolved", "source_supported": False,
                "semantic_verified": True}} for row in rows},
            "summary": {"checked": 2, "verified": 0, "corrected": 0, "unresolved": 2},
            "translation_sources": []}
    monkeypatch.setattr("core.streaming.pipeline.review_native_rows", review)
    monkeypatch.setattr(AutomaticTranslationReviewer, "review", lambda *a, **k: pytest.fail("Chinese source review"))
    asyncio.run(current._review_translations(regenerate_audio=True, defer_audio=True))
    assert current.segments[112].final_vi == "Chúng ta là bạn mà?"
    assert current.segments[112].needs_review
    assert current.segments[112].audio_path is None
    assert current.segments[112].status == "WAITING"


def test_native_fit_never_invokes_legacy_chinese_pacing(session, monkeypatch, tmp_path):
    row = session.segments[6]
    row.evidence_mode = "native-recovery-ko"
    seen = []
    class StopProbe(Exception):
        pass
    def speech(**kwargs):
        seen.append(kwargs)
        raise StopProbe()
    monkeypatch.setattr("core.streaming.pipeline.synthesize_natural_speech", speech)
    with pytest.raises(StopProbe):
        asyncio.run(session._fit_dub(row, text=row.final_vi, source=row.text_zh,
            output_path=tmp_path / "speech.wav", translator=object(), neighbor_rescue=True))
    assert len(seen) == 1
    from core.multilingual_translation import NativePacingTranslator
    assert isinstance(seen[0]["translator"], NativePacingTranslator)
    assert seen[0]["translator"].language == "ko"


def test_retry_worker_routes_missing_native_vi_to_translation(session, monkeypatch):
    row = session.segments[6]
    row.evidence_mode, row.final_vi, row.status = "native-recovery-ko", "", "WAITING"
    row._retry_synthesis = True
    session.is_running = True
    session.pause_event.set()
    calls = []
    def review(rows, **kwargs):
        calls.extend(item["id"] for item in rows)
        return {"segments": {row.id: {"final_vi": "Đã dịch."}}}
    monkeypatch.setattr("core.streaming.nonvisual_translation.review_native_rows", review)
    async def speech(item, **kwargs):
        assert item.final_vi == "Đã dịch."
        item.status = "READY"
    monkeypatch.setattr(session, "_synthesize_segment", speech)
    session.queue.put_nowait((row.start, row.id))
    asyncio.run(session._worker_loop())
    assert calls == [row.id]


def test_mixed_interval_routes_native_rows_and_publishes_one_complete_callback(session, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock
    native = SegmentItem(10, 1., 2., 1.)
    native.text_zh, native.source_language = "안 했어", "ko"
    chinese = SegmentItem(11, 3., 4., 1.)
    chinese.text_zh, chinese.source_language = "我还没做", "zh"
    callbacks = []
    def prepass(path, rows, **kwargs):
        assert [row.id for row in rows] == [11]
        assert kwargs["chunk_callback"] is None
        return {"segments": {11: {"id": 11, "final_vi": "Tôi chưa làm."}},
                "screen_texts": [], "summary": "Chinese", "translation_sources": []}
    def review(rows, **kwargs):
        assert [row["id"] for row in rows] == [10]
        assert rows[0]["ocr_evidence"][0]["text_zh"] == "还没做"
        return {"segments": {10: {**native.to_dict(), "final_vi": "Chưa làm.",
            "needs_review": True, "verification": {"status": "unresolved"}}}}
    monkeypatch.setattr("core.streaming.pipeline.review_native_rows", review)
    ocr = SimpleNamespace(extract=lambda *args, **kwargs: [{"start": 1., "end": 2., "text_zh": "还没做"}])
    result = session._native_aware_prepass(SimpleNamespace(prepass=prepass, screen_ocr=ocr), "source.mp4", [native, chinese],
        start_time=0., end_time=5., context_segments=[native, chinese],
        chunk_callback=lambda result, lo, hi: callbacks.append((result, lo, hi)))
    assert set(result["segments"]) == {10, 11}
    assert result["segments"][10]["evidence_mode"] == "native-recovery-ko"
    assert len(callbacks) == 1 and callbacks[0][1:] == (0., 5.)


def test_native_source_metadata_survives_grounding_and_parallel_snapshot(session):
    session.total_duration = 8.
    rows = session._grounded_visual_segments([{"start": 0., "end": 2., "text_zh": "안 했어",
        "source_language": "ko", "source_language_probability": .95, "source_language_uncertain": False}])
    assert rows[0]["source_language"] == "ko"
    native = SegmentItem(0, 0., 2., 2.)
    native.text_zh = native.asr_text = rows[0]["text_zh"]
    native.source_language = rows[0]["source_language"]
    session.segments = {0: native}
    source, context = session._parallel_source_snapshot(0., 3.)
    assert source[0].source_language == context[0].source_language == "ko"


def test_first_native_publication_commits_review_checkpoint_and_whole_interval(session, monkeypatch):
    from types import SimpleNamespace
    row = SegmentItem(0, 1., 2., 1.)
    row.text_zh = row.asr_text = "안 했어"
    row.source_language = "ko"
    session.segments = {0: row}
    session.video_intelligence = SimpleNamespace(provider="opencode")
    session._visual_completed_seconds = 0.
    finish = AsyncMock()
    monkeypatch.setattr(session, "_finish_visual_chunk", finish)
    monkeypatch.setattr(session, "_invalidate_changed_address_verifications", AsyncMock())
    monkeypatch.setattr(session, "_update_ready", AsyncMock())
    result = {"segments": {0: {**row.to_dict(), "text_zh": row.text_zh,
        "final_vi": "Chưa làm.", "translation_provider": "opencode", "evidence_mode": "native-recovery-ko",
        "needs_review": True, "verification": {"status": "unresolved", "source_supported": False}}}}
    asyncio.run(session._publish_visual_chunk(result, 0., 5.))
    assert session._visual_completed_seconds == 5.
    assert row.final_vi == "Chưa làm." and row.needs_review
    assert row.verification["source_supported"] is False
    cached = session._visual_reviewed_drafts[0]
    assert cached["context"] == session._visual_source_context_identity()
    assert finish.await_args.args[0] == {0}
    assert finish.await_args.args[1] == set(), "Native reviewed draft must not enter Chinese review"


def test_native_ocr_is_bounded_temporal_reference_and_keeps_source_unchanged():
    from types import SimpleNamespace
    from core.streaming.native_recovery import native_ocr_evidence
    row = native_row()
    before = deepcopy(row)
    spans = []
    def extract(path, start, end, **kwargs):
        spans.append((start, end))
        return [{"start": start + 1, "end": start + 2, "text_zh": "我们是朋友"}]
    copied = native_ocr_evidence(SimpleNamespace(screen_ocr=SimpleNamespace(extract=extract)),
                                "source.mp4", [row], 0., 100.)
    assert spans == [(0., 45.), (45., 90.), (90., 100.)]
    assert row == before and copied[0]["text_zh"] == row["text_zh"]
    assert len(copied[0]["ocr_evidence"]) == 1
    assert copied[0]["ocr_evidence"][0]["kind"] == "reference"
    assert copied[0]["verification"]["source_supported"] is False

@pytest.mark.parametrize('defer_audio', [True, False])
def test_review_owned_revision_preserves_timed_out_audit(reviewed_session, monkeypatch, defer_audio):
    from core.multilingual_translation import VERSION
    from core.engines.translation.opencode_client import OpenCodeTimeoutError
    from core.streaming.native_audit_retry import make_pending_audit, pending_native_audit
    current = reviewed_session
    for row in current.segments.values():
        row.evidence_mode, row.source_language = 'native-recovery-ko', 'ko'
        row.text_zh = '우리 친구잖아?'
    def review(rows, **kwargs):
        output = {}
        for source in rows:
            draft = {**source.to_dict(), 'literal_vi':'Chúng ta là bạn mà?',
                'natural_vi':'Chúng ta là bạn mà?', 'final_vi':'Chúng ta là bạn mà?',
                'needs_review':True, 'review_reason':'Audit timeout'}
            marker = make_pending_audit(draft,draft,[],version=VERSION,model='test',
                                        error=OpenCodeTimeoutError('timeout'))
            draft['verification'] = {'status':'unresolved','source_supported':False,
                'multilingual_review':{'pending_audit':marker}}
            output[source.id]=draft
        return {'segments':output,'summary':{'checked':len(output),'unresolved':len(output)},'translation_sources':[]}
    monkeypatch.setattr('core.streaming.pipeline.review_native_rows', review)
    if not defer_audio:
        async def fail_edit(*args, **kwargs): raise RuntimeError('speech temporarily unavailable')
        monkeypatch.setattr(current,'edit_segment',fail_edit)
    asyncio.run(current._review_translations(regenerate_audio=True,defer_audio=defer_audio))
    for row in current.segments.values():
        marker=pending_native_audit(row)
        assert marker and marker['revision']==row.revision
        assert row.final_vi=='Chúng ta là bạn mà?' and row.needs_review
