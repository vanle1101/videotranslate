param([switch]$SkipModel)
$ErrorActionPreference = 'Stop'
$studioRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $studioRoot
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:HF_HUB_DISABLE_XET = '1'

foreach ($tool in @('ffmpeg', 'ffprobe')) {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
        throw "$tool is missing from PATH. Install FFmpeg and open a new terminal."
    }
}
if (-not (Test-Path -LiteralPath 'venv\Scripts\python.exe')) {
    # Reuse compatible system packages, without modifying the global installation.
    py -3.12 -m venv --system-site-packages venv
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 is required. Environment creation failed.' }
}
$pythonExe = Join-Path $studioRoot 'venv\Scripts\python.exe'
Write-Host 'Dependencies and the Whisper Small model use approximately 1-2 GB of disk space.'
& $pythonExe -m pip install --no-cache-dir -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
if (-not (Test-Path -LiteralPath '.env')) {
    Copy-Item -LiteralPath '.env.example' -Destination '.env'
}
if (-not $SkipModel) {
    & $pythonExe -B -c "from faster_whisper.utils import download_model; from config import settings; p=settings.WORKSPACE_DIR/'models'/('faster-whisper-'+settings.WHISPER_MODEL_SIZE); ready=all((p/n).is_file() and (p/n).stat().st_size>0 for n in ('model.bin','config.json','tokenizer.json','vocabulary.txt')); print(p if ready else download_model(settings.WHISPER_MODEL_SIZE, output_dir=str(p)))"
    if ($LASTEXITCODE -ne 0) { throw 'Whisper download failed. Check Internet connectivity and rerun setup.bat.' }
}
& $pythonExe -B (Join-Path $PSScriptRoot 'check_runtime.py')
if ($LASTEXITCODE -ne 0) { throw 'Runtime checks failed.' }
& (Join-Path $PSScriptRoot 'create_shortcut.ps1')
