import os
import sys
from pathlib import Path
from typing import Literal
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent
WORKSPACE_DIR = BASE_DIR / "workspace"
LOGS_DIR = WORKSPACE_DIR / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)

class SafeStream:
    """Safe stream wrapper for Windows GUI / pythonw.exe where stdout/stderr is None."""
    def __init__(self, target_stream=None, log_file=None):
        self.target = target_stream
        self.log_file = log_file
        self.encoding = "utf-8"
        self.errors = "replace"

    def write(self, s):
        if not s:
            return 0
        if self.target is not None:
            try:
                self.target.write(s)
            except Exception:
                pass
        if self.log_file:
            try:
                with open(self.log_file, "a", encoding="utf-8", errors="replace") as f:
                    f.write(s)
            except Exception:
                pass
        return len(s)

    def flush(self):
        if self.target is not None and hasattr(self.target, "flush"):
            try:
                self.target.flush()
            except Exception:
                pass

    def isatty(self):
        if self.target is not None and hasattr(self.target, "isatty"):
            try:
                return bool(self.target.isatty())
            except Exception:
                pass
        return False

    def reconfigure(self, **kwargs):
        if self.target is not None and hasattr(self.target, "reconfigure"):
            try:
                self.target.reconfigure(**kwargs)
            except Exception:
                pass

# Protect against pythonw.exe None stdout/stderr
if sys.stdout is None:
    sys.stdout = SafeStream(log_file=LOGS_DIR / "stdout.log")
elif hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

if sys.stderr is None:
    sys.stderr = SafeStream(log_file=LOGS_DIR / "stderr.log")
elif hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

class Settings(BaseSettings):
    # App config
    APP_NAME: str = "Douyin2TikTok AI Studio"
    HOST: str = "127.0.0.1"
    PORT: int = 0  # 0 indicates dynamic ephemeral port allocation
    DEBUG: bool = False
    PREWARM_MODELS: bool = False

    # Directories
    BASE_DIR: Path = BASE_DIR
    WORKSPACE_DIR: Path = WORKSPACE_DIR
    INPUT_DIR: Path = WORKSPACE_DIR / "inputs"
    OUTPUT_DIR: Path = WORKSPACE_DIR / "outputs"
    TEMP_DIR: Path = WORKSPACE_DIR / "temp"

    # LLM Translation API Settings
    # OpenCode reuses its local login; no translation LLM is loaded into RAM.
    LLM_PROVIDER: str = "opencode" # also 'openrouter-free', 'free', 'gemini', 'deepseek', 'openai', 'muse'
    MUSE_BROWSER_MODE: Literal["dedicated", "existing"] = "dedicated"
    MUSE_CHROME_PORT: int = Field(default=0, ge=0, le=65535)
    OPENCODE_API_KEY: str = "" # Optional local override; otherwise read OpenCode auth.json.
    OPENCODE_MODEL: str = "muse-spark-1.3-contributor-free"
    OPENCODE_TIMEOUT: float = 60.0
    # A single free provider request at a time by default. Stage deadlines
    # shorten, never extend, each adapter's existing request timeout.
    OPENCODE_CONCURRENCY: int = Field(default=1, ge=1, le=4)
    OPENCODE_QUEUE_LIMIT: int = Field(default=24, ge=1, le=256)
    # Queue admission is separate from a provider request deadline. It must
    # cover the longest bounded review request already ahead in the FIFO.
    OPENCODE_QUEUE_TIMEOUT: float = Field(default=180.0, gt=0, le=600)
    OPENCODE_CIRCUIT_FAILURES: int = Field(default=4, ge=1, le=20)
    OPENCODE_CIRCUIT_COOLDOWN: float = Field(default=30.0, gt=0, le=300)
    OPENCODE_TASK_TIMEOUTS: dict[str, float] = Field(default_factory=lambda: {
        "translation": 60.0, "summary": 60.0, "semantic_review": 90.0,
        "address_review": 90.0, "fluency_review": 60.0,
    })
    OPENCODE_SCHEMA_REPAIR_ATTEMPTS: int = Field(default=2, ge=0, le=4)
    OPENROUTER_API_KEY: str = "" # Otherwise reuse OpenRouter-Free login from OpenCode.
    OPENROUTER_MODEL: str = "inclusionai/ling-3.0-flash-sante:free"
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    DEEPSEEK_API_KEY: str = os.getenv("DEEPSEEK_API_KEY", "")
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"

    # Default LLM Models
    GEMINI_MODEL: str = "gemini-2.5-flash"
    DEEPSEEK_MODEL: str = "deepseek-chat"

    # Audio & Vocal Separation
    # Options: 'roformer' (via python-audio-separator), 'demucs', 'none'
    SEPARATION_ENGINE: str = "realtime"
    SUPPRESSION_MODE: Literal["AUTO", "DSP_MONO_ADAPTIVE_FORMANT", "DSP_STEREO_CENTER_CANCEL"] = "AUTO"
    INITIAL_BUFFER_SECONDS: float = Field(default=10.0, gt=0, le=120)
    DEMUCS_MODEL: str = "htdemucs"
    ROFORMER_MODEL: str = "model_bs_roformer_ep_317_sdr_12.9755.ckpt"

    # Chinese Speech-to-Text (ASR)
    # Options: 'sensevoice', 'faster-whisper', 'whisper'
    ASR_ENGINE: str = "faster-whisper" # fallback fast and reliable
    WHISPER_MODEL_SIZE: str = "small"
    DEVICE: str = "cpu" # Switch to cuda only after verifying the CUDA runtime.
    WHISPER_COMPUTE_TYPE: str = "int8"
    ASR_CPU_THREADS: int = 6
    ASR_PROCESS_TIMEOUT: float = Field(default=600.0, gt=0, le=21600)
    ASR_PROCESS_QUEUE_TIMEOUT: float = Field(default=180.0, gt=0, le=21600)
    ASR_REVIEW_PROCESS_TIMEOUT: float = Field(default=600.0, gt=0, le=21600)

    # Vietnamese Text-to-Speech (TTS)
    # Options: 'edge-tts', 'vieneu-tts', 'zerotts', 'custom-api'
    TTS_ENGINE: str = "edge-tts" # default zero-config, can switch to vieneu-tts / zerotts
    EDGE_VOICE: str = "vi-VN-HoaiMyNeural" # 'vi-VN-HoaiMyNeural' (Nữ) or 'vi-VN-NamMinhNeural' (Nam)
    VIENEU_API_URL: str = "http://127.0.0.1:8001/tts"
    CUSTOM_TTS_URL: str = ""

    # Audio Mixing / Ducking
    BGM_VOLUME_DUCKED_DB: float = -14.0 # Volume of BGM when voice is speaking
    BGM_VOLUME_NORMAL_DB: float = -2.0  # Volume of BGM when no speech
    VOICE_VOLUME_BOOST_DB: float = 2.0  # Boost for Vietnamese voiceover

    # Subtitle & Video layout
    SUBTITLE_FONT: str = "Arial"
    SUBTITLE_FONT_SIZE: int = 18
    # ASS uses BBGGRR; this is the bright yellow used by the preview.
    SUBTITLE_PRIMARY_COLOR: str = "&H0000EBFF"
    SUBTITLE_OUTLINE_COLOR: str = "&H00000000" # Black outline
    MASK_CHINESE_SUBTITLE: bool = True # Apply sleek blurred glass banner to cover Chinese hard sub

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env", env_file_encoding="utf-8", extra="allow"
    )

settings = Settings()
settings.INPUT_DIR.mkdir(parents=True, exist_ok=True)
settings.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
settings.TEMP_DIR.mkdir(parents=True, exist_ok=True)
