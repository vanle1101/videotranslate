"""Regression guards; these isolated checks are not real-provider acceptance."""
import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import main
from core.streaming.export import HQExporter


@pytest.mark.parametrize('consumer', ['running', 'editing', 'exporting'])
def test_settings_cannot_change_consumed_configuration_mid_run(monkeypatch, consumer):
    monkeypatch.setattr(main, 'active_streaming_sessions', {
        'run': SimpleNamespace(is_running=consumer == 'running', is_editing=consumer == 'editing')})
    monkeypatch.setattr(main, 'active_export_tasks', {'export': {'status': 'RUNNING' if consumer == 'exporting' else 'COMPLETED'}})
    monkeypatch.setattr(main.settings, 'LLM_PROVIDER', 'opencode')
    writes = []
    monkeypatch.setattr(main, 'update_env_file', lambda values: writes.append(values))
    with pytest.raises(HTTPException) as error:
        asyncio.run(main.update_settings(main.ConfigRequest(llm_provider='free')))
    assert error.value.status_code == 409
    assert not writes and main.settings.LLM_PROVIDER == 'opencode'


@pytest.mark.parametrize('case', ['missing', 'empty', 'bad_metadata', 'no_audio', 'duration', 'decode_failure'])
def test_export_rejects_missing_or_unreadable_media(tmp_path, monkeypatch, case):
    from core.streaming import export as module
    video = tmp_path / 'result.mp4'
    if case != 'missing':
        video.write_bytes(b'' if case == 'empty' else b'fixture')
    metadata = {'format': {'duration': '2'}, 'streams': [
        {'codec_type': 'video', 'width': 10, 'height': 10}, {'codec_type': 'audio'}]}
    if case == 'bad_metadata':
        metadata = {}
    elif case == 'no_audio':
        metadata['streams'].pop()
    elif case == 'duration':
        metadata['format']['duration'] = '20'

    def media(command, *args, **kwargs):
        if command[0] == 'ffmpeg' and case == 'decode_failure':
            raise RuntimeError('decode failed')
        return json.dumps(metadata).encode()

    monkeypatch.setattr(module, 'run_media', media)
    with pytest.raises((ValueError, RuntimeError)):
        HQExporter.validate_output(video, 2)
