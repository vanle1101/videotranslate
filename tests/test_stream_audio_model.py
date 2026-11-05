"""Tiny authoritative tensor faults; these checks do not qualify a QA model."""
from copy import deepcopy

import pytest
import torch
from safetensors.torch import save_file

from workspace.audit.recovery_20261010.stream_audio_model import stream_weights


class TinyModel(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.model=torch.nn.Sequential(torch.nn.Linear(4,4),torch.nn.ReLU(),torch.nn.Linear(4,4))
        self.audio_tower=torch.nn.Linear(4,4)
        self.lm_head=torch.nn.Linear(4,4,bias=False)


def assets(tmp_path, missing=False, wrong_shape=False):
    torch.manual_seed(4871)
    source=TinyModel().eval()
    state={'thinker.'+k:v.detach().clone() for k,v in source.state_dict().items()}
    if missing:state.pop('thinker.model.0.weight')
    if wrong_shape:state['thinker.model.0.weight']=torch.ones(2,2)
    save_file(state,str(tmp_path/'weights.safetensors'))
    return source,{name:'weights.safetensors' for name in state}


def test_streamed_quantization_retains_native_audio_and_full_parameter_coverage(tmp_path):
    original,index=assets(tmp_path)
    with torch.device('meta'):loaded=TinyModel()
    result=stream_weights(loaded,tmp_path,index,reserve_bytes=0)
    assert result=={'authoritative_parameters_loaded':7,'text_linear_layers_quantized':3}
    assert torch.equal(original.audio_tower.weight,loaded.audio_tower.weight)
    assert torch.equal(original.audio_tower.bias,loaded.audio_tower.bias)
    assert not any(p.is_meta for p in loaded.parameters())
    value=torch.randn(3,4)
    torch.testing.assert_close(original.lm_head(original.model(value)),loaded.lm_head(loaded.model(value)),atol=.015,rtol=.03)


@pytest.mark.parametrize('missing,wrong_shape',[(True,False),(False,True)])
def test_missing_or_mismatched_authoritative_weight_cannot_load(tmp_path,missing,wrong_shape):
    _,index=assets(tmp_path,missing,wrong_shape)
    with torch.device('meta'):loaded=TinyModel()
    with pytest.raises(ValueError,match='weight'):
        stream_weights(loaded,tmp_path,index,reserve_bytes=0)


def test_loader_stops_before_violating_ram_reserve(tmp_path,monkeypatch):
    import psutil
    from types import SimpleNamespace
    _,index=assets(tmp_path)
    with torch.device('meta'):loaded=TinyModel()
    monkeypatch.setattr(psutil,'virtual_memory',lambda:SimpleNamespace(available=100))
    with pytest.raises(MemoryError,match='RAM reserve'):
        stream_weights(loaded,tmp_path,index,reserve_bytes=200)
    assert all(parameter.is_meta for parameter in loaded.parameters())
