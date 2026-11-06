import sys
import json
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

p = Path("workspace/projects/douyin_7692745161054506290.json")
data = json.loads(p.read_text(encoding="utf-8"))
segments = {s["id"]: s for s in data.get("segments", [])}
for sid in range(48, 55):
    s = segments[sid]
    print(f"ID {sid:2d}: start={s['start']:6.2f} end={s['end']:6.2f} dub_start={s.get('dub_start')} dub_end={s.get('dub_end')} ZH={repr(s['text_zh'])} VI={repr(s.get('final_vi'))}")
