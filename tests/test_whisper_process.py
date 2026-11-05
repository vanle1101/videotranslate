"""Fault injection for disposable native workers; never mocked real QA."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from config import settings
from core.engines.asr import whisper_process as module
from core.runtime_context import execution_context


def _cpu_gap_coverage():
    from core.engines.asr.speech_gap_diagnostic import build_gap_diagnostic
    diagnostic = build_gap_diagnostic(original=[{"start": 10 * 16000, "end": 11 * 16000}],
        vocal=[], gaps=[(10., 11.)], samples=32 * 16000, rate=16000,
        window_index=1, window=(9.7 * 16000, 11.3 * 16000),
        selected_evidence="original_audio", final_evidence="original_audio", triggers=[],
        original_vad_supplied=True, vocal_vad_supplied=False)
    return {"unresolved_windows": [[9.7, 11.3]], "missing_speech_spans": [[10., 11.]],
            "diagnostics": [diagnostic], "tail_unresolved": False}


def test_cuda_setup_clears_stale_hidden_gpu_sentinel(monkeypatch):
    from core.asr import _prepare_windows_cuda_runtime
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "-1")
    _prepare_windows_cuda_runtime()
    assert os.environ.get("CUDA_VISIBLE_DEVICES") is None


def test_short_confirmed_speech_gap_uses_bounded_cpu_retry_and_preserves_timeline(tmp_path, monkeypatch):
    import wave
    from types import SimpleNamespace
    source = tmp_path / "source.wav"
    with wave.open(str(source), "wb") as audio:
        audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        audio.writeframes(b"\0\0" * (32 * 16000))
    requests = []
    def run_worker(path, options, timeout, check, **kwargs):
        requests.append((Path(path), dict(options), kwargs))
        with wave.open(str(path), "rb") as audio:
            crop_seconds = audio.getnframes() / audio.getframerate()
        assert crop_seconds < 4
        return [SimpleNamespace(start=.8, end=1.5, text="recovered", words=[
            SimpleNamespace(word="recovered", start=.9, end=1.4)])]
    model = module.SubprocessWhisperModel("small", "cuda")
    monkeypatch.setattr(model, "_run_worker", run_worker)
    original = [SimpleNamespace(start=1, end=2, text="existing", words=[])]
    gaps = _cpu_gap_coverage()
    recovered = model._retry_unresolved_on_cpu(source, {"beam_size": 5,
        "word_timestamps": True, "utterance_language_detection": True}, gaps,
        {"language": "zh", "probability": .95}, 30, None)
    merged, remaining = module._merge_focused_recovery(original, gaps, recovered, {})
    assert [(round(row.start, 2), round(row.end, 2), row.text) for row in merged] == [
        (1, 2, "existing"), (9.9, 10.6, "recovered")]
    assert round(merged[1].words[0].start, 2) == 10.0
    assert remaining["unresolved_windows"] == []
    assert len(requests) == 1
    path, options, kwargs = requests[0]
    assert not path.exists()
    assert options["language"] == "zh" and options["vad_filter"] is False
    assert options["condition_on_previous_text"] is False
    assert "utterance_language_detection" not in options
    assert kwargs == {"device": "cpu", "compute_type": "int8", "cpu_threads": 2}


def test_focused_recovery_keeps_complete_utterance_in_retry_context_before_gap():
    from types import SimpleNamespace
    from core.engines.asr.speech_gap_diagnostic import build_gap_diagnostic

    diagnostic = build_gap_diagnostic(original=[{"start": 0, "end": 2_340 * 16}],
        vocal=[], gaps=[(.18, 2.34)], samples=20 * 16000, rate=16000,
        window_index=0, window=(.18, 6.252), selected_evidence="original_audio",
        final_evidence="original_audio", triggers=[], original_vad_supplied=True,
        vocal_vad_supplied=False)
    coverage = {"unresolved_windows": [[.18, 6.252]],
        "missing_speech_spans": [[.18, 2.34]], "diagnostics": [diagnostic],
        "tail_unresolved": False}
    recovered = [SimpleNamespace(start=0., end=2.34, text="完整恢复的句子", words=[
        SimpleNamespace(word="完整恢复的句子", start=.02, end=2.30)])]

    merged, remaining = module._merge_focused_recovery([], coverage, recovered, {})

    assert [(row.start, row.end, row.text) for row in merged] == [
        (0., 2.34, "完整恢复的句子")]
    assert remaining["unresolved_windows"] == []


def test_focused_recovery_deduplicates_identical_existing_turn_when_it_covers_gap():
    from types import SimpleNamespace
    from core.engines.asr.speech_gap_diagnostic import build_gap_diagnostic

    diagnostic = build_gap_diagnostic(original=[{"start": 9_700 * 16, "end": 11_300 * 16}],
        vocal=[], gaps=[(10., 10.6)], samples=32 * 16000, rate=16000,
        window_index=1, window=(9.7, 11.3), selected_evidence="original_audio",
        final_evidence="original_audio", triggers=[], original_vad_supplied=True,
        vocal_vad_supplied=False)
    coverage = {"unresolved_windows": [[9.7, 11.3]],
        "missing_speech_spans": [[10., 10.6]], "diagnostics": [diagnostic],
        "tail_unresolved": False}
    existing = SimpleNamespace(start=9.9, end=10.8, text="已经恢复的句子", words=[])
    duplicate = SimpleNamespace(start=9.91, end=10.79, text="已经恢复的句子", words=[])

    merged, remaining = module._merge_focused_recovery([existing], coverage, [duplicate], {})

    assert merged == [existing]
    assert remaining["unresolved_windows"] == []


def test_focused_recovery_does_not_clear_a_short_vad_gap_between_recognized_turns():
    from types import SimpleNamespace
    from core.engines.asr.speech_gap_diagnostic import build_gap_diagnostic

    diagnostic = build_gap_diagnostic(original=[
            {"start": int(.48 * 16000), "end": int(2.56 * 16000)},
            {"start": int(2.976 * 16000), "end": int(3.104 * 16000)},
            {"start": int(3.584 * 16000), "end": int(5.952 * 16000)}],
        vocal=[], gaps=[(2.976, 3.104)], samples=20 * 16000, rate=16000,
        window_index=0, window=(.18, 6.252), selected_evidence="original_audio",
        final_evidence="original_audio", triggers=[], original_vad_supplied=True,
        vocal_vad_supplied=False)
    coverage = {"unresolved_windows": [[.18, 6.252]],
        "missing_speech_spans": [[2.976, 3.104]], "diagnostics": [diagnostic],
        "tail_unresolved": False}
    rows = [SimpleNamespace(start=0., end=2.34, text="开始句", words=[]),
            SimpleNamespace(start=3.36, end=4.46, text="中间句", words=[]),
            SimpleNamespace(start=4.46, end=5.68, text="后一句", words=[])]

    merged, remaining = module._merge_focused_recovery(rows, coverage, rows, {})

    assert merged == rows
    assert remaining == coverage


def test_focused_recovery_keeps_recognized_speech_islands_and_blocks_only_the_residual_gap():
    from types import SimpleNamespace
    from core.engines.asr.partial_coverage import validate_coverage
    from core.engines.asr.speech_gap_diagnostic import build_gap_diagnostic

    regions = [
        {"start": int(.48 * 16000), "end": int(2.56 * 16000)},
        {"start": int(2.976 * 16000), "end": int(3.104 * 16000)},
        {"start": int(3.584 * 16000), "end": int(5.952 * 16000)},
    ]
    diagnostic = build_gap_diagnostic(original=regions, vocal=[], gaps=[(2.976, 3.104)],
        samples=20 * 16000, rate=16000, window_index=0, window=(.18, 6.252),
        selected_evidence="original_audio", final_evidence="original_audio", triggers=[],
        original_vad_supplied=False, vocal_vad_supplied=False)
    coverage = {"unresolved_windows": [[.18, 6.252]],
        "missing_speech_spans": [[.18, 6.252]], "diagnostics": [diagnostic],
        "tail_unresolved": False}
    recovered = [
        SimpleNamespace(start=.52, end=2.50, text="完整的第一句", words=[]),
        SimpleNamespace(start=3.62, end=5.90, text="完整的第二句", words=[]),
    ]

    merged, remaining = module._merge_focused_recovery([], coverage, recovered, {})

    assert [row.text for row in merged] == ["完整的第一句", "完整的第二句"]
    assert remaining["unresolved_windows"] == [[2.976, 3.104]]
    assert remaining["missing_speech_spans"] == [[2.976, 3.104]]
    assert remaining["diagnostics"][0]["window"] == [2.976, 3.104]
    validate_coverage(remaining, 20, merged)


def test_empty_crop_without_complete_original_vad_does_not_promote_partial_recovery():
    from types import SimpleNamespace
    from core.engines.asr.speech_gap_diagnostic import build_gap_diagnostic

    diagnostic = build_gap_diagnostic(original=[], vocal=[], gaps=[], samples=20 * 16000,
        rate=16000, window_index=0, window=(.18, 6.252), selected_evidence="original_audio",
        final_evidence="original_audio", triggers=[], original_vad_supplied=False,
        vocal_vad_supplied=False)
    coverage = {"unresolved_windows": [[.18, 6.252]],
        "missing_speech_spans": [[.18, 6.252]], "diagnostics": [diagnostic],
        "tail_unresolved": False}
    recovered = [SimpleNamespace(start=0., end=2.34, text="第一句", words=[]),
        SimpleNamespace(start=3.36, end=5.68, text="第二句", words=[])]

    merged, remaining = module._merge_focused_recovery([], coverage, recovered, {})

    assert merged == []
    assert remaining == coverage


def test_focused_recovery_never_publishes_a_row_that_crosses_a_residual_speech_gap():
    from types import SimpleNamespace
    from core.engines.asr.speech_gap_diagnostic import build_gap_diagnostic

    diagnostic = build_gap_diagnostic(original=[
            {"start": int(.48 * 16000), "end": int(2.56 * 16000)},
            {"start": int(2.976 * 16000), "end": int(3.104 * 16000)},
            {"start": int(3.584 * 16000), "end": int(5.952 * 16000)}],
        vocal=[], gaps=[(2.976, 3.104)], samples=20 * 16000, rate=16000,
        window_index=0, window=(.18, 6.252), selected_evidence="original_audio",
        final_evidence="original_audio", triggers=[], original_vad_supplied=True,
        vocal_vad_supplied=False)
    coverage = {"unresolved_windows": [[.18, 6.252]],
        "missing_speech_spans": [[.18, 6.252]], "diagnostics": [diagnostic],
        "tail_unresolved": False}
    recovered = [
        SimpleNamespace(start=.52, end=3.02, text="跨过缺口的句子", words=[]),
        SimpleNamespace(start=3.62, end=5.90, text="完整的第二句", words=[]),
    ]

    merged, remaining = module._merge_focused_recovery([], coverage, recovered, {})

    assert [row.text for row in merged] == ["完整的第二句"]
    assert remaining["unresolved_windows"] == [[.48, 2.56], [2.976, 3.104]]
    assert remaining["missing_speech_spans"] == remaining["unresolved_windows"]


def test_lazy_recovery_publishes_cpu_and_vocal_rows_and_clears_only_proven_gap(tmp_path, monkeypatch):
    import wave
    from types import SimpleNamespace
    from core.engines.asr.speech_gap_diagnostic import build_gap_diagnostic

    source = tmp_path / "source.wav"
    with wave.open(str(source), "wb") as audio:
        audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        audio.writeframes(b"\0\0" * (20 * 16000))

    regions = [(.48, 2.56), (2.976, 3.104), (3.584, 5.952)]
    diagnostic = build_gap_diagnostic(original=[
        {"start": int(start * 16000), "end": int(end * 16000)}
        for start, end in regions], vocal=[], gaps=[(2.976, 3.104)],
        samples=20 * 16000, rate=16000, window_index=0,
        window=(.18, 6.252), selected_evidence="original_audio",
        final_evidence="original_audio", triggers=[], original_vad_supplied=False,
        vocal_vad_supplied=False)
    initial_coverage = {"unresolved_windows": [[.18, 6.252]],
        "missing_speech_spans": [[2.976, 3.104]], "diagnostics": [diagnostic],
        "tail_unresolved": False}
    cpu_rows = [SimpleNamespace(start=0., end=2.34, text="first", words=[]),
        SimpleNamespace(start=3.36, end=4.46, text="second", words=[]),
        SimpleNamespace(start=4.46, end=5.68, text="third", words=[])]
    vocal_row = SimpleNamespace(start=2.8, end=3.3, text="recovered gap", words=[])
    calls = []

    def run_worker(path, options, timeout, check, *, metadata_callback=None,
                   coverage_callback=None, **kwargs):
        calls.append(dict(options))
        if "vocal_evidence_path" in options:
            assert options["vocal_evidence_spans"] == [[2.976, 3.104]]
            coverage_callback({"unresolved_windows": [], "missing_speech_spans": [],
                "diagnostics": [], "tail_unresolved": False})
            return [vocal_row]
        metadata_callback({"language": "zh", "probability": .95})
        coverage_callback(initial_coverage)
        return []

    model = module.SubprocessWhisperModel("small", "cuda")
    monkeypatch.setattr(settings, "ASR_VOCAL_EVIDENCE_STRATEGY", "on_gap", raising=False)
    monkeypatch.setattr(model, "_run_worker", run_worker)
    monkeypatch.setattr(model, "_retry_unresolved_on_cpu", lambda *args: cpu_rows)
    monkeypatch.setattr("core.engines.asr.vocal_evidence.prepare_vocal_evidence",
        lambda source, **kwargs: source)
    metadata, final_coverage = {}, {}
    rows, _ = model.transcribe(source, utterance_language_detection=True,
        metadata_callback=metadata.update, coverage_callback=final_coverage.update)

    assert [row.text for row in rows] == ["first", "recovered gap", "second", "third"]
    assert final_coverage == {"unresolved_windows": [], "missing_speech_spans": [],
        "diagnostics": [], "tail_unresolved": False}
    assert metadata == {"language": "zh", "probability": .95}
    assert len(calls) == 2


def test_short_speech_cpu_retry_skips_low_confidence_and_long_windows(tmp_path, monkeypatch):
    source = tmp_path / "source.wav"
    with __import__("wave").open(str(source), "wb") as audio:
        audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        audio.writeframes(b"\0\0" * (32 * 16000))
    model = module.SubprocessWhisperModel("small", "cuda")
    monkeypatch.setattr(model, "_run_worker", lambda *a, **k: pytest.fail("fallback must be skipped"))
    gaps = _cpu_gap_coverage()
    assert model._retry_unresolved_on_cpu(source, {}, gaps,
        {"language": "zh", "probability": .79}, 30, None) == []
    long_gaps = {**gaps, "unresolved_windows": [[0., 20.]]}
    assert model._retry_unresolved_on_cpu(source, {}, long_gaps,
        {"language": "zh", "probability": .95}, 30, None) == []


@pytest.fixture
def fake_worker(tmp_path, monkeypatch):
    """Launch a real child process with injected protocol/failure behavior."""
    monkeypatch.setattr(settings, "TEMP_DIR", tmp_path)
    audio = tmp_path / "speech.wav"
    audio.write_bytes(b"fixture")
    original = subprocess.Popen
    calls = []
    def install(program):
        def launch(command, **kwargs):
            calls.append({"command": command, "environment": kwargs["env"],
                          "request": json.loads(Path(command[-2]).read_text(encoding="utf-8"))})
            process = original([sys.executable, "-B", "-X", "utf8", "-c", program,
                                command[-1], command[-2]], **kwargs)
            calls[-1]["process"] = process
            return process
        monkeypatch.setattr(module.subprocess, "Popen", launch)
        return calls
    return audio, install


def test_native_worker_preserves_configuration_validates_output_and_exits(fake_worker):
    audio, install = fake_worker
    calls = install('''
import json, sys
with open(sys.argv[1], 'w', encoding='utf-8') as out:
    out.write(json.dumps({'event':'segment', 'segment': {'start':0, 'end':1.2,
        'text':'你好', 'words':[{'start':0.1,'end':1.1,'word':'你好'}]}})+'\\n')
    out.write(json.dumps({'event':'completed', 'segments':1})+'\\n')
''')
    rows, info = module.SubprocessWhisperModel("small").transcribe(audio, language="zh", beam_size=5,
                                                                        vad_filter=True)
    assert [(row.text, row.start, row.end, row.words[0].word) for row in rows] == [("你好", 0, 1.2, "你好")]
    assert info is None
    assert calls[0]["request"]["model_size"] == "small"
    assert calls[0]["request"]["compute_type"] == settings.WHISPER_COMPUTE_TYPE
    assert calls[0]["request"]["options"] == {"language": "zh", "beam_size": 5, "vad_filter": True}
    assert calls[0]["environment"]["MKL_DISABLE_FAST_MM"] == "1"
    assert calls[0]["process"].poll() == 0
    assert not list(settings.TEMP_DIR.glob("whisper-worker-*"))


def test_vocal_evidence_has_a_separate_long_deadline(fake_worker, monkeypatch):
    audio, install = fake_worker
    calls = install("import json,sys; open(sys.argv[1],'w').write(json.dumps({'event':'completed','segments':0})+'\\n')")
    deadlines = []
    monkeypatch.setattr(settings, "ASR_PROCESS_TIMEOUT", 15, raising=False)
    monkeypatch.setattr(settings, "ASR_VOCAL_EVIDENCE_TIMEOUT", 3600, raising=False)
    monkeypatch.setattr("core.engines.asr.vocal_evidence.prepare_vocal_evidence",
        lambda source, **kwargs: deadlines.append(kwargs["timeout"]) or source)
    rows, _ = module.SubprocessWhisperModel("small", "cpu").transcribe(audio,
        language=None, utterance_language_detection=True)
    assert list(rows) == []
    assert deadlines == [3600]
    assert len(calls) == 1 and calls[0]["process"].poll() == 0


@pytest.mark.parametrize("output", ["not-json\\n", "[]\\n", '{"event":"completed","segments":2}\\n',
    '{"event":"segment","segment":{"start":0,"end":1,"text":"x","words":[]}}\\n',
    '{"event":"segment","segment":{"start":1,"end":0,"text":"x","words":[]}}\\n'])
def test_invalid_or_partial_native_output_never_becomes_completed(fake_worker, output):
    audio, install = fake_worker
    calls = install("import sys; open(sys.argv[1], 'w').write(" + repr(output.replace("\\n", "\n")) + ")")
    rows, _ = module.SubprocessWhisperModel("small").transcribe(audio)
    with pytest.raises(module.ASRProcessError):
        list(rows)
    assert calls[0]["process"].poll() is not None
    assert not list(settings.TEMP_DIR.glob("whisper-worker-*"))


def test_native_crash_does_not_publish_partial_transcription(fake_worker):
    audio, install = fake_worker
    calls = install("import sys; print('mkl_malloc: failed to allocate memory', file=sys.stderr); sys.exit(7)")
    rows, _ = module.SubprocessWhisperModel("small").transcribe(audio)
    with pytest.raises(module.ASRProcessError, match="mkl_malloc"):
        list(rows)
    assert calls[0]["process"].poll() == 7


def test_gpu_oom_after_partial_output_retries_cpu_once_without_duplicate_rows(fake_worker, caplog):
    audio, install = fake_worker
    calls = install('''
import json, sys
request=json.load(open(sys.argv[2],encoding='utf-8'))
with open(sys.argv[1], 'w',encoding='utf-8') as out:
    text='partial GPU' if request['device']=='cuda' else 'complete CPU'
    out.write(json.dumps({'event':'segment','segment':{'start':0,'end':1,'text':text,'words':[]}})+'\\n')
    if request['device']=='cuda':
        out.write(json.dumps({'event':'error','code':'cuda_out_of_memory','device':'cuda'})+'\\n')
    else:
        out.write(json.dumps({'event':'completed','segments':1})+'\\n')
''')
    previous = settings.WHISPER_COMPUTE_TYPE, settings.DEVICE
    states = []
    handle = module.SubprocessWhisperModel("small", "cuda")
    rows, _ = handle.transcribe(audio, language="zh", beam_size=5, status_callback=states.append)
    assert [row.text for row in rows] == ["complete CPU"]
    assert [call["request"]["device"] for call in calls] == ["cuda", "cpu"]
    assert calls[1]["request"]["compute_type"] == "int8"
    assert calls[0]["request"]["options"] == calls[1]["request"]["options"] == {"language":"zh", "beam_size":5}
    assert calls[0]["request"]["model_size"] == calls[1]["request"]["model_size"] == "small"
    assert (settings.WHISPER_COMPUTE_TYPE, settings.DEVICE) == previous and handle.device == "cuda"
    assert len(states) == 1 and "CPU int8" in states[0]
    assert "WHISPER_CPU_FALLBACK" in caplog.text
    assert all(call["process"].poll() is not None for call in calls)
    assert not list(settings.TEMP_DIR.glob("whisper-worker-*"))


@pytest.mark.parametrize("device,code", [("cuda","invalid_output"), ("cpu","cuda_out_of_memory")])
def test_wrong_device_or_non_oom_error_never_falls_back(fake_worker, device, code):
    audio, install = fake_worker
    program = "import json,sys; open(sys.argv[1],'w').write(json.dumps(" + repr({
        "event":"error", "code":code, "device":"cuda"}) + ")+ '\\n')"
    calls = install(program)
    rows, _ = module.SubprocessWhisperModel("small", device).transcribe(audio)
    with pytest.raises(module.ASRProcessError):
        list(rows)
    assert len(calls) == 1


def test_second_worker_failure_is_bounded_and_does_not_publish_gpu_rows(fake_worker):
    audio, install = fake_worker
    calls = install('''
import json,sys
request=json.load(open(sys.argv[2],encoding='utf-8'))
with open(sys.argv[1],'w') as out:
    if request['device']=='cuda':
        out.write(json.dumps({'event':'error','code':'cuda_out_of_memory','device':'cuda'})+'\\n')
    else: raise RuntimeError('CPU failed')
''')
    rows, _ = module.SubprocessWhisperModel("small", "cuda").transcribe(audio)
    with pytest.raises(module.ASRProcessError, match="CPU failed"):
        next(rows)
    assert len(calls) == 2 and all(call["process"].poll() is not None for call in calls)


def test_cancel_at_fallback_boundary_never_launches_cpu(fake_worker):
    audio, install = fake_worker
    calls = install('''
import json,sys
open(sys.argv[1],'w').write(json.dumps({'event':'error','code':'cuda_out_of_memory','device':'cuda'})+'\\n')
''')
    cancelled = []
    with execution_context("fallback-cancel", lambda: bool(cancelled)):
        rows, _ = module.SubprocessWhisperModel("small", "cuda").transcribe(audio,
            status_callback=cancelled.append)
        with pytest.raises(asyncio.CancelledError):
            next(rows)
    assert len(calls) == 1 and calls[0]["process"].poll() is not None


def test_real_worker_protocol_classifies_lazy_cuda_oom(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from core.engines.asr import whisper_worker
    monkeypatch.setattr(settings, "WHISPER_COMPUTE_TYPE", "int8")
    monkeypatch.setattr(settings, "ASR_CPU_THREADS", 4)
    request, output = tmp_path / "request.json", tmp_path / "output.jsonl"
    request.write_text(json.dumps({"model_size":"small", "device":"cuda", "compute_type":"int8",
        "cpu_threads":4, "audio_path":"fake.wav", "options":{"language":"zh"}}))
    def segments():
        yield SimpleNamespace(start=0, end=1, text="partial", words=[])
        raise RuntimeError("CUDA failed with error out of memory")
    model = SimpleNamespace(transcribe=lambda *args, **kwargs: (segments(), None))
    monkeypatch.setattr("core.asr.load_whisper_model", lambda *args: model)
    whisper_worker.run(request, output)
    records = [json.loads(line) for line in output.read_text().splitlines()]
    assert [row["event"] for row in records] == ["segment", "error"]
    assert records[-1] == {"event":"error", "code":"cuda_out_of_memory", "device":"cuda"}


@pytest.mark.parametrize("error", [RuntimeError("permission denied"), RuntimeError("out of memory on CPU"),
    RuntimeError("CUDA invalid configuration"), ValueError("CUDA out of memory")])
def test_worker_oom_classifier_is_narrow(error):
    from core.engines.asr.whisper_worker import _cuda_out_of_memory
    assert not _cuda_out_of_memory(error)


def test_timeout_kills_reaps_worker_and_releases_slot(fake_worker, monkeypatch):
    audio, install = fake_worker
    monkeypatch.setattr(settings, "ASR_PROCESS_TIMEOUT", .2, raising=False)
    calls = install("import time; time.sleep(30)")
    rows, _ = module.SubprocessWhisperModel("small").transcribe(audio)
    with pytest.raises(TimeoutError):
        list(rows)
    assert calls[0]["process"].poll() is not None
    assert module._WORKER_SLOT.acquire(blocking=False)
    module._WORKER_SLOT.release()
    assert not list(settings.TEMP_DIR.glob("whisper-worker-*"))


def test_cancellation_interrupts_native_inference_without_waiting_for_segment(fake_worker):
    audio, install = fake_worker
    calls = install("import time; time.sleep(30)")
    started = time.monotonic()
    with execution_context("native-cancel", lambda: time.monotonic() - started > .25):
        rows, _ = module.SubprocessWhisperModel("small").transcribe(audio)
        with pytest.raises(asyncio.CancelledError):
            list(rows)
    assert calls[0]["process"].poll() is not None
    assert time.monotonic() - started < 5


def test_partial_iterator_is_cancelled_without_publishing_before_completion(fake_worker):
    audio, install = fake_worker
    calls = install('''
import json, sys, time
with open(sys.argv[1], 'w') as out:
    out.write(json.dumps({'event':'segment', 'segment': {'start':0,'end':1,'text':'x','words':[]}})+'\\n')
    out.flush()
    time.sleep(30)
''')
    started = time.monotonic()
    with execution_context("partial-cancel", lambda: time.monotonic() - started > .25):
        rows, _ = module.SubprocessWhisperModel("small").transcribe(audio)
        with pytest.raises(asyncio.CancelledError):
            next(rows)
    assert calls[0]["process"].poll() is not None


def test_cancelled_queued_request_never_spawns_second_native_worker(fake_worker):
    audio, install = fake_worker
    calls = install("raise AssertionError('must not launch')")
    assert module._WORKER_SLOT.acquire(blocking=False)
    started = time.monotonic()
    try:
        with execution_context("queued-cancel", lambda: time.monotonic() - started > .15):
            rows, _ = module.SubprocessWhisperModel("small").transcribe(audio)
            with pytest.raises(asyncio.CancelledError):
                list(rows)
    finally:
        module._WORKER_SLOT.release()
    assert not calls


@pytest.fixture
def evidence_models(fake_worker, monkeypatch):
    audio, install = fake_worker
    monkeypatch.setattr(settings, "BASE_DIR", audio.parent)
    models = audio.parent / "workspace" / "models"
    for name, files in (("sensevoice_onnx", ("model.int8.onnx", "tokens.txt")),
                        ("faster-whisper-small", ("config.json", "model.bin", "tokenizer.json"))):
        folder = models / name
        folder.mkdir(parents=True)
        for filename in files:
            (folder / filename).touch()
    return audio, install


def test_review_uses_two_disposable_workers_and_bounded_batches(evidence_models):
    from core.translation_review import _LocalAudioEvidence
    audio, install = evidence_models
    calls = install('''
import json, sys
request=json.load(open(sys.argv[2],encoding='utf-8'))
with open(sys.argv[1], 'w', encoding='utf-8') as out:
    for row in request['rows']:
        out.write(json.dumps({'event':'evidence','id':row['id'],'engine':request['engine'],'text':'你好'})+'\\n')
    out.write(json.dumps({'event':'completed','rows':len(request['rows']),'engine':request['engine']})+'\\n')
''')
    progress = []
    rows = [{"id": index, "start": index, "end": index + 1} for index in range(13)]
    result = _LocalAudioEvidence().collect(audio, rows, progress_callback=progress.append)
    assert result == {row["id"]: {"sensevoice": "你好", "faster-whisper-small": "你好"} for row in rows}
    assert [call["request"]["engine"] for call in calls] == [
        "sensevoice", "faster-whisper-small", "sensevoice", "faster-whisper-small"]
    assert [len(call["request"]["rows"]) for call in calls] == [12, 12, 1, 1]
    assert progress == sorted(progress) and progress[-1] == 100
    assert all(call["process"].poll() == 0 for call in calls)
    assert not list(settings.TEMP_DIR.glob("review-asr-worker-*"))


@pytest.mark.parametrize("failure", ["missing", "duplicate", "wrong_id", "wrong_engine", "numeric_text", "no_end"])
def test_invalid_review_batch_is_not_accepted(evidence_models, failure):
    from core.translation_review import _LocalAudioEvidence
    audio, install = evidence_models
    calls = install('''
import json, sys
request=json.load(open(sys.argv[2],encoding='utf-8'))
engine=request['engine']; failure=''' + repr(failure) + '''
records=[{'event':'evidence','id':0,'engine':engine,'text':'你好'}]
if failure=='missing': records=[]
if failure=='duplicate': records*=2
if failure=='wrong_id': records[0]['id']=7
if failure=='wrong_engine': records[0]['engine']='other'
if failure=='numeric_text': records[0]['text']=42
if failure!='no_end': records.append({'event':'completed','rows':1,'engine':engine})
with open(sys.argv[1], 'w',encoding='utf-8') as out:
    for record in records: out.write(json.dumps(record)+'\\n')
''')
    evidence = _LocalAudioEvidence()
    result = evidence.collect(audio, [{"id": 0, "start": 0, "end": 1}])
    assert result == {0: {}}
    assert isinstance(evidence.last_error, module.ASRProcessError)
    assert set(evidence.last_errors[0]) == {"sensevoice", "faster-whisper-small"}
    assert len(calls) == 2 and all(call["process"].poll() is not None for call in calls)
    assert not list(settings.TEMP_DIR.glob("review-asr-worker-*"))


def test_later_evidence_failure_retains_and_caches_only_complete_readings(evidence_models):
    from core.translation_review import _LocalAudioEvidence
    from core.review_checkpoint import ReviewCheckpoint
    audio, install = evidence_models
    program = '''
import json, sys
request=json.load(open(sys.argv[2],encoding='utf-8'))
if request['engine']=='sensevoice' and request['rows'][0]['id']==12:
    with open(sys.argv[1],'w',encoding='utf-8') as out:
        out.write(json.dumps({'event':'evidence','id':12,'engine':'sensevoice','text':'wrong partial'})+'\\n')
    sys.exit(7)
with open(sys.argv[1],'w',encoding='utf-8') as out:
    for row in request['rows']:
        out.write(json.dumps({'event':'evidence','id':row['id'],'engine':request['engine'],'text':'你好'})+'\\n')
    out.write(json.dumps({'event':'completed','rows':len(request['rows']),'engine':request['engine']})+'\\n')
'''
    calls = install(program)
    directory = audio.parent / "checkpoints"
    checkpoint = ReviewCheckpoint(audio, "offline-evidence", directory=directory,
                                  runtime_revision={"test": True})
    rows = [{"id": index, "start": index, "end": index + 1} for index in range(13)]
    evidence = _LocalAudioEvidence()
    result = evidence.collect(audio, rows, checkpoint=checkpoint)
    assert all(result[index] == {"sensevoice": "你好", "faster-whisper-small": "你好"} for index in range(12))
    assert result[12] == {"faster-whisper-small": "你好"}
    assert set(evidence.last_errors) == {12}
    assert set(evidence.last_errors[12]) == {"sensevoice"}
    assert len(calls) == 4 and len(list(directory.glob("*.review"))) == 25
    assert "wrong partial" not in "".join(path.read_text(encoding="utf-8") for path in directory.glob("*.review"))

    # A terminal marker is still required: use a healthy worker rather than
    # accepting the failed child's incomplete first attempt.
    previous_calls = len(calls)
    calls = install(program.replace(
        "if request['engine']=='sensevoice' and request['rows'][0]['id']==12:", "if False:"))
    recovered = _LocalAudioEvidence().collect(audio, rows, checkpoint=checkpoint)
    assert recovered == {row["id"]: {"sensevoice": "你好", "faster-whisper-small": "你好"} for row in rows}
    assert len(calls) == previous_calls + 1
    assert calls[-1]["request"]["rows"] == [rows[12]] and calls[-1]["request"]["engine"] == "sensevoice"
    assert len(list(directory.glob("*.review"))) == 26
    assert calls[-1]["process"].poll() == 0
    assert not list(settings.TEMP_DIR.glob("review-asr-worker-*"))


def test_review_cancellation_kills_worker_and_uses_source_asr_slot(evidence_models):
    from core.translation_review import _LocalAudioEvidence
    audio, install = evidence_models
    calls = install("import time; time.sleep(30)")
    started = time.monotonic()
    with pytest.raises(asyncio.CancelledError):
        _LocalAudioEvidence().collect(audio, [{"id": 0, "start": 0, "end": 1}],
                                     cancel_check=lambda: time.monotonic() - started > .25)
    assert calls[0]["process"].poll() is not None
    assert module._WORKER_SLOT.acquire(blocking=False)
    started = time.monotonic()
    try:
        with pytest.raises(asyncio.CancelledError):
            _LocalAudioEvidence().collect(audio, [{"id": 0, "start": 0, "end": 1}],
                                         cancel_check=lambda: time.monotonic() - started > .15)
    finally:
        module._WORKER_SLOT.release()
    assert len(calls) == 1


def contextual_worker_program(failure=None):
    from core.engines.asr.contextual_evidence import PADDING
    return '''
import json, sys
request=json.load(open(sys.argv[2],encoding='utf-8'))
engine=request['engine']; failure=''' + repr(failure) + '''
records=[]
for row in request['rows']:
    start=row['start']; end=row['end']
    timing=([{'text':'你','at':start+.2},{'text':'好','at':start+.4}]
        if engine=='sensevoice' else [{'text':'你','start':start+.2,'end':start+.3,'probability':.99},
        {'text':'好','start':start+.4,'end':start+.5,'probability':.99}])
    record={'event':'evidence','id':row['id'],'engine':engine,'text':'你好',
        'crop_start':max(0,start-PADDING),'crop_end':end+PADDING,'timing':timing}
    if engine=='faster-whisper-small':
        if failure=='wrong_time': record['timing'][0]['end']=99
        elif failure=='missing_timing': record.pop('timing')
        elif failure=='partial_text': record['text']='你好不好'
    records.append(record)
if not (engine=='faster-whisper-small' and failure in ('no_end','exit_after_marker')):
    records.append({'event':'completed','rows':len(request['rows']),'engine':engine})
elif failure=='exit_after_marker':
    records.append({'event':'completed','rows':len(request['rows']),'engine':engine})
with open(sys.argv[1],'w',encoding='utf-8') as out:
    for record in records: out.write(json.dumps(record)+'\\n')
if engine=='faster-whisper-small' and failure=='exit_after_marker': sys.exit(7)
'''.replace('PADDING', repr(PADDING))


@pytest.mark.parametrize("failure", ["wrong_time", "missing_timing", "partial_text", "no_end", "exit_after_marker"])
def test_contextual_native_failure_cannot_publish_or_cache_partial_evidence(evidence_models, failure):
    from core.translation_review import _LocalAudioEvidence
    from core.review_checkpoint import ReviewCheckpoint
    audio, install = evidence_models
    calls = install(contextual_worker_program(failure))
    checkpoint = ReviewCheckpoint(audio, "offline-context", directory=audio.parent/'context-checkpoints',
                                  runtime_revision={"test": True})
    row = {"id": 0, "start": 0., "end": 1., "text_zh": "你好"}
    adapter = _LocalAudioEvidence()
    values = adapter.collect_contextual(audio, [row], [], checkpoint=checkpoint)
    assert values == {0: {"sensevoice": "你好"}}
    assert set(adapter.last_contextual_errors[0]) == {"faster-whisper-small"}
    assert len(adapter.last_contextual_evidence[0]) == 1
    assert len(list(checkpoint.directory.glob('*.review'))) == 1
    assert all(call['process'].poll() is not None for call in calls)
    assert not list(settings.TEMP_DIR.glob('review-asr-context-*'))


def test_contextual_native_resume_is_bound_to_exact_source_and_neighbor_versions(evidence_models):
    from core.translation_review import _LocalAudioEvidence
    from core.review_checkpoint import ReviewCheckpoint
    audio, install = evidence_models
    calls = install(contextual_worker_program())
    checkpoint = ReviewCheckpoint(audio, "offline-context", directory=audio.parent/'context-checkpoints',
                                  runtime_revision={"test": True})
    row = {"id": 0, "start": 0., "end": 1., "text_zh": "你好"}
    prior = {"id": 1, "start": 1., "end": 2., "text_zh": "不行", "verification": {"source_supported": True}}
    adapter = _LocalAudioEvidence()
    values = adapter.collect_contextual(audio, [row], [prior], checkpoint=checkpoint)
    assert values == {0: {"sensevoice": "你好", "faster-whisper-small": "你好"}}
    assert all(call['request']['contextual'] is True for call in calls)
    assert len(calls) == 2
    assert adapter.collect_contextual(audio, [row], [prior], checkpoint=checkpoint) == values
    assert len(calls) == 2
    prior['text_zh'] = '可以'
    assert adapter.collect_contextual(audio, [row], [prior], checkpoint=checkpoint) == values
    assert len(calls) == 4
    prior['verification']['source_supported'] = False
    assert adapter.collect_contextual(audio, [row], [prior], checkpoint=checkpoint) == values
    assert len(calls) == 6
    assert all(call['process'].poll() == 0 for call in calls)


@pytest.mark.parametrize("parent_exits", [False, True])
def test_owned_descendant_is_stopped_even_if_worker_exits_first(fake_worker, parent_exits):
    import psutil
    audio, install = fake_worker
    child_pid = audio.parent / "descendant.pid"
    calls = install('''
import subprocess, sys, json, time
child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'])
open(''' + repr(str(child_pid)) + ''','w').write(str(child.pid))
with open(sys.argv[1],'w') as out:
    out.write(json.dumps({'event':'segment','segment':{'start':0,'end':1,'text':'x','words':[]}})+'\\n')
    out.flush()
    if ''' + repr(parent_exits) + ''':
        out.write(json.dumps({'event':'completed','segments':1})+'\\n')
        out.flush()
    else: time.sleep(30)
''')
    started = time.monotonic()
    with execution_context("descendant-cancel", None if parent_exits else lambda: time.monotonic() - started > .25):
        rows, _ = module.SubprocessWhisperModel("small").transcribe(audio)
        if parent_exits:
            assert [row.text for row in rows] == ["x"]
        else:
            with pytest.raises(asyncio.CancelledError):
                next(rows)
    assert calls[0]["process"].poll() is not None
    pid = int(child_pid.read_text())
    assert not psutil.pid_exists(pid)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Object active-process drain")
@pytest.mark.parametrize("descendants", [1, 17])
def test_exited_worker_job_drains_child_holding_pcm_before_directory_cleanup(fake_worker, monkeypatch,
                                                                          descendants):
    import psutil
    from core.engines.asr import native_process
    audio, install = fake_worker
    child_pid = audio.parent / "pcm-child.pid"
    drains = []
    original = native_process._WindowsJob.terminate_and_drain
    def drain(owner, *args, **kwargs):
        original(owner, *args, **kwargs)
        drains.append(all(not psutil.pid_exists(pid) for pid in json.loads(child_pid.read_text())))
    monkeypatch.setattr(native_process._WindowsJob, "terminate_and_drain", drain)
    child_program = ("import os,sys,time; "
        "stream=open('held-'+str(os.getpid())+'.wav','w'); "
        "open(sys.argv[1],'w').write(str(os.getpid())); "
        "time.sleep(30)")
    calls = install('''
import subprocess,sys,json,time
request=json.load(open(sys.argv[2],encoding='utf-8'))
ready=[__import__('os').path.join(request['work_directory'],'ready-'+str(i))
       for i in range(''' + repr(descendants) + ''')]
children=[subprocess.Popen([sys.executable,'-c', ''' + repr(child_program) + ''',marker],
                          cwd=request['work_directory']) for marker in ready]
while not all(__import__('os').path.exists(marker) for marker in ready): time.sleep(.01)
with open(''' + repr(str(child_pid)) + ''','w') as out:
    json.dump([child.pid for child in children],out)
with open(sys.argv[1],'w') as out:
    out.write(json.dumps({'event':'completed','segments':0})+'\\n')
''')
    rows, _ = module.SubprocessWhisperModel("small").transcribe(audio)
    assert list(rows) == []
    assert calls[0]["process"].poll() == 0
    assert drains == [True]
    assert not list(settings.TEMP_DIR.glob("whisper-worker-*"))


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Object ownership")
def test_job_member_drain_leaves_independent_process_running():
    from core.engines.asr import native_process
    independent = subprocess.Popen([sys.executable, "-B", "-c", "import time; time.sleep(30)"],
                                   creationflags=subprocess.CREATE_NO_WINDOW)
    owner, worker = None, None
    try:
        owner = native_process._WindowsJob()
        worker = subprocess.Popen([sys.executable, "-B", "-c", "import time; time.sleep(30)"],
                                  creationflags=subprocess.CREATE_NO_WINDOW | 0x00000004)
        owner.attach_and_resume(worker)
        assert {process.pid for process in owner._owned_processes()} == {worker.pid}
        owner.terminate_and_drain()
        assert worker.wait(timeout=2) != 0
        assert independent.poll() is None
    finally:
        if owner is not None:
            owner.close()
        for process in (worker, independent):
            if process is not None:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)
                process._handle.Close()
