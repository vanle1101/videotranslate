from unittest.mock import Mock

import pytest

from config import settings
from core.translation_context import (contains_address_expression, needs_address_audit,
    address_expressions, validate_address_reading, address_reading_prompt, address_review_instruction)

from core.translation_review import AutomaticTranslationReviewer


def test_address_neutral_thant_tu_and_number_are_not_blocked_by_uncertain_reading():
    reading = {0: {"uncertain": True}}
    for source, candidate in (("啥", "Hả?"), ("十九", "Mười chín."), ("旁白", "Cô ấy bước vào.")):
        audit = {"address_applicable": False, "address_verified": False, "address_neutral_faithful": True,
                 "semantic_verified": True, "address_reason": "Câu cảm thán/số/lời kể giữ đủ ý không cần đổi xưng hô."}
        assert AutomaticTranslationReviewer._address_gate(reading, 0, audit, source, candidate) is False


def test_relation_bearing_candidate_still_requires_address_proof():
    reading = {0: {"uncertain": True}}
    audit = {"address_applicable": True, "address_verified": False, "address_reason": "chưa rõ"}
    assert AutomaticTranslationReviewer._address_gate(reading, 0, audit, "我问你", "Chị hỏi em.") is True
    assert AutomaticTranslationReviewer._address_gate(reading, 0, {"address_applicable": False}, "我问你", "Tôi thấy mẹ.") is True


def test_old_provider_reply_uses_conservative_relation_fallback():
    reading = {0: {"uncertain": True}}
    assert AutomaticTranslationReviewer._address_gate(reading, 0, {}, "啥", "Hả?") is True
    assert AutomaticTranslationReviewer._address_gate(reading, 0, {}, "我问你", "Chị hỏi em.") is True
    for candidate in ("Thế mày?", "Mẹ ơi!", "Em!", "Tôi thấy mẹ.", "Mày nhìn con mèo."):
        assert AutomaticTranslationReviewer._address_gate(reading, 0, {}, "我问你", candidate) is True


def test_provider_cannot_hide_visible_relation_by_calling_it_neutral():
    reading = {0: {"uncertain": True}}
    audit = {"address_applicable": False, "address_verified": False}
    assert AutomaticTranslationReviewer._address_applicable(audit, "我问你", "Thế mày?") is True
    assert AutomaticTranslationReviewer._address_gate(reading, 0, audit, "我问你", "Thế mày?") is True


def test_neutral_output_preserves_separate_not_applicable_flag():
    reading = {0: {"uncertain": True}}
    audit = {"address_applicable": False, "address_verified": False, "address_neutral_faithful": True,
             "semantic_verified": True, "address_reason": "Giữ đúng số tuổi, không cần phân vai."}
    assert AutomaticTranslationReviewer._address_applicable(audit, "十九", "Mười chín.") is False
    assert AutomaticTranslationReviewer._address_gate(reading, 0, audit, "十九", "Mười chín.") is False


def test_new_relationship_cannot_pass_when_no_source_reading_was_requested():
    assert AutomaticTranslationReviewer._address_gate({}, 0,
        {"address_applicable": False, "address_verified": False}, "你先走吧", "Em đi trước nhé.") is True


def test_address_verdict_helper_is_conservative_without_source_reading():
    assert not AutomaticTranslationReviewer._address_verified(
        {}, 0, {"address_verified": True, "address_reason": "provider said so"})


def listener_reading():
    return {12: {"id": 12, "self_address": "", "listener_address": "chị",
        "self_uncertain": True, "listener_uncertain": False, "uncertain": True,
        "reason": "Lời gọi trực tiếp 姐 xác nhận người nghe được gọi chị, chưa biết cách tự xưng.",
        "turn_check": {"ambiguous_roles": ["self"], "reason": "Nguồn chưa xác định vai người nói.",
                       "evidence": [{"id": 12, "quote": "拜托姐"}]},
        "evidence": [{"id": 12, "quote": "拜托姐"}]}}


def listener_audit():
    return {"address_applicable": True, "address_verified": True, "semantic_verified": True,
        "address_reason": "Chỉ giữ cách gọi chị trực tiếp trong 拜托姐, không thêm tự xưng hay ruột thịt.",
        "address_uses": [{"term": "Chị", "role": "listener"}, {"term": "chị", "role": "listener"}]}


def test_known_direct_listener_does_not_require_an_unused_self_address():
    assert address_expressions("Chị ơi, nhờ chị đấy!") == ["Chị", "chị"]
    assert AutomaticTranslationReviewer._address_gate(listener_reading(), 12, listener_audit(),
        "拜托姐", "Chị ơi, nhờ chị đấy!") is False


def test_exact_named_listener_retains_full_grounded_form_at_its_position():
    reading = listener_reading()
    reading[12]["listener_address"] = "anh Dã"
    reading[12]["evidence"] = [{"id": 12, "quote": "野哥"}]
    audit = {**listener_audit(), "address_uses": [{"term": "Anh Dã", "role": "listener"}]}
    assert address_expressions("Anh Dã ơi!") == ["Anh"]
    assert address_expressions("Anh Dã ơi!", grounded_terms=["anh Dã"]) == ["Anh Dã"]
    assert AutomaticTranslationReviewer._address_gate(reading, 12, audit, "野哥", "Anh Dã ơi!") is False


@pytest.mark.parametrize("candidate,uses", [
    ("Anh ơi!", [{"term": "Anh", "role": "listener"}]),
    ("Anh Dã ơi!", [{"term": "Anh", "role": "listener"}]),
    ("Anh, Dã ơi!", [{"term": "Anh Dã", "role": "listener"}]),
    ("Anh Dũng ơi!", [{"term": "Anh Dũng", "role": "listener"}]),
    ("Anh hỏi anh Dã.", [{"term": "anh Dã", "role": "listener"}, {"term": "Anh", "role": "listener"}]),
    ("Anh Dã ơi!", [{"term": "Anh Dã", "role": "self"}]),
])
def test_named_role_does_not_prove_abbreviations_other_names_order_or_unknown_self(candidate, uses):
    reading = listener_reading()
    reading[12]["listener_address"] = "anh Dã"
    audit = {**listener_audit(), "address_uses": uses}
    assert AutomaticTranslationReviewer._address_gate(reading, 12, audit, "野哥", candidate) is True


def test_grounded_terms_never_shorten_compound_kinship_or_classify_new_roles():
    assert address_expressions("Bố mẹ về rồi.", grounded_terms=["bố", "mẹ"]) == ["Bố mẹ"]
    assert address_expressions("Con gái à.", grounded_terms=["con"]) == ["Con gái"]
    assert address_expressions("Anh rất tức!", grounded_terms=["anh rất tức"]) == ["Anh"]
    assert address_expressions("Anh Dã, chị Mai ơi!", grounded_terms=["anh Dã", "chị Mai"]) == ["Anh Dã", "chị Mai"]


def test_known_self_does_not_require_an_unused_listener_address():
    reading = {0: {"id": 0, "self_address": "con", "listener_address": "",
        "self_uncertain": False, "listener_uncertain": True, "uncertain": True,
        "reason": "Nguồn và ngữ cảnh xác nhận người con tự nói đói, không cần thêm lời gọi.",
        "evidence": [{"id": 0, "quote": "我饿了"}]}}
    audit = {**listener_audit(), "address_uses": [{"term": "Con", "role": "self"}]}
    assert AutomaticTranslationReviewer._address_gate(reading, 0, audit, "我饿了", "Con đói rồi.") is False


@pytest.mark.parametrize("uses,candidate", [
    ([{"term": "Em", "role": "self"}, {"term": "chị", "role": "listener"}], "Em nhờ chị!"),
    ([{"term": "chị", "role": "listener"}], "Em nhờ chị!"),
    ([{"term": "Chị", "role": "self"}, {"term": "chị", "role": "listener"}], "Chị nhờ chị!"),
    ([{"term": "Chị", "role": "listener"}], "Chị ơi, nhờ chị đấy!"),
    ([{"term": "em", "role": "listener"}], "Em ơi!"),
    ([{"term": "chị", "role": "unknown"}], "Chị ơi!"),
    ([], "Chị ơi!"),
    (None, "Chị ơi!"),
])
def test_partial_address_certainty_cannot_cover_missing_extra_or_uncertain_usage(uses, candidate):
    audit = {**listener_audit(), "address_uses": uses}
    assert AutomaticTranslationReviewer._address_gate(listener_reading(), 12, audit, "拜托姐", candidate) is True


@pytest.mark.parametrize("field,value", [("listener_uncertain", True), ("listener_uncertain", None),
    ("listener_address", "em"), ("listener_address", "chị/em"), ("evidence", [])])
def test_granular_acceptance_requires_cited_source_reading_for_the_used_role(field, value):
    reading = listener_reading()
    reading[12][field] = value
    assert AutomaticTranslationReviewer._address_gate(reading, 12, listener_audit(),
        "拜托姐", "Chị ơi, nhờ chị đấy!") is True


@pytest.mark.parametrize("field,value", [("address_verified", False), ("semantic_verified", False),
    ("address_reason", ""), ("address_uses", [])])
def test_granular_source_reading_does_not_replace_independent_semantic_review(field, value):
    audit = {**listener_audit(), field: value}
    assert AutomaticTranslationReviewer._address_gate(listener_reading(), 12, audit,
        "拜托姐", "Chị ơi, nhờ chị đấy!") is True


def test_granular_reading_validation_and_prompt_do_not_make_unknown_self_certain():
    rows = [{"id": 12, "text_zh": "拜托姐"}]
    data = {"address_context": list(listener_reading().values())}
    reading = validate_address_reading(data, rows, rows)
    assert reading[12]["uncertain"] is True
    assert reading[12]["listener_uncertain"] is False
    assert reading[12]["self_uncertain"] is True
    assert "self_uncertain" in address_reading_prompt(rows, rows)
    assert "address_uses" in address_review_instruction(reading)
    assert "CHỈ các vai thực sự" in address_review_instruction(reading)


@pytest.mark.parametrize("field,value", [("listener_uncertain", "false"),
    ("listener_address", ""), ("evidence", []), ("uncertain", False)])
def test_malformed_or_contradictory_role_certainty_is_rejected(field, value):
    item = listener_reading()[12]
    item[field] = value
    rows = [{"id": 12, "text_zh": "拜托姐"}]
    with pytest.raises(ValueError):
        validate_address_reading({"address_context": [item]}, rows, rows)


def test_legacy_uncertain_reading_does_not_gain_partial_certainty_from_the_audit():
    reading = listener_reading()
    del reading[12]["listener_uncertain"]
    assert AutomaticTranslationReviewer._address_gate(reading, 12, listener_audit(),
        "拜托姐", "Chị ơi, nhờ chị đấy!") is True


def test_full_review_accepts_only_the_source_grounded_listener_usage(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "opencode")
    candidate = "Chị ơi, nhờ chị đấy!"
    source = {"id": 12, "start": 12.42, "end": 13.3, "text_zh": "拜托姐",
        "literal_vi": candidate, "natural_vi": candidate, "final_vi": candidate,
        "needs_review": False, "review_reason": ""}
    def respond(prompt, **kwargs):
        if "ID cần kiểm định: " in prompt:
            return {"address_context": list(listener_reading().values())}
        return {"segments": [{**source, **listener_audit(), "source_evidence_ids": ["review0"],
            "verification_reason": "Giữ lời nhờ vả và gọi chị trực tiếp, không thêm tự xưng."}],
            "screen_texts": [], "summary": ""}
    client = Mock(has_credentials=True, model="offline-role-scope", translate=Mock(side_effect=respond))
    scanner = Mock(extract=Mock(return_value=[{"start": 12.42, "end": 13.3, "text_zh": "拜托姐",
        "confidence": .99, "bbox": [.2, .7, .5, .1]}]))
    row = AutomaticTranslationReviewer(client, scanner, audio_evidence=False).review("unused", [source], [])["segments"][12]
    audit = row["verification"]
    assert audit["source_supported"] and audit["semantic_verified"] and audit["address_verified"]
    assert audit["address_context"]["uncertain"] is True
    assert audit["address_uses"] == listener_audit()["address_uses"]
    assert not row["needs_review"]
    assert client.translate.call_count == 3


def test_dropping_essential_speaker_cannot_pass_only_because_words_are_neutral():
    assert AutomaticTranslationReviewer._address_gate({0: {"uncertain": True}}, 0,
        {"address_applicable": False, "address_neutral_faithful": False, "semantic_verified": True,
         "address_reason": "Bản dịch bỏ ý tôi mới là người hỏi."}, "这话应该我来问吧", "Phải hỏi chứ.") is True


@pytest.mark.parametrize("candidate", ["Tôi đang làm việc.", "Bạn đi trước.", "Cậu hỏi nhé.",
    "Cháu hỏi nhé.", "Dì đến đây.", "Ta sẽ đi.", "Mi nói đi.", "Thế mày?"])
def test_selector_and_verifier_use_the_same_pronoun_detection(candidate):
    assert contains_address_expression(candidate)
    assert needs_address_audit([{"text_zh": "我在工作", "final_vi": candidate}])
    assert AutomaticTranslationReviewer._address_applicable({}, "我在工作", candidate)


@pytest.mark.parametrize("candidate", ["Ba phút.", "Đi một mình.", "Con vật chạy qua.",
    "Thuốc dạ dày vẫn để ở ngăn thứ ba.", "Đến vào thứ Ba."])
def test_unambiguous_non_address_spans_do_not_trigger_pronoun_audit(candidate):
    assert not contains_address_expression(candidate)
    assert not needs_address_audit([{"text_zh": "三分钟", "final_vi": candidate}])
    assert not AutomaticTranslationReviewer._address_applicable({"address_applicable": False}, "三分钟", candidate)


def test_excluded_numeric_span_does_not_remove_a_real_parent_address():
    assert contains_address_expression("Ba chờ ba phút.")
    assert contains_address_expression("Mày đi một mình.")
    assert address_expressions("Ba để thuốc ở ngăn thứ ba.") == ["Ba"]


@pytest.mark.parametrize("initial,candidate,expected_reading", [
    ("Tôi đang làm việc.", "Tôi đang làm việc.", True),
    ("Đang làm việc.", "Em đang làm việc.", False),
])
def test_real_review_path_requests_reading_for_existing_pronoun_and_records_new_unproved_pronoun(
        monkeypatch, initial, candidate, expected_reading):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "opencode")
    source = {"id": 0, "start": 0., "end": 2., "text_zh": "我在工作",
              "literal_vi": initial, "natural_vi": initial, "final_vi": initial,
              "needs_review": False, "review_reason": ""}
    requests = []
    def respond(prompt, **kwargs):
        requests.append(prompt)
        if "ID cần kiểm định: " in prompt:
            return {"address_context": [{"id": 0, "self_address": "tôi", "listener_address": "",
                "uncertain": False, "reason": "Người nói tự nói đang làm việc, không suy thêm quan hệ.",
                "turn_check": {"ambiguous_roles": [], "reason": "Không có lời đối chiếu của người khác.",
                               "evidence": [{"id": 0, "quote": "我在工作"}]},
                "evidence": [{"id": 0, "quote": "我在工作"}]}]}
        return {"segments": [{**source, "literal_vi": candidate, "natural_vi": candidate, "final_vi": candidate,
            "semantic_verified": True, "verification_reason": "Giữ đang làm việc.",
            "source_evidence_ids": ["review0"], "address_applicable": True,
            "address_verified": True, "address_reason": "Đối chiếu lời nguồn."}], "screen_texts": [], "summary": ""}
    client = Mock(has_credentials=True, model="offline-selection", translate=Mock(side_effect=respond))
    scanner = Mock(extract=Mock(return_value=[{"start": 0., "end": 2., "text_zh": "我在工作",
        "confidence": .99, "bbox": [.2, .7, .5, .1]}]))
    reviewer = AutomaticTranslationReviewer(client, scanner, audio_evidence=False)
    result = reviewer.review("unused", [source], [])["segments"][0]
    assert any("ID cần kiểm định: " in prompt for prompt in requests) is expected_reading
    assert result["verification"]["address_applicable"] is True
    assert result["verification"]["address_verified"] is expected_reading
    assert result["needs_review"] is (not expected_reading)


def test_uncertain_first_person_warning_does_not_claim_a_sibling_relationship(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "opencode")
    source = {"id": 0, "start": 0., "end": 2., "text_zh": "跟我走",
              "literal_vi": "Đi theo tôi.", "natural_vi": "Đi theo tôi.",
              "final_vi": "Đi theo tôi.", "needs_review": False, "review_reason": ""}
    def respond(prompt, **kwargs):
        if "ID cần kiểm định: " in prompt:
            return {"address_context": [{"id": 0, "self_address": "", "listener_address": "",
                "uncertain": True, "reason": "Chưa có căn cứ xác định quan hệ giữa hai người.",
                "turn_check": {"ambiguous_roles": ["self", "listener"], "reason": "Chưa rõ người nói/người nghe.",
                               "evidence": [{"id": 0, "quote": "跟我走"}]},
                "evidence": [{"id": 0, "quote": "跟我走"}]}]}
        return {"segments": [{**source, "semantic_verified": True,
            "verification_reason": "Giữ hành động đi theo người nói.",
            "source_evidence_ids": ["review0"], "address_applicable": True,
            "address_verified": False, "address_reason": "Tôi chưa được xác nhận theo quan hệ."}],
            "screen_texts": [], "summary": ""}
    client = Mock(has_credentials=True, model="offline-warning", translate=Mock(side_effect=respond))
    scanner = Mock(extract=Mock(return_value=[{"start": 0., "end": 2., "text_zh": "跟我走",
        "confidence": .99, "bbox": [.2, .7, .5, .1]}]))
    row = AutomaticTranslationReviewer(client, scanner, audio_evidence=False).review("unused", [source], [])["segments"][0]
    assert row["needs_review"] and row["verification"]["source_supported"]
    assert not row["verification"]["address_verified"]
    assert "Chưa đủ bằng chứng" in row["review_reason"]
    assert "chị/em" not in row["review_reason"]
    assert row["verification"]["reason"] == row["review_reason"]


@pytest.mark.parametrize("candidate,expected", [
    ("Này, cô gái!", ["cô gái"]),
    ("Con gái à.", ["Con gái"]),
    ("Con trai ơi, nhờ con trai đấy!", ["Con trai", "con trai"]),
    ("Cô gái ơi, nhờ cô gái đấy!", ["Cô gái", "cô gái"]),
    ("Em nhờ chị gái hỏi anh trai.", ["Em", "chị gái", "anh trai"]),
    ("Em trai và em gái đợi bố mẹ.", ["Em trai", "em gái", "bố mẹ"]),
    ("Nếu bố mẹ không quen nhau…", ["bố mẹ"]),
    ("Ba má gọi mẹ ba.", ["Ba má", "mẹ ba"]),
    ("Bố và mẹ đợi con.", ["Bố", "mẹ", "con"]),
    ("Mẹ mẹ đợi nhé.", ["Mẹ", "mẹ"]),
    ("Ba ba chờ ba phút.", ["Ba", "ba"]),
])
def test_address_selector_preserves_complete_phrases_and_repeated_occurrences(candidate, expected):
    assert address_expressions(candidate) == expected


def phrase_reading(term, role, *, certain=True, source="姑娘"):
    other = "self" if role == "listener" else "listener"
    return {10: {"id": 10, f"{role}_address": term if certain else "",
        f"{role}_uncertain": not certain, f"{other}_address": "", f"{other}_uncertain": True,
        "uncertain": True, "reason": "Chỉ vai được dùng có dẫn chứng độc lập từ lời nguồn.",
        "turn_check": {"ambiguous_roles": [other] if certain else ["self", "listener"],
            "reason": "Vai chưa dùng còn chưa rõ; không suy sự chắc chắn sang vai này.",
            "evidence": [{"id": 10, "quote": source}]},
        "evidence": [{"id": 10, "quote": source}]}}


def phrase_audit(uses):
    return {"address_applicable": True, "address_verified": True, "semantic_verified": True,
        "address_reason": "Đã đối chiếu đúng cụm được dùng với lời nguồn độc lập.",
        "address_uses": uses}


def test_actual_cogai_phrase_matches_grounded_listener_without_guessing_speaker():
    reading = phrase_reading("cô gái", "listener")
    audit = phrase_audit([{"term": "cô gái", "role": "listener"}])
    assert not AutomaticTranslationReviewer._address_gate(reading, 10, audit, "姑娘", "Này, cô gái!")
    assert reading[10]["self_uncertain"] is True
    assert reading[10]["uncertain"] is True


@pytest.mark.parametrize("term,source", [("Con gái", "女儿"), ("Con trai", "儿子")])
def test_full_child_address_matches_only_independently_grounded_whole_phrase(term, source):
    audit = phrase_audit([{"term": term, "role": "listener"}])
    assert not AutomaticTranslationReviewer._address_gate(
        phrase_reading(term, "listener", source=source), 10, audit, source, term + " à.")
    assert AutomaticTranslationReviewer._address_gate(
        phrase_reading("con", "listener", source=source), 10, audit, source, term + " à.")
    assert AutomaticTranslationReviewer._address_gate(
        phrase_reading(term, "listener", certain=False, source=source), 10, audit, source, term + " à.")


@pytest.mark.parametrize("term,source", [("bố mẹ", "我们要是不认识"), ("ba má", "我们要是不认识")])
def test_collective_self_requires_explicit_certainty_about_the_whole_phrase(term, source):
    candidate = f"Nếu {term} không quen nhau…"
    audit = phrase_audit([{"term": term, "role": "self"}])
    assert not AutomaticTranslationReviewer._address_gate(
        phrase_reading(term, "self", source=source), 10, audit, source, candidate)
    assert AutomaticTranslationReviewer._address_gate(
        phrase_reading(term, "self", certain=False, source=source), 10, audit, source, candidate)


@pytest.mark.parametrize("uses,candidate,reading_term", [
    ([{"term": "cô", "role": "listener"}], "Này, cô gái!", "cô gái"),
    ([{"term": "cô gái", "role": "listener"}], "Này, cô gái!", "cô"),
    ([{"term": "cô gái", "role": "self"}], "Này, cô gái!", "cô gái"),
    ([{"term": "cô gái", "role": "listener"}], "Cô gái ơi, cô gái!", "cô gái"),
    ([{"term": "cô gái", "role": "listener"}, {"term": "cô gái", "role": "listener"}],
        "Cô gái ơi!", "cô gái"),
    ([{"term": "bố", "role": "listener"}, {"term": "mẹ", "role": "listener"}],
        "Bố mẹ ơi!", "bố mẹ"),
    ([{"term": "bố mẹ", "role": "listener"}], "Bố mẹ ơi!", "bố"),
])
def test_complete_phrase_gate_rejects_prefixes_wrong_roles_missing_and_invented_uses(uses, candidate, reading_term):
    assert AutomaticTranslationReviewer._address_gate(
        phrase_reading(reading_term, "listener"), 10, phrase_audit(uses), "姑娘", candidate)


def test_repeated_complete_phrase_passes_only_with_every_grounded_use():
    uses = [{"term": "Cô gái", "role": "listener"}, {"term": "cô gái", "role": "listener"}]
    assert not AutomaticTranslationReviewer._address_gate(
        phrase_reading("cô gái", "listener"), 10, phrase_audit(uses), "姑娘", "Cô gái ơi, cô gái!")


def test_third_person_exclusion_preserves_other_real_addresses():
    assert address_expressions("Cậu ấy bảo em nhờ chị.") == ["em", "chị"]
    assert address_expressions("Mẹ thấy cậu ấy gọi mẹ.") == ["Mẹ", "mẹ"]
    assert address_expressions("Cậu ấy bảo cậu đi trước.") == ["cậu"]
    assert not contains_address_expression("Cậu ấy đi trước.")
    audit = {"address_applicable": False, "address_neutral_faithful": True,
        "semantic_verified": True, "address_reason": "Giữ lời kể người thứ ba, không gán vai người nghe."}
    assert AutomaticTranslationReviewer._address_gate(
        {10: {"uncertain": True}}, 10, audit, "他先走", "Cậu ấy đi trước.") is False
    assert AutomaticTranslationReviewer._address_gate(
        {10: {"uncertain": True}}, 10, audit, "他叫你先走", "Cậu ấy bảo em đi trước.") is True


@pytest.mark.parametrize("term", ["anh ta", "cô ta", "chị ta", "ông ta", "bà ta", "cậu ta"])
def test_explicit_third_person_ta_phrase_does_not_become_self_address(term):
    assert address_expressions(f"Sau này {term} làm ăn thế nào?") == []
    assert address_expressions(f"Mẹ của {term} gọi con.") == ["con"]
    audit = {"address_applicable": False, "address_neutral_faithful": True,
        "semantic_verified": True, "address_reason": "Nguồn 他 nói về người thứ ba."}
    assert not AutomaticTranslationReviewer._address_gate(
        {10: {"uncertain": True}}, 10, audit, "以后他混的怎么样", f"Sau này {term} làm ăn thế nào?")
    audit["semantic_verified"] = False
    assert AutomaticTranslationReviewer._address_gate(
        {10: {"uncertain": True}}, 10, audit, "以后他混的怎么样", f"Sau này {term} làm ăn thế nào?")


def test_third_person_ta_exclusion_preserves_standalone_and_separated_roles():
    assert address_expressions("Ta về rồi.") == ["Ta"]
    assert address_expressions("Anh, ta đi thôi!") == ["Anh", "ta"]
    assert address_expressions("Cô ấy bảo anh ta gọi mẹ.") == ["mẹ"]


def test_kinship_references_cannot_bypass_source_grounded_role_schema():
    # The schema currently only proves self/listener. Do not classify a kinship
    # noun or named honorific as harmless by silently removing it from the gate.
    reading = {10: {"uncertain": True}}
    audit = {"address_applicable": False, "address_neutral_faithful": True,
        "semantic_verified": True, "address_reason": "Provider gọi đây là lời kể."}
    for candidate in ("Con gái!", "Mẹ!", "Anh Dã là bạn trên mạng."):
        assert contains_address_expression(candidate)
        assert AutomaticTranslationReviewer._address_gate(reading, 10, audit, "他女儿", candidate)


@pytest.mark.parametrize("candidate,terms", [
    ("Đúng là con gái cậu ấy à?", []),
    ("Mẹ của cô ấy rất xinh.", []),
    ("Bố anh ấy đến rồi.", []),
    ("Chị gái của cậu ấy đến rồi.", []),
    ("Con gái của tôi đến rồi.", ["tôi"]),
    ("Mẹ của em gọi chị.", ["em", "chị"]),
    ("Mẹ ơi, con gái cậu ấy về rồi.", ["Mẹ"]),
    ("Con gái, cậu ấy gọi con.", ["Con gái", "con"]),
    ("Mẹ em đến rồi.", ["Mẹ", "em"]),
])
def test_explicit_possessive_reference_does_not_invent_self_or_listener(candidate, terms):
    assert address_expressions(candidate) == terms


def test_actual_third_person_kinship_still_requires_independent_neutral_semantic_verdict():
    reading = {74: {"uncertain": True}}
    audit = {"address_applicable": False, "address_neutral_faithful": True,
        "semantic_verified": True,
        "address_reason": "你真是他女儿 hỏi đúng người nghe có phải con gái người thứ ba; không chọn cách gọi người nghe."}
    assert not AutomaticTranslationReviewer._address_gate(
        reading, 74, audit, "你真是他女儿", "Đúng là con gái cậu ấy à?")
    for key in ("address_neutral_faithful", "semantic_verified"):
        assert AutomaticTranslationReviewer._address_gate(
            reading, 74, {**audit, key: False}, "你真是他女儿", "Đúng là con gái cậu ấy à?")
    assert AutomaticTranslationReviewer._address_gate(
        reading, 74, audit, "你真是他女儿", "Em đúng là con gái cậu ấy à?")


def test_full_review_recovers_actual_grounded_cogai_phrase(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "opencode")
    candidate = "Này, cô gái!"
    source = {"id": 10, "start": 14.99, "end": 15.47, "text_zh": "姑娘",
        "asr_text": "姑娘", "literal_vi": candidate, "natural_vi": candidate, "final_vi": candidate,
        "needs_review": True, "review_reason": "Lượt cũ chưa khớp cả cụm cách gọi."}
    reading = phrase_reading("cô gái", "listener")
    def respond(prompt, **kwargs):
        if "ID cần kiểm định: " in prompt:
            return {"address_context": list(reading.values())}
        return {"segments": [{**source, "needs_review": False, "review_reason": "",
            **phrase_audit([{"term": "cô gái", "role": "listener"}]),
            "source_evidence_ids": ["review0"],
            "verification_reason": "Giữ lời gọi trực tiếp người nghe trong 姑娘; không đoán người nói."}],
            "screen_texts": [], "summary": ""}
    client = Mock(has_credentials=True, model="offline-complete-phrase", translate=Mock(side_effect=respond))
    scanner = Mock(extract=Mock(return_value=[{"start": 14.99, "end": 15.47, "text_zh": "姑娘",
        "confidence": .99, "bbox": [.2, .7, .5, .1]}]))
    row = AutomaticTranslationReviewer(client, scanner, audio_evidence=False).review(
        "unused", [source], [])["segments"][10]
    assert not row["needs_review"]
    assert row["final_vi"] == candidate
    assert row["verification"]["source_supported"] and row["verification"]["semantic_verified"]
    assert row["verification"]["address_verified"]
    assert row["verification"]["address_context"]["self_uncertain"]
    assert client.translate.call_count == 3
