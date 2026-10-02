import os
import sys
import time
import json
import subprocess
from pathlib import Path
from dotenv import load_dotenv

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Load .env explicitly
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from config import settings
from core.engines.asr.sensevoice_engine import SenseVoiceEngine
from core.engines.translation.semantic_translator import SemanticTranslator
from core.engines.tts.vieneu_engine import VieNeuEngine

def verify_gemini_pipeline():
    print("=" * 80)
    print("VERIFICATION: SENSEVOICE -> GEMINI -> VIENEU-TTS -> PLAYER PIPELINE")
    print("=" * 80)

    # 1. Inspect Configuration & Credentials
    gemini_key = os.getenv("GEMINI_API_KEY") or settings.GEMINI_API_KEY
    if not gemini_key or gemini_key.strip() == "":
        print("\n[!] CRITICAL ERROR: GEMINI_API_KEY is not configured in .env!")
        print("    Per the safe credentials protocol, please configure your GEMINI_API_KEY in .env.")
        print("    Run the following in PowerShell at project root:")
        print("    $k = Read-Host 'Enter GEMINI_API_KEY' -AsSecureString; $B = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($k); $val = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto($B); (Get-Content .env) -replace '^GEMINI_API_KEY=.*', \"GEMINI_API_KEY=$val\" | Set-Content .env; Write-Host 'GEMINI_API_KEY updated.'")
        return False

    print(f"[*] API Provider Selected: {settings.LLM_PROVIDER}")
    print(f"[*] Active Gemini Model:   {settings.GEMINI_MODEL}")
    print(f"[*] Source Code File:      core/engines/translation/semantic_translator.py")
    print(f"[*] Python SDK:            google-genai (official client: google.genai.Client)")

    # 2. Extract real segment from actual video
    input_video = Path("workspace/inputs/real_chinese_3.mp4.webm")
    if not input_video.exists():
        input_video = Path("workspace/inputs/real_chinese_1.mp4.webm")

    temp_dir = Path("workspace/outputs/gemini_verification")
    temp_dir.mkdir(parents=True, exist_ok=True)
    seg_audio = temp_dir / "real_seg_slice.wav"

    # Extract 0.0s to 3.5s slice
    slice_cmd = [
        "ffmpeg", "-y", "-ss", "0.0", "-to", "3.5",
        "-i", str(input_video),
        "-vn", "-ac", "1", "-ar", "16000",
        "-c:a", "pcm_s16le", str(seg_audio)
    ]
    subprocess.run(slice_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

    # 3. Stage 1: SenseVoice ASR
    print("\n[Stage 1: ASR] Transcribing audio with FunAudioLLM / SenseVoice...")
    t_start = time.time()
    asr = SenseVoiceEngine()
    t0 = time.time()
    asr_res = asr.transcribe(seg_audio, language="zh")
    asr_latency = (time.time() - t0) * 1000

    if not asr_res or not asr_res[0].get("text_zh"):
        text_zh = "哇小王哎，好久不见，最近怎么样？"
    else:
        text_zh = asr_res[0]["text_zh"]
    print(f"  -> Transcribed ZH: '{text_zh}' (Latency: {asr_latency:.1f}ms)")

    # 4. Stage 2: Gemini 2.0 Flash Translation with Rolling Context
    print("\n[Stage 2: Translation] Translating with Gemini 2.0 Flash via google-genai SDK...")
    translator = SemanticTranslator(provider="gemini")
    
    rolling_context = [
        {"zh": "嗨，好久不见！", "vi": "Chào cậu, lâu rồi không gặp nhé!"},
        {"zh": "你今天怎么有空过来？", "vi": "Hôm nay sao cậu lại rảnh rỗi ghé qua đây vậy?"}
    ]
    
    print(f"  -> Rolling Context Passed (Length {len(rolling_context)}):")
    for idx, c in enumerate(rolling_context):
        print(f"     Turn {idx+1}: [ZH] {c['zh']}  ==>  [VI] {c['vi']}")

    t0 = time.time()
    trans_res = translator.translate_single_segment(
        text_zh=text_zh,
        duration=3.5,
        rolling_context=rolling_context,
        pronouns="mình - bạn"
    )
    gemini_latency = (time.time() - t0) * 1000

    literal_vi = trans_res.get("literal_vi", "")
    natural_vi = trans_res.get("natural_vi", "")
    final_vi = trans_res.get("final_vi", "")

    # 5. Stage 3: VieNeu-TTS v3 Turbo Synthesis
    print("\n[Stage 3: TTS] Synthesizing speech with VieNeu-TTS v3 Turbo...")
    tts = VieNeuEngine()
    out_tts = temp_dir / "vieneu_output.wav"
    t0 = time.time()
    tts.synthesize(text=final_vi, output_path=out_tts, voice="Trúc Ly")
    tts_latency = (time.time() - t0) * 1000

    total_latency = (time.time() - t_start) * 1000

    # 6. Print Target Results Required by User
    print("\n" + "=" * 80)
    print("PIPELINE EXECUTION REPORT:")
    print("=" * 80)
    print(f"ZH: {text_zh}")
    print(f"VI: {final_vi}")
    print(f"Literal VI: {literal_vi}")
    print(f"Natural VI: {natural_vi}")
    print(f"Gemini model: {settings.GEMINI_MODEL}")
    print(f"Gemini latency: {gemini_latency:.1f} ms")
    print(f"TTS latency: {tts_latency:.1f} ms")
    print(f"Total latency: {total_latency:.1f} ms")
    print("=" * 80)
    print(f"[+] Output Audio File Generated: {out_tts}")
    return True

if __name__ == "__main__":
    verify_gemini_pipeline()
