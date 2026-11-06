"""Check saved wording and playable speech before/after restarting Studio."""
import hashlib
import io
import json
import sys
from pathlib import Path

import numpy as np
import requests
import soundfile as sf

from apply_context_roles_265796c3 import EDITS

sys.stdout.reconfigure(encoding="utf-8")
base, label = sys.argv[1:3]
out = Path(__file__).parent
response = requests.get(base + '/api/streaming/265796c3', timeout=30)
response.raise_for_status()
data = response.json()
original = json.loads((out / 'context-roles-265796c3-before.json').read_text(encoding='utf-8'))
before = {r['id']: r for r in original['segments']}
assert len(data['segments']) == len(before) == 195
checked = []
for row in data['segments']:
    sid = row['id']
    assert row['text_zh'] == before[sid]['text_zh'], sid
    if sid + 1 not in EDITS:
        assert row['final_vi'] == before[sid]['final_vi'], sid
        continue
    assert row['final_vi'] == EDITS[sid + 1], (sid, row['final_vi'])
    assert row['status'] in ('READY', 'PLAYED'), (sid, row['status'])
    assert not row['timing_issue'] and not row['tts_voice_outdated'], sid
    audio = requests.get(base + row['audio_url'], timeout=30)
    audio.raise_for_status()
    samples, rate = sf.read(io.BytesIO(audio.content))
    assert len(samples) > 0 and np.isfinite(samples).all() and np.max(np.abs(samples)) > .001, sid
    checked.append({'number': sid+1, 'text': row['final_vi'], 'revision': row['revision'],
        'wav_seconds': len(samples)/rate, 'speech_end': row.get('speech_end'),
        'dub_tail_limit': row.get('dub_tail_limit'), 'speed_ratio': row['speed_ratio'],
        'sha256': hashlib.sha256(audio.content).hexdigest()})
assert len(checked) == len(EDITS)
result = {'label': label, 'segments': 195, 'edited': len(checked),
    'unchanged_other_texts': 195-len(checked), 'all_source_unchanged': True,
    'joke_153_159_unchanged': True, 'missing_speech_ids': data['missing_speech_ids'],
    'review_summary': data['review_summary'], 'audio_checks': checked}
if label == 'after-restart':
    earlier = json.loads((out / 'context-roles-verified-before-restart.json').read_text(encoding='utf-8'))
    assert result['audio_checks'] == earlier['audio_checks'], 'Restart changed saved wording/audio'
    result['restart_preserved_all_edits_and_audio'] = True
(out / f'context-roles-verified-{label}.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({k:v for k,v in result.items() if k!='audio_checks'}, ensure_ascii=False, indent=2))
