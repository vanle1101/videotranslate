"""A diagnosed source meaning conflict is separate from ASR spelling agreement.

This record grants no source, speaker or semantic approval. It keeps a known
conflict open until the source wording is actually corrected or the recorded
conflict is explicitly resolved, rather than clearing it on another model flag.
"""
from core.chinese_text import comparable_chinese


def validate_source_ambiguity(value):
    if (not isinstance(value, dict) or value.get('method') != 'homophone-context-conflict'
            or value.get('status') != 'unresolved' or type(value.get('id')) is not int
            or value['id'] < 0):
        raise ValueError('Mâu thuẫn nghĩa nguồn thiếu phương pháp/ID/trạng thái hợp lệ.')
    for key in ('text_zh', 'reason'):
        if (not isinstance(value.get(key), str) or not value[key].strip()
                or len(value[key]) > 1500 or '\x00' in value[key]):
            raise ValueError('Mâu thuẫn nghĩa nguồn thiếu lời nguồn/lý do hợp lệ.')
    evidence = value.get('evidence')
    if not isinstance(evidence, list) or not 1 <= len(evidence) <= 20:
        raise ValueError('Mâu thuẫn nghĩa nguồn thiếu ngữ cảnh được ghi nhận.')
    for item in evidence:
        if (not isinstance(item, dict) or type(item.get('id')) is not int or item['id'] < 0
                or not isinstance(item.get('text_zh'), str) or not item['text_zh'].strip()
                or len(item['text_zh']) > 1500 or '\x00' in item['text_zh']):
            raise ValueError('Trích dẫn mâu thuẫn nghĩa nguồn không hợp lệ.')
    return {key: value[key] for key in ('id', 'method', 'status', 'text_zh', 'reason')} | {
        'evidence': [{'id': item['id'], 'text_zh': item['text_zh']} for item in evidence]}


def source_ambiguity_reason(row):
    value = (row.get('verification') or {}).get('source_ambiguity')
    if value is None:
        return None
    proof = validate_source_ambiguity(value)
    if proof['id'] != row.get('id'):
        raise ValueError('Mâu thuẫn nghĩa nguồn không thuộc đúng câu.')
    # Cosmetic formatting cannot clear the hold. A genuinely corrected source
    # has a different lexical key; normal OCR/audio/source gates still apply.
    if comparable_chinese(proof['text_zh']) == comparable_chinese(row.get('text_zh', '')):
        return proof['reason']
    return None
