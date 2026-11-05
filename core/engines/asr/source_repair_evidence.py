"""Joint ownership evidence for repairing legacy source sentence boundaries.

This does not translate, identify speakers, confirm silence or publish edits.
Every new phrase must be supported by both complete native window decodes.
"""
from core.chinese_text import comparable_chinese, comparable_audio_chinese
from core.engines.asr.contextual_evidence import validate_reading, number, WORD_TOLERANCE
import re


def _source_key(text):
    value = comparable_audio_chinese(text)
    # A whole four-digit reading such as 二零二五 and 2025 has the same
    # exact digits. Restrict this to the entire phrase; do not combine adjacent
    # year/count utterances or normalize disputed units/decimal punctuation.
    if re.fullmatch(r'[零〇一二三四五六七八九]{4}', value):
        return ''.join(str({'零':0,'〇':0,'一':1,'二':2,'三':3,'四':4,'五':5,
                           '六':6,'七':7,'八':8,'九':9}[char]) for char in value)
    return value


def prove_joint_source(record, requested_row, candidates, engine):
    """Assign each native lexical token once within the proposed repair span.

    The original request binding is validated unchanged. Candidate boundaries
    may differ from the legacy ASR rows; no native record is relabelled to
    pretend that it was decoded from a different crop or requested ID.
    """
    if not validate_reading(record, requested_row, engine) or not candidates:
        return None
    ids = set()
    previous = -1.
    for row in candidates:
        if (type(row.get('id')) is not int or row['id'] in ids
                or not all(number(row.get(k)) for k in ('start', 'end'))
                or not record['crop_start'] <= row['start'] < row['end'] <= record['crop_end']
                or row['start'] < previous or not comparable_chinese(row.get('text_zh', ''))):
            return None
        previous = row['end'];ids.add(row['id'])
    left, right = candidates[0]['start'], candidates[-1]['end']
    assigned = {row['id']: [] for row in candidates}
    for token in record['timing']:
        if not comparable_chinese(token['text']):
            continue
        if engine == 'sensevoice':
            at = token['at']
            if not left <= at < right:
                continue
            owners = [row['id'] for row in candidates if row['start'] <= at < row['end']]
        else:
            start, end = token['start'], token['end']
            if end <= left or start >= right:
                continue
            if end <= start or token['probability'] < .7:
                return None
            owners = [row['id'] for row in candidates
                if start >= row['start'] - WORD_TOLERANCE - 1e-7
                and end <= row['end'] + WORD_TOLERANCE + 1e-7]
        # No vote, no disputed token dropping, and no duplicate ownership.
        if len(owners) != 1:
            return None
        assigned[owners[0]].append(dict(token))
    proof = []
    for row in candidates:
        observed = ''.join(token['text'] for token in assigned[row['id']])
        if not observed or _source_key(observed) != _source_key(row['text_zh']):
            return None
        proof.append({'id':row['id'],'start':row['start'],'end':row['end'],
            'text_zh':observed,'engine':engine,'timing':assigned[row['id']]})
    return proof
