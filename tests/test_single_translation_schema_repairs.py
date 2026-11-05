import json
from unittest.mock import Mock

import pytest

from config import settings
from core.engines.translation.semantic_translator import SemanticTranslator
from core.structured_response import StructuredResponseError


def test_single_sentence_allows_two_structure_repairs_without_changing_source(monkeypatch):
    monkeypatch.setattr(settings, "OPENCODE_SCHEMA_REPAIR_ATTEMPTS", 2)
    engine = SemanticTranslator("opencode")
    request = Mock(side_effect=['{"final_vi":', '{}', json.dumps({
        "literal_vi": "Xin chào.", "natural_vi": "Xin chào.", "final_vi": "Xin chào."})])
    monkeypatch.setattr(engine, "_opencode_request", request)
    assert engine.translate_single_segment("你好", 2)["final_vi"] == "Xin chào."
    assert request.call_count == 3
    original = request.call_args_list[0].args[1]
    assert all(call.args[1].startswith(original) for call in request.call_args_list)


def test_single_sentence_stops_after_second_structure_repair(monkeypatch):
    monkeypatch.setattr(settings, "OPENCODE_SCHEMA_REPAIR_ATTEMPTS", 2)
    engine = SemanticTranslator("opencode")
    request = Mock(return_value='{"literal_vi":')
    monkeypatch.setattr(engine, "_opencode_request", request)
    with pytest.raises(StructuredResponseError):
        engine.translate_single_segment("你好", 2)
    assert request.call_count == 3
