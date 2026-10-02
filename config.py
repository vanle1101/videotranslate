import os
from pathlib import Path
from pydantic_settings import BaseSettings

BASE_DIR = Path(__file__).resolve().parent
WORKSPACE_DIR = BASE_DIR / "workspace"
WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)

class Settings(BaseSettings):
    # App config
    APP_NAME: str = "Douyin2TikTok AI Studio"
    HOST: str = "127.0.0.1"
    PORT: int = 8000
    DEBUG: bool = True

    # Directories
    BASE_DIR: Path = BASE_DIR
    WORKSPACE_DIR: Path = WORKSPACE_DIR
    INPUT_DIR: Path = WORKSPACE_DIR / "inputs"
    OUTPUT_DIR: Path = WORKSPACE_DIR / "outputs"
    TEMP_DIR: Path = WORKSPACE_DIR / "temp"

    # LLM Translation API Settings
    # Supports Gemini API, DeepSeek API, OpenAI API
    LLM_PROVIDER: str = "gemini" # 'gemini', 'deepseek', 'openai'
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    DEEPSEEK_API_KEY: str = os.getenv("DEEPSEEK_API_KEY", "")
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_BASE_URL: str = "https://api.deepseek.com/v1"

    # Default LLM Models
    GEMINI_MODEL: str = "gemini-2.0-flash"
    DEEPSEEK_MODEL: str = "deepseek-chat"

    # Audio & Vocal Separation
    # Options: 'roformer' (via python-audio-separator), 'demucs', 'none'
    SEPARATION_ENGINE: str = "demucs"
    DEMUCS_MODEL: str = "htdemucs"
    ROFORMER_MODEL: str = "model_bs_roformer_ep_317_sdr_12.9755.ckpt"

    # Chinese Speech-to-Text (ASR)
    # Options: 'sensevoice', 'faster-whisper', 'whisper'
    ASR_ENGINE: str = "faster-whisper" # fallback fast and reliable
    WHISPER_MODEL_SIZE: str = "small" # 1050 Ti friendly (small fits easily in 4GB or CPU)
    DEVICE: str = "cuda" # 'cuda' or 'cpu'

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
    SUBTITLE_FONT: str = "Montserrat ExtraBold"
    SUBTITLE_FONT_SIZE: int = 18
    SUBTITLE_PRIMARY_COLOR: str = "&H0000FFFF" # Yellow BGR
    SUBTITLE_OUTLINE_COLOR: str = "&H00000000" # Black outline
    MASK_CHINESE_SUBTITLE: bool = True # Apply sleek blurred glass banner to cover Chinese hard sub

    class Config:
        env_file = ".env"
        extra = "allow"

settings = Settings()
settings.INPUT_DIR.mkdir(parents=True, exist_ok=True)
settings.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
settings.TEMP_DIR.mkdir(parents=True, exist_ok=True)
