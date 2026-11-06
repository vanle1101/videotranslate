import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.voice_catalog import list_voices
from core.voice_preview import voice_preview_manager

def main():
    voices = list_voices()
    print(f"Bắt đầu tải trước mẫu cho tất cả {len(voices)} giọng đọc...")
    t0 = time.time()
    for idx, v in enumerate(voices, 1):
        vid = v['id']
        vname = v['name']
        vengine = v['engine']
        try:
            res = voice_preview_manager.create(vid)
            status = res.get('status')
            cached = res.get('cached')
            print(f"[{idx}/{len(voices)}] {vname} ({vengine}) -> {status} (cached={cached})")
        except Exception as e:
            print(f"[{idx}/{len(voices)}] {vname} ({vengine}) -> LỖI: {e}")
    elapsed = round(time.time() - t0, 1)
    print(f"\nHoàn tất tải trước toàn bộ mẫu giọng sau {elapsed} giây!")

if __name__ == "__main__":
    main()
