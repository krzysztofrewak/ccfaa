import math

import pytest

from brewery_schedule_digital_twin.agents import OperationsAgent
from brewery_schedule_digital_twin.domain import (
    Batch,
    BottlingLine,
    Brew,
    Brewery,
    Brewhouse,
    FermentationAssignment,
    Fill,
    Recipe,
    Schedule,
    Vessel,
)
from brewery_schedule_digital_twin.scheduling import HeuristicScheduler, SchedulingError
from brewery_schedule_digital_twin.signals import DayOffSignal

STRONG_LINE = BottlingLine("line", run_volume_l=math.inf, max_runs_per_day=5)


def _brewery(
    *capacities: float,
    brewhouses: list[Brewhouse] | None = None,
    bottling_lines: list[BottlingLine] | None = None,
) -> Brewery:
    return Brewery(
        name="Plant",
        vessels=[Vessel.unitank("f", i, c) for i, c in enumerate(capacities, start=1)],
        brewhouses=brewhouses or [Brewhouse("brewhouse", yield_l=5_000, max_brews_per_day=2)],
        bottling_lines=bottling_lines or [STRONG_LINE],
    )


def _schedule(recipe: Recipe, *assignments: FermentationAssignment, planned_brew_day: int = 0) -> Schedule:
    schedule = Schedule()
    for batch_id in dict.fromkeys(a.batch_id for a in assignments):
        schedule.add_batch(Batch(batch_id, batch_id, recipe, planned_brew_day=planned_brew_day))
    for assignment in assignments:
        schedule.add_assignment(assignment)
    return schedule


def _brewed(
    batch_id: str, vessel: Vessel, *brews: tuple[int, int, float, str | None], **kwargs: object
) -> FermentationAssignment:
    """An assignment filled from brews given as (number, day, volume_hl, brewhouse)."""
    fills = tuple(Fill(Brew(batch_id, number, day, brewhouse=name), volume) for number, day, volume, name in brews)
    return FermentationAssignment(
        batch_id,
        vessel,
        sum(fill.volume_hl for fill in fills),
        start_day=min(day for _, day, _, _ in brews),
        base_fermentation_days=10,
        fills=fills,
        **kwargs,  # type: ignore[arg-type]
    )


# Equipment


def test_brewhouse_yield_is_given_in_litres() -> None:
    assert Brewhouse("bh", yield_l=1_000, max_brews_per_day=1).yield_hl == 10.0


@pytest.mark.parametrize("yield_l, max_brews", [(0, 1), (1_000, 0)])
def test_brewhouse_rejects_non_positive_settings(yield_l: float, max_brews: int) -> None:
    with pytest.raises(ValueError):
        Brewhouse("bh", yield_l=yield_l, max_brews_per_day=max_brews)


@pytest.mark.parametrize("run_volume_l, max_runs", [(0, 1), (1_000, 0)])
def test_bottling_line_rejects_non_positive_settings(run_volume_l: float, max_runs: int) -> None:
    with pytest.raises(ValueError):
        BottlingLine("line", run_volume_l=run_volume_l, max_runs_per_day=max_runs)


def test_bottling_line_needs_a_run_per_started_run_volume() -> None:
    line = BottlingLine("line", run_volume_l=2_000, max_runs_per_day=3)

    assert [line.runs_for(v) for v in (5.0, 20.0, 20.5, 100.0)] == [1, 1, 2, 5]
    assert STRONG_LINE.runs_for(1_000.0) == 1


def test_brewery_needs_a_brewhouse_and_a_bottling_line() -> None:
    with pytest.raises(ValueError):
        Brewery("Plant", vessels=[], brewhouses=[], bottling_lines=[STRONG_LINE])
    with pytest.raises(ValueError):
        Brewery("Plant", vessels=[], brewhouses=[Brewhouse("bh", 1_000, 1)], bottling_lines=[])


def test_brewery_rejects_duplicate_brewhouse_and_line_names() -> None:
    with pytest.raises(ValueError):
        _brewery(10.0, brewhouses=[Brewhouse("bh", 1_000, 1), Brewhouse("bh", 2_000, 1)])
    with pytest.raises(ValueError):
        _brewery(10.0, bottling_lines=[STRONG_LINE, STRONG_LINE])


def test_brewery_sums_up_its_brewhouses_and_lines() -> None:
    brewery = _brewery(
        10.0,
        brewhouses=[Brewhouse("small", 1_000, 2), Brewhouse("large", 3_000, 1)],
        bottling_lines=[STRONG_LINE, BottlingLine("cans", run_volume_l=500, max_runs_per_day=4)],
    )

    assert (brewery.kettle_yield_hl, brewery.max_brews_per_day, brewery.max_bottling_runs_per_day) == (30.0, 3, 9)


def test_none_names_the_only_brewhouse_and_line() -> None:
    brewery = _brewery(10.0)
    assert brewery.brewhouse(None).name == "brewhouse"
    assert brewery.bottling_line(None) is STRONG_LINE

    several = _brewery(
        10.0,
        brewhouses=[Brewhouse("a", 1_000, 1), Brewhouse("b", 1_000, 1)],
        bottling_lines=[STRONG_LINE, BottlingLine("cans", run_volume_l=500, max_runs_per_day=4)],
    )
    with pytest.raises(KeyError):
        several.brewhouse(None)
    with pytest.raises(KeyError):
        several.bottling_line(None)
    with pytest.raises(KeyError):
        several.brewhouse("c")


# Bottling over several runs


def test_bottling_runs_start_on_the_bottling_day() -> None:
    with pytest.raises(ValueError):
        FermentationAssignment(
            "001", Vessel.unitank("f", 1, 100.0), 100.0, 0, 10, bottling_day=9, bottling_runs=(10, 10)
        )


def test_a_vessel_is_occupied_until_its_last_bottling_run() -> None:
    assignment = FermentationAssignment(
        "001", Vessel.unitank("f", 1, 100.0), 100.0, 0, 10, bottling_day=9, bottling_runs=(9, 9, 9, 10, 10)
    )

    assert assignment.effective_bottling_runs == (9, 9, 9, 10, 10)
    assert assignment.last_bottling_day == 10
    assert assignment.end_day == 11
    assert not assignment.bottling_done(10)
    assert assignment.bottling_done(11)


def test_a_single_run_is_on_the_bottling_day() -> None:
    assignment = FermentationAssignment("001", Vessel.unitank("f", 1, 10.0), 10.0, 0, 10, bottling_day=12)

    assert assignment.effective_bottling_runs == (12,)
    assert assignment.end_day == 13


def test_later_bottling_runs_are_scheduled_work(recipe: Recipe) -> None:
    brewery = _brewery(100.0)
    assignment = FermentationAssignment("001", brewery.vessels[0], 100.0, 0, 10, bottling_day=9, bottling_runs=(9, 10))
    schedule = _schedule(recipe, assignment)

    assert schedule.has_scheduled_work_on(10)
    assessment = OperationsAgent().assess(brewery, schedule, DayOffSignal(day=1, off_day=10), current_day=1)
    assert assessment.decision != "KEEP"


# Validation


def test_validate_checks_each_brew_against_its_brewhouse(recipe: Recipe) -> None:
    brewery = _brewery(100.0, brewhouses=[Brewhouse("small", 2_000, 2), Brewhouse("large", 5_000, 2)])
    schedule = _schedule(recipe, _brewed("001", brewery.vessels[0], (1, 0, 30.0, "small"), (2, 0, 50.0, "large")))

    assert schedule.validate(brewery) == ["brew '001/1' totals 30 hl, more than the yield of brewhouse 'small' (20 hl)"]


def test_validate_counts_brews_per_brewhouse(recipe: Recipe) -> None:
    brewery = _brewery(100.0, brewhouses=[Brewhouse("a", 5_000, 1), Brewhouse("b", 5_000, 1)])
    one_each = _brewed("001", brewery.vessels[0], (1, 0, 50.0, "a"), (2, 0, 50.0, "b"))
    both_in_a = _brewed("001", brewery.vessels[0], (1, 0, 50.0, "a"), (2, 0, 50.0, "a"))

    assert _schedule(recipe, one_each).validate(brewery) == []
    assert _schedule(recipe, both_in_a).validate(brewery) == [
        "brewhouse 'a' has 2 brews on day 0 (max 1): ['001/1', '001/2']"
    ]


def test_validate_needs_a_brewhouse_name_when_there_are_several(recipe: Recipe) -> None:
    brewery = _brewery(100.0, brewhouses=[Brewhouse("a", 5_000, 1), Brewhouse("b", 5_000, 1)])
    schedule = _schedule(recipe, FermentationAssignment("001", brewery.vessels[0], 50.0, 0, 10))

    assert schedule.validate(brewery) == [
        "brew '001/1': brewery 'Plant' has several brewhouses, so a brew must name one"
    ]


def test_validate_rejects_one_brew_from_different_brewhouses(recipe: Recipe) -> None:
    brewery = _brewery(50.0, 50.0, brewhouses=[Brewhouse("a", 5_000, 1), Brewhouse("b", 5_000, 1)])
    schedule = _schedule(
        recipe,
        _brewed("001", brewery.vessels[0], (1, 0, 25.0, "a")),
        _brewed("001", brewery.vessels[1], (1, 0, 25.0, "b")),
    )

    assert "brew '001/1' comes from different brewhouses ['a', 'b']" in schedule.validate(brewery)


def test_validate_needs_enough_bottling_runs_for_the_volume(recipe: Recipe) -> None:
    brewery = _brewery(100.0, bottling_lines=[BottlingLine("line", run_volume_l=2_000, max_runs_per_day=5)])
    schedule = _schedule(
        recipe,
        FermentationAssignment("001", brewery.vessels[0], 50.0, 0, 10, bottling_day=9, bottling_runs=(9, 9)),
    )

    assert schedule.validate(brewery) == ["batch '001' in 'f1' needs 3 bottling runs on line 'line' but has 2"]


def test_validate_counts_bottling_runs_per_line_and_day(recipe: Recipe) -> None:
    lines = [BottlingLine("a", run_volume_l=2_000, max_runs_per_day=2), BottlingLine("b", math.inf, 1)]
    brewery = _brewery(100.0, 10.0, bottling_lines=lines)
    schedule = _schedule(
        recipe,
        FermentationAssignment(
            "001", brewery.vessels[0], 50.0, 0, 10, bottling_day=9, bottling_line="a", bottling_runs=(9, 9, 9)
        ),
        FermentationAssignment("002", brewery.vessels[1], 10.0, 0, 10, bottling_day=9, bottling_line="b"),
    )

    assert schedule.validate(brewery) == ["line 'a' would need 3 bottling runs on day 9 (max 2)"]


# Scheduling


def _plan(brewery: Brewery, *batches: Batch) -> Schedule:
    schedule = Schedule()
    for batch in batches:
        schedule.add_batch(batch)
    result = HeuristicScheduler().replan(brewery, schedule, current_day=0)
    assert result.validate(brewery) == []
    return result


def _brews(schedule: Schedule, batch_id: str) -> list[tuple[str, int, str | None]]:
    """The (id, day, brewhouse) of every brew of a batch."""
    brews = {fill.brew for a in schedule.assignments_of(batch_id) for fill in a.effective_fills}
    return sorted((brew.id, brew.day, brew.brewhouse) for brew in brews)


def test_a_single_brewhouse_brewery_names_no_brewhouse_or_line(recipe: Recipe) -> None:
    result = _plan(_brewery(50.0), Batch("A", "a", recipe, planned_brew_day=0, volume_hl=50.0))

    [assignment] = result.assignments_of("A")
    assert (assignment.fills, assignment.bottling_line, assignment.bottling_runs) == ((), None, ())


def test_brews_in_the_smallest_brewhouse_that_makes_the_brew(recipe: Recipe) -> None:
    brewhouses = [Brewhouse("large", 5_000, 1), Brewhouse("small", 2_000, 1)]
    result = _plan(
        _brewery(50.0, 20.0, brewhouses=brewhouses),
        Batch("S", "s", recipe, planned_brew_day=0, volume_hl=20.0),
        Batch("L", "l", recipe, planned_brew_day=0, volume_hl=50.0),
    )

    assert _brews(result, "S") == [("S/1", 0, "small")]
    assert _brews(result, "L") == [("L/1", 0, "large")]


def test_uses_a_larger_brewhouse_when_the_smaller_one_is_busy(recipe: Recipe) -> None:
    brewhouses = [Brewhouse("large", 5_000, 1), Brewhouse("small", 2_000, 1)]
    result = _plan(
        _brewery(20.0, 20.0, brewhouses=brewhouses),
        Batch("A", "a", recipe, planned_brew_day=0, volume_hl=20.0),
        Batch("B", "b", recipe, planned_brew_day=0, volume_hl=20.0),
    )

    assert _brews(result, "A") + _brews(result, "B") == [("A/1", 0, "small"), ("B/1", 0, "large")]


def test_merges_brews_from_two_brewhouses_on_one_day(recipe: Recipe) -> None:
    brewhouses = [Brewhouse("a", 5_000, 1), Brewhouse("b", 5_000, 1)]
    result = _plan(_brewery(100.0, brewhouses=brewhouses), Batch("BIG", "big", recipe, 0, volume_hl=100.0))

    assert _brews(result, "BIG") == [("BIG/1", 0, "a"), ("BIG/2", 0, "b")]


def test_bottles_a_large_vessel_in_several_runs_as_fast_as_the_line_allows(recipe: Recipe) -> None:
    line = BottlingLine("line", run_volume_l=2_000, max_runs_per_day=3)
    result = _plan(_brewery(100.0, bottling_lines=[line]), Batch("BIG", "big", recipe, 0, volume_hl=100.0))

    [assignment] = result.assignments_of("BIG")
    assert (assignment.bottling_day, assignment.bottling_runs) == (9, (9, 9, 9, 10, 10))
    assert assignment.end_day == 11


def test_bottling_skips_a_day_off(recipe: Recipe) -> None:
    line = BottlingLine("line", run_volume_l=2_000, max_runs_per_day=3)
    brewery = _brewery(100.0, bottling_lines=[line])
    brewery.mark_day_off(10)

    result = _plan(brewery, Batch("BIG", "big", recipe, 0, volume_hl=100.0))

    [assignment] = result.assignments_of("BIG")
    assert assignment.bottling_runs == (9, 9, 9, 11, 11)


def test_bottles_on_the_line_that_finishes_soonest(recipe: Recipe) -> None:
    lines = [BottlingLine("slow", run_volume_l=1_000, max_runs_per_day=2), BottlingLine("fast", 5_000, 1)]
    result = _plan(_brewery(50.0, bottling_lines=lines), Batch("A", "a", recipe, 0, volume_hl=50.0))

    [assignment] = result.assignments_of("A")
    assert (assignment.bottling_line, assignment.bottling_runs) == ("fast", ())


def test_vessels_of_one_batch_may_go_to_different_lines(recipe: Recipe) -> None:
    lines = [BottlingLine("a", math.inf, 1), BottlingLine("b", math.inf, 1)]
    result = _plan(
        _brewery(20.0, 20.0, brewhouses=[Brewhouse("bh", 4_000, 1)], bottling_lines=lines),
        Batch("A", "a", recipe, 0, volume_hl=40.0),
    )

    assert sorted((a.fermenter.id, a.bottling_line, a.bottling_day) for a in result.assignments_of("A")) == [
        ("f1", "a", 9),
        ("f2", "b", 9),
    ]


def test_a_vessel_waits_for_the_bottling_of_the_previous_batch(recipe: Recipe) -> None:
    line = BottlingLine("line", run_volume_l=2_000, max_runs_per_day=3)
    first, second = Batch("A", "a", recipe, 0, volume_hl=100.0), Batch("B", "b", recipe, 10, volume_hl=100.0)

    schedule = Schedule(batches={"A": first, "B": second})

    result = HeuristicScheduler().replan(_brewery(100.0, bottling_lines=[line]), schedule, current_day=0)

    assert result.assignments_of("A")[0].end_day == 11
    assert result.brew_day_of("B") == 11


def test_prefers_the_line_with_fewer_runs_when_both_finish_on_the_same_day(recipe: Recipe) -> None:
    lines = [BottlingLine("bottles", run_volume_l=1_000, max_runs_per_day=4), BottlingLine("cans", 3_000, 1)]
    result = _plan(_brewery(30.0, bottling_lines=lines), Batch("A", "a", recipe, 0, volume_hl=30.0))

    [assignment] = result.assignments_of("A")
    assert (assignment.bottling_line, assignment.bottling_runs) == ("cans", ())


def test_bottling_ends_on_the_target_bottling_day(recipe: Recipe) -> None:
    line = BottlingLine("line", run_volume_l=2_000, max_runs_per_day=3)
    batch = Batch("BIG", "big", recipe, 0, volume_hl=100.0, target_bottling_day=14)

    result = _plan(_brewery(100.0, bottling_lines=[line]), batch)

    [assignment] = result.assignments_of("BIG")
    assert (assignment.bottling_day, assignment.bottling_runs) == (13, (13, 13, 13, 14, 14))


def test_a_target_too_early_for_all_runs_is_infeasible(recipe: Recipe) -> None:
    line = BottlingLine("line", run_volume_l=2_000, max_runs_per_day=3)
    schedule = Schedule(batches={"BIG": Batch("BIG", "big", recipe, 0, volume_hl=100.0, target_bottling_day=9)})

    with pytest.raises(SchedulingError):
        HeuristicScheduler(max_horizon_days=30).replan(_brewery(100.0, bottling_lines=[line]), schedule, 0)
