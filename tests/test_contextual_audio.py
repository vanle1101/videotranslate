"""Recovery tests use measured protocol fixtures, never real models/providers."""
from copy import deepcopy
from contextlib import nullcontext
import asyncio
import json
import wave
from types import SimpleNamespace

import pytest

from config import settings
from core import contextual_audio as module


@pytest.fixture
def source(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "BASE_DIR", tmp_path)
    for name, files in (("sensevoice_onnx", ("model.int8.onnx", "tokens.txt")),
                        ("faster-whisper-small", ("model.bin", "config.json", "tokenizer.json"))):
        folder = tmp_path / "workspace" / "models" / name
        folder.mkdir(parents=True)
        for filename in files:
            (folder / filename).touch()
    path = tmp_path / "source.wav"
    path.write_bytes(b"source identity fixture")
    return path


def row():
    return {"id": 27, "start": 11., "end": 12., "source_asr_start": 10., "source_asr_end": 12.,
            "source_asr_row_id": 5, "source_piece_index": 1, "source_piece_count": 2,
            "verification": {"status": "unresolved", "source_supported": False}}


def readings():
    return {"sensevoice": "你好", "faster-whisper-small": "再见"}


def proof(source):
    target = row()
    return {"revision": module.REVISION, "input_hash": module.identity(target, source, "auto", readings()),
        "target_start": 11., "target_end": 12., "requested_start": 10., "requested_end": 12.,
        "decode_start": 10., "decode_end": 12., "full_text": "朋友你好", "sensevoice_text": "你好",
        "exact_whisper_text": "再见", "words_complete": True,
        "words": [{"word": "朋友", "start": 10., "end": 10.9}, {"word": "你好", "start": 11.1, "end": 11.9}],
        "target_text": "你好", "reason": "corroborated"}


def install_worker(monkeypatch, response, *, failure=None):
    from core.engines.asr import native_process
    calls = []
    monkeypatch.setattr(native_process, "native_worker_slot", lambda check: nullcontext())
    def records(module_name, request, **kwargs):
        calls.append(deepcopy(request))
        if failure:
            raise failure
        yield {"event": "evidence", "engine": "faster-whisper-small", "id": request["rows"][0]["id"],
               "text": response["full_text"], **{key: response[key] for key in
                   ("decode_start", "decode_end", "words", "words_complete")}}
        yield {"event": "completed", "engine": "faster-whisper-small", "rows": 1}
    monkeypatch.setattr(native_process, "iter_native_records", records)
    return calls


def test_parent_and_padding_windows_never_group_by_repeated_chunk_local_id():
    assert module.window(row()) == {"start": 10., "end": 12.}
    standalone = {**row(), "source_asr_start": 11., "source_asr_end": 12.}
    assert module.window(standalone) == {"start": 10., "end": 13.}
    other = {**row(), "start": 101., "end": 102., "source_asr_start": 100., "source_asr_end": 102.}
    assert module.window(other) == {"start": 100., "end": 102.}
    assert module.window({"start": 0., "end": 20.}) is None


@pytest.mark.parametrize("change,reason", [
    ({"words_complete": False}, "missing_word_timing"),
    ({"words": []}, "missing_word_timing"),
    ({"full_text": "朋友你好不"}, "incomplete_word_coverage"),
    ({"words": [{"word": "朋友", "start": 10., "end": 10.9}, {"word": "你好", "start": 10.99, "end": 11.9}]}, "boundary_straddle"),
    ({"words": [{"word": "朋友", "start": 10., "end": 10.9}, {"word": "你好", "start": 11.1, "end": 12.01}]}, "invalid_word_timing"),
    ({"words": [{"word": "朋友", "start": 10., "end": 11.3}, {"word": "你好", "start": 11.1, "end": 11.9}]}, "boundary_straddle"),
    ({"words": [{"word": "朋友", "start": 10., "end": 10.9}, {"word": "你好", "start": 11.1, "end": 11.1}]}, "zero_length_word"),
    ({"sensevoice_text": "不同"}, "independent_disagreement"),
])
def test_negative_attribution_is_explicit_and_durable(source, change, reason):
    value = {**proof(source), **change}
    value["target_text"], value["reason"] = module.attribution(value)
    assert value["reason"] == reason
    assert module.clean_proof(value) == value


def test_whole_parent_is_not_relabelled_and_full_provenance_roundtrips(source):
    from core.streaming.session_store import _clean
    value = proof(source)
    cleaned = _clean({"contextual_audio": value})["contextual_audio"]
    assert json.loads(json.dumps(cleaned)) == value
    assert cleaned["full_text"] == "朋友你好" and cleaned["target_text"] == "你好"
    assert module.attribution({**value, "sensevoice_text": "朋友你好"})[1] == "independent_disagreement"
    with pytest.raises(ValueError):
        _clean({"contextual_audio": {**value, "target_text": "朋友你好"}})


def test_saved_evidence_reaudit_retains_actual_contextual_crop(source, monkeypatch):
    from core.translation_review import AutomaticTranslationReviewer
    value = proof(source)
    target = {**row(), 'text_zh':'你好', 'final_vi':'Xin chào',
        'verification':{'status':'unresolved', 'contextual_audio':value}}
    result = {'segments':{27:target}, 'screen_texts':[]}
    before = deepcopy(result)
    def review(self, path, rows, screens, **kwargs):
        return {'segments':{27:{**rows[0], 'verification':{'status':'unresolved'}}}}
    monkeypatch.setattr(AutomaticTranslationReviewer, 'review', review)
    reviewed = AutomaticTranslationReviewer(audio_evidence=False).review_saved_evidence(result)
    assert reviewed['segments'][27]['verification']['contextual_audio'] == value
    assert result == before


def test_bounded_negative_skips_worker_until_target_strategy_or_source_changes(source, monkeypatch):
    value = proof(source)
    value["words_complete"] = False
    calls = install_worker(monkeypatch, value)
    target = row()
    result = module.recover(source, target, "auto", readings())
    assert result["reason"] == "missing_word_timing" and len(calls) == 1
    target["verification"]["contextual_audio"] = result
    target["verification"]["audio_evidence"] = [
        {"engine": engine, "text_zh": text} for engine, text in readings().items()]
    assert not module.pending(target, source, "auto")
    assert module.recover(source, target, "auto", readings()) == result and len(calls) == 1
    assert module.pending(target, source, "zh")
    target["source_asr_start"] = 9.9
    assert module.pending(target, source, "auto")
    target["source_asr_start"] = 10.
    source.write_bytes(b"different source")
    assert module.pending(target, source, "auto")


@pytest.mark.parametrize("error", [TimeoutError(), asyncio.CancelledError()])
def test_failure_and_cancel_do_not_publish_or_cache_negative(source, monkeypatch, error):
    calls = install_worker(monkeypatch, proof(source), failure=error)
    class Cache:
        force = False
        saved = []
        def load(self, stage): return None
        def store(self, stage, payload): self.saved.append(payload)
    cache = Cache()
    target = row()
    before = deepcopy(target)
    with pytest.raises(type(error)):
        module.recover(source, target, "auto", readings(), checkpoint=cache)
    assert calls and cache.saved == [] and target == before


def test_corroboration_enters_existing_exact_target_pair_without_overwriting_source(source, monkeypatch):
    from core.translation_review import _LocalAudioEvidence
    from core.engines.asr import native_process
    monkeypatch.setattr(native_process, "native_worker_slot", lambda check: nullcontext())
    calls = []
    def records(module_name, request, **kwargs):
        calls.append(deepcopy(request))
        extra = {}
        text = readings()[request["engine"]]
        if request.get("contextual"):
            value = proof(source)
            text = value["full_text"]
            extra = {key: value[key] for key in ("decode_start", "decode_end", "words", "words_complete")}
        yield {"event": "evidence", "id": 27, "engine": request["engine"], "text": text, **extra}
        yield {"event": "completed", "engine": request["engine"], "rows": 1}
    monkeypatch.setattr(native_process, "iter_native_records", records)
    target = row()
    before = deepcopy(target)
    collector = _LocalAudioEvidence()
    result = collector.collect(source, [target], source_language="auto")
    assert result == {27: {"sensevoice": "你好", "faster-whisper-small": "你好"}}
    assert collector.last_contextual[27]["exact_whisper_text"] == "再见"
    assert collector.last_contextual[27]["full_text"] == "朋友你好"
    assert target == before and len(calls) == 3


def test_manual_row_never_enters_contextual_worker(source, monkeypatch):
    from core.translation_review import _LocalAudioEvidence
    from core.engines.asr import native_process
    monkeypatch.setattr(native_process, "native_worker_slot", lambda check: nullcontext())
    calls = []
    def records(module_name, request, **kwargs):
        calls.append(request)
        assert not request.get("contextual")
        yield {"event": "evidence", "id": 27, "engine": request["engine"], "text": readings()[request["engine"]]}
        yield {"event": "completed", "engine": request["engine"], "rows": 1}
    monkeypatch.setattr(native_process, "iter_native_records", records)
    target = row()
    target["verification"]["status"] = "manual"
    collector = _LocalAudioEvidence()
    collector.collect(source, [target])
    assert len(calls) == 2 and collector.last_contextual == {}


def test_contextual_timeout_retains_exact_readings_without_negative_stamp(source, monkeypatch):
    from core.translation_review import _LocalAudioEvidence
    from core.engines.asr import native_process
    monkeypatch.setattr(native_process, "native_worker_slot", lambda check: nullcontext())
    def records(module_name, request, **kwargs):
        if request.get("contextual"):
            raise TimeoutError("bounded contextual decoder")
        yield {"event": "evidence", "id": 27, "engine": request["engine"], "text": readings()[request["engine"]]}
        yield {"event": "completed", "engine": request["engine"], "rows": 1}
    monkeypatch.setattr(native_process, "iter_native_records", records)
    collector = _LocalAudioEvidence()
    assert collector.collect(source, [row()]) == {27: readings()}
    assert isinstance(collector.last_errors[27]["contextual-whisper"], TimeoutError)
    assert collector.last_contextual == {}


def test_changed_source_rejects_worker_proof_before_cache_publication(source, monkeypatch):
    from core.engines.asr import native_process
    monkeypatch.setattr(native_process, "native_worker_slot", lambda check: nullcontext())
    value = proof(source)
    def records(*args, **kwargs):
        yield {"event": "evidence", "engine": "faster-whisper-small", "id": 27, "text": value["full_text"],
               **{key: value[key] for key in ("decode_start", "decode_end", "words", "words_complete")}}
        source.write_bytes(b"replacement source")
        yield {"event": "completed", "engine": "faster-whisper-small", "rows": 1}
    monkeypatch.setattr(native_process, "iter_native_records", records)
    with pytest.raises(native_process.ASRProcessError, match="changed"):
        module.recover(source, row(), "auto", readings())


def test_worker_emits_actual_decode_window_and_measured_words_at_eof(tmp_path, monkeypatch):
    import sys
    from core.engines.asr.review_evidence_worker import collect_engine
    path = tmp_path / "source.wav"
    with wave.open(str(path), "wb") as audio:
        audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        audio.writeframes(b"\x01\x00" * 32000)
    class Decoder:
        def __init__(self, *args, **kwargs): pass
        def transcribe(self, path, **kwargs):
            assert kwargs["word_timestamps"] and not kwargs["vad_filter"]
            with wave.open(str(path), "rb") as audio:
                assert audio.getnframes() == 24000  # .5 to 2.0, no artificial silence
            return iter([SimpleNamespace(text="Hi", words=[SimpleNamespace(word="Hi", start=.6, end=1.)])]), None
    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=Decoder))
    results = []
    collect_engine({"video_path": str(path), "work_directory": str(tmp_path), "contextual": True,
        "engine": "faster-whisper-small", "whisper_model_path": "unused", "source_language": "en",
        "rows": [{"id": 0, "start": .5, "end": 3.}]}, results.append)
    assert results[0]["decode_start"] == .5 and results[0]["decode_end"] == 2.
    assert results[0]["words"] == [{"word": "Hi", "start": 1.1, "end": 1.5}]
    assert not (tmp_path / "speech.wav").exists()
