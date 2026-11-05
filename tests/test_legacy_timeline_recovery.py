"""Repair genuine legacy collisions without omitting unknown source speech."""
from copy import deepcopy

import pytest

from core.engines.alignment.dub_timing import (plan_legacy_overlap_recovery,
    available_reflow_duration, plan_reflow)


def legacy_rows():
    # A measured WAV borrows 254 ms into an unknown source row. A distant
    # valid row currently gets capacity=0 because the whole graph overlaps.
    return [
        {'id': 87, 'start': 127.12, 'end': 128.34, 'dub_start': 127.04375,
         'dub_end': 128.5935, 'status': 'READY', 'audio_duration': 1.54975},
        {'id': 88, 'start': 128.34, 'end': 128.9, 'status': 'READY',
         'final_vi': '', 'confirmed_silence': False},
        {'id': 136, 'start': 190.96, 'end': 191.56, 'dub_start': 190.96,
         'dub_end': 191.80520833333335, 'status': 'FAILED'},
        {'id': 137, 'start': 192.9, 'end': 193.32, 'status': 'WAITING'},
    ]


def apply(rows, plan):
    return [{**row, **plan.get(row['id'], {})} for row in rows]


def test_legacy_reservation_collision_blocks_unrelated_pacing_until_proven_repair():
    rows = legacy_rows()
    original = deepcopy(rows)
    assert available_reflow_duration(rows, 136, total_duration=320.) == 0.
    repair = plan_legacy_overlap_recovery(rows, total_duration=320.)
    assert set(repair) == {88}  # Full existing WAV and source times stay intact.
    repaired = apply(rows, repair)
    assert repaired[1]['dub_end'] - repaired[1]['dub_start'] == pytest.approx(.56)
    assert repaired[1]['confirmed_silence'] is False and repaired[1]['final_vi'] == ''
    assert repaired[0] == original[0]
    assert rows == original
    assert available_reflow_duration(repaired, 136, total_duration=320.) == pytest.approx(1.3)
    assert plan_reflow(repaired, 136, 1.159, total_duration=320.) is not None
    assert plan_legacy_overlap_recovery(repaired, total_duration=320.) == {}


def test_disjoint_collisions_are_all_proven_before_any_plan_is_returned():
    rows = legacy_rows()[:2] + [
        {'id': 118, 'start': 167.85, 'end': 168.79, 'dub_start': 168.15783333333331,
         'dub_end': 169.08791666666664, 'status': 'READY', 'audio_duration': .9300833333333334},
        {'id': 119, 'start': 168.79, 'end': 169.39, 'status': 'READY'},
        {'id': 162, 'start': 218.24, 'end': 218.94, 'dub_start': 218.321875,
         'dub_end': 219.29, 'status': 'READY', 'audio_duration': .968125},
        {'id': 163, 'start': 218.94, 'end': 219.54, 'status': 'READY'},
    ]
    original = deepcopy(rows)
    assert plan_legacy_overlap_recovery(rows, total_duration=320., max_repairs=1) is None
    plan = plan_legacy_overlap_recovery(rows, total_duration=320.)
    assert set(plan) == {88, 119, 163}
    repaired = apply(rows, plan)
    for row in repaired:
        start, end = row.get('dub_start', row['start']), row.get('dub_end', row['end'])
        assert abs(start - row['start']) <= .35 + 1e-9
        assert abs(end - row['end']) <= .35 + 1e-9
        if row.get('audio_duration'):
            assert end - start >= row['audio_duration'] - 1e-9
    assert rows == original


def test_manual_source_reservation_is_never_moved_to_hide_collision():
    rows = legacy_rows()
    assert plan_legacy_overlap_recovery(rows, total_duration=320., immutable_ids=[88]) is None
    assert 'dub_start' not in rows[1]


@pytest.mark.parametrize('mutation', ['overlong_wav', 'bad_endpoint', 'duplicate_id', 'source_overlap', 'tail'])
def test_unproven_or_corrupt_graph_never_returns_partial_repair(mutation):
    rows = legacy_rows()
    if mutation == 'overlong_wav': rows[0]['audio_duration'] = 2.0
    if mutation == 'bad_endpoint': rows[0]['dub_end'] = 129.0
    if mutation == 'duplicate_id': rows[1]['id'] = rows[0]['id']
    if mutation == 'source_overlap': rows[1]['start'] = 128.2
    total = 128.9 if mutation == 'tail' else 320.
    original = deepcopy(rows)
    assert plan_legacy_overlap_recovery(rows, total_duration=total) is None
    assert rows == original
