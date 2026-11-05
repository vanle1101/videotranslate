from copy import deepcopy
import pytest
from core.source_ambiguity import source_ambiguity_reason
from core.streaming.session_store import _clean
from core.translation_review import AutomaticTranslationReviewer


def row():
    return {'id': 133, 'text_zh': '就是为了让自己不出声。', 'final_vi': 'Không lên tiếng.',
        'needs_review': False, 'verification': {'status': 'verified', 'semantic_verified': True,
            'source_ambiguity': {'id': 133, 'method': 'homophone-context-conflict',
                'status': 'unresolved', 'text_zh': '就是为了让自己不出声。',
                'reason': '声/生 have the same pronunciation; the recorded context disputes silence.',
                'evidence': [{'id': 130, 'text_zh': '还会有你吗'}]}}}


def test_conflict_survives_disk_metadata_roundtrip_and_formatting():
    source = row()
    restored = deepcopy(source)
    restored['verification'] = _clean(source['verification'])
    restored['text_zh'] = '就是为了让自己不出声'
    result = AutomaticTranslationReviewer._summarize({'segments': {133: restored}})
    restored = result['segments'][133]
    assert restored['needs_review'] and not restored['verification']['semantic_verified']
    assert result['summary']['unresolved'] == 1 and result['summary']['verified'] == 0
    assert restored['verification']['source_ambiguity'] == source['verification']['source_ambiguity']


def test_source_correction_is_distinct_from_repeating_the_same_ambiguous_spelling():
    source = row()
    source['text_zh'] = '就是为了让自己不出生。'
    assert source_ambiguity_reason(source) is None
    # Clearing the old spelling's hold is never itself approval.
    source['needs_review'] = True
    source['verification'].update(status='incomplete', semantic_verified=False)
    result = AutomaticTranslationReviewer._summarize({'segments': {133: source}})
    assert source['needs_review'] and result['summary']['incomplete'] == 1


@pytest.mark.parametrize('change', [
    {'id': True}, {'status': 'verified'}, {'method': 'model-self-approval'},
    {'reason': ''}, {'evidence': []}, {'text_zh': 'bad\x00source'},
])
def test_invalid_conflict_metadata_cannot_be_persisted(change):
    source = row()
    source['verification']['source_ambiguity'].update(change)
    with pytest.raises(ValueError):
        _clean(source['verification'])


def test_conflict_from_another_row_cannot_change_this_rows_verdict():
    source = row()
    source['id'] = 134
    with pytest.raises(ValueError):
        AutomaticTranslationReviewer._summarize({'segments': {134: source}})
