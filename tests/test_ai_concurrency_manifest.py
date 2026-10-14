"""Per-project AI capacity survives both manifest encodings and legacy reads."""
import copy
import pytest
from config import settings
from core.streaming import session_store as store


def project(capacity=20):
    return {"version":1,"task_id":"capacity-test","updated_at":1.0,"state":"STOPPED",
            "session":{"voice":"vi-VN-HoaiMyNeural","tts_engine_name":"edge-tts",
                       "asr_engine_name":"faster-whisper","total_duration":0,"ai_concurrency":capacity},
            "segments":[]}


@pytest.mark.parametrize('capacity',[1,3,5,10,15,20,30,40,50])
@pytest.mark.parametrize('compressed',[False,True])
def test_capacity_roundtrips_through_manifest_schema(capacity,compressed,monkeypatch):
    monkeypatch.setattr(store,'COMPRESS_AFTER_BYTES',1 if compressed else 1024*1024)
    payload=project(capacity)
    restored=store._decode_project(store._encode_project(payload))
    store._validate(restored,'capacity-test')
    assert restored['session']['ai_concurrency']==capacity


@pytest.mark.parametrize('invalid',[True,False,0,51,-1,20.0,'20',None])
def test_capacity_rejects_invalid_manifest_values(invalid):
    with pytest.raises(ValueError,match='AI'):
        store._validate(project(invalid),'capacity-test')


def test_legacy_manifest_validation_does_not_rewrite_or_add_defaults():
    payload=project()
    del payload['session']['ai_concurrency']
    original=copy.deepcopy(payload)
    store._validate(payload,'capacity-test')
    assert 'ai_concurrency' not in payload['session']
    assert original['segments']==payload['segments']


def test_restore_passes_current_default_for_legacy_but_preserves_saved_choice(monkeypatch):
    from core.streaming import pipeline
    class ReachedConstructor(Exception): pass
    observed=[]
    def constructor(*args,**kwargs):
        observed.append(kwargs['ai_concurrency'])
        raise ReachedConstructor
    monkeypatch.setattr(pipeline,'StreamingPipelineSession',constructor)
    monkeypatch.setattr(pipeline,'active_streaming_sessions',{})
    monkeypatch.setattr(store,'_availability',lambda data:{})
    monkeypatch.setattr(settings,'TRANSLATION_CHUNK_WORKERS',40)
    for capacity in [None,20,50]:
        payload=project(capacity)
        if capacity is None: del payload['session']['ai_concurrency']
        monkeypatch.setattr(store,'_read',lambda task_id:payload)
        with pytest.raises(ReachedConstructor): store._restore_saved_session_owned('capacity-test')
    assert observed==[40,20,50]
