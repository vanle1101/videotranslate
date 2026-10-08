"""Offline readiness check; run with the project's venv Python interpreter."""
import importlib
import importlib.metadata
import re
import shutil
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import settings


REQUIRED_MODULES = (
    'fastapi', 'uvicorn', 'PySide6.QtWebEngineWidgets', 'faster_whisper',
    'edge_tts', 'yt_dlp', 'soundfile', 'psutil', 'numpy', 'scipy', 'pydub',
    'av', 'cv2', 'rapidocr_onnxruntime',
)
# Keep these bounds aligned with requirements.txt. A newer package inherited
# through system-site-packages is not proof that the current adapter supports it.
REQUIRED_VERSIONS = {
    'numpy': ((1, 24, 0), (2, 0, 0), 'numpy>=1.24,<2'),
    'scipy': ((1, 11, 0), None, 'scipy>=1.11'),
    'av': ((11, 0, 0), (19, 0, 0), 'av>=11,<19'),
}


def release_version(value):
    """Read the release portion of a package version without dropping suffixes.

    Using split/is digit silently parsed 2.0rc1 as (2,), while an absent version
    bypassed the check entirely. The declared constraints refer to releases;
    pre-release installations are conservatively rejected for this stable runtime.
    """
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r'(\d+)\.(\d+)(?:\.(\d+))?(?:\.post\d+)?(?:\+[a-zA-Z0-9.-]+)?', value.strip())
    if not match:
        return None
    return tuple(int(part or 0) for part in match.groups())


def check_versions():
    failures = []
    for name, (minimum, maximum, requirement) in REQUIRED_VERSIONS.items():
        try:
            raw = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            failures.append(f'{name} package metadata is missing; expected {requirement}.')
            continue
        version = release_version(raw)
        if version is None:
            failures.append(f'{name} package version could not be checked; expected {requirement}.')
        elif version < minimum or (maximum is not None and version >= maximum):
            # Only normalized numeric version data is printed, never arbitrary
            # metadata strings or environment credentials.
            release = '.'.join(map(str, version))
            failures.append(f'{name} {release} is unsupported; expected {requirement} in the project venv.')
        else:
            print(f'OK version: {name} {".".join(map(str, version))}')
    return failures


def main():
    failures = []
    for name in REQUIRED_MODULES:
        try:
            importlib.import_module(name)
            print(f'OK module: {name}')
        except Exception as exc:
            failures.append(f'{name} could not be imported ({type(exc).__name__}).')
    # The visual-translation path imports OCR lazily, so a missing optional
    # module otherwise looks healthy until the user clicks Start.  Enforce the
    # versions declared by requirements.txt before launch and catch the common
    # --system-site-packages NumPy/SciPy mismatch explicitly.
    failures.extend(check_versions())
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
    for name in ('model.bin', 'config.json', 'tokenizer.json', 'vocabulary.txt'):
        path = model / name
        if not path.is_file() or path.stat().st_size <= 0:
            failures.append(f'Missing Whisper file: {name}. Run setup.bat.')
    print(f'Profile: {settings.WHISPER_MODEL_SIZE}, {settings.DEVICE}, '
          f'{settings.WHISPER_COMPUTE_TYPE}, {settings.ASR_CPU_THREADS} CPU threads, '
          f'{settings.TTS_ENGINE}')
    for failure in failures:
        print(f'ERROR: {failure}')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
