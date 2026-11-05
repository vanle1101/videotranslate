"""Dub reflow must never borrow timing from an unverified preceding packet."""
from copy import deepcopy
import math

import pytest

from core.engines.alignment.dub_timing import available_reflow_duration, plan_reflow


def row(identity, start, end, audio=None, **extra):
    return {"id": identity, "start": start, "end": end,
            "status": "READY" if audio is not None else "WAITING",
            "audio_duration": audio, **extra}


def test_backward_candidate_crossing_island_floor_uses_forward_capacity():
    rows = [row("actual-stable-id", 64., 65.)]
    before = deepcopy(rows)
    unbounded = plan_reflow(rows, "actual-stable-id", 1.2, total_duration=66.)
    assert unbounded["actual-stable-id"]["dub_start"] < 64
    bounded = plan_reflow(rows, "actual-stable-id", 1.2, total_duration=66., minimum_start=64.)
    assert bounded == {"actual-stable-id": pytest.approx({"dub_start": 64., "dub_end": 65.2})}
    assert rows == before


def test_entire_island_capacity_cannot_include_preceding_unknown_source():
    rows = [row(70, 64., 65.)]
    assert available_reflow_duration(rows, 70, total_duration=65.) == pytest.approx(1.35)
    assert available_reflow_duration(rows, 70, total_duration=65., minimum_start=64.) == 1.
    assert plan_reflow(rows, 70, 1.000001, total_duration=65., minimum_start=64.) is None


def test_predecessor_cannot_cross_floor_when_focus_requires_more_room():
    rows = [row(70, 64., 65., 1.), row(82, 65., 65.5), row(97, 65.5, 66.)]
    before = deepcopy(rows)
    options = dict(total_duration=66.35, minimum_start=64.)
    capacity = available_reflow_duration(rows, 82, **options)
    assert capacity == pytest.approx(.85)
    plan = plan_reflow(rows, 82, capacity, **options)
    assert plan is not None and set(plan) == {82, 97}
    assert plan[82] == pytest.approx({"dub_start": 65., "dub_end": 65.85})
    assert plan[97] == pytest.approx({"dub_start": 65.85, "dub_end": 66.35})
    assert plan_reflow(rows, 82, capacity + .00001, **options) is None
    assert rows == before


def test_backshift_still_allowed_inside_verified_island():
    rows = [row("same-id", 64.2, 65.2)]
    plan = plan_reflow(rows, "same-id", 1.1, total_duration=66., minimum_start=64.)
    assert plan == {"same-id": pytest.approx({"dub_start": 64.1, "dub_end": 65.2})}


def test_unknown_predecessor_reserves_complete_duration_after_island_floor():
    rows = [row(300, 64., 65.), row(700, 65., 65.5)]
    arguments = dict(total_duration=65.7, minimum_start=64.)
    assert available_reflow_duration(rows, 700, **arguments) == pytest.approx(.7)
    plan = plan_reflow(rows, 700, .7, **arguments)
    assert plan == {700: pytest.approx({"dub_start": 65., "dub_end": 65.7})}
    assert plan_reflow(rows, 700, .71, **arguments) is None


@pytest.mark.parametrize("minimum", [-1, True, False, None, "64", math.inf, -math.inf, math.nan, 65., 66.])
def test_invalid_or_empty_source_window_has_no_capacity_or_plan(minimum):
    rows = [row(70, 64., 65.)]
    arguments = dict(total_duration=65., minimum_start=minimum)
    assert available_reflow_duration(rows, 70, **arguments) == 0.
    assert plan_reflow(rows, 70, .5, **arguments) is None


@pytest.mark.parametrize("record", [row(70, 63.999999999, 65.),
    row(70, 64., 65., dub_start=63.999999999, dub_end=65.)])
@pytest.mark.parametrize("fixed", [(), (70,)])
def test_current_source_or_dub_before_floor_rejected_even_when_focus_already_fits(record, fixed):
    arguments = dict(total_duration=66., minimum_start=64., fixed_ids=fixed)
    assert available_reflow_duration([record], 70, **arguments) == 0.
    assert plan_reflow([record], 70, .5, **arguments) is None


def test_fixed_rows_remain_at_exact_timing_and_capacity_inside_island():
    rows = [row(70, 64., 65., .8), row(82, 65., 65.5)]
    arguments = dict(total_duration=65.8, minimum_start=64., fixed_ids=(70,))
    assert available_reflow_duration(rows, 82, **arguments) == pytest.approx(.8)
    plan = plan_reflow(rows, 82, .7, **arguments)
    assert plan == {82: pytest.approx({"dub_start": 65., "dub_end": 65.7})}
    fixed_focus = {**arguments, "fixed_ids": (70, 82)}
    assert available_reflow_duration(rows, 82, **fixed_focus) == .5
    assert plan_reflow(rows, 82, .7, **fixed_focus) is None


def test_tail_evidence_and_original_seconds_survive_floor_aware_plan():
    rows = [row(70, 64., 64.9, dub_tail_limit=65.9), row(82, 66., 67.)]
    before = deepcopy(rows)
    arguments = dict(total_duration=67., minimum_start=64.)
    assert available_reflow_duration(rows, 70, **arguments) == pytest.approx(1.9)
    plan = plan_reflow(rows, 70, 1.7, **arguments)
    assert plan == {70: pytest.approx({"dub_start": 64., "dub_end": 65.7})}
    assert rows == before
    assert plan_reflow(rows, 70, 1.91, **arguments) is None


@pytest.mark.parametrize("minimum", [0., 30., 64., 100.125])
@pytest.mark.parametrize("previous_audio", [None, .7, 1.])
@pytest.mark.parametrize("duration", [.4, .7, .85, 1.])
def test_translation_of_origin_preserves_capacity_ids_and_complete_audio(minimum, previous_audio, duration):
    original = [row("before", 0., 1., previous_audio), row("focus", 1., 1.5),
                row("after", 1.5, 2., .45)]
    shifted = [{**record, "start": record["start"] + minimum, "end": record["end"] + minimum}
               for record in original]
    before = deepcopy(shifted)
    capacity = available_reflow_duration(original, "focus", total_duration=2.35)
    assert available_reflow_duration(shifted, "focus", total_duration=minimum + 2.35,
                                    minimum_start=minimum) == pytest.approx(capacity)
    normal = plan_reflow(original, "focus", duration, total_duration=2.35)
    bounded = plan_reflow(shifted, "focus", duration, total_duration=minimum + 2.35,
                          minimum_start=minimum)
    assert (normal is None) == (bounded is None)
    if normal is not None:
        assert set(normal) == set(bounded)
        for identity, bounds in normal.items():
            assert bounded[identity] == pytest.approx({key: value + minimum for key, value in bounds.items()})
            assert bounded[identity]["dub_start"] >= minimum
        for record in shifted:
            timing = bounded.get(record["id"], {"dub_start": record["start"], "dub_end": record["end"]})
            if record["audio_duration"] is not None:
                assert timing["dub_end"] - timing["dub_start"] + 1e-9 >= record["audio_duration"]
    assert shifted == before
