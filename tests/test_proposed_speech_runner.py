"""Fault injection for finite, source-bound editor proposal admission."""
from unittest.mock import Mock

import pytest

from core.engines.translation.semantic_translator import PacingReviewRejected
from workspace.audit.recovery_20261010.run_proposed_speech import ProposedTranslator, runner


def setup(monkeypatch, side_effect=None):
    monkeypatch.setattr(runner, 'event', Mock())
    base = Mock()
    base.rewrite_for_pacing.return_value = {
        'final_vi': 'Phía sau có chìa dự phòng.',
        'pacing_verification': {'status': 'verified'},
    }
    base.rewrite_for_pacing.side_effect = side_effect
    proxy = ProposedTranslator(base, [{'id': 93, 'text_zh': '后面放着备用钥匙',
        'candidates': ['Phía sau có chìa dự phòng.']}])
    return proxy, base


def context(sid=93):
    return [{'id': sid, 'start': 1., 'end': 2., 'is_focus': True}]


def test_exact_source_and_row_can_admit_each_draft_only_once(monkeypatch):
    proxy, base = setup(monkeypatch)
    proxy.rewrite_for_pacing('后面放着备用钥匙', 'Lời cũ.', 1.5, context(), measured_duration=1.6)
    assert base.rewrite_for_pacing.call_args.kwargs == {
        'proposed_vi': 'Phía sau có chìa dự phòng.', 'measured_duration': 1.6}
    with pytest.raises(PacingReviewRejected):
        proxy.rewrite_for_pacing('后面放着备用钥匙', 'Lời cũ.', 1.5, context())
    assert base.rewrite_for_pacing.call_count == 1


@pytest.mark.parametrize('source,sid', [('changed-source', 93), ('后面放着备用钥匙', 94)])
def test_changed_source_or_neighbor_cannot_consume_or_receive_draft(monkeypatch, source, sid):
    proxy, base = setup(monkeypatch)
    with pytest.raises(PacingReviewRejected):
        proxy.rewrite_for_pacing(source, 'Lời cũ.', 1.5, context(sid))
    assert base.rewrite_for_pacing.call_count == 0
    assert len(proxy.proposals[93]['remaining']) == 1


def test_rejection_never_repeats_provider_requests_for_the_same_draft(monkeypatch):
    proxy, base = setup(monkeypatch, PacingReviewRejected('Meaning rejected', code='semantic_mismatch'))
    for _ in range(3):
        with pytest.raises(PacingReviewRejected):
            proxy.rewrite_for_pacing('后面放着备用钥匙', 'Lời cũ.', 1.5, context())
    assert base.rewrite_for_pacing.call_count == 1


def test_transport_failure_surfaces_without_retrying_draft(monkeypatch):
    proxy, base = setup(monkeypatch, RuntimeError('Provider quota unavailable'))
    with pytest.raises(RuntimeError, match='quota'):
        proxy.rewrite_for_pacing('后面放着备用钥匙', 'Lời cũ.', 1.5, context())
    assert base.rewrite_for_pacing.call_count == 1
