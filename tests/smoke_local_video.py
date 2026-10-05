"""Opt-in online smoke test; --keep-output retains only the completed demo MP4."""
import argparse
import asyncio
import json
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import settings


async def main(keep_output=False):
    import edge_tts
    from core.streaming.pipeline import create_streaming_session, active_streaming_sessions
    from core.streaming.export import HQExporter

    task_id = 'smoke_' + uuid.uuid4().hex[:8]
    cache_dir = settings.WORKSPACE_DIR / 'cache' / task_id
    output = settings.OUTPUT_DIR / f'douyin_translated_{task_id}_hq.mp4'
    completed = False
    try:
        with tempfile.TemporaryDirectory(prefix='smoke_', dir=settings.TEMP_DIR) as tmp:
            folder = Path(tmp)
            speech = folder / 'speech.mp3'
            await edge_tts.Communicate(
                '大家好，欢迎来到我的频道。今天的天气很好，我们一起出去散步吧。',
                'zh-CN-XiaoxiaoNeural',
            ).save(str(speech))
            video = folder / 'video thử nghiệm.mp4'
            subprocess.run([
                'ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
                'color=c=0x152238:s=360x640:r=24', '-i', str(speech),
                '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p',
                '-c:a', 'aac', '-shortest', str(video),
            ], check=True)
            session = create_streaming_session(task_id, video, initial_buffer_seconds=3)
            await asyncio.wait_for(session.start(), timeout=90)
            await asyncio.wait_for(session.worker_task, timeout=300)
            assert not session.error, session.error
            rows = [dict(seg.to_dict(), audio_path=seg.audio_path) for seg in session.segments.values()]
            assert rows and all(row['status'] == 'READY' for row in rows)
            spoken = [row for row in rows if row['text_zh']]
            assert spoken and all(row['final_vi'] and row['final_vi'] != row['text_zh'] for row in spoken)
            assert all(row['audio_path'] and Path(row['audio_path']).read_bytes()[:4] == b'RIFF' for row in spoken)
            result = await asyncio.to_thread(HQExporter().export, task_id, video, rows, session.total_duration)
            probe = subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-of', 'json',
                                    result['final_video_path']], capture_output=True, text=True, check=True)
            streams = json.loads(probe.stdout)['streams']
            assert {'audio', 'video'} <= {stream['codec_type'] for stream in streams}
            completed = True
            print(json.dumps({'result': 'PASS', 'profile': settings.WHISPER_MODEL_SIZE,
                              'duration': session.total_duration, 'segments': len(rows),
                              'transcript': [row['text_zh'] for row in spoken],
                              'translation': [row['final_vi'] for row in spoken],
                              'export_bytes': output.stat().st_size,
                              'output_path': str(output.resolve()) if keep_output else None}, ensure_ascii=False, indent=2))
    finally:
        session = active_streaming_sessions.pop(task_id, None)
        if session and session.worker_task and not session.worker_task.done():
            session.stop()
            await asyncio.gather(session.worker_task, return_exceptions=True)
        if not (keep_output and completed):
            output.unlink(missing_ok=True)
        # This exact unique directory was created by this test, never user media.
        if cache_dir.exists() and cache_dir.resolve().parent == (settings.WORKSPACE_DIR / 'cache').resolve():
            shutil.rmtree(cache_dir)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--keep-output', action='store_true',
                        help='Keep the single completed demonstration MP4 in outputs.')
    asyncio.run(main(keep_output=parser.parse_args().keep_output))
