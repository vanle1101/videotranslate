"""Offline readiness check; run with the project's venv Python interpreter."""
import importlib
import shutil
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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
    if settings.LLM_PROVIDER == 'openrouter-free':
        from core.engines.translation.openrouter_client import resolve_openrouter_key
        if not resolve_openrouter_key():
            failures.append('OpenRouter key missing. Connect OpenRouter in OpenCode or set OPENROUTER_API_KEY.')
        else:
            print(f'OK translation: OpenRouter Free, {settings.OPENROUTER_MODEL}, local key detected')
    if settings.LLM_PROVIDER == 'opencode':
        from core.engines.translation.opencode_client import find_opencode_executable, resolve_api_key
        if not find_opencode_executable():
            failures.append('OpenCode CLI missing. Install OpenCode or choose another translation provider.')
        elif not resolve_api_key():
            failures.append('OpenCode key missing. Connect OpenCode Zen with /connect.')
        else:
            print(f'OK translation: OpenCode CLI, {settings.OPENCODE_MODEL}, local key detected')
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
