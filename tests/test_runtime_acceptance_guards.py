"""Regression guards; these isolated checks are not real-provider acceptance."""
import asyncio
import json
import shutil
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

import main
from core.streaming.export import HQExporter


def test_semantic_review_invalidates_old_hardsub_translation_before_publication(tmp_path, monkeypatch):
    from core.streaming.pipeline import SegmentItem, StreamingPipelineSession
    monkeypatch.setattr(main.settings, 'BASE_DIR', tmp_path)
    item = StreamingPipelineSession('screen-review', tmp_path / 'source.mp4', visual_translation=True)
    segment = SegmentItem(0, 1., 3., 2.)
    segment.status = 'READY'
    segment.text_zh, segment.final_vi = '跟我走', 'Đi theo tôi.'
    segment.verification = {'status': 'verified', 'semantic_verified': True}
    item.segments = {0: segment}
    item.screen_texts = [{'id': 'o0', 'start': 1., 'end': 3., 'kind': 'subtitle',
                         'text_zh': '跟我走', 'text_vi': 'Đi theo tôi.',
                         'needs_review': False, 'source_region_verified': True}]
    item.emit = AsyncMock()
    item.report_progress = AsyncMock()
    item._run_blocking = AsyncMock(return_value={
        'segments': {0: {**segment.to_dict(), 'needs_review': True,
            'review_reason': 'Chưa xác định người nói.',
            'verification': {'status': 'unresolved', 'semantic_verified': False}}},
        'summary': {'checked': 1, 'verified': 0, 'corrected': 0, 'unresolved': 1}})
    asyncio.run(item._review_translations(regenerate_audio=True))
    assert item.screen_texts[0]['needs_review'] is True
    assert item.screen_texts[0]['source_region_verified'] is True
    updates = [call.args[1] for call in item.emit.call_args_list if call.args[0] == 'screen_update']
    completed = [call.args[1] for call in item.emit.call_args_list if call.args[0] == 'review_complete']
    assert updates and completed
    assert updates[-1]['screen_texts'] == completed[-1]['screen_texts'] == item.screen_texts


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


def test_export_validation_decodes_the_complete_result(tmp_path, monkeypatch):
    """A valid header plus a corrupt tail must not pass a prefix-only probe."""
    from core.streaming import export as module
    video = tmp_path / "result.mp4"
    video.write_bytes(b"fixture")
    metadata = json.dumps({'format': {'duration': '2'}, 'streams': [
        {'codec_type': 'video', 'width': 10, 'height': 10}, {'codec_type': 'audio'}]}).encode()
    commands = []

    def media(command, *args, **kwargs):
        commands.append(command)
        return metadata if command[0] == 'ffprobe' else b''

    monkeypatch.setattr(module, 'run_media', media)
    HQExporter.validate_output(video, 2)
    decode = next(command for command in commands if command[0] == 'ffmpeg')
    assert '-t' not in decode
    assert decode[-2:] == ['null', '-']


@pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'), reason='FFmpeg required')
def test_real_export_rejects_corruption_after_first_second(tmp_path):
    from core.media_process import run_media
    video = tmp_path / 'late-corruption.mp4'
    run_media(['ffmpeg', '-v', 'error', '-nostdin', '-y', '-f', 'lavfi', '-i',
               'testsrc2=s=160x240:r=10:d=4', '-f', 'lavfi', '-i',
               'sine=frequency=440:duration=4', '-c:v', 'libx264', '-g', '20',
               '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', str(video)])
    HQExporter.validate_output(video, 4)
    packets = json.loads(run_media(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
        '-show_packets', '-show_entries', 'packet=pts_time,pos,size', '-of', 'json', str(video)],
        capture_output=True))['packets']
    packet = next(item for item in packets if float(item['pts_time']) >= 2)
    with video.open('r+b') as output:
        output.seek(int(packet['pos']))
        output.write(bytes(int(packet['size'])))
    # Reproduce the old blind spot: the opening second is still decodable.
    run_media(['ffmpeg', '-v', 'error', '-xerror', '-nostdin', '-i', str(video),
               '-t', '1', '-map', '0:v:0', '-map', '0:a:0', '-f', 'null', '-'])
    with pytest.raises(RuntimeError):
        HQExporter.validate_output(video, 4)
