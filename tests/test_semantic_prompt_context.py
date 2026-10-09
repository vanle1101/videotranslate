"""Offline regression for complete source context through real prompt builders."""
from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import Mock

from config import settings
from core.semantic_segments import SEMANTIC_TRANSLATION_POLICY
from core.translation_context import dialogue_context
from core.translation_review import AutomaticTranslationReviewer
from core.video_intelligence import VideoIntelligence


def source_rows():
    proof = {"speaker_id": "audio-a", "verified": True, "method": "audio_diarization",
        "confidence": .96, "scope_id": "source-video", "model": "actual-upstream-model"}
    return [{"id": 112, "start": 180., "end": 181.25, "text_zh": "我真的不是回来",
             "speaker_id": "audio-a", "speaker_evidence": deepcopy(proof),
             "source_asr_row_id": 17, "source_piece_index": 0, "source_piece_count": 2},
            {"id": 113, "start": 181.3, "end": 182.8, "text_zh": "跟你开玩笑的",
             "speaker_id": "audio-a", "speaker_evidence": deepcopy(proof),
             "source_asr_row_id": 17, "source_piece_index": 1, "source_piece_count": 2}]


def semantic_payload(prompt):
    value = prompt.split("Semantic context (reference-only IDs", 1)[1].split(": ", 1)[1]
    return json.JSONDecoder().raw_decode(value)[0]


def test_prepass_keeps_full_source_proof_and_manual_protection_across_batch_boundary(tmp_path):
    rows = source_rows()
    rows[1].update(verification={"status": "manual"}, revision=3)
    processor = object.__new__(VideoIntelligence)
    processor._checkpoint_context = None
    processor._source_dialogue = []
    processor._checkpoint_identity = Mock(return_value=None)
    seen = []
    processor._prepass = lambda *args, **kwargs: seen.extend(deepcopy(processor._source_dialogue)) or {}
    processor.prepass(tmp_path / "source.mp4", [rows[0]], 183, context_segments=rows)
    unit, = VideoIntelligence._semantic_context(seen, [rows[0]])["units"]
    assert unit["source_ids"] == [112, 113]
    assert unit["same_speaker_confirmed"] and not unit["can_combine_dubbing"]
    assert unit["source_parts"][1]["manual_edit"]
    assert unit["source_parts"][1]["manual_revision"] == 3
    assert seen[0]["speaker_evidence"] == rows[0]["speaker_evidence"]
    seen[0]["speaker_evidence"]["confidence"] = 0
    assert rows[0]["speaker_evidence"]["confidence"] == .96
    assert processor._source_dialogue == []  # Another video cannot inherit this context.


def test_changed_speaker_proof_invalidates_visual_checkpoint_identity(tmp_path):
    path = tmp_path / "source.mp4"
    path.write_bytes(b"offline-source-identity")
    processor = object.__new__(VideoIntelligence)
    processor.provider = "opencode"
    processor.client = SimpleNamespace(model="offline-context-test")
    rows = source_rows()
    before = processor._checkpoint_identity(path, rows, 183)
    rows[1]["speaker_evidence"]["scope_id"] = "different-audio-scope"
    after = processor._checkpoint_identity(path, rows, 183)
    assert before and after and before["key"] != after["key"]


def test_source_translation_and_recheck_share_full_semantic_unit_with_one_row_batches(monkeypatch):
    rows = source_rows()
    requested = []
    contexts = []
    processor = object.__new__(VideoIntelligence)
    processor.provider = "opencode"
    processor._checkpoint_context = None
    processor._source_dialogue = deepcopy(rows)
    processor.OPENCODE_SPEECH_BATCH_SIZE = 1
    processor._write_checkpoint = Mock()

    def respond(prompt, **kwargs):
        requested.append(prompt)
        if "Requested ASR: " in prompt:
            focus = json.loads(prompt.split("Requested ASR: ")[1].split("\nMeasured OCR:")[0])
            context = json.loads(prompt.split("\nContext: ")[1].split("\nCác semantic_units")[0])
            contexts.append(context["semantic_context"])
            return {"segments": [{"id": row["id"], "text_zh": row["asr_text"],
                "evidence_ids": [], "needs_review": False, "review_reason": ""} for row in focus]}
        focus = json.JSONDecoder().raw_decode(prompt.split("Mốc câu cần xuất: ")[1])[0]
        context = json.loads(prompt.split("Toàn bộ ngữ cảnh đoạn (chỉ tham khảo): ")[1].split("\nNgữ cảnh trước:")[0])
        contexts.append(context["semantic_context"])
        return {"segments": [{"id": row["id"], "start": row["start"], "end": row["end"],
            "text_zh": row["corrected_text_zh"], "literal_vi": "Lời Việt có căn cứ.",
            "natural_vi": "Lời Việt có căn cứ.", "final_vi": "Lời Việt có căn cứ.",
            "needs_review": True, "review_reason": "Fixture không xác nhận ngữ nghĩa."} for row in focus],
            "screen_texts": [], "summary": "Mệnh đề tiếp qua hai ID."}

    client = Mock(has_credentials=True, model="offline-context-test", translate=Mock(side_effect=respond))
    monkeypatch.setattr("core.video_intelligence.OpenCodeZenClient", Mock(return_value=client))
    payload = [{"id": row["id"], "start": row["start"], "end": row["end"], "asr_text": row["text_zh"]} for row in rows]
    result = processor._translate_text(payload, [], "", 180, 183)
    assert len(requested) == 6 and all(SEMANTIC_TRANSLATION_POLICY in prompt for prompt in requested)
    assert contexts[0] == contexts[1] == contexts[2]
    assert contexts[3] == contexts[4] == contexts[5]
    assert all(context["units"][0]["source_ids"] == [112, 113] for context in contexts)
    assert set(result["segments"]) == {112, 113}
    # Stable complete-unit input hashes participate in each persisted batch key.
    stages = [call.args[0] for call in processor._write_checkpoint.call_args_list]
    assert all(isinstance(stage["context"], str) and len(stage["context"]) == 64 for stage in stages)


def test_review_and_address_prompts_and_checkpoints_use_identical_original_semantic_source(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "opencode")
    rows = source_rows()
    for row in rows:
        row.update(literal_vi="Lời Việt.", natural_vi="Lời Việt.", final_vi="Lời Việt.",
                   needs_review=False, review_reason="")
    rows[0]["text_zh"] = "姐姐我真的不是回来"
    requests = []

    def respond(prompt, **kwargs):
        requests.append(prompt)
        if "ID cần kiểm định: " in prompt:
            return {"address_context": [{"id": 112, "self_address": "", "listener_address": "",
                "self_uncertain": True, "listener_uncertain": True, "uncertain": True,
                "reason": "Speaker evidence không xác định người nghe hay quan hệ.",
                "evidence": [{"id": 112, "quote": rows[0]["text_zh"]}],
                "turn_check": {"ambiguous_roles": ["self", "listener"], "reason": "Quan hệ chưa đủ rõ.",
                    "evidence": [{"id": 112, "quote": rows[0]["text_zh"]}]}}]}
        return {"segments": [{**rows[0], "semantic_verified": True,
            "verification_reason": "Đã đọc phần nối nhưng quan hệ chưa rõ.", "source_evidence_ids": ["review0"],
            "address_applicable": False, "address_neutral_faithful": False, "address_verified": False,
            "address_uses": [], "address_reason": "Không bỏ qua quan hệ chưa xác minh."}],
            "screen_texts": [], "summary": ""}

    client = Mock(has_credentials=True, model="offline-context-test", translate=Mock(side_effect=respond))
    scanner = Mock(extract=Mock(return_value=[{"start": 180., "end": 181.25,
        "text_zh": rows[0]["text_zh"], "confidence": .99, "bbox": [.1, .7, .7, .08]}]))
    reviewer = AutomaticTranslationReviewer(client, scanner, audio_evidence=False)
    checkpoint = Mock(load=Mock(return_value=None), store=Mock())
    reviewer._checkpoint = Mock(return_value=checkpoint)
    result = reviewer.review("unused", [rows[0]], [], context_segments=rows)
    assert len(requests) == 3
    assert all(SEMANTIC_TRANSLATION_POLICY in prompt for prompt in requests)
    expected = VideoIntelligence._semantic_context(rows, [rows[0]])
    assert all(semantic_payload(prompt) == expected for prompt in requests)
    stages = [call.args[0] for call in checkpoint.load.call_args_list if call.args[0]["kind"] in (
        "address_context", "review_batch")]
    assert len(stages) == 2 and all(stage["semantic_context"] == expected for stage in stages)
    assert set(result["segments"]) == {112}  # The adjoining context ID is never translated twice.
    assert result["segments"][112]["needs_review"]


def test_semantic_context_retains_unknown_speaker_and_missing_silent_focus():
    rows = source_rows()
    for row in rows:
        row.pop("speaker_id")
        row.pop("speaker_evidence")
    rows.append({"id": 114, "start": 183., "end": 184., "text_zh": ""})
    payload = VideoIntelligence._semantic_context(rows, [rows[0], rows[2]])
    assert payload["missing_focus_ids"] == [114]
    unit = payload["units"][0]
    assert unit["kind"] == "contextual_exchange" and not unit["can_combine_dubbing"]
