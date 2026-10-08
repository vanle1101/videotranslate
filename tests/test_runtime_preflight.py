"""Launcher readiness rejects real missing imports and incompatible packages."""
import importlib.metadata
from types import SimpleNamespace

import pytest

from scripts import check_runtime


@pytest.fixture
def ready(monkeypatch, tmp_path):
    model = tmp_path / 'models' / 'faster-whisper-small'
    model.mkdir(parents=True)
    for name in ('model.bin', 'config.json', 'tokenizer.json', 'vocabulary.txt'):
        (model / name).write_bytes(b'present')
    monkeypatch.setattr(check_runtime, 'settings', SimpleNamespace(
        LLM_PROVIDER='free', WORKSPACE_DIR=tmp_path, WHISPER_MODEL_SIZE='small',
        DEVICE='cpu', WHISPER_COMPUTE_TYPE='int8', ASR_CPU_THREADS=4, TTS_ENGINE='edge-tts'))
    modules = []
    monkeypatch.setattr(check_runtime.importlib, 'import_module', lambda name: modules.append(name))
    monkeypatch.setattr(check_runtime.shutil, 'which', lambda name: name)
    versions = {'numpy':'1.26.4', 'scipy':'1.15.3', 'av':'18.0.0'}
    monkeypatch.setattr(check_runtime.importlib.metadata, 'version', lambda name: versions[name])
    return model, modules, versions


def test_valid_declared_runtime_passes(ready, capsys):
    assert check_runtime.main() == 0
    _, modules, _ = ready
    assert {'numpy','scipy','av','cv2','rapidocr_onnxruntime'} <= set(modules)
    assert 'ERROR:' not in capsys.readouterr().out


@pytest.mark.parametrize('missing', ['rapidocr_onnxruntime','cv2','scipy','av'])
def test_missing_lazy_dependency_fails_launcher_without_leaking_exception(ready, monkeypatch, capsys, missing):
    def imported(name):
        if name == missing:
            raise ModuleNotFoundError('credential-shaped diagnostic must never be echoed')
    monkeypatch.setattr(check_runtime.importlib, 'import_module', imported)
    assert check_runtime.main() == 1
    output = capsys.readouterr().out
    assert f'ERROR: {missing} could not be imported (ModuleNotFoundError).' in output
    assert 'credential-shaped' not in output


@pytest.mark.parametrize('name,version', [('numpy','2.5.3'),('numpy','2.0.0'),('numpy','1.23.5'),
                                        ('av','19.0.0'),('av','10.0.0'),('scipy','1.10.1')])
def test_version_outside_declared_requirements_fails(ready, capsys, name, version):
    ready[2][name] = version
    assert check_runtime.main() == 1
    assert f'{name} {version} is unsupported' in capsys.readouterr().out


@pytest.mark.parametrize('version', ['2.0.0rc1','not-a-version',''])
def test_uncheckable_numpy_version_is_not_silently_accepted(ready, capsys, version):
    ready[2]['numpy'] = version
    assert check_runtime.main() == 1
    assert 'numpy package version could not be checked' in capsys.readouterr().out


def test_missing_version_metadata_fails_closed(ready, monkeypatch, capsys):
    def version(name):
        if name == 'numpy':
            raise importlib.metadata.PackageNotFoundError(name)
        return ready[2][name]
    monkeypatch.setattr(check_runtime.importlib.metadata, 'version', version)
    assert check_runtime.main() == 1
    assert 'numpy package metadata is missing' in capsys.readouterr().out


@pytest.mark.parametrize('filename', ['model.bin','config.json','tokenizer.json','vocabulary.txt'])
def test_empty_model_assets_do_not_pass_readiness(ready, capsys, filename):
    (ready[0] / filename).write_bytes(b'')
    assert check_runtime.main() == 1
    assert f'Missing Whisper file: {filename}' in capsys.readouterr().out


def test_supported_local_and_postrelease_versions_parse():
    assert check_runtime.release_version('1.26.4+mkl') == (1,26,4)
    assert check_runtime.release_version('1.26.4.post1') == (1,26,4)
    assert check_runtime.release_version('18.0') == (18,0,0)
    assert check_runtime.release_version('18') is None
    assert check_runtime.release_version(None) is None
