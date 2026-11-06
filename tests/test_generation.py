import random
from dataclasses import replace
from itertools import pairwise

import pytest

from brewery_schedule_digital_twin.domain import BottlingLine, Brewery, Brewhouse, Recipe, Schedule, Vessel
from brewery_schedule_digital_twin.generation import ScheduleGenerator, occupancy
from brewery_schedule_digital_twin.scheduling import HeuristicScheduler

RECIPES = [Recipe("Short", 8), Recipe("Medium", 12), Recipe("Long", 15)]


def _brewery(*capacities: float) -> Brewery:
    return Brewery(
        name="Plant",
        vessels=[Vessel.unitank("f", i, c) for i, c in enumerate(capacities or (1_000.0,) * 4, start=1)],
        brewhouses=[Brewhouse("brewhouse", kettle_yield=1_000, max_brews_per_day=1)],
        bottling_lines=[BottlingLine("line", run_volume=1_000, max_runs_per_day=1)],
    )


def _fill(fill_ratio: float, seed: int = 1, horizon_days: int = 60, brewery: Brewery | None = None) -> Schedule:
    return ScheduleGenerator().fill(brewery or _brewery(), RECIPES, fill_ratio, horizon_days, random.Random(seed))


@pytest.mark.parametrize("seed", range(10))
def test_a_full_brewery_has_its_tanks_busy_back_to_back(seed: int) -> None:
    brewery = _brewery()
    schedule = _fill(1.0, seed, brewery=brewery)

    assert schedule.validate(brewery) == []
    assert occupancy(brewery, schedule, 60) == 1.0
    for vessel in brewery.vessels:
        on_tank = sorted(schedule.assignments_on_fermenter(vessel.id), key=lambda a: a.start_day)
        assert on_tank[0].start_day <= 0 < on_tank[0].end_day
        assert on_tank[-1].end_day >= 60
        assert all(later.start_day == earlier.end_day for earlier, later in pairwise(on_tank))


@pytest.mark.parametrize("fill_ratio", [0.3, 0.5, 0.7, 0.9])
@pytest.mark.parametrize("seed", range(5))
def test_fills_to_the_target_or_just_above_it(fill_ratio: float, seed: int) -> None:
    brewery = _brewery()
    schedule = _fill(fill_ratio, seed, brewery=brewery)

    assert schedule.validate(brewery) == []
    # Above the target by less than the longest batch, 15 of 4 x 60 tank-days.
    assert fill_ratio <= occupancy(brewery, schedule, 60) < fill_ratio + 15 / (4 * 60)


def test_the_same_seed_gives_the_same_schedule() -> None:
    assert _fill(0.7, seed=3) == _fill(0.7, seed=3)


def test_batches_are_numbered_by_brew_day() -> None:
    schedule = _fill(0.8)

    brew_days = [schedule.brew_day_of(batch_id) for batch_id in sorted(schedule.batches)]
    assert brew_days == sorted(brew_days)


@pytest.mark.parametrize("capacities", [(20.0, 10.0, 5.0, 5.0), (5.0, 5.0, 5.0, 5.0), (20.0, 20.0, 10.0, 5.0, 2.5)])
@pytest.mark.parametrize("fill_ratio", [0.7, 1.0])
@pytest.mark.parametrize("seed", range(5))
def test_tanks_of_different_sizes_are_filled_to_capacity(
    capacities: tuple[float, ...], fill_ratio: float, seed: int
) -> None:
    brewery = _brewery(*capacities)
    schedule = _fill(fill_ratio, seed, brewery=brewery)

    assert schedule.validate(brewery) == []
    assert all(a.volume == a.fermenter.capacity for a in schedule.assignments)


def test_a_tank_larger_than_the_kettle_yield_is_filled_from_several_brews() -> None:
    brewery = _brewery(2_000.0, 1_000.0)
    schedule = _fill(1.0, brewery=brewery)

    assert schedule.validate(brewery) == []
    large = schedule.assignments_on_fermenter("f1")
    assert large
    for assignment in large:
        assert [fill.volume for fill in assignment.fills] == [1_000.0, 1_000.0]
        assert len({fill.brew for fill in assignment.fills}) == 2
        # One run of 1000 l a day bottles the 2000 l over two days.
        assert len(assignment.bottling_runs) == 2


def test_tanks_smaller_than_the_kettle_yield_free_on_the_same_day_share_a_brew() -> None:
    brewery = _brewery(500.0, 500.0)
    shared = [
        assignments
        for seed in range(10)
        for schedule in [_fill(1.0, seed, brewery=brewery)]
        for assignments in map(schedule.assignments_of, schedule.batches)
        if len(assignments) > 1
    ]

    assert shared
    for assignments in shared:
        assert len({fill.brew for a in assignments for fill in a.fills}) == 1
        assert sum(a.volume for a in assignments) == 1_000.0


def test_occupancy_counts_vessel_days_within_the_horizon() -> None:
    brewery = _brewery()
    schedule = _fill(1.0)

    occupied = sum(min(a.end_day, 30) - max(a.start_day, 0) for a in schedule.assignments if a.start_day < 30)
    assert occupancy(brewery, schedule, 30) == occupied / (4 * 30)


@pytest.mark.parametrize(
    ("fill_ratio", "horizon_days", "recipes"),
    [(0.0, 60, RECIPES), (1.1, 60, RECIPES), (0.5, 0, RECIPES), (0.5, 60, [])],
)
def test_rejects_invalid_settings(fill_ratio: float, horizon_days: int, recipes: list[Recipe]) -> None:
    with pytest.raises(ValueError):
        ScheduleGenerator().fill(_brewery(), recipes, fill_ratio, horizon_days, random.Random(0))


@pytest.mark.parametrize("capacities", [(10.0,) * 4, (20.0, 10.0, 5.0, 5.0), (20.0, 20.0, 10.0)])
@pytest.mark.parametrize("seed", range(5))
def test_replanning_a_generated_schedule_without_a_signal_changes_nothing(
    capacities: tuple[float, ...], seed: int
) -> None:
    brewery = Brewery(
        name="Plant",
        vessels=[Vessel.unitank("f", i, c) for i, c in enumerate(capacities, start=1)],
        brewhouses=[Brewhouse("brewhouse", kettle_yield=600, max_brews_per_day=2)],
        bottling_lines=[BottlingLine.by_daily_volume("line", volume_per_day=2_000)],
    )
    schedule = _fill(0.9, seed, brewery=brewery)

    replanned = HeuristicScheduler().replan(brewery, schedule.clone(), current_day=5)

    assert placements(replanned) == placements(schedule)


def _two_brewhouses_and_three_lines() -> Brewery:
    return Brewery(
        name="Plant",
        vessels=[Vessel.unitank("f", i, c) for i, c in enumerate([2_000.0] * 6 + [1_000.0] * 4, start=1)],
        brewhouses=[Brewhouse(f"brewhouse{i}", kettle_yield=1_000, max_brews_per_day=1) for i in (1, 2)],
        bottling_lines=[BottlingLine.by_daily_volume(name, volume_per_day=1_000) for name in ("a", "b", "c")],
    )


@pytest.mark.parametrize("seed", range(5))
def test_a_brewery_of_several_brewhouses_and_lines_brews_and_bottles_in_all_of_them(seed: int) -> None:
    brewery = _two_brewhouses_and_three_lines()
    schedule = _fill(1.0, seed, brewery=brewery)

    assert schedule.validate(brewery) == []
    brews = {fill.brew for a in schedule.assignments for fill in a.effective_fills}
    assert {brew.brewhouse for brew in brews} == {"brewhouse1", "brewhouse2"}
    assert {a.bottling_line for a in schedule.assignments} == {"a", "b", "c"}


@pytest.mark.parametrize("seed", range(10))
@pytest.mark.parametrize("fill_ratio", [1.0, 0.95, 0.9])
def test_removing_batches_never_bottles_a_tank_later_on_several_busy_lines(seed: int, fill_ratio: float) -> None:
    brewery = replace(
        _two_brewhouses_and_three_lines(),
        brewhouses=[Brewhouse(f"brewhouse{i}", kettle_yield=1_000, max_brews_per_day=2) for i in (1, 2)],
        bottling_lines=[
            BottlingLine.by_daily_volume(name, volume_per_day=volume)
            for name, volume in (("a", 500), ("b", 300), ("c", 200))
        ],
    )

    assert _fill(fill_ratio, seed, brewery=brewery).validate(brewery) == []


@pytest.mark.parametrize("seed", range(5))
def test_replanning_a_schedule_of_several_brewhouses_and_lines_without_a_signal_changes_nothing(seed: int) -> None:
    brewery = _two_brewhouses_and_three_lines()
    schedule = _fill(0.9, seed, brewery=brewery)

    replanned = HeuristicScheduler().replan(brewery, schedule.clone(), current_day=5)

    assert placements(replanned) == placements(schedule)


def placements(schedule: Schedule) -> set[tuple]:
    return {
        (a.batch_id, a.fermenter.id, a.start_day, a.effective_bottling_runs, a.end_day) for a in schedule.assignments
    }
