import asyncio
from pathlib import Path
from core.subtitle import SubtitleGenerator
from core.tts import VietnameseTTS
from core.audio_ducking import PremiumAudioMixer
from config import settings

async def run_modules():
    settings.TEMP_DIR.mkdir(parents=True, exist_ok=True)
    print("=== TEST 1: Subtitle Generator ===")
    sub_gen = SubtitleGenerator()
    dummy_segments = [
        {"id": 0, "start": 0.5, "end": 3.0, "duration": 2.5, "text": "大家好，今天给大家分享一个好东西。", "vi_text": "Chào cả nhà, hôm nay mình chia sẻ món này đỉnh chóp luôn!"},
        {"id": 1, "start": 3.2, "end": 6.0, "duration": 2.8, "text": "真的太好用了，赶紧试试看！", "vi_text": "Dùng cực kỳ thích, các bạn thử ngay đi nhé!"}
    ]
    test_srt = settings.TEMP_DIR / "test.srt"
    test_ass = settings.TEMP_DIR / "test.ass"
    sub_gen.generate_srt(dummy_segments, test_srt)
    sub_gen.generate_ass(dummy_segments, test_ass)
    print(f"[OK] SRT generated ({test_srt.stat().st_size} bytes)")
    print(f"[OK] ASS generated ({test_ass.stat().st_size} bytes)")

    print("\n=== TEST 2: Vietnamese Edge-TTS ===")
    tts = VietnameseTTS(voice="vi-VN-HoaiMyNeural")
    test_audio = settings.TEMP_DIR / "test_tts.mp3"
    await tts.generate_speech_segment("Xin chào, đây là âm thanh thử nghiệm từ studio.", test_audio)
    dur = tts.get_audio_duration(test_audio)
    print(f"[OK] Audio generated: {test_audio.name}, duration: {dur:.2f}s")

    print("\n=== TEST 3: Audio Ducking ===")
    mixer = PremiumAudioMixer()
    out_mixed = settings.TEMP_DIR / "test_mixed.wav"
    # Mix test_audio with itself as a small, real media smoke check.
    mixer.mix(test_audio, test_audio, out_mixed, total_duration=dur or None)
    print(f"[OK] Audio Ducking mixed: {out_mixed.name} ({out_mixed.stat().st_size} bytes)")

    print("\n=== ALL TESTS PASSED! ===")

if __name__ == "__main__":
    asyncio.run(run_modules())
