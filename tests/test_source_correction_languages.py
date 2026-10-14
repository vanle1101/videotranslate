"""Cross-script captions cannot replace measured speech, including cache replay."""
import json

import pytest

from core.video_intelligence import VideoIntelligence


@pytest.mark.parametrize('original,caption', [
    ('여기에는 재미있는 책이 많습니다.', '这里有很多有趣的书。'),
    ('Tomorrow I will return.', '明天会回来。'),
    ('今日は図書館に行きます。', '今天去图书馆。'),
    ('Hello 今天很好', '今天很好'),
])
def test_cited_translated_caption_never_becomes_spoken_source(original, caption):
    engine = object.__new__(VideoIntelligence)
    engine.source_language = 'auto'
    payload = [{'id': 0, 'start': 0., 'end': 3., 'asr_text': original}]
    observed = [{'id': 'o0', 'start': 0., 'end': 3., 'text_zh': caption,
                 'confidence': .99, 'kind': 'subtitle'}]
    raw = json.dumps({'segments': [{'id': 0, 'text_zh': caption, 'evidence_ids': ['o0'],
                      'needs_review': False, 'review_reason': ''}]})
    result = engine._validate_source_response(raw, payload, observed)[0]
    assert result['text_zh'] == original
    assert result['needs_review'] and not result['source_supported']
    assert result['evidence_ids'] == []
    # Replaying a previously cached response runs the same source gate.
    assert engine._validate_source_response(raw, payload, observed)[0] == result


def test_matching_korean_caption_can_correct_korean_source():
    engine = object.__new__(VideoIntelligence)
    engine.source_language = 'auto'
    payload = [{'id': 0, 'start': 0., 'end': 3., 'asr_text': '도서관에 갑니다'}]
    corrected = '오늘은 도서관에 갑니다.'
    observed = [{'id': 'o0', 'start': 0., 'end': 3., 'text_zh': corrected,
                 'confidence': .99, 'kind': 'subtitle'}]
    raw = json.dumps({'segments': [{'id': 0, 'text_zh': corrected, 'evidence_ids': ['o0'],
                      'needs_review': False, 'review_reason': ''}]})
    result = engine._validate_source_response(raw, payload, observed)[0]
    assert result['text_zh'] == corrected
    assert result['source_supported'] and not result['needs_review']


def test_source_script_guard_preserves_existing_exact_unit_spelling_equivalence():
    from core.source_language import source_ocr_script_conflict
    assert not source_ocr_script_conflict('跑了100m', '跑了100米')
    assert source_ocr_script_conflict('Run 100m', '跑100米')
