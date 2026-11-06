"""Apply explicit, auditable OCR-supported code-switch corrections."""
from pathlib import Path
import json
import sys
import re
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.recover_korean_source import atomic_json

# Chinese spoken turns corroborated by audio phonetics and visible subtitles.
# These are not translations of Korean subtitles into fabricated Korean speech.
CORRECTIONS=[(13,16.12,'左右，抵拢倒拐'),(16.12,17.68,'第一次出国'),(17.68,19.5,'为人做事低调点'),
             (58.62,59.82,'还怪善良的嘞'),(60.74,61.28,'哦，好'),(61.28,63,'还帮同学占座位'),
             (71.48,73.02,'那等他来了我再让嘛'),
             (260.5,264.7,'哇，果然是有钱人家的傻娃儿，遭人欺负了都不晓得还手。'),
             (304.96,306.5,'着啥急，说快了我听不懂。'),(307.12,308.36,'莫怕。'),
             (139,140.14,'不客气哈。'),(339.3,341.32,'看不出来吗？'),
             (117.9,119.88,'没什么事我就先挂了哈，奶奶。'),(154.06,155.2,'没钱的时候除外。'),
             (123.44,124.32,'喂，我说……')]


def main():
    d=ROOT/'workspace/audit/korean_recovery_20261014'
    result=json.loads((d/'source.json').read_text(encoding='utf8'))
    rows=result['segments']
    excluded=[]
    kept=[]
    for row in rows:
        if (row['end']-row['start']<.15 or 48<row['start']<51
                or '정답' in row['text'] or 190<row['start']<194.95 or 198.8<row['start']<199
                or 88.6<row['start']<101):
            excluded.append({**row,'exclusion_reason':'Invalid short ASR timing or uncorroborated repeated hallucination; kept for audit, not declared silence.'})
        else:
            kept.append(row)
    rows=kept
    for row in rows:
        if row['start']==135.04:
            row['raw_asr_before_correction']=[{'text':row['text'],'source_method':row['source_method']}]
            row.update(text='감사합니다',start=135.36,end=136.4,
                       source_method='medium-korean-asr-with-original-chinese-subtitle',
                       status='hypothesis_needs_semantic_review',words=[])
    if result['recognition_coverage']['short_native_decode_seconds']>=101:
        # Two conversations overlap here. Forced zh/ko ASR alternately invents
        # phonetics and repeated insults. Retain visible subtitle evidence as
        # Chinese TEXT source, explicitly not native-audio verification.
        for start,end,text in [(86.66,88,'你别在这睡。'),(88.33,89.67,'你怎么哭着脸？'),
              (89.33,91.33,'记得好好学习，到了新地方。'),(90,91.67,'谁叫你还搞不清楚，'),
              (91.67,92.33,'千万莫跟人动手。'),(92,93.33,'我跟你的差别。'),
              (92.33,93.33,'晓得。'),(93.67,94.33,'你真的……'),(95,95.67,'我保证先不主动。'),
              (97,97.67,'我儿豁嘛。'),(98.33,99,'地震了。'),(99.67,100.67,'真是闯鬼了。')]:
            rows.append({'start':start,'end':end,'text':text,'source_language':'zh',
                         'audio_language':'mixed-uncertain','source_method':'visible-chinese-subtitle-transcription',
                         'status':'ocr_supported_source_draft','words':[],
                         'issues':['simultaneous_dialogue','subtitle_timing_not_independent_audio_timing'],
                         'ocr_evidence':[{'start':start,'end':end,'text_zh':text}]})
    for start,end,text in CORRECTIONS:
        matching=[r for r in rows if start-.025 <=r['start']<end and r['end']<=end+.025]
        if not matching:
            continue
        original=[{k:r[k] for k in ('start','end','text','source_language')} for r in matching]
        first={**matching[0],'start':start,'end':end,'text':text,'source_language':'zh',
               'source_method':'audio-phonetics-with-original-chinese-ocr',
               'raw_asr_before_correction':original,'status':'ocr_supported_source_draft',
               'words':[], 'issues':['dialect_spelling_requires_review'] if start==13 else []}
        first['ocr_evidence']=[x for r in matching for x in r.get('ocr_evidence',[])]
        rows=[r for r in rows if r not in matching]+[first]
    # A short Chinese remark was rejected by midpoint ownership in the adjacent
    # decode; retain it as an explicit source-supported recovered utterance.
    if result['recognition_coverage']['short_native_decode_seconds']>=56:
        rows.append({'start':53.67,'end':55.33,'text':'这个人','source_language':'zh',
                     'source_method':'original-chinese-ocr-with-audio-alternative',
                     'words':[],'status':'ocr_supported_source_draft','issues':['timing_from_subtitle_evidence'],
                     'ocr_evidence':[{'start':53.666667,'end':55.333333,'text_zh':'这个人'}]})
    if result['recognition_coverage']['short_native_decode_seconds']>=200:
        rows.extend([
            {'start':192.25,'end':194.3,'text':'浪费粮食，可耻。','source_language':'zh',
             'source_method':'original-chinese-ocr-with-audio-alternative','words':[],
             'status':'ocr_supported_source_draft','issues':['timing_from_subtitle_evidence'],
             'ocr_evidence':[{'start':192.333333,'end':193.,'text_zh':'浪费粮食'},
                             {'start':193.666667,'end':194.333333,'text_zh':'可耻'}]},
            {'start':194.95,'end':195.35,'text':'뭐?','source_language':'ko',
             'source_method':'sensevoice-with-korean-asr-and-ocr','words':[],
             'status':'hypothesis_needs_semantic_review','issues':['timing_from_subtitle_evidence']},
            {'start':198.95,'end':199.8,'text':'한도훈','source_language':'ko',
             'source_method':'name-call-supported-by-original-subtitle','words':[],
             'status':'hypothesis_needs_semantic_review','issues':['name_requires_review'],
             'ocr_evidence':[{'start':199.113333,'end':199.78,'text_zh':'韩道勋'}]},
        ])
        rows.append({'start':159.56,'end':160.42,'text':'한도훈!','source_language':'ko',
                     'source_method':'medium-asr-name-supported-by-ocr','words':[],
                     'status':'hypothesis_needs_semantic_review','issues':['name_requires_review'],
                     'raw_asr_before_correction':[{'start':159.56,'end':160.42,'text':'한도원!'}]})
    for alternative in result.get('unowned_overlap_hypotheses',[]):
        if alternative['text']=='야' and 250<alternative['start']<253:
            rows.append({**alternative,'source_method':'small-overlap-boundary-recovered',
                         'status':'hypothesis_needs_semantic_review','issues':['boundary_recovered']})
    if result['recognition_coverage']['short_native_decode_seconds']>=353:
        rows.append({'start':351.56,'end':352.24,'text':'打工啊。','source_language':'zh',
                     'source_method':'sensevoice-native-zh-with-original-subtitle','words':[],
                     'status':'ocr_supported_source_draft','issues':['timing_from_subtitle_evidence'],
                     'ocr_evidence':[{'start':351.566667,'end':352.233333,'text_zh':'打工啊'}]})
    rows.sort(key=lambda r:(r['start'],r['end']))
    deduplicated=[]
    normalize=lambda text:re.sub(r'[^\w]','',text)
    for row in rows:
        previous=deduplicated[-1] if deduplicated else None
        if (previous and previous['start']<=row['start'] and previous['end']>=row['end']
                and normalize(row['text']) in normalize(previous['text'])):
            excluded.append({**row,'exclusion_reason':'Same phrase fully contained in preceding overlapping decode.'})
            continue
        deduplicated.append(row)
    rows=deduplicated
    for index,row in enumerate(rows):
        row['id']=index
        row['source_text']=row['text']
        row['source_uncertain']=row.get('status')!='ocr_supported_source_draft'
    result['segments']=rows
    result['excluded_asr_hypotheses']=excluded
    result['source_prepared_seconds']=result['recognition_coverage']['short_native_decode_seconds']
    atomic_json(d/'curated_source.json',result)
    partial={**result,'segments':[r for r in rows if r['end']<=80],
             'source_prepared_seconds':min(80,result['source_prepared_seconds'])}
    atomic_json(d/'partial_source.json',partial)
    print(f'CURATED {len(rows)}; partial {len(partial["segments"])}',flush=True)


if __name__=='__main__':
    main()
