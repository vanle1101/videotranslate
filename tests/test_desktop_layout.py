import json

import pytest
from PySide6.QtCore import QObject, QSettings

from desktop_app import DesktopBridge


def make_bridge(path):
    parent = QObject()
    settings = QSettings(str(path), QSettings.Format.IniFormat)
    return parent, DesktopBridge(parent, layout_settings=settings), settings


def test_layout_persists_in_native_settings_and_reset_is_scoped(tmp_path):
    path = tmp_path / "layout.ini"
    parent, bridge, settings = make_bridge(path)
    settings.setValue("other/setting", "preserved")
    assert bridge.getColumnWidths() == "null"
    assert bridge.setColumnWidths("[2, 5, 3]")
    next_parent, next_bridge, next_settings = make_bridge(path)
    assert json.loads(next_bridge.getColumnWidths()) == [.2, .5, .3]
    assert next_bridge.resetColumnWidths()
    assert next_bridge.getColumnWidths() == "null"
    assert next_settings.value("other/setting") == "preserved"


@pytest.mark.parametrize("raw", ["{}", "[1,2]", "[true,1,2]", '["1",2,3]', "[-1,2,3]", "[0,2,3]", "[NaN,1,2]", "[Infinity,1,2]", "[1e308,1e308,1e308]", "[1e-320,1e308,1]", "oops"])
def test_invalid_layout_does_not_overwrite_saved_preference(tmp_path, raw):
    parent, bridge, settings = make_bridge(tmp_path / "layout.ini")
    assert bridge.setColumnWidths("[2,5,3]")
    assert bridge.setColumnWidths(raw) is False
    assert json.loads(bridge.getColumnWidths()) == [.2, .5, .3]
    settings.setValue("editor/column-widths-v1", raw)
    assert bridge.getColumnWidths() == "null"
