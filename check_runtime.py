"""Offline readiness check; run with the project's venv Python interpreter."""
import importlib
import shutil
import sys
from config import settings


def main():
    failures = []
    for name in ('fastapi', 'uvicorn', 'PySide6.QtWebEngineWidgets', 'faster_whisper',
                 'edge_tts', 'yt_dlp', 'soundfile', 'psutil'):
        try:
            importlib.import_module(name)
            print(f'OK module: {name}')
        except Exception as exc:
            failures.append(f'{name}: {exc}')
    for name in ('ffmpeg', 'ffprobe'):
        if not shutil.which(name):
            failures.append(f'{name} missing from PATH')
        else:
            print(f'OK tool: {name}')
    model = settings.WORKSPACE_DIR / 'models' / f'faster-whisper-{settings.WHISPER_MODEL_SIZE}'
    for name in ('model.bin', 'config.json', 'tokenizer.json'):
        if not (model / name).is_file():
            failures.append(f'Missing Whisper file: {name}. Run setup.bat.')
    print(f'Profile: {settings.WHISPER_MODEL_SIZE}, {settings.DEVICE}, '
          f'{settings.WHISPER_COMPUTE_TYPE}, {settings.ASR_CPU_THREADS} CPU threads, '
          f'{settings.TTS_ENGINE}')
    for failure in failures:
        print(f'ERROR: {failure}')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
