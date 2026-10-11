"""Source subtitle blur follows observed source time, independent of narration."""
from copy import deepcopy

import pytest

from core.subtitle_cues import build_caption_layout, build_source_subtitle_masks


def ocr(**changes):
    return {"id": "source-ocr", "start": 1., "end": 3., "text_zh": "你来了",
        "kind": "subtitle", "source_method": "local-ocr", "confidence": .98,
        "needs_review": False, "bbox": [.3, .7, .4, .04], **changes}


def speech(**changes):
    return {"id": 11, "start": 1., "end": 3., "text_zh": "你来了", "final_vi": "Anh đến rồi.",
        "speech_start": 1.3, "speech_end": 2.4,
        "subtitle_cues": [{"start": 1.3, "end": 2.4, "text": "Anh đến rồi."}], **changes}


def active(masks, time):
    return [mask for mask in masks if mask["start"] <= time < mask["end"]]


def test_source_masks_start_before_and_outlast_dub_cues_and_cover_tts_pause():
    segment = speech(subtitle_cues=[{"start": 1.3, "end": 1.8, "text": "Anh"},
                                  {"start": 2.1, "end": 2.4, "text": "đến rồi."}])
    masks = build_source_subtitle_masks([ocr()], [segment], duration=4)
    assert masks == [{"start": .9, "end": 3.1, "bbox": [.3, .7, .4, .04]}]
    for time in (1., 1.2, 1.9, 2.9, 3.05):
        assert active(masks, time)
    assert not active(masks, .8) and not active(masks, 3.2)
    assert build_caption_layout([segment], [ocr()], caption_style={"blur_original": True})["source_masks"] == masks


def test_verified_source_geometry_does_not_require_a_vietnamese_caption_or_translation():
    assert build_source_subtitle_masks([ocr()], [], duration=2.95) == [
        {"start": .9, "end": 2.95, "bbox": [.3, .7, .4, .04]}]
    draft = speech(needs_review=True, subtitle_cues=[])
    before = deepcopy(draft)
    plan = build_caption_layout([draft], [ocr()], caption_style={"blur_original": True})
    assert plan["cues"] == [] and active(plan["source_masks"], 2.8)
    assert draft == before and draft["needs_review"] is True


def test_short_ocr_gap_between_same_band_boxes_has_conservative_union_mask():
    boxes = [ocr(end=1.8), ocr(id="next", start=2.03, end=3., bbox=[.28, .704, .43, .04])]
    masks = build_source_subtitle_masks(boxes)
    bridge = active(masks, 1.915)
    assert bridge, "230 ms sampling gap is covered even after first TTS ended"
    assert any(mask["bbox"][0] == .28 and sum(mask["bbox"][::2]) >= .71 for mask in bridge)
    assert not active(masks, .8) and not active(masks, 3.2)
    assert all(mask["bbox"][3] <= .15 and mask["bbox"][2] * mask["bbox"][3] <= .20 for mask in masks)
    clipped = build_source_subtitle_masks(boxes, duration=1.95)
    assert all(mask["start"] < mask["end"] <= 1.95 for mask in clipped)


@pytest.mark.parametrize("nested_box,nested_end", [
    ([.301, .7, .4, .04], .8),
    ([.3, .4, .4, .04], .8),
    ([.3, .4, .4, .04], 1.18),
    ([.78, .7, .15, .04], 1.18),
])
def test_nested_ocr_observation_does_not_hide_longer_same_band_transition(nested_box, nested_end):
    rows = [ocr(start=0., end=1.),
        ocr(id="nested", start=.5, end=nested_end, bbox=nested_box),
        ocr(id="next", start=1.22, end=2.)]
    masks = build_source_subtitle_masks(rows)
    bridge = [mask for mask in active(masks, 1.11) if mask["bbox"] == pytest.approx([.3, .7, .4, .04])]
    assert bridge, "The enclosing OCR interval still bridges its valid 220 ms transition"
    assert all(mask["bbox"][3] <= .15 for mask in masks)
    assert not any(mask["bbox"][1] < .7 and mask["bbox"][1] + mask["bbox"][3] >= .74
        for mask in masks), "An unrelated band never becomes part of the bridge"


@pytest.mark.parametrize("nested_box", [[.3, .4, .4, .04], [.78, .7, .15, .04]])
def test_recent_unrelated_nested_region_cannot_bridge_long_same_band_gap(nested_box):
    rows = [ocr(start=0., end=1.),
        ocr(id="unrelated", start=.5, end=1.48, bbox=nested_box),
        ocr(id="next", start=1.5, end=2.)]
    masks = build_source_subtitle_masks(rows)
    assert not any(mask["bbox"] == pytest.approx([.3, .7, .4, .04]) for mask in active(masks, 1.25))
    assert all(mask["bbox"][3] <= .15 for mask in masks)


@pytest.mark.parametrize("changes", [
    {"start": 2.5}, {"bbox": [.3, .4, .4, .04]}, {"bbox": [.78, .7, .15, .04]},
])
def test_long_gap_different_band_or_separate_location_is_not_blindly_bridged(changes):
    masks = build_source_subtitle_masks([ocr(end=1.8), ocr(**{"id": "next", "start": 2.03, **changes})])
    assert not active(masks, 1.915)


@pytest.mark.parametrize("changes", [{"kind": "title"}, {"source_method": "video-ai"},
    {"source_region_verified": False}, {"needs_review": True}, {"confidence": .89},
    {"bbox": [0., .7, 1., .25]}])
def test_untrusted_source_or_title_never_becomes_a_mask(changes):
    assert build_source_subtitle_masks([ocr(**changes)], [speech()]) == []


def split_line():
    full_text, fragment = "姑娘你是不是认错人了", "你是不是认错人了"
    box = [.33194, .893827, .33194, .062963]
    evidence = {"id": "review3", "start": 15.2, "end": 16.98,
        "text_zh": fragment, "full_text_zh": full_text, "source_scope_ids": [10, 11],
        "source_scope_ambiguous": False, "confidence": .96, "bbox": box}
    segment = speech(start=15.48, end=16.98, text_zh=fragment, speech_start=15.55, speech_end=16.8,
        subtitle_cues=[{"start": 15.55, "end": 16.8, "text": "Có phải chị nhận nhầm người không?"}],
        needs_review=True, preview_is_draft=True,
        verification={"status": "corrected", "source_supported": True, "semantic_verified": True,
            "second_pass_status": "completed", "evidence_mode": "fresh-ocr-text-review",
            "evidence_ids": ["review3"], "evidence": [evidence]})
    source = ocr(start=15.21, end=16.98, text_zh=full_text, needs_review=True,
        source_region_verified=False, bbox=box)
    continuation = ocr(id="ocr2", start=16.98, end=17.313333, text_zh=full_text,
        needs_review=True, source_region_verified=False, bbox=[.33229, .89259, .33333, .06296])
    return segment, source, continuation


def test_reviewed_split_utterance_full_line_scope_covers_source_transition_without_approving_roles():
    segment, source, continuation = split_line()
    before = deepcopy([segment, source, continuation])
    masks = build_source_subtitle_masks([source, continuation], [segment], duration=20)
    for time in (15.15, 16., 16.95, 17.1, 17.3):
        assert active(masks, time), time
    assert not active(masks, 14.9) and not active(masks, 17.5)
    assert [segment, source, continuation] == before
    assert source["source_region_verified"] is False and segment["needs_review"] is True


@pytest.mark.parametrize("change", [{"source_scope_ids": [10]}, {"source_scope_ids": ["11"]},
    {"source_scope_ambiguous": True}, {"source_scope_ambiguous": None},
    {"source_scope_conflict": True}, {"full_text_zh": "别的台词你是不是认错人了"}])
def test_fragment_cannot_borrow_full_line_geometry_without_matching_explicit_scope(change):
    segment, source, continuation = split_line()
    segment["verification"]["evidence"][0].update(change)
    assert build_source_subtitle_masks([source, continuation], [segment]) == []


@pytest.mark.parametrize("change", [{"text_zh": "另一个句子"}, {"kind": "title", "text_zh": "另一个标题"},
    {"source_method": "video-ai"}, {"confidence": .8}, {"bbox": [.1, .3, .4, .05]},
    {"start": 17.4, "end": 17.7}])
def test_reviewed_region_does_not_promote_unrelated_or_distant_ocr_continuation(change):
    segment, source, continuation = split_line()
    continuation.update(change)
    masks = build_source_subtitle_masks([source, continuation], [segment])
    assert active(masks, 16.) and not active(masks, 17.15)


def test_misclassified_title_continuation_requires_real_subtitle_anchor_full_line_scope_and_same_box():
    segment, source, continuation = split_line()
    continuation["kind"] = "title"
    before = deepcopy([segment, source, continuation])
    masks = build_source_subtitle_masks([source, continuation], [segment])
    assert active(masks, 17.1)
    assert [segment, source, continuation] == before, "The source title classification remains unchanged"
    # Same-title text alone cannot create an anchor or approve its semantics.
    assert build_source_subtitle_masks([continuation], [segment]) == []
    continuation["bbox"] = [.1, .1, .8, .08]
    assert not active(build_source_subtitle_masks([source, continuation], [segment]), 17.1)
    continuation["bbox"] = source["bbox"]
    segment["verification"]["evidence"][0]["source_scope_ambiguous"] = True
    assert build_source_subtitle_masks([source, continuation], [segment]) == []


@pytest.mark.parametrize("duration", [True, 0, -1, float("nan"), "20"])
def test_invalid_mask_duration_rejected(duration):
    with pytest.raises(ValueError):
        build_source_subtitle_masks([ocr()], duration=duration)
