import asyncio
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, str(Path(__file__).parent.parent))
from core.streaming.pipeline import create_streaming_session

async def test_streaming():
    print("=" * 70)
    print("⚡ TESTING REAL-TIME STREAMING SEGMENT PIPELINE")
    print("=" * 70)

    input_video = Path("workspace/inputs/e2e_input.mp4")
    if not input_video.exists():
        print("[!] Input video not found!")
        return

    events_received = []

    async def on_event(event_type: str, data: dict):
        events_received.append((event_type, data))
        if event_type == "segment_update":
            s_id = data.get("id")
            status = data.get("status")
            zh = data.get("text_zh", "")
            vi = data.get("final_vi", data.get("text_vi", ""))
            print(f"  [Seg #{s_id}] Status: {status:11s} | ZH: {zh:15s} | VI: {vi}")
        elif event_type == "ready_to_play":
            print(f"\n🎯 [READY TO PLAY] Time To First Play (TTFP): {data.get('time_to_first_play')}s!")
            print(f"   Playable Range: 0.0s -> {data.get('playable_until')}s\n")
        elif event_type == "telemetry":
            buf = data.get("buffer_ahead", 0)
            rtf = data.get("realtime_factor", 0)
            print(f"  [Telemetry] Buffer Ahead: +{buf:.1f}s | RTF: {rtf:.1f}x")

    session = create_streaming_session(
        task_id="test_stream",
        video_path=input_video,
        initial_buffer_seconds=3.0,
        voice="Trúc Ly",
        tts_engine_name="vieneu",
        asr_engine_name="sensevoice",
        event_callback=on_event
    )

    t0 = time.time()
    await session.start()

    # Wait for completion or initial play
    while session.is_running:
        await asyncio.sleep(0.5)

    elapsed = round(time.time() - t0, 2)
    print("\n" + "=" * 70)
    print(f"✅ STREAMING SESSION COMPLETED IN {elapsed}s")
    print("=" * 70)
    tel = session.get_telemetry()
    print(f"Total Video Duration: {session.total_duration:.2f}s")
    print(f"Time To First Play (TTFP): {tel.get('time_to_first_play')}s")
    print(f"Realtime Factor (RTF): {tel.get('realtime_factor')}x")
    print(f"Playable Buffer: {tel.get('playable_until')}s")
    print(f"Segments Count: {len(session.segments)}")

    for seg in session.segments.values():
        print(f"\n--- Segment #{seg.id} ---")
        print(f"  Window: {seg.start}s -> {seg.end}s (Dur: {seg.duration}s)")
        print(f"  Chinese ASR: {seg.text_zh} (Emotion: {seg.emotion})")
        print(f"  Vietnamese Dub: {seg.final_vi}")
        print(f"  TTS Duration: {seg.tts_duration}s (Speed Ratio: {seg.speed_ratio}x)")
        print(f"  Audio File: {seg.audio_path}")

if __name__ == "__main__":
    asyncio.run(test_streaming())
