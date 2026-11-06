import sys
import json
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

p = Path("workspace/projects/douyin_7692745161054506290.json")
data = json.loads(p.read_text(encoding="utf-8"))
segments = {s["id"]: s for s in data.get("segments", [])}

for target_id in [49, 67, 91, 99, 188]:
    print("=" * 60)
    for sid in range(max(0, target_id - 1), min(len(segments), target_id + 2)):
        s = segments[sid]
        print(f"ID {sid:3d} [{s['start']:6.2f} - {s['end']:6.2f} (dur={s['duration']:4.2f}s)] dub=[{s.get('dub_start')} - {s.get('dub_end')}] status={s['status']} ZH='{s['text_zh']}' VI='{s.get('final_vi')}'")
