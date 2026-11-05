"""Offline edit races through the CLI translator's actual provider boundary."""
from copy import deepcopy
import json

import pytest

from core.engines.translation.semantic_translator import SemanticTranslator


def source():
    return [{"id": index, "start": index * 2., "end": index * 2. + 1.,
             "text_zh": "你好", "revision": 2} for index in range(3)]


def install_provider(monkeypatch, translator, *, on_summary=None, on_translation=None):
    summaries, batches = [], []

    def request(system, prompt):
        if prompt.startswith("Transcript video:"):
            summaries.append(json.loads(prompt.split("\n", 1)[1]))
            if on_summary:
                on_summary()
            return json.dumps({"theme": "Đối thoại", "pronouns": "Chưa rõ", "terms": []})
        batch = json.JSONDecoder().raw_decode(prompt.split("chuyển ngữ:\n", 1)[1])[0]
        batches.append(batch)
        if on_translation:
            on_translation()
        return json.dumps({"results": [{"id": row["id"], "literal_vi": "Xin chào.",
            "natural_vi": "Xin chào.", "final_vi": "Xin chào."} for row in batch]})

    monkeypatch.setattr(translator, "_opencode_request", request)
    return summaries, batches


@pytest.mark.parametrize("marker", [{"manual_edit": True}, {"user_edited": True},
                                    {"verification": {"status": "manual"}}])
@pytest.mark.parametrize("field", ["final_vi", "vi_text"])
def test_manual_translation_is_context_only_and_returned_unchanged(monkeypatch, marker, field):
    translator = SemanticTranslator("opencode")
    rows = source()
    rows[1].update(marker, **{field: "Lời biên tập.", "audio_path": "keep.wav"})
    before = deepcopy(rows)
    summaries, batches = install_provider(monkeypatch, translator)
    result = translator.translate(rows)
    assert [row["id"] for row in summaries[0]] == [0, 1, 2]
    assert [row["id"] for batch in batches for row in batch] == [0, 2]
    assert result[1] == before[1]
    assert result[0]["final_vi"] == result[2]["final_vi"] == "Xin chào."
    assert rows == before


def test_all_manual_rows_do_not_call_provider(monkeypatch):
    translator = SemanticTranslator("opencode")
    rows = source()
    for row in rows:
        row.update(manual_edit=True, final_vi="Bản sửa.")
    monkeypatch.setattr(translator, "_opencode_request", lambda *args: pytest.fail("Provider called"))
    assert translator.translate(rows) == rows


@pytest.mark.parametrize("edit", [
    {"manual_edit": True, "final_vi": "Sửa khi đang dịch."},
    {"verification": {"status": "manual"}, "vi_text": "Sửa khi đang dịch."},
    {"revision": 3, "final_vi": "Sửa khi đang dịch."},
    {"revision": 3, "final_vi": ""},
    {"final_vi": "Sửa không tăng revision."},
])
def test_late_provider_result_never_overwrites_current_edit(monkeypatch, edit):
    translator = SemanticTranslator("opencode")
    rows = source()
    install_provider(monkeypatch, translator, on_translation=lambda: rows[1].update(edit))
    result = translator.translate(rows)
    assert result[1] == rows[1]
    assert result[0]["final_vi"] == result[2]["final_vi"] == "Xin chào."


def test_edit_during_summary_is_excluded_from_translation_targets(monkeypatch):
    translator = SemanticTranslator("opencode")
    rows = source()
    _, batches = install_provider(monkeypatch, translator,
        on_summary=lambda: rows[1].update(revision=3, manual_edit=True, final_vi="Sửa sớm."))
    result = translator.translate(rows)
    assert [row["id"] for batch in batches for row in batch] == [0, 2]
    assert result[1] == rows[1]


def test_manual_flag_without_text_still_translates_missing_line(monkeypatch):
    translator = SemanticTranslator("opencode")
    rows = source()
    rows[1].update(manual_edit=True, final_vi="  ", vi_text="")
    _, batches = install_provider(monkeypatch, translator)
    result = translator.translate(rows)
    assert [row["id"] for batch in batches for row in batch] == [0, 1, 2]
    assert result[1]["final_vi"] == "Xin chào."


def test_manual_neighbors_are_original_source_only_in_translation_context(monkeypatch):
    translator = SemanticTranslator("opencode")
    rows = source()
    for index in (0, 2):
        rows[index].update(manual_edit=True, final_vi="Lời biên tập riêng.",
                           audio_path="private.wav", custom_metadata="not source")
    calls = []
    monkeypatch.setattr(translator, "_extract_context_and_glossary", lambda text: {})

    def request(system, prompt):
        targets = json.JSONDecoder().raw_decode(prompt.split("chuyển ngữ:\n", 1)[1])[0]
        context = json.JSONDecoder().raw_decode(system.split("trả ID của chúng: ", 1)[1])[0]
        calls.append((targets, context))
        return json.dumps({"results": [{"id": 1, "literal_vi": "Xin chào.",
            "natural_vi": "Xin chào.", "final_vi": "Xin chào."}]})

    monkeypatch.setattr(translator, "_opencode_request", request)
    result = translator.translate(rows)
    targets, neighbors = calls[0]
    assert [row["id"] for row in targets] == [1]
    assert [row["id"] for row in neighbors] == [0, 2]
    assert all(row["text_zh"] == "你好" and row["manual_edit"] for row in neighbors)
    assert all(not {"final_vi", "audio_path", "custom_metadata"}.intersection(row) for row in neighbors)
    assert result[0] == rows[0] and result[2] == rows[2]


@pytest.mark.parametrize("stage", ["summary", "translation"])
@pytest.mark.parametrize("change", ["reorder", "remove", "append", "replace_id"])
def test_structural_edits_merge_by_current_stable_id(monkeypatch, stage, change):
    translator = SemanticTranslator("opencode")
    rows = source()

    def edit():
        if change == "reorder":
            rows.reverse()
        elif change == "remove":
            rows.pop(1)
        elif change == "append":
            rows.append({"id": 99, "start": 20., "end": 21., "text_zh": "新", "final_vi": "Mới."})
        else:
            rows[1] = {**rows[1], "id": 99, "final_vi": "Mới."}

    install_provider(monkeypatch, translator, **{f"on_{stage}": edit})
    result = translator.translate(rows)
    assert [row["id"] for row in result] == [row["id"] for row in rows]
    for actual, current in zip(result, rows):
        if current["id"] == 99:
            assert actual == current
        else:
            assert actual["final_vi"] == "Xin chào."


def test_duplicate_id_introduced_during_request_fails_explicitly(monkeypatch):
    translator = SemanticTranslator("opencode")
    rows = source()
    install_provider(monkeypatch, translator, on_translation=lambda: rows.append(dict(rows[0])))
    with pytest.raises(ValueError, match="Duplicate source IDs"):
        translator.translate(rows)
