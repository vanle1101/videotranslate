from scripts.recover_korean_source import assemble, owns, windows


def test_windows_bound_decode_and_cover_source():
    spans = list(windows(395.782676))
    assert len(spans) == 10
    assert spans[0][1] == 0
    assert spans[-1][2] == 395.782676
    assert all(decode_end - decode_start <= 48 for _, _, _, decode_start, decode_end in spans)
    assert all(left[2] == right[1] for left, right in zip(spans, spans[1:]))


def test_boundary_phrase_has_one_owner_and_is_not_word_split():
    phrase = {'start': 39., 'end': 41., 'text': '우리 친구잖아?', 'words': []}
    assert not owns(phrase, 0, 40, 80)
    assert owns(phrase, 40, 80, 80)
    chunks = [{'index': 0, 'core_start': 0, 'core_end': 40, 'rows': [phrase]},
              {'index': 1, 'core_start': 40, 'core_end': 80, 'rows': [phrase]}]
    result = assemble(chunks, {}, 80)
    assert len(result['segments']) == 1
    assert len(result['overlap_alternatives']) == 1
    assert result['segments'][0]['text'] == phrase['text']
    assert result['segments'][0]['start'] == 39
    assert result['segments'][0]['end'] == 41


def test_different_boundary_segmentation_requires_review():
    chunks = [{'index': 0, 'core_start': 0, 'core_end': 40,
               'rows': [{'start': 38, 'end': 41, 'text': '안녕', 'words': []}]},
              {'index': 1, 'core_start': 40, 'core_end': 80,
               'rows': [{'start': 39.8, 'end': 42, 'text': '안녕하세요', 'words': []}]}]
    result = assemble(chunks, {}, 80)
    assert len(result['segments']) == 2
    assert result['boundary_review'] == [{'code': 'overlapping_phrases', 'ids': [0, 1], 'cross_chunk': True}]
