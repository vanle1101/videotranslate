"""Reject corrupt durable routing flags before restoring runtime state."""
from copy import deepcopy

import pytest

from test_speaker_confirmation import session
from core.streaming.session_store import _read, _validate


@pytest.mark.parametrize("field,value", [
    ("tts_voice_outdated", "false"),
    ("speaker_review_pending", 1),
    ("voice_id", "edge:unconfigured"),
])
def test_invalid_stored_speaker_runtime_contract_is_rejected(session, field, value):
    payload = deepcopy(_read(session.task_id))
    payload["segments"][0][field] = value
    with pytest.raises(ValueError):
        _validate(payload, session.task_id)


def test_legacy_project_without_optional_speaker_flags_remains_valid(session):
    payload = deepcopy(_read(session.task_id))
    for row in payload["segments"]:
        for field in ("voice_id", "tts_voice_outdated", "speaker_review_pending"):
            row.pop(field, None)
    _validate(payload, session.task_id)
