import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import studio_layout


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(studio_layout.settings, "WORKSPACE_DIR", tmp_path)
    app = FastAPI()
    app.include_router(studio_layout.router)
    with TestClient(app) as connection:
        yield connection


def test_layout_survives_new_app_instance_and_reset(client, tmp_path):
    assert client.get("/api/studio/layout").json() == {"version": 1, "groups": {}}
    layout = {"version": 1, "groups": {
        "columns": [25, 43, 32], "left": [42, 58],
        "middle": [60, 25, 15], "right": [55, 45],
    }}
    assert client.put("/api/studio/layout", json=layout).json() == layout
    another_app = FastAPI()
    another_app.include_router(studio_layout.router)
    with TestClient(another_app) as another_client:
        assert another_client.get("/api/studio/layout").json() == layout
        assert another_client.put("/api/studio/layout", json={"version": 1, "groups": {}}).status_code == 200
    assert json.loads((tmp_path / "studio_layout.json").read_text()) == {"version": 1, "groups": {}}


@pytest.mark.parametrize("contents", ["{broken", '{"version":2,"groups":{}}',
    '{"groups":{"columns":[1,2]}}', '{"groups":{"left":[NaN,2]}}'])
def test_corrupt_layout_falls_back(client, tmp_path, contents):
    (tmp_path / "studio_layout.json").write_text(contents)
    assert client.get("/api/studio/layout").json() == {"version": 1, "groups": {}}


@pytest.mark.parametrize("payload", [
    {"version": 2, "groups": {}}, {"version": True, "groups": {}},
    {"groups": {"unknown": [1, 2]}}, {"groups": {"left": [1]}},
    {"groups": {"columns": [1, 2]}}, {"groups": {"left": [0, 2]}},
    {"groups": {"left": [-1, 2]}}, {"groups": {"left": [True, 2]}},
    {"groups": {"left": ["1", 2]}}, {"groups": []}, {"extra": 1},
    {"groups": {"left": [10 ** 400, 2]}},
    {"groups": {"middle": [60, 40]}},
    {"groups": {"right": [55, 27, 18]}},
])
def test_invalid_layout_rejected_without_replacing_saved(client, tmp_path, payload):
    layout = {"version": 1, "groups": {"left": [1, 3]}}
    assert client.put("/api/studio/layout", json=layout).status_code == 200
    assert client.put("/api/studio/layout", json=payload).status_code == 422
    assert client.get("/api/studio/layout").json() == layout


@pytest.mark.parametrize("ratio", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_sizes_rejected(ratio):
    with pytest.raises(ValueError):
        studio_layout.StudioLayout(groups={"left": [ratio, 1]})


def test_legacy_rows_fall_back_without_losing_other_saved_dimensions(client, tmp_path):
    saved = {"version": 1, "groups": {
        "columns": [30, 40, 30], "left": [35, 65],
        "middle": [60, 40], "right": [55, 27, 18],
    }}
    (tmp_path / "studio_layout.json").write_text(json.dumps(saved))
    expected = {"version": 1, "groups": {
        "columns": [30, 40, 30], "left": [35, 65],
    }}
    assert client.get("/api/studio/layout").json() == expected
    assert json.loads((tmp_path / "studio_layout.json").read_text()) == saved


def test_mixed_legacy_and_current_rows_preserve_current_dimensions(client, tmp_path):
    saved = {"version": 1, "groups": {
        "middle": [60, 40], "right": [30, 70],
    }}
    (tmp_path / "studio_layout.json").write_text(json.dumps(saved))
    assert client.get("/api/studio/layout").json() == {
        "version": 1, "groups": {"right": [30, 70]},
    }


def test_atomic_replace_failure_preserves_previous_and_cleans_temp(client, tmp_path, monkeypatch):
    initial = {"version": 1, "groups": {"left": [1, 3]}}
    client.put("/api/studio/layout", json=initial)

    def fail_replace(*args):
        raise PermissionError("locked")

    monkeypatch.setattr(studio_layout.os, "replace", fail_replace)
    response = client.put("/api/studio/layout", json={"groups": {"left": [2, 2]}})
    assert response.status_code == 500
    assert client.get("/api/studio/layout").json() == initial
    assert list(tmp_path.iterdir()) == [tmp_path / "studio_layout.json"]
