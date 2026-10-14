"""Target-only accent repair must never approve an unsupported source rewrite."""
from copy import deepcopy
from unittest.mock import Mock

import pytest

from config import settings
from core.translation_review import AutomaticTranslationReviewer


def row(**changes):
    return {"id": 1, "start": 0., "end": 4., "text_zh": "今天的天气很好",
        "literal_vi": "Hom nay troi dep qua.", "natural_vi": "Hom nay troi dep qua.",
        "final_vi": "Hom nay troi dep qua.", "needs_review": True,
        "review_reason": "Nguồn chưa rõ.",
        "verification": {"status": "unresolved", "source_supported": False,
            "source_accepted": False, "semantic_verified": False, "address_verified": False}, **changes}


def answer(**changes):
    return {"final_vi": "Hôm nay trời đẹp quá.", "orthography_only": True,
        "ambiguous": False, "reason": "Ngữ pháp bản nháp đủ rõ; chỉ thêm dấu.", **changes}


def repair(source=None, responses=None, checkpoint=None, check=lambda: None):
    source = source or row()
    client = Mock(model="muse", has_credentials=True)
    client.translate.side_effect = responses or [answer(), answer()]
    reviewer = AutomaticTranslationReviewer(client, Mock(), audio_evidence=False)
    result = {"segments": {1: deepcopy(source)}, "summary": {}}
    result = reviewer._repair_target_orthography(result, [source], [source], client, check, checkpoint)
    return result["segments"][1], client


def test_independent_target_repair_preserves_source_uncertainty_and_drafts():
    original = row()
    result, client = repair(original)
    assert result["final_vi"] == "Hôm nay trời đẹp quá."
    assert original == row()
    for key in ("text_zh", "literal_vi", "natural_vi", "start", "end"):
        assert result[key] == original[key]
    assert result["needs_review"] is True
    audit = result["verification"]
    assert audit["status"] == "unresolved"
    assert audit["source_accepted"] is audit["source_supported"] is False
    assert audit["semantic_verified"] is audit["address_verified"] is False
    assert audit["target_orthography"]["probabilistic"] is True
    assert client.translate.call_count == 2


@pytest.mark.parametrize("first,second", [
    (answer(ambiguous=True), answer()),
    (answer(), answer(ambiguous=True)),
    (answer(), answer(orthography_only=False)),
    (answer(), answer(final_vi="Hôm nay trời rét quá.")),
    (answer(final_vi="Hôm nay trời đẹp quá!"), answer(final_vi="Hôm nay trời đẹp quá!")),
])
def test_ambiguity_disagreement_and_word_or_punctuation_changes_retain_draft(first, second):
    result, _ = repair(responses=[first, second])
    assert result["final_vi"] == row()["final_vi"]
    assert result["verification"]["target_orthography"]["status"] == "review_required"


@pytest.mark.parametrize("before,after", [
    ("Ba dang o nha.", "Bà đang ở nhà."),
    ("Toi da gap Lan roi.", "Tôi đã gặp Lân rồi."),
    ("Ban ay den day roi.", "Bạn ấy đến đây rồi."),
])
def test_no_relationship_or_name_guess_from_accent_free_skeleton(before, after):
    assert not AutomaticTranslationReviewer._orthography_only(before, after)


@pytest.mark.parametrize("changes", [
    {"verification": {"status": "manual"}},
    {"final_vi": "Hôm nay trời đẹp quá."},
    {"final_vi": "OK"},
])
def test_manual_and_non_candidates_do_not_request_provider(changes):
    source = row(**changes)
    result, client = repair(source)
    assert result["final_vi"] == source["final_vi"]
    client.translate.assert_not_called()


def test_prior_semantic_approval_does_not_skip_ascii_target_check():
    source = row(needs_review=False, verification={"status": "verified",
        "source_supported": True, "semantic_verified": True})
    assert AutomaticTranslationReviewer._qualified_context({}, source) == {}
    result, client = repair(source)
    assert client.translate.call_count == 2
    assert result["final_vi"] == answer()["final_vi"]
    assert result["needs_review"] is True
    assert result["verification"]["source_supported"] is True
    assert result["verification"]["semantic_verified"] is False


def test_naturally_unaccented_words_can_remain_unchanged_without_fake_correction():
    source = row(final_vi="Em mang hoa cho anh.", needs_review=False,
        verification={"status": "verified", "source_supported": True, "semantic_verified": True})
    unchanged = answer(final_vi=source["final_vi"])
    result, _ = repair(source, [unchanged, unchanged])
    assert result["final_vi"] == source["final_vi"]
    assert result["verification"]["target_orthography"]["status"] == "unchanged"
    assert result["needs_review"] is False
    assert AutomaticTranslationReviewer._qualified_context({}, result) == result


@pytest.mark.parametrize("responses", [None, [TimeoutError()]])
def test_orthography_provenance_and_failure_survive_storage_sanitizer(responses):
    from core.streaming.session_store import _clean
    result, _ = repair(responses=responses)
    original = deepcopy(result["verification"])
    cleaned = _clean(original)
    assert cleaned["target_orthography"] == original["target_orthography"]
    assert cleaned["semantic_verified"] is False


def test_review_cache_namespace_tracks_orthography_policy_implementation():
    from core.review_checkpoint import implementation_revision, file_digest
    from core import translation_review
    assert implementation_revision()["translation_review.py"] == file_digest(translation_review.__file__)


@pytest.mark.parametrize("response", [TimeoutError(), {"final_vi": "Hôm nay trời đẹp quá."}])
def test_optional_failure_retains_draft_and_visible_diagnostic(response):
    result, client = repair(responses=[response])
    assert result["final_vi"] == row()["final_vi"]
    assert result["needs_review"] is True
    assert result["verification"]["target_orthography"]["status"] == "failed"
    assert client.translate.call_count == 1


def test_cancel_propagates_after_request_instead_of_becoming_failed_draft():
    calls = 0

    def check():
        nonlocal calls
        calls += 1
        if calls > 1:
            raise RuntimeError("cancelled")

    with pytest.raises(RuntimeError, match="cancelled"):
        repair(check=check)


def test_transport_failure_enters_existing_review_retry_ledger():
    source = row()
    client = Mock(model="muse", has_credentials=True)
    client.translate.side_effect = TimeoutError()
    reviewer = AutomaticTranslationReviewer(client, Mock(), audio_evidence=False)
    result = reviewer._repair_target_orthography(
        {"segments": {1: deepcopy(source)}, "summary": {}}, [source], [source],
        client, lambda: None)
    assert result["retry_failures"] == [{"segment_ids": [1], "code": "provider_timeout"}]
    assert result["segments"][1]["final_vi"] == source["final_vi"]
    assert result["segments"][1]["verification"]["status"] == "incomplete"


def test_checkpoint_reuses_both_judgements_with_exact_target_and_context_identity():
    checkpoint = Mock()
    checkpoint.load.return_value = {"first": answer(), "second": answer()}
    result, client = repair(checkpoint=checkpoint)
    assert result["final_vi"] == answer()["final_vi"]
    client.translate.assert_not_called()
    identity = checkpoint.load.call_args.args[0]
    assert identity["text"] == row()["final_vi"]
    assert identity["context"][0]["text_zh"] == row()["text_zh"]


def test_corrupt_optional_checkpoint_gets_fresh_bounded_judgements():
    checkpoint = Mock()
    checkpoint.load.return_value = {"first": {}, "second": answer()}
    result, client = repair(checkpoint=checkpoint)
    assert result["final_vi"] == answer()["final_vi"]
    assert client.translate.call_count == 2
    checkpoint.store.assert_called_once()


def test_real_review_rejected_source_does_not_supply_target_spelling(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "opencode")
    source = row()
    source.pop("verification")
    rejected = {**source, "text_zh": "明天会下雨", "final_vi": "Ngày mai trời mưa.",
        "semantic_verified": True, "needs_review": False, "source_evidence_ids": [],
        "verification_reason": "Model đoán lại nguồn."}
    response = {"segments": [rejected], "screen_texts": [], "summary": ""}
    client = Mock(model="muse", has_credentials=True)
    client.translate.side_effect = [response, response, answer(), answer()]
    scanner = Mock()
    scanner.extract.return_value = []
    reviewer = AutomaticTranslationReviewer(client, scanner, audio_evidence=False)
    progress = []
    original_translate = client.translate.side_effect
    def translate(*args, **kwargs):
        # Even the last optional repair request is still active review work.
        assert 100 not in progress
        return next(original_translate)
    client.translate.side_effect = translate
    result = reviewer.review("unused.mp4", [source], [], progress_callback=progress.append)
    fixed = result["segments"][1]
    assert fixed["text_zh"] == source["text_zh"]
    assert fixed["final_vi"] == answer()["final_vi"]
    assert fixed["verification"]["semantic_verified"] is False
    assert fixed["needs_review"] is True
    assert result["summary"]["unresolved"] == 1
    assert progress[-1] == 100
    # A new independent target request does not see the rejected source or VI.
    for call in client.translate.call_args_list[2:]:
        assert "明天会下雨" not in call.args[0]
        assert "Ngày mai trời mưa" not in call.args[0]
