"""Build reviewable mixed-language source hypotheses and a coverage ledger.

Complete coverage means every source second has been submitted to independent
recognition. It is deliberately distinct from verified transcription quality.
"""
from pathlib import Path
import json
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.recover_korean_source import atomic_json


def overlap(a,b):
    return a['start'] < b['end'] and b['start'] < a['end']


def main():
    d=ROOT/'workspace/audit/korean_recovery_20261014'
    read=lambda p:json.loads(p.read_text(encoding='utf8'))
    windows=read(d/'short_windows/all.json')
    chinese=read(d/'chinese_turns.json') if (d/'chinese_turns.json').exists() else []
    independent=read(d/'independent_evidence.json')
    project=read(ROOT/'workspace/projects/2b5ef22b.json')
    ocr=project['session']['screen_texts']
    long_pass=read(d/'novad/source.json')
    vad_pass=read(d/'source_vad_hypothesis.json') if (d/'source_vad_hypothesis.json').exists() else read(d/'source.json')
    if not (d/'source_vad_hypothesis.json').exists():
        atomic_json(d/'source_vad_hypothesis.json',vad_pass)
    rows=[]
    alternatives=[]
    for window in windows:
        native=next((x for x in chinese if x['start']==window['core_start'] and x['end']==window['core_end']),None)
        hypotheses=native['segments'] if native else window['segments']
        for row in hypotheses:
            midpoint=(row['start']+row['end'])/2
            if not window['core_start'] <= midpoint < window['core_end']:
                alternatives.append({**row,'source_language':window['source_language'],'reason':'neighboring_window_ownership'})
                continue
            rows.append({**row,'source_language':window['source_language'],
                         'source_method':'whisper-medium-native' if native else 'whisper-small-native-short',
                         'ownership_start':window['core_start'],'ownership_end':window['core_end']})
    rows.sort(key=lambda x:(x['start'],x['end']))
    for index,row in enumerate(rows):
        row['id']=index
        row['status']='hypothesis_needs_semantic_review'
        row['independent_audio_evidence']=[x for x in independent if overlap(row,x)]
        row['ocr_evidence']=[{key:value for key,value in x.items() if key in {'start','end','text_zh','kind','confidence'}}
                             for x in ocr if overlap(row,x)]
        row['alternative_audio_hypotheses']=[{key:x[key] for key in ('start','end','text')}
             for x in long_pass['segments']+vad_pass['segments'] if overlap(row,x)]
        row['issues']=[]
        if row['end']-row['start']<.15:
            row['issues'].append('short_asr_timing')
        if index and row['start']<rows[index-1]['end']:
            row['issues'].append('overlapping_phrase_timing')
    unowned=[]
    for alternative in alternatives:
        if not any(overlap(alternative,row) for row in rows):
            unowned.append(alternative)
    scanned_end=max((w['core_end'] for w in windows),default=0)
    duration=float(project['session']['total_duration'])
    result={'version':2,'status':'source_hypotheses_ready' if scanned_end>=duration else 'running',
            'duration':duration,'language':'mixed-ko-zh','source_video':project['session']['video_path'],
            'segments':rows,'overlap_alternatives':alternatives,'unowned_overlap_hypotheses':unowned,
            'recognition_coverage':{'short_native_decode_seconds':scanned_end,
                                  'independent_audio_decode_seconds':max(x['end'] for x in independent),
                                  'all_audio_submitted':scanned_end>=duration,
                                  'speech_content_verified':False},
            'source_language_regions':[{'start':w['core_start'],'end':w['core_end'],'language':w['source_language']} for w in windows],
            'note':'Read-only recovery. Full recognition coverage is NOT certification that every spoken word is correct. Mixed dialogue, dialect, music and overlap require semantic/OCR review. Preserve original project.'}
    atomic_json(d/'source.json',result)
    print(f'SOURCE rows={len(rows)} scanned={scanned_end:.3f}/{duration:.3f} unowned={len(unowned)}',flush=True)


if __name__=='__main__':
    main()
