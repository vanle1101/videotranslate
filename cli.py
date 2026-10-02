import argparse
import asyncio
from pathlib import Path
from core.pipeline import VideoTranslationPipeline
from config import settings

def main():
    parser = argparse.ArgumentParser(description="Douyin2TikTok CLI Studio")
    parser.add_argument("-i", "--input", help="URL or path to video file")
    parser.add_argument("-f", "--folder", help="Folder containing multiple videos to process in batch")
    parser.add_argument("-v", "--voice", default=settings.EDGE_VOICE, help="TTS voice (default: vi-VN-HoaiMyNeural)")
    parser.add_argument("--no-mask", action="store_true", help="Disable Chinese subtitle frosted mask")

    args = parser.parse_args()

    if not args.input and not args.folder:
        parser.print_help()
        return

    pipeline = VideoTranslationPipeline()

    if args.folder:
        folder_path = Path(args.folder)
        videos = [p for p in folder_path.glob("*") if p.suffix.lower() in [".mp4", ".mov", ".mkv", ".webm"]]
        print(f"[*] Found {len(videos)} videos in {folder_path}...")
        for vid in videos:
            print(f"\n==========================================")
            print(f"Processing: {vid.name}")
            print(f"==========================================")
            asyncio.run(pipeline.run(
                video_input=str(vid),
                voice=args.voice,
                mask_chinese=not args.no_mask
            ))
    elif args.input:
        print(f"[*] Processing single input: {args.input}")
        asyncio.run(pipeline.run(
            video_input=args.input,
            voice=args.voice,
            mask_chinese=not args.no_mask
        ))

if __name__ == "__main__":
    main()
