import asyncio
import wave
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from config import settings
from core.engines.asr import vocal_evidence, whisper_process
from core.engines.asr import vocal_evidence_worker
from core.engines.asr.utterance_language import transcribe_local_speech


def pcm(path, samples=1600, channels=1, rate=16000, value=0):
    with wave.open(str(path), "wb") as wav:
        wav.setparams((channels, 2, rate, 0, "NONE", "not compressed"))
        wav.writeframes(bytes([value, 0]) * samples * channels)


def test_missing_raw_speech_uses_stem_and_healthy_window_keeps_original():
    calls = []
    class Model:
        def transcribe(self, audio, **options):
            calls.append(float(audio[0]))
            row = SimpleNamespace(start=.3, end=1.2, text="你好", words=[])
            return iter([row]), SimpleNamespace(language="zh", language_probability=.99)
    rows, _ = transcribe_local_speech(Model(), np.ones(160000), {}, regions=[
        {"start": 16000, "end": 32000}], vocal_audio=np.full(160000, 2.), vocal_regions=[
        {"start": 16000, "end": 32000}, {"start": 80000, "end": 96000}])
    result = list(rows)
    assert calls == [1., 2.]
    assert [r.source_audio_evidence for r in result] == ["original_audio", "vocal_stem_unverified"]
    assert result[1].start == pytest.approx(5.)


def setup_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "BASE_DIR", tmp_path)
    model = tmp_path / "workspace/models/audio_separator" / vocal_evidence.MODEL
    model.parent.mkdir(parents=True)
    model.write_bytes(b"model")
    model.with_suffix(".yaml").write_bytes(b"model config")
    source = tmp_path / "source.wav"
    pcm(source)
    return source


@pytest.mark.parametrize("failure", [RuntimeError("separation failed"), asyncio.CancelledError()])
def test_failure_or_cancellation_cannot_publish_cache(tmp_path, monkeypatch, failure):
    source = setup_cache(tmp_path, monkeypatch)
    def records(module, request, **kwargs):
        Path(request["output_path"]).write_bytes(b"partial" * 10)
        raise failure
        yield
    monkeypatch.setattr(vocal_evidence, "iter_native_records", records)
    with pytest.raises(type(failure)):
        vocal_evidence.prepare_vocal_evidence(source, timeout=30)
    assert not source.with_name("source_vocal_evidence.wav").exists()
    assert not list(tmp_path.glob("vocal-evidence-*"))


def test_cache_is_content_scoped_and_worker_reaped_before_return(tmp_path, monkeypatch):
    source = setup_cache(tmp_path, monkeypatch)
    calls = []
    def records(module, request, **kwargs):
        calls.append("start")
        try:
            pcm(Path(request["output_path"]))
            yield {"event": "completed"}
        finally:
            calls.append("reaped")
    monkeypatch.setattr(vocal_evidence, "iter_native_records", records)
    first = vocal_evidence.prepare_vocal_evidence(source, timeout=30)
    assert calls == ["start", "reaped"]
    assert vocal_evidence.prepare_vocal_evidence(source, timeout=30) == first
    assert calls == ["start", "reaped"]
    pcm(source, value=1)
    vocal_evidence.prepare_vocal_evidence(source, timeout=30)
    assert calls == ["start", "reaped", "start", "reaped"]


def test_partial_evidence_cache_is_scoped_to_missing_spans(tmp_path, monkeypatch):
    source = setup_cache(tmp_path, monkeypatch)
    seen = []
    def records(module, request, **kwargs):
        seen.append(request["spans"])
        pcm(Path(request["output_path"]))
        yield {"event": "completed"}
    monkeypatch.setattr(vocal_evidence, "iter_native_records", records)
    first = vocal_evidence.prepare_vocal_evidence(source, timeout=30, spans=[[.02, .03]])
    assert vocal_evidence.prepare_vocal_evidence(source, timeout=30, spans=[[.02, .03]]) == first
    vocal_evidence.prepare_vocal_evidence(source, timeout=30, spans=[[.04, .05]])
    vocal_evidence.prepare_vocal_evidence(source, timeout=30, spans=[[.04, .05]],
        crop_padding=4., minimum_window_seconds=7.)
    assert seen == [[[.02, .03]], [[.04, .05]], [[.04, .05]]]


def test_partial_evidence_windows_keep_context_and_merge():
    assert vocal_evidence_worker._partial_windows([[2., 2.1], [3., 3.1]], 10.) == [[.5, 4.6]]
    assert vocal_evidence_worker._partial_windows([[0., .1]], 10.) == [[0., 2.6]]
    assert vocal_evidence_worker._partial_windows([[2.976, 3.104]], 10.,
        padding=4., minimum=7.) == [[0., 7.104]]


def test_partial_evidence_replaces_only_the_uncertain_audio_window(tmp_path):
    import soundfile as sf

    source, output = tmp_path / "source.wav", tmp_path / "evidence.wav"
    sf.write(source, np.zeros((64000, 1), dtype=np.float32), 16000, subtype="PCM_16")
    class Separator:
        def separate(self, audio_path, output_dir):
            output_dir.mkdir(parents=True)
            audio, rate = sf.read(audio_path, dtype="float32", always_2d=True)
            vocal = output_dir / "vocals.wav"
            sf.write(vocal, np.full_like(audio, .5), rate, subtype="PCM_16")
            return vocal, output_dir / "instrumental.wav"
    def convert(args):
        source_path = Path(args[args.index("-i") + 1])
        target_path = Path(args[-1])
        audio, rate = sf.read(source_path, dtype="float32", always_2d=True)
        sf.write(target_path, audio, rate, subtype="PCM_16")

    vocal_evidence_worker._make_partial_evidence({"audio_path": str(source),
        "work_directory": str(tmp_path), "spans": [[1., 1.1]]}, Separator(), output, convert)
    result, rate = sf.read(output, dtype="float32", always_2d=True)
    assert rate == 16000 and result.shape == (64000, 1)
    assert result[8000, 0] == pytest.approx(.5, abs=.01)
    assert result[56000, 0] == pytest.approx(0., abs=.01)


def test_missing_span_forces_stem_even_when_vad_boundaries_match():
    calls = []
    class Model:
        def transcribe(self, audio, **options):
            calls.append(float(audio[0]))
            row = SimpleNamespace(start=.3, end=1.2, text="你好", words=[])
            return iter([row]), SimpleNamespace(language="zh", language_probability=.99)
    regions = [{"start": 16000, "end": 32000}]
    rows, _ = transcribe_local_speech(Model(), np.ones(160000), {}, regions=regions,
        vocal_audio=np.full(160000, 2.), vocal_regions=regions, vocal_spans=[[1., 2.]])
    result = list(rows)
    assert calls == [2.]
    assert result[0].source_audio_evidence == "vocal_stem_unverified"


def test_separator_failure_never_starts_asr(tmp_path, monkeypatch):
    source = tmp_path / "source.wav"
    source.write_bytes(b"source")
    def fail(*args, **kwargs):
        raise RuntimeError("separation unavailable")
    monkeypatch.setattr(vocal_evidence, "prepare_vocal_evidence", fail)
    monkeypatch.setattr(whisper_process, "iter_native_records", lambda *a, **k: pytest.fail("ASR must not start"))
    rows, _ = whisper_process.SubprocessWhisperModel("small").transcribe(source,
        language=None, utterance_language_detection=True)
    with pytest.raises(RuntimeError, match="separation unavailable"):
        list(rows)


def test_cpu_evidence_worker_does_not_probe_hidden_cuda_device():
    torch = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: True))
    vocal_evidence_worker._configure_cpu_backend(torch, force_cpu=True)
    assert torch.cuda.is_available() is False


def test_gpu_evidence_worker_keeps_cuda_detection():
    torch = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: True))
    vocal_evidence_worker._configure_cpu_backend(torch, force_cpu=False)
    assert torch.cuda.is_available() is True


def test_stem_evidence_survives_durable_metadata_contract():
    from core.streaming.session_store import SEGMENT_FIELDS, _validate_source_metadata
    from core.streaming.pipeline import SOURCE_METADATA_FIELDS
    assert "source_audio_evidence" in SEGMENT_FIELDS
    assert "source_audio_evidence" in SOURCE_METADATA_FIELDS
    _validate_source_metadata({"source_audio_evidence": "vocal_stem_unverified"})
    with pytest.raises(ValueError):
        _validate_source_metadata({"source_audio_evidence": "verified_by_separation"})


@pytest.mark.parametrize("samples,channels,rate", [(800, 1, 16000), (1600, 2, 16000), (1600, 1, 8000)])
def test_invalid_pcm_cannot_enter_evidence_cache(tmp_path, monkeypatch, samples, channels, rate):
    source = setup_cache(tmp_path, monkeypatch)
    def records(module, request, **kwargs):
        pcm(Path(request["output_path"]), samples=samples, channels=channels, rate=rate)
        yield {"event": "completed"}
    monkeypatch.setattr(vocal_evidence, "iter_native_records", records)
    with pytest.raises(whisper_process.ASRProcessError):
        vocal_evidence.prepare_vocal_evidence(source, timeout=30)
    assert not source.with_name("source_vocal_evidence.wav").exists()
