"""Read-only live provider probe for the source-grounded mother/son exchange."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import settings
from core.engines.translation.opencode_client import OpenCodeZenClient
from core.translation_context import dialogue_context
from core.translation_review import AutomaticTranslationReviewer

sys.stdout.reconfigure(encoding="utf-8")
data = json.loads(Path('reports/context-roles-265796c3-before.json').read_text(encoding='utf-8'))
rows = [{key: row[key] for key in ('id', 'start', 'end', 'text_zh', 'final_vi')}
        for row in data['segments']]
focus = [row for row in rows if row['id'] in (66, 67)]
context = dialogue_context(rows, focus)
client = OpenCodeZenClient(model=settings.OPENCODE_MODEL, timeout=120)
reading = AutomaticTranslationReviewer._address_reading(client, focus, context, lambda: None)
Path('reports/context-roles-live-probe.json').write_text(json.dumps(reading, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(reading, ensure_ascii=False, indent=2))
