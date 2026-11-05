"""Timed context may corroborate a clipped crop; it never approves meaning."""
from copy import deepcopy
from unittest.mock import Mock

import pytest

from core.engines.asr.contextual_evidence import METHOD, PADDING, scope_reading, validate_reading
from core.streaming.session_store import _clean
from core.translation_review import AutomaticTranslationReviewer, _LocalAudioEvidence


def target():
    return {"id": 32, "start": 44.32, "end": 45.52, "text_zh": "我十八",
        "final_vi": "Mười tám tuổi.", "literal_vi": "Mười tám tuổi.", "natural_vi": "Mười tám tuổi.",
        "needs_review": True, "review_reason": "Crop chưa rõ.", "verification": {"source_supported": False}}


def neighbor():
    return {"id": 31, "start": 43.28, "end": 44.32, "text_zh": "你女儿",
        "verification": {"source_supported": True}}


def reading(engine="faster-whisper-small"):
    value = {"event": "evidence", "engine": engine, "id": 32,
        "text": "女儿我十八", "crop_start": 44.32 - PADDING, "crop_end": 45.52 + PADDING}
    if engine == "sensevoice":
        value["timing"] = [{"text": text, "at": at} for text, at in
            (("女", 44.04), ("儿", 44.26), ("我", 45.1), ("十", 45.28), ("八", 45.46))]
    else:
        value["timing"] = [{"text": text, "start": start, "end": end, "probability": .98}
            for text, start, end in (("女", 44., 44.22), ("儿", 44.22, 44.46),
                ("我", 44.9, 45.26), ("十", 45.26, 45.44), ("八", 45.44, 45.58))]
    return value


def test_context_retains_full_target_and_supported_neighbor_ownership():
    source, prior = target(), neighbor()
    before = deepcopy([source, prior])
    sense = scope_reading(reading("sensevoice"), source, [prior])
    whisper = scope_reading(reading(), source, [prior])
    assert sense["text_zh"] == whisper["text_zh"] == "我十八"
    assert sense["evidence"] == []
    assert whisper["evidence"] == [{"id": 31, "quote": "儿", "start": 44.22, "end": 44.46}]
    assert [source, prior] == before
    verification = {"audio_context_evidence": [sense, whisper], "audio_context_consensus": True}
    assert _clean(verification) == verification


def test_native_word_integrity_does_not_depend_on_display_sentence_spaces():
    row = target()
    text = "你闭嘴 十二岁在院子里洗澡 被隔壁大婶撞见"
    record = {"event": "evidence", "id": row["id"], "engine": "faster-whisper-large-v3",
        "text": text, "crop_start": row["start"]-PADDING, "crop_end": row["end"]+PADDING,
        "timing": [{"text": char, "start": 40.+i*.1, "end": 40.1+i*.1, "probability": .98}
            for i,char in enumerate(text.replace(" ", ""))]}
    assert validate_reading(record, row, record["engine"])
    record["timing"] = [item for item in record["timing"] if item["text"] != "不"]
    # A real omitted word (including negation) still invalidates the reading.
    record["text"] = "你闭嘴 十二岁不在院子里洗澡 被隔壁大婶撞见"
    assert not validate_reading(record, row, record["engine"])


@pytest.mark.parametrize("change", ["unknown_neighbor", "wrong_neighbor", "duplicate_owner",
    "low_confidence", "long_crossing", "shortened_source", "changed_number", "zero_word",
    "omitted_timing_word", "nan_time", "unordered", "wrong_crop", "wrong_engine", "missing_timing"])
def test_ambiguous_or_corrupt_context_cannot_become_source_evidence(change):
    source, prior, record = target(), neighbor(), reading()
    context = [prior]
    if change == "unknown_neighbor": prior["verification"]["source_supported"] = False
    elif change == "wrong_neighbor": prior["text_zh"] = "女友"
    elif change == "duplicate_owner": context.append({**deepcopy(prior), "id": 99})
    elif change == "low_confidence": record["timing"][2]["probability"] = .3
    elif change == "long_crossing": record["timing"][1]["end"] = 44.9
    elif change == "shortened_source": source["text_zh"] = "回来我十八"
    elif change == "changed_number": source["text_zh"] = "我十九"
    elif change == "zero_word": record["timing"][3]["end"] = record["timing"][3]["start"]
    elif change == "omitted_timing_word": record["timing"].pop()
    elif change == "nan_time": record["timing"][2]["end"] = float("nan")
    elif change == "unordered": record["timing"].reverse()
    elif change == "wrong_crop": record["crop_start"] = 43.
    elif change == "wrong_engine": record["engine"] = "majority-vote"
    elif change == "missing_timing": record.pop("timing")
    assert scope_reading(record, source, context) is None


def test_empty_context_is_not_silence_or_missing_text_recovery():
    row = target()
    row["text_zh"] = ""
    record = reading("sensevoice")
    record["timing"], record["text"] = [], ""
    assert scope_reading(record, row, []) is None


@pytest.mark.parametrize("prior,accepted", [("你18", True), ("你28", False), ("你8", False),
    ("你118", False), ("我18", True), ("第18", False)])
def test_boundary_numeral_compares_full_value_instead_of_matching_last_digit(prior, accepted):
    row = {"id": 2, "start": 10., "end": 11., "text_zh": "你好"}
    previous = {"id": 1, "start": 9., "end": 10., "text_zh": prior,
        "verification": {"source_supported": True}}
    timing = [{"text": text, "start": start, "end": end, "probability": .99}
        for text, start, end in (("你", 9.2, 9.7), ("十", 9.7, 9.9), ("八", 9.9, 10.1),
            ("你", 10.2, 10.4), ("好", 10.4, 10.6))]
    record = {"event": "evidence", "id": 2, "engine": "faster-whisper-small", "text": "你十八你好",
        "crop_start": 10. - PADDING, "crop_end": 11. + PADDING, "timing": timing}
    result = scope_reading(record, row, [previous])
    assert bool(result) is accepted
    if accepted:
        reference = result['evidence'][0]
        assert reference['text_zh'] == '十八' and reference['source'] == prior
        assert _clean({'audio_context_evidence': [result]})['audio_context_evidence'][0] == result


@pytest.mark.parametrize("provider", ["opencode", "ollama"])
@pytest.mark.parametrize("semantic_pass", [False, True])
@pytest.mark.parametrize("whisper_model", ["small", "large-v3"])
def test_contextual_consensus_still_requires_actual_semantic_and_address_audits(
        monkeypatch, provider, semantic_pass, whisper_model):
    from config import settings
    monkeypatch.setattr(settings, "LLM_PROVIDER", provider)
    monkeypatch.setattr(settings, "ASR_REVIEW_WHISPER_MODEL", whisper_model)
    whisper_engine = "faster-whisper-" + whisper_model
    row = target()
    audio = _LocalAudioEvidence()
    audio.collect = Mock(return_value={32: {"sensevoice": "我8。", whisper_engine: "我失败了"}})
    audio.last_errors, audio.last_error = {}, None
    audio.last_contextual_errors = {}
    audio.last_contextual_evidence = {32: [scope_reading(reading(engine), row, [neighbor()])
        for engine in ("sensevoice", whisper_engine)]}
    audio.collect_contextual = Mock(return_value={32: {"sensevoice": "我十八", whisper_engine: "我十八"}})
    client = Mock(has_credentials=True, model="offline")
    reply = {**row, "needs_review": not semantic_pass, "semantic_verified": semantic_pass,
        "verification_reason": "Đối chiếu đủ số tuổi.", "address_applicable": False,
        "address_neutral_faithful": True, "address_verified": False,
        "address_reason": "Không chọn quan hệ.", "address_uses": []}
    client.translate.return_value = {"segments": [reply], "screen_texts": [], "summary": ""}
    reviewer = AutomaticTranslationReviewer(client, Mock(), audio)
    reviewer._review_client = Mock(return_value=client)
    reviewer._address_reading = Mock(return_value={})
    result = reviewer.resolve_audio_uncertainty("unused", {"segments": {32: row},
        "screen_texts": [], "translation_sources": []}, context_segments=[neighbor(), row])
    value = result["segments"][32]
    assert value["needs_review"] is not semantic_pass
    assert value["verification"]["source_supported"] is True
    assert value["verification"]["provider"] == provider
    assert value["verification"]["audio_consensus"] is False
    assert value["verification"]["audio_context_consensus"] is True
    assert value["verification"]["audio_evidence"][0]["text_zh"] == "我8。"
    assert value["verification"]["audio_evidence"][1]["engine"] == whisper_engine
    assert value["verification"]["audio_context_evidence"][1]["engine"] == whisper_engine
    assert value["verification"]["evidence_mode"] == "dual-local-asr-context-text-review"
    assert client.translate.call_count == 2
    assert reviewer._address_reading.call_count == 1
    audio.collect_contextual.assert_called_once()
    assert _clean(value["verification"])["audio_context_evidence"] == value["verification"]["audio_context_evidence"]


def test_context_worker_failure_is_redacted_and_retains_draft(monkeypatch, caplog):
    from config import settings
    monkeypatch.setattr(settings, "LLM_PROVIDER", "opencode")
    row = target()
    audio = _LocalAudioEvidence()
    audio.collect = Mock(return_value={32: {"sensevoice": "我8。", "faster-whisper-small": "我失败了"}})
    audio.last_errors, audio.last_error = {}, None
    audio.collect_contextual = Mock(side_effect=RuntimeError("private-context-credential"))
    client = Mock(has_credentials=True, model="offline")
    result = AutomaticTranslationReviewer(client, Mock(), audio).resolve_audio_uncertainty("unused",
        {"segments": {32: row}, "screen_texts": [], "translation_sources": []})
    value = result["segments"][32]
    assert value["needs_review"] and value["final_vi"] == row["final_vi"]
    assert value["verification"]["diagnostic"]["code"] == "asr_failed"
    client.translate.assert_not_called()
    assert "private-context-credential" not in caplog.text + str(result)


@pytest.mark.parametrize("change", ["long_crop", "consensus", "operational_failure"])
def test_contextual_fallback_is_not_retried_for_healthy_or_operationally_failed_rows(monkeypatch, change):
    from config import settings
    monkeypatch.setattr(settings, "LLM_PROVIDER", "opencode")
    row = target()
    audio = _LocalAudioEvidence()
    audio.last_errors, audio.last_error = {}, None
    values = {"sensevoice": "我8", "faster-whisper-small": "我失败了"}
    if change == "long_crop": row["end"] = 50.
    elif change == "consensus": values["faster-whisper-small"] = "我8"
    else: audio.last_errors = {32: {"sensevoice": TimeoutError("private")}}
    audio.collect = Mock(return_value={32: values})
    audio.collect_contextual = Mock(side_effect=AssertionError("must not retry"))
    client = Mock(has_credentials=True, model="offline")
    reviewer = AutomaticTranslationReviewer(client, Mock(), audio)
    reviewer._address_reading = Mock(side_effect=RuntimeError("stop synthetic semantic request"))
    reviewer.resolve_audio_uncertainty("unused", {"segments": {32: row}, "screen_texts": [], "translation_sources": []})
    audio.collect_contextual.assert_not_called()


def test_context_provenance_rejects_arbitrary_fields_and_bad_ownership():
    item = scope_reading(reading(), target(), [neighbor()])
    item["credential"] = "private"
    assert "credential" not in _clean({"audio_context_evidence": [item]})["audio_context_evidence"][0]
    item["evidence"][0]["id"] = True
    with pytest.raises(ValueError):
        _clean({"audio_context_evidence": [item]})
