from core.dialogue_segments import split_dialogue_segments


def word(text, start, end, speaker=None):
    item = {"word": text, "start": start, "end": end}
    if speaker is not None:
        item["speaker"] = speaker
    return item


def row(text, start, end, words, **extra):
    return {"id": extra.pop("id", 7), "start": start, "end": end,
            "duration": end - start, "text_zh": text, "words": words,
            "emotion": "<|NEUTRAL|>", **extra}


def test_sentence_punctuation_uses_word_boundaries_and_keeps_outer_times():
    rows = split_dialogue_segments([row(
        "你好。你吃饭了吗？", 1.0, 4.0,
        [word("你好。", 1.2, 1.8), word("你吃饭了吗？", 2.1, 3.2)],
    )])
    assert [(item["start"], item["end"], item["text_zh"]) for item in rows] == [
        (1.0, 1.8, "你好。"), (2.1, 4.0, "你吃饭了吗？")
    ]
    assert [item["id"] for item in rows] == [0, 1]


def test_real_pause_splits_without_guessing_a_midpoint():
    rows = split_dialogue_segments([row(
        "你先说 我听着", 0.0, 3.0,
        [word("你先说", 0.3, 1.0), word("我听着", 1.45, 2.0)],
    )], pause_threshold=0.3)
    assert [(item["start"], item["end"]) for item in rows] == [(0.0, 1.0), (1.45, 3.0)]
    assert rows[1]["start"] != 1.0 + (3.0 - 1.0) / 2


def test_speaker_label_transition_splits_only_when_word_labels_change():
    rows = split_dialogue_segments([row(
        "甲乙", 0.0, 2.0,
        [word("甲", 0.1, 0.5, "A"), word("乙", 0.55, 1.0, "B")],
    )])
    assert [item["text_zh"] for item in rows] == ["甲", "乙"]
    assert [item["speaker"] for item in rows] == ["A", "B"]


def test_short_one_word_reply_is_never_merged_or_dropped():
    rows = split_dialogue_segments([
        row("嗯", 0.0, 0.25, [word("嗯", 0.05, 0.20)]),
        row("我知道了。", 1.0, 2.0, [word("我知道了。", 1.05, 1.8)]),
    ])
    assert [item["text_zh"] for item in rows] == ["嗯", "我知道了。"]
    assert [(item["start"], item["end"]) for item in rows] == [(0.0, 0.25), (1.0, 2.0)]


def test_adjacent_source_rows_remain_separate_even_without_pause():
    rows = split_dialogue_segments([
        row("第一行", 0.0, 1.0, [word("第一行", 0.1, 0.9)]),
        row("第二行", 1.0, 2.0, [word("第二行", 1.05, 1.9)]),
    ])
    assert [item["text_zh"] for item in rows] == ["第一行", "第二行"]
    assert [(item["start"], item["end"]) for item in rows] == [(0.0, 1.0), (1.0, 2.0)]


def test_without_measured_words_row_is_preserved_for_compatibility():
    source = row("整行保留。", 2.0, 5.0, [])
    rows = split_dialogue_segments([source])
    assert len(rows) == 1
    assert rows[0]["text_zh"] == source["text_zh"]
    assert rows[0]["start"] == source["start"] and rows[0]["end"] == source["end"]


def test_rounded_word_overlap_keeps_row_instead_of_creating_rejected_intervals():
    source = row("你好。再见。", 0.0, 2.0,
                 [word("你好。", 0, 1), word("再见。", .99, 2)])
    rows = split_dialogue_segments([source])
    assert [(item["start"], item["end"], item["text_zh"]) for item in rows] == [(0, 2, "你好。再见。")]
    assert rows[0]["words"] == []


def test_incomplete_word_metadata_does_not_drop_source_characters():
    source = row("你好。再见。", 0.0, 2.0,
                 [word("你好。", 0, 1), word("再", 1.1, 2)])
    rows = split_dialogue_segments([source])
    assert len(rows) == 1 and rows[0]["text_zh"] == "你好。再见。"
    assert rows[0]["words"] == []


def test_punctuation_differences_do_not_discard_valid_measured_words():
    rows = split_dialogue_segments([row("你好！再见！", 0.0, 2.0,
                 [word("你好。", 0, 1), word("再见。", 1.1, 2)])])
    assert [item["text_zh"] for item in rows] == ["你好。", "再见。"]
