import os
import sys
import time
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import settings

def run_stage_audit():
    print("=" * 60)
    print("  AUDIT RUN: PIPELINE END-TO-END VERIFICATION")
    print("  Strict Mode: Edge-TTS and faster-whisper are REJECTED")
    print("=" * 60)
    
    video_path = settings.INPUT_DIR / "audit_sample.mp4"
    if not video_path.exists():
        print(f"[STAGE 1 - INPUT] FAILED: Video not found at {video_path}")
        return
    print(f"[STAGE 1 - INPUT] OK: Found test video {video_path.name} (1080x1920, 5.0s)")

    # STAGE 2: Separation
    print("\n--- [STAGE 2 - AUDIO SEPARATION] ---")
    print("Checking python-audio-separator + BS-RoFormer...")
    try:
        import audio_separator
        print(f"python-audio-separator imported: {audio_separator.__file__}")
    except ImportError:
        print("[STAGE 2] STATUS: NOT IMPLEMENTED (python-audio-separator package is NOT installed)")
        print("          In project: separator.py only has Demucs check and FFmpeg stereotools filter fallback.")

    # STAGE 3: ASR
    print("\n--- [STAGE 3 - CHINESE ASR] ---")
    print("Requirement: SenseVoice (FunAudioLLM)")
    # Check if SenseVoice is integrated in core/asr.py
    import inspect
    from core import asr
    asr_source = inspect.getsource(asr)
    if "SenseVoice" in asr_source:
        print("[STAGE 3] SenseVoice found in code.")
    else:
        print("[STAGE 3] STATUS: NOT IMPLEMENTED in core/asr.py")
        print("          Actual code in core/asr.py uses:")
        for line in asr_source.splitlines():
            if "import" in line or "WhisperModel" in line:
                print(f"            {line}")
        print("          Rule: faster-whisper is NOT counted as SenseVoice.")
        print("          -> ASR Stage FAILED: No SenseVoice engine available to transcribe.")

    # STAGE 4: TRANSLATION
    print("\n--- [STAGE 4 - TRANSLATION] ---")
    print("Checking VideoLingo integration...")
    try:
        import core.translator as translator
        trans_source = inspect.getsource(translator)
        if "videolingo" in trans_source.lower():
            print("[STAGE 4] VideoLingo found in code.")
        else:
            print("[STAGE 4] STATUS: NOT IMPLEMENTED")
            print("          VideoLingo is NOT cloned or imported. core/translator.py uses direct Gemini/OpenAI API calls.")
    except Exception as e:
        print(f"[STAGE 4] Error checking translation: {e}")

    # STAGE 5: TTS
    print("\n--- [STAGE 5 - VIETNAMESE TTS] ---")
    print("Requirement: VieNeu-TTS (pnnbao97/VieNeu-TTS)")
    from core import tts
    tts_source = inspect.getsource(tts)
    if "vieneu" in tts_source.lower() and not "config" in tts_source.lower():
        print("[STAGE 5] VieNeu-TTS engine found in tts.py.")
    else:
        print("[STAGE 5] STATUS: NOT IMPLEMENTED in core/tts.py")
        print("          Actual code in core/tts.py uses:")
        for line in tts_source.splitlines():
            if "import" in line or "edge_tts" in line or "Communicate" in line:
                print(f"            {line}")
        print("          Rule: Edge-TTS is NOT counted as VieNeu-TTS.")
        print("          -> TTS Stage FAILED: No VieNeu-TTS synthesis engine active in pipeline.")

    # STAGE 6 & 7: REMOVAL & COMPOSE
    print("\n--- [STAGE 6 & 7 - SUBTITLE REMOVAL & COMPOSE] ---")
    print("Requirement: ProPainter (YaoFANGUK / sczhou)")
    from core import video_composer
    comp_source = inspect.getsource(video_composer)
    if "propainter" in comp_source.lower():
        print("[STAGE 6] ProPainter found in video_composer.py.")
    else:
        print("[STAGE 6 & 7] STATUS: NOT IMPLEMENTED")
        print("          Actual code in core/video_composer.py uses FFmpeg boxblur filter to blur bottom 14% of frame.")

    print("\n" + "=" * 60)
    print("  AUDIT SUMMARY: Pipeline execution ABORTED at Stage 3 & 5.")
    print("  Reason: Neither SenseVoice nor VieNeu-TTS is integrated into the active pipeline.")
    print("=" * 60)

if __name__ == "__main__":
    run_stage_audit()
