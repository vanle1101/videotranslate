from copy import deepcopy

import pytest

from core.engines.asr.source_repair_evidence import prove_joint_source


REQUEST={'id':48,'start':63.76,'end':65.18}
CANDIDATES=[{'id':47,'start':62.76,'end':63.78,'text_zh':'二零二五'},
            {'id':48,'start':63.78,'end':65.18,'text_zh':'二十五年以后'}]


def record():
    pieces=[('二',62.76,63.28),('零',63.28,63.46),('二',63.46,63.56),('五',63.56,63.78),
            ('二',63.78,64.3),('十',64.3,64.62),('五',64.62,64.76),('年',64.76,64.9),
            ('以',64.9,64.98),('后',64.98,65.2)]
    return {'event':'evidence','engine':'faster-whisper-large-v3','id':48,
            'crop_start':59.26,'crop_end':69.68,'text':'二零二五 二十五年以后',
            'timing':[{'text':t,'start':s,'end':e,'probability':.95} for t,s,e in pieces]}


def test_measured_joint_source_keeps_original_decode_binding_and_unique_tokens():
    raw=record();before=deepcopy(raw)
    proof=prove_joint_source(raw,REQUEST,CANDIDATES,raw['engine'])
    assert [p['text_zh'] for p in proof]==['二零二五','二十五年以后']
    assert sum(len(p['timing']) for p in proof)==len(raw['timing'])
    assert raw==before
    assert prove_joint_source(raw,CANDIDATES[1],CANDIDATES,raw['engine']) is None


@pytest.mark.parametrize('change',['wrong_id','crop','partial','low_probability','zero_duration','crossing','negation','number'])
def test_unproven_joint_source_cannot_retire_or_hide_disputed_words(change):
    raw=record();candidates=deepcopy(CANDIDATES)
    if change=='wrong_id':raw['id']=49
    elif change=='crop':raw['crop_end']=70.
    elif change=='partial':raw['timing'].pop()
    elif change=='low_probability':raw['timing'][4]['probability']=.2
    elif change=='zero_duration':raw['timing'][4]['end']=raw['timing'][4]['start']
    elif change=='crossing':candidates[0]['end']=candidates[1]['start']=64.
    elif change=='negation':raw['timing'][6]['text']='不';raw['text']='二零二五二十不年以后'
    elif change=='number':candidates[1]['text_zh']='二十六年以后'
    assert prove_joint_source(raw,REQUEST,candidates,raw['engine']) is None


def test_sensevoice_exact_points_are_owned_once_without_changing_numeral_value():
    raw={'event':'evidence','engine':'sensevoice','id':48,'crop_start':59.26,'crop_end':69.68,
         'text':'2025 25年以后','timing':[
             {'text':t,'at':s} for t,s in zip('202525年以后',[63.28,63.4,63.58,63.7,64.48,64.72,64.9,65.02,65.14])]}
    proof=prove_joint_source(raw,REQUEST,CANDIDATES,'sensevoice')
    assert [p['text_zh'] for p in proof]==['2025','25年以后']
    candidates=deepcopy(CANDIDATES);candidates[0]['end']=63.6;candidates[1]['start']=63.8
    assert prove_joint_source(raw,REQUEST,candidates,'sensevoice') is None
