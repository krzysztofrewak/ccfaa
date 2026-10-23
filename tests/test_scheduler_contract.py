"""Rules every scheduler must keep, on every brewery configuration.

A new scheduler is checked by adding it to ``SCHEDULERS``; a new kind of
configuration by adding a scenario to ``SCENARIOS``.
"""

import dataclasses
import math
from collections.abc import Callable

import pytest

from brewery_schedule_digital_twin.domain import (
    Batch,
    BottlingLine,
    Brewery,
    Brewhouse,
    Recipe,
    Schedule,
    Vessel,
    VesselRole,
)
from brewery_schedule_digital_twin.scheduling import HeuristicScheduler, Scheduler

SCHEDULERS: dict[str, Callable[[], Scheduler]] = {
    "heuristic": HeuristicScheduler,
}

ALE = Recipe(name="Ale", fermentation_days=10)
LAGER = Recipe(name="Lager", fermentation_days=21)
STOUT = Recipe(name="Stout", fermentation_days=14)


def _brewery(
    *capacities: float,
    kettle_yield: float = 5_000.0,
    max_brews_per_day: int = 2,
    max_brew_spread_days: int = 2,
    brewhouses: list[Brewhouse] | None = None,
    bottling_lines: list[BottlingLine] | None = None,
) -> Brewery:
    return Brewery(
        name="Contract Brewery",
        vessels=[Vessel.unitank("f", i, c) for i, c in enumerate(capacities, start=1)],
        brewhouses=brewhouses
        or [Brewhouse("brewhouse", kettle_yield=kettle_yield, max_brews_per_day=max_brews_per_day)],
        bottling_lines=bottling_lines or [BottlingLine("line", run_volume=math.inf, max_runs_per_day=2)],
        days_off={7, 14, 21, 28},
        max_brew_spread_days=max_brew_spread_days,
    )


def _scenario_single_brews_split_between_small_vessels() -> tuple[Brewery, list[Batch]]:
    brewery = _brewery(500.0, 500.0, 500.0, 1_000.0, kettle_yield=1_000.0, max_brews_per_day=1)
    batches = [Batch(f"S{i}", f"single {i}", ALE, planned_brew_day=i) for i in range(4)]
    return brewery, batches


def _scenario_merge_into_a_large_vessel() -> tuple[Brewery, list[Batch]]:
    brewery = _brewery(10_000.0, 5_000.0, kettle_yield=5_000.0)
    batches = [
        Batch("BIG", "big", LAGER, planned_brew_day=0, volume=10_000.0),
        Batch("SMALL", "small", ALE, planned_brew_day=0, volume=5_000.0),
    ]
    return brewery, batches


def _scenario_merge_over_a_day_off() -> tuple[Brewery, list[Batch]]:
    brewery = _brewery(10_000.0, kettle_yield=5_000.0, max_brews_per_day=1)
    return brewery, [Batch("BIG", "big", ALE, planned_brew_day=6, volume=10_000.0)]


def _scenario_split_and_merge() -> tuple[Brewery, list[Batch]]:
    brewery = _brewery(10_000.0, 10_000.0, 3_000.0, kettle_yield=5_000.0, max_brews_per_day=1)
    batches = [
        Batch("BIG", "big", LAGER, planned_brew_day=0, volume=12_000.0),
        Batch("ODD", "odd", ALE, planned_brew_day=1, volume=7_000.0),
    ]
    return brewery, batches


def _scenario_maintenance_and_targets() -> tuple[Brewery, list[Batch]]:
    brewery = _brewery(10_000.0, 5_000.0, 5_000.0, kettle_yield=5_000.0, max_brew_spread_days=0)
    brewery.mark_fermenter_maintenance("f1", start_day=0, duration_days=5)
    batches = [
        Batch("BIG", "big", ALE, planned_brew_day=0, volume=10_000.0, target_bottling_day=20),
        Batch("A", "a", ALE, planned_brew_day=0, target_bottling_day=12),
        Batch("B", "b", LAGER, planned_brew_day=2, volume=2_500.0),
    ]
    return brewery, batches


def _scenario_blend_in_one_vessel() -> tuple[Brewery, list[Batch]]:
    brewery = _brewery(10_000.0, 5_000.0, kettle_yield=5_000.0)
    batches = [
        Batch("BLD", "blend", STOUT, planned_brew_day=0, blend={STOUT: 7_000.0, ALE: 3_000.0}),
        Batch("A", "a", ALE, planned_brew_day=0),
    ]
    return brewery, batches


def _scenario_blend_split_between_vessels() -> tuple[Brewery, list[Batch]]:
    brewery = _brewery(6_000.0, 6_000.0, kettle_yield=5_000.0, max_brews_per_day=1)
    return brewery, [Batch("BLD", "blend", STOUT, planned_brew_day=1, blend={STOUT: 8_000.0, LAGER: 4_000.0})]


def _scenario_brewhouses_of_different_sizes() -> tuple[Brewery, list[Batch]]:
    brewhouses = [Brewhouse("small", kettle_yield=2_000, max_brews_per_day=1), Brewhouse("large", 5_000, 1)]
    brewery = _brewery(10_000.0, 5_000.0, 2_000.0, 2_000.0, brewhouses=brewhouses)
    batches = [
        Batch("BIG", "big", LAGER, planned_brew_day=0, volume=10_000.0),
        Batch("MID", "mid", ALE, planned_brew_day=0, volume=5_000.0),
        Batch("S1", "small 1", ALE, planned_brew_day=0, volume=2_000.0),
        Batch("S2", "small 2", ALE, planned_brew_day=1, volume=2_000.0),
    ]
    return brewery, batches


def _scenario_bottling_bottleneck() -> tuple[Brewery, list[Batch]]:
    brewery = _brewery(10_000.0, 10_000.0, 5_000.0, bottling_lines=[BottlingLine("line", 2_000, max_runs_per_day=3)])
    batches = [
        Batch("A", "a", ALE, planned_brew_day=0, volume=10_000.0),
        Batch("B", "b", ALE, planned_brew_day=0, volume=10_000.0),
        Batch("C", "c", ALE, planned_brew_day=1, volume=5_000.0, target_bottling_day=15),
    ]
    return brewery, batches


def _scenario_two_bottling_lines() -> tuple[Brewery, list[Batch]]:
    lines = [BottlingLine("bottles", 1_000, max_runs_per_day=4), BottlingLine("cans", 3_000, max_runs_per_day=1)]
    brewery = _brewery(6_000.0, 6_000.0, 3_000.0, bottling_lines=lines)
    batches = [
        Batch("A", "a", ALE, planned_brew_day=0, volume=12_000.0),
        Batch("B", "b", STOUT, planned_brew_day=0, volume=3_000.0),
    ]
    return brewery, batches


SCENARIOS: dict[str, Callable[[], tuple[Brewery, list[Batch]]]] = {
    "single brews split between small vessels": _scenario_single_brews_split_between_small_vessels,
    "merge into a large vessel": _scenario_merge_into_a_large_vessel,
    "merge over a day off": _scenario_merge_over_a_day_off,
    "split and merge": _scenario_split_and_merge,
    "maintenance and targets": _scenario_maintenance_and_targets,
    "blend in one vessel": _scenario_blend_in_one_vessel,
    "blend split between vessels": _scenario_blend_split_between_vessels,
    "brewhouses of different sizes": _scenario_brewhouses_of_different_sizes,
    "bottling bottleneck": _scenario_bottling_bottleneck,
    "two bottling lines": _scenario_two_bottling_lines,
}


@pytest.mark.parametrize("scenario", SCENARIOS.values(), ids=SCENARIOS.keys())
@pytest.mark.parametrize("make_scheduler", SCHEDULERS.values(), ids=SCHEDULERS.keys())
def test_scheduler_produces_a_valid_schedule(
    make_scheduler: Callable[[], Scheduler], scenario: Callable[[], tuple[Brewery, list[Batch]]]
) -> None:
    brewery, batches = scenario()
    schedule = Schedule()
    for batch in batches:
        schedule.add_batch(batch)

    result = make_scheduler().replan(brewery, schedule, current_day=0)

    for batch in batches:
        assert result.assignments_of(batch.id), f"batch {batch.id!r} is not scheduled"
        assert result.brew_day_of(batch.id) >= batch.planned_brew_day
        requested = batch.volume if batch.volume is not None else brewery.kettle_yield
        assert math.isclose(sum(a.volume for a in result.assignments_of(batch.id)), requested)
        if batch.target_bottling_day is not None:
            assert max(a.last_bottling_day for a in result.assignments_of(batch.id)) == batch.target_bottling_day
        if batch.blend is not None:
            total = sum(batch.blend.values())
            for a in result.assignments_of(batch.id):
                for recipe, share in batch.blend.items():
                    in_vessel = sum(f.volume for f in a.effective_fills if batch.recipe_of(f.brew) == recipe)
                    assert math.isclose(in_vessel / a.volume, share / total), f"{a.fermenter.id} is off the blend"
    for a in result.assignments:
        assert a.fermenter.has_role(VesselRole.FULL)
        assert a.bottling_day is not None and a.bottling_day >= a.last_fermentation_day
        assert all(brewery.is_working_day(day) for day in a.effective_bottling_runs)
        for window_start, window_end in brewery.fermenter_maintenance_windows(a.fermenter.id):
            assert a.end_day <= window_start or window_end <= a.start_day, f"{a.fermenter.id} is under maintenance"

    declared = Schedule(
        batches={
            batch.id: dataclasses.replace(batch, planned_brew_day=result.brew_day_of(batch.id)) for batch in batches
        },
        assignments=result.assignments,
    )
    assert declared.validate(brewery) == []
