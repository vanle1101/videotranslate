"""Fault injection for disposable native workers; never mocked real QA."""
import asyncio
import json
from pathlib import Path
import subprocess
import sys
import time

import pytest

from config import settings
from core.engines.asr import whisper_process as module
from core.runtime_context import execution_context


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


def test_cuda_inference_oom_discards_partial_rows_and_reaps_before_cpu(fake_worker, monkeypatch):
    audio, install = fake_worker
    calls = install('''
import json, sys
request=json.load(open(sys.argv[2],encoding='utf-8'))
device=request['device']
with open(sys.argv[1], 'w') as out:
    out.write(json.dumps({'event':'runtime','device':device,'compute_type':request['compute_type']})+'\\n')
    out.write(json.dumps({'event':'segment','segment':{'start':0,'end':1,
        'text':'discard gpu' if device=='cuda' else 'complete cpu','words':[]}})+'\\n')
    out.write(json.dumps({'event':'failed','code':'cuda_inference_oom','device':'cuda'}
        if device=='cuda' else {'event':'completed','segments':1})+'\\n')
''')
    original_records = module.iter_native_records
    def checked_records(*args, **kwargs):
        if args[1]["device"] == "cpu":
            assert calls[0]["process"].poll() is not None
        return original_records(*args, **kwargs)
    monkeypatch.setattr(module, "iter_native_records", checked_records)
    model = module.SubprocessWhisperModel("small", device="cuda")
    rows, _ = model.transcribe(audio, language="ko")
    assert [row.text for row in rows] == ["complete cpu"]
    assert [call["request"]["device"] for call in calls] == ["cuda", "cpu"]
    assert calls[1]["request"]["compute_type"] == "int8"
    assert calls[0]["request"]["options"] == calls[1]["request"]["options"] == {"language":"ko"}
    assert all(call["process"].poll() is not None for call in calls)
    assert model.runtime_info == {"device":"cpu", "compute_type":"int8", "fallback_reason":"cuda_inference_oom"}


def test_cancel_during_cpu_fallback_drains_both_workers(fake_worker):
    audio, install = fake_worker
    calls = install('''
import json, sys, time
request=json.load(open(sys.argv[2],encoding='utf-8'))
if request['device']=='cuda':
    open(sys.argv[1],'w').write(json.dumps({'event':'failed','code':'cuda_inference_oom','device':'cuda'})+'\\n')
else:
    time.sleep(30)
''')
    with execution_context("cancel-cpu-fallback", lambda: len(calls) == 2):
        rows, _ = module.SubprocessWhisperModel("small", device="cuda").transcribe(audio)
        with pytest.raises(asyncio.CancelledError):
            list(rows)
    assert len(calls) == 2
    assert all(call["process"].poll() is not None for call in calls)
    assert module._WORKER_SLOT.acquire(blocking=False)
    module._WORKER_SLOT.release()


def test_cpu_failure_after_cuda_oom_is_terminal_and_publishes_nothing(fake_worker):
    audio, install = fake_worker
    calls = install('''
import json, sys
request=json.load(open(sys.argv[2],encoding='utf-8'))
with open(sys.argv[1],'w') as out:
    out.write(json.dumps({'event':'segment','segment':{'start':0,'end':1,'text':'partial','words':[]}})+'\\n')
    out.write(json.dumps({'event':'failed','code':'cuda_inference_oom','device':request['device']})+'\\n')
''')
    rows, _ = module.SubprocessWhisperModel("small", device="cuda").transcribe(audio)
    with pytest.raises(module.ASRProcessError):
        next(rows)
    assert len(calls) == 2


def test_cuda_stderr_without_typed_worker_failure_never_triggers_retry(fake_worker):
    audio, install = fake_worker
    calls = install("import sys; print('CUDA out of memory', file=sys.stderr); sys.exit(7)")
    rows, _ = module.SubprocessWhisperModel("small", device="cuda").transcribe(audio)
    with pytest.raises(module.ASRProcessError):
        list(rows)
    assert len(calls) == 1


@pytest.mark.parametrize("message,device,expected", [
    ("CUDA failed with error out of memory", "cuda", True),
    ("CUDA out of memory", "cuda", True),
    ("cudaErrorMemoryAllocation", "cuda", True),
    ("CUDA out of memory", "cpu", False),
    ("mkl_malloc failed to allocate memory", "cpu", False),
    ("CUDA driver initialization failed", "cuda", False),
])
def test_worker_oom_classification_is_specific(message, device, expected):
    from core.engines.asr.whisper_worker import _is_cuda_oom
    assert _is_cuda_oom(RuntimeError(message), device) is expected
    assert not _is_cuda_oom(ValueError(message), device)


def test_real_worker_emits_typed_failure_after_lazy_inference_oom(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from core.engines.asr.whisper_worker import run
    import core.asr
    def segments():
        yield SimpleNamespace(start=0., end=1., text="partial", words=[])
        raise RuntimeError("CUDA failed with error out of memory")
    model = SimpleNamespace(model=SimpleNamespace(device="cuda", compute_type="float16"),
                            transcribe=lambda *a, **kw: (segments(), None))
    monkeypatch.setattr(core.asr, "load_whisper_model", lambda *a: model)
    monkeypatch.setattr(settings, "WHISPER_COMPUTE_TYPE", "int8")
    monkeypatch.setattr(settings, "ASR_CPU_THREADS", 1)
    request, result = tmp_path / "request.json", tmp_path / "result.jsonl"
    request.write_text(json.dumps({"model_size":"small", "device":"cuda", "compute_type":"float16",
        "cpu_threads":1, "audio_path":"fixture.wav", "options":{"language":"ko"}}))
    run(request, result)
    records = [json.loads(line) for line in result.read_text().splitlines()]
    assert [row["event"] for row in records] == ["runtime", "segment", "failed"]
    assert records[-1] == {"event":"failed", "code":"cuda_inference_oom", "device":"cuda"}


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


def test_closing_iterator_drains_child_before_returning(fake_worker):
    audio, install = fake_worker
    calls = install('''
import json, sys, time
with open(sys.argv[1], 'w') as out:
    out.write(json.dumps({'event':'segment', 'segment': {'start':0,'end':1,'text':'x','words':[]}})+'\\n')
    out.flush()
    time.sleep(30)
''')
    rows, _ = module.SubprocessWhisperModel("small").transcribe(audio)
    assert next(rows).text == "x"
    rows.close()
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
    assert [len(call["request"]["rows"]) for call in calls] == [8, 8, 5, 5]
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
if request['engine']=='sensevoice' and request['rows'][0]['id']==8:
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
    assert all(result[index] == {"sensevoice": "你好", "faster-whisper-small": "你好"} for index in range(8))
    assert all(result[index] == {"faster-whisper-small": "你好"} for index in range(8, 13))
    assert result[12] == {"faster-whisper-small": "你好"}
    assert set(evidence.last_errors) == set(range(8, 13))
    assert set(evidence.last_errors[12]) == {"sensevoice"}
    assert len(calls) == 4 and len(list(directory.glob("*.review"))) == 21
    assert "wrong partial" not in "".join(path.read_text(encoding="utf-8") for path in directory.glob("*.review"))

    # A terminal marker is still required: use a healthy worker rather than
    # accepting the failed child's incomplete first attempt.
    previous_calls = len(calls)
    calls = install(program.replace(
        "if request['engine']=='sensevoice' and request['rows'][0]['id']==8:", "if False:"))
    recovered = _LocalAudioEvidence().collect(audio, rows, checkpoint=checkpoint)
    assert recovered == {row["id"]: {"sensevoice": "你好", "faster-whisper-small": "你好"} for row in rows}
    assert len(calls) == previous_calls + 1
    assert calls[-1]["request"]["rows"] == rows[8:13] and calls[-1]["request"]["engine"] == "sensevoice"
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
    rows, _ = module.SubprocessWhisperModel("small").transcribe(audio)
    assert next(rows).text == "x"
    if parent_exits:
        assert list(rows) == []
    else:
        rows.close()
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
