"""The local visual-translation provider honors per-video worker selection."""
import asyncio
import pytest
from test_preview_chunked_pipeline import preview


def test_full_ollama_visual_pipeline_dispatches_selected_parallel_capacity(preview, monkeypatch):
    session, *_ = preview
    from core.streaming import pipeline

    monkeypatch.setattr(pipeline.settings, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr("core.streaming.chunked_source.ensure_source_identity", lambda _session: None)
    session.translation_mode = "full"
    session.visual_translation = True
    session.total_duration = 64.
    session.translation_chunk_workers = 50
    calls = []

    async def parallel(target, workers):
        calls.append((target, workers))

    async def complete():
        return None

    monkeypatch.setattr(session, "_produce_parallel_visual", parallel)
    monkeypatch.setattr(session, "_complete_chunked_source", complete)
    asyncio.run(session._produce_chunked_visual())

    assert calls == [(64., 50)]


def test_paused_session_cancels_visual_prepass_at_next_provider_boundary(preview):
    from core.video_intelligence import VideoIntelligence, VideoIntelligenceError

    session, *_ = preview
    assert not session._visual_prepass_cancelled()
    session.is_paused = True
    assert session._visual_prepass_cancelled()
    with pytest.raises(VideoIntelligenceError, match="hủy phân tích hình ảnh"):
        VideoIntelligence._check_cancelled(session._visual_prepass_cancelled)
