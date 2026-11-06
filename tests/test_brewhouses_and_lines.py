import math
from collections import Counter

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

STRONG_LINE = BottlingLine("line", run_volume=math.inf, max_runs_per_day=5)


def _brewery(
    *capacities: float,
    brewhouses: list[Brewhouse] | None = None,
    bottling_lines: list[BottlingLine] | None = None,
) -> Brewery:
    return Brewery(
        name="Plant",
        vessels=[Vessel.unitank("f", i, c) for i, c in enumerate(capacities, start=1)],
        brewhouses=brewhouses or [Brewhouse("brewhouse", kettle_yield=5_000, max_brews_per_day=2)],
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
    """An assignment filled from brews given as (number, day, volume, brewhouse)."""
    fills = tuple(Fill(Brew(batch_id, number, day, brewhouse=name), volume) for number, day, volume, name in brews)
    return FermentationAssignment(
        batch_id,
        vessel,
        sum(fill.volume for fill in fills),
        start_day=min(day for _, day, _, _ in brews),
        base_fermentation_days=10,
        fills=fills,
        **kwargs,  # type: ignore[arg-type]
    )


# Equipment


@pytest.mark.parametrize("kettle_yield, max_brews", [(0, 1), (1_000, 0)])
def test_brewhouse_rejects_non_positive_settings(kettle_yield: float, max_brews: int) -> None:
    with pytest.raises(ValueError):
        Brewhouse("bh", kettle_yield=kettle_yield, max_brews_per_day=max_brews)


@pytest.mark.parametrize("run_volume, max_runs", [(0, 1), (1_000, 0)])
def test_bottling_line_rejects_non_positive_settings(run_volume: float, max_runs: int) -> None:
    with pytest.raises(ValueError):
        BottlingLine("line", run_volume=run_volume, max_runs_per_day=max_runs)


def test_bottling_line_needs_a_run_per_started_run_volume() -> None:
    line = BottlingLine("line", run_volume=2_000, max_runs_per_day=3)

    assert [line.runs_for(v) for v in (500.0, 2_000.0, 2_050.0, 10_000.0)] == [1, 1, 2, 5]
    assert STRONG_LINE.runs_for(100_000.0) == 1


def test_a_line_by_daily_volume_counts_runs_of_a_hundred_litres() -> None:
    line = BottlingLine.by_daily_volume("line", 2_000)

    assert (line.run_volume, line.max_runs_per_day) == (100, 20)
    assert (line.runs_for(500.0), line.runs_for(3_000.0), line.runs_for(450.0)) == (5, 30, 5)
    with pytest.raises(ValueError, match="multiple"):
        BottlingLine.by_daily_volume("line", 2_050)


def test_brewery_needs_a_brewhouse_and_a_bottling_line() -> None:
    with pytest.raises(ValueError):
        Brewery("Plant", vessels=[], brewhouses=[], bottling_lines=[STRONG_LINE])
    with pytest.raises(ValueError):
        Brewery("Plant", vessels=[], brewhouses=[Brewhouse("bh", 1_000, 1)], bottling_lines=[])


def test_brewery_rejects_duplicate_brewhouse_and_line_names() -> None:
    with pytest.raises(ValueError):
        _brewery(1_000.0, brewhouses=[Brewhouse("bh", 1_000, 1), Brewhouse("bh", 2_000, 1)])
    with pytest.raises(ValueError):
        _brewery(1_000.0, bottling_lines=[STRONG_LINE, STRONG_LINE])


def test_brewery_sums_up_its_brewhouses_and_lines() -> None:
    brewery = _brewery(
        1_000.0,
        brewhouses=[Brewhouse("small", 1_000, 2), Brewhouse("large", 3_000, 1)],
        bottling_lines=[STRONG_LINE, BottlingLine("cans", run_volume=500, max_runs_per_day=4)],
    )

    assert (brewery.kettle_yield, brewery.max_brews_per_day, brewery.max_bottling_runs_per_day) == (3_000, 3, 9)


def test_none_names_the_only_brewhouse_and_line() -> None:
    brewery = _brewery(1_000.0)
    assert brewery.brewhouse(None).name == "brewhouse"
    assert brewery.bottling_line(None) is STRONG_LINE

    several = _brewery(
        1_000.0,
        brewhouses=[Brewhouse("a", 1_000, 1), Brewhouse("b", 1_000, 1)],
        bottling_lines=[STRONG_LINE, BottlingLine("cans", run_volume=500, max_runs_per_day=4)],
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
            "001", Vessel.unitank("f", 1, 10_000.0), 10_000.0, 0, 10, bottling_day=9, bottling_runs=(10, 10)
        )


def test_a_vessel_is_occupied_until_its_last_bottling_run() -> None:
    assignment = FermentationAssignment(
        "001", Vessel.unitank("f", 1, 10_000.0), 10_000.0, 0, 10, bottling_day=9, bottling_runs=(9, 9, 9, 10, 10)
    )

    assert assignment.effective_bottling_runs == (9, 9, 9, 10, 10)
    assert assignment.last_bottling_day == 10
    assert assignment.end_day == 11
    assert not assignment.bottling_done(10)
    assert assignment.bottling_done(11)


def test_a_single_run_is_on_the_bottling_day() -> None:
    assignment = FermentationAssignment("001", Vessel.unitank("f", 1, 1_000.0), 1_000.0, 0, 10, bottling_day=12)

    assert assignment.effective_bottling_runs == (12,)
    assert assignment.end_day == 13


def test_later_bottling_runs_are_scheduled_work(recipe: Recipe) -> None:
    brewery = _brewery(10_000.0)
    assignment = FermentationAssignment(
        "001", brewery.vessels[0], 10_000.0, 0, 10, bottling_day=9, bottling_runs=(9, 10)
    )
    schedule = _schedule(recipe, assignment)

    assert schedule.has_scheduled_work_on(10)
    assessment = OperationsAgent().assess(brewery, schedule, DayOffSignal(day=1, off_day=10), current_day=1)
    assert assessment.decision != "KEEP"


# Validation


def test_validate_checks_each_brew_against_its_brewhouse(recipe: Recipe) -> None:
    brewery = _brewery(10_000.0, brewhouses=[Brewhouse("small", 2_000, 2), Brewhouse("large", 5_000, 2)])
    schedule = _schedule(recipe, _brewed("001", brewery.vessels[0], (1, 0, 3_000.0, "small"), (2, 0, 5_000.0, "large")))

    assert schedule.validate(brewery) == [
        "brew '001/1' totals 3000 l, more than the yield of brewhouse 'small' (2000 l)"
    ]


def test_validate_counts_brews_per_brewhouse(recipe: Recipe) -> None:
    brewery = _brewery(10_000.0, brewhouses=[Brewhouse("a", 5_000, 1), Brewhouse("b", 5_000, 1)])
    one_each = _brewed("001", brewery.vessels[0], (1, 0, 5_000.0, "a"), (2, 0, 5_000.0, "b"))
    both_in_a = _brewed("001", brewery.vessels[0], (1, 0, 5_000.0, "a"), (2, 0, 5_000.0, "a"))

    assert _schedule(recipe, one_each).validate(brewery) == []
    assert _schedule(recipe, both_in_a).validate(brewery) == [
        "brewhouse 'a' has 2 brews on day 0 (max 1): ['001/1', '001/2']"
    ]


def test_validate_needs_a_brewhouse_name_when_there_are_several(recipe: Recipe) -> None:
    brewery = _brewery(10_000.0, brewhouses=[Brewhouse("a", 5_000, 1), Brewhouse("b", 5_000, 1)])
    schedule = _schedule(recipe, FermentationAssignment("001", brewery.vessels[0], 5_000.0, 0, 10))

    assert schedule.validate(brewery) == [
        "brew '001/1': brewery 'Plant' has several brewhouses, so a brew must name one"
    ]


def test_validate_rejects_one_brew_from_different_brewhouses(recipe: Recipe) -> None:
    brewery = _brewery(5_000.0, 5_000.0, brewhouses=[Brewhouse("a", 5_000, 1), Brewhouse("b", 5_000, 1)])
    schedule = _schedule(
        recipe,
        _brewed("001", brewery.vessels[0], (1, 0, 2_500.0, "a")),
        _brewed("001", brewery.vessels[1], (1, 0, 2_500.0, "b")),
    )

    assert "brew '001/1' comes from different brewhouses ['a', 'b']" in schedule.validate(brewery)


def test_validate_needs_enough_bottling_runs_for_the_volume(recipe: Recipe) -> None:
    brewery = _brewery(10_000.0, bottling_lines=[BottlingLine("line", run_volume=2_000, max_runs_per_day=5)])
    schedule = _schedule(
        recipe,
        FermentationAssignment("001", brewery.vessels[0], 5_000.0, 0, 10, bottling_day=9, bottling_runs=(9, 9)),
    )

    assert schedule.validate(brewery) == ["batch '001' in 'f1' needs 3 bottling runs on line 'line' but has 2"]


def test_validate_counts_bottling_runs_per_line_and_day(recipe: Recipe) -> None:
    lines = [BottlingLine("a", run_volume=2_000, max_runs_per_day=2), BottlingLine("b", math.inf, 1)]
    brewery = _brewery(10_000.0, 1_000.0, bottling_lines=lines)
    schedule = _schedule(
        recipe,
        FermentationAssignment(
            "001", brewery.vessels[0], 5_000.0, 0, 10, bottling_day=9, bottling_line="a", bottling_runs=(9, 9, 9)
        ),
        FermentationAssignment("002", brewery.vessels[1], 1_000.0, 0, 10, bottling_day=9, bottling_line="b"),
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
    result = _plan(_brewery(5_000.0), Batch("A", "a", recipe, planned_brew_day=0, volume=5_000.0))

    [assignment] = result.assignments_of("A")
    assert (assignment.fills, assignment.bottling_line, assignment.bottling_runs) == ((), None, ())


def test_brews_in_the_smallest_brewhouse_that_makes_the_brew(recipe: Recipe) -> None:
    brewhouses = [Brewhouse("large", 5_000, 1), Brewhouse("small", 2_000, 1)]
    result = _plan(
        _brewery(5_000.0, 2_000.0, brewhouses=brewhouses),
        Batch("S", "s", recipe, planned_brew_day=0, volume=2_000.0),
        Batch("L", "l", recipe, planned_brew_day=0, volume=5_000.0),
    )

    assert _brews(result, "S") == [("S/1", 0, "small")]
    assert _brews(result, "L") == [("L/1", 0, "large")]


def test_uses_a_larger_brewhouse_when_the_smaller_one_is_busy(recipe: Recipe) -> None:
    brewhouses = [Brewhouse("large", 5_000, 1), Brewhouse("small", 2_000, 1)]
    result = _plan(
        _brewery(2_000.0, 2_000.0, brewhouses=brewhouses),
        Batch("A", "a", recipe, planned_brew_day=0, volume=2_000.0),
        Batch("B", "b", recipe, planned_brew_day=0, volume=2_000.0),
    )

    assert _brews(result, "A") + _brews(result, "B") == [("A/1", 0, "small"), ("B/1", 0, "large")]


def test_merges_brews_from_two_brewhouses_on_one_day(recipe: Recipe) -> None:
    brewhouses = [Brewhouse("a", 5_000, 1), Brewhouse("b", 5_000, 1)]
    result = _plan(_brewery(10_000.0, brewhouses=brewhouses), Batch("BIG", "big", recipe, 0, volume=10_000.0))

    assert _brews(result, "BIG") == [("BIG/1", 0, "a"), ("BIG/2", 0, "b")]


def test_bottles_a_large_vessel_in_several_runs_as_fast_as_the_line_allows(recipe: Recipe) -> None:
    line = BottlingLine("line", run_volume=2_000, max_runs_per_day=3)
    result = _plan(_brewery(10_000.0, bottling_lines=[line]), Batch("BIG", "big", recipe, 0, volume=10_000.0))

    [assignment] = result.assignments_of("BIG")
    assert (assignment.bottling_day, assignment.bottling_runs) == (9, (9, 9, 9, 10, 10))
    assert assignment.end_day == 11


def test_bottling_skips_a_day_off(recipe: Recipe) -> None:
    line = BottlingLine("line", run_volume=2_000, max_runs_per_day=3)
    brewery = _brewery(10_000.0, bottling_lines=[line])
    brewery.mark_day_off(10)

    result = _plan(brewery, Batch("BIG", "big", recipe, 0, volume=10_000.0))

    [assignment] = result.assignments_of("BIG")
    assert assignment.bottling_runs == (9, 9, 9, 11, 11)


def test_bottles_on_the_line_that_finishes_soonest(recipe: Recipe) -> None:
    lines = [BottlingLine("slow", run_volume=1_000, max_runs_per_day=2), BottlingLine("fast", 5_000, 1)]
    result = _plan(_brewery(5_000.0, bottling_lines=lines), Batch("A", "a", recipe, 0, volume=5_000.0))

    [assignment] = result.assignments_of("A")
    assert (assignment.bottling_line, assignment.bottling_runs) == ("fast", ())


def test_vessels_of_one_batch_may_go_to_different_lines(recipe: Recipe) -> None:
    lines = [BottlingLine("a", math.inf, 1), BottlingLine("b", math.inf, 1)]
    result = _plan(
        _brewery(2_000.0, 2_000.0, brewhouses=[Brewhouse("bh", 4_000, 1)], bottling_lines=lines),
        Batch("A", "a", recipe, 0, volume=4_000.0),
    )

    assert sorted((a.fermenter.id, a.bottling_line, a.bottling_day) for a in result.assignments_of("A")) == [
        ("f1", "a", 9),
        ("f2", "b", 9),
    ]


def test_vessels_beyond_the_daily_runs_start_bottling_on_later_days(recipe: Recipe) -> None:
    line = BottlingLine("line", run_volume=2_000, max_runs_per_day=1)
    result = _plan(
        _brewery(3_000.0, 3_000.0, brewhouses=[Brewhouse("bh", 6_000, 1)], bottling_lines=[line]),
        Batch("A", "a", recipe, 0, volume=6_000.0),
    )

    assert sorted(
        (a.fermenter.id, a.bottling_day, a.effective_bottling_runs, a.end_day) for a in result.assignments_of("A")
    ) == [("f1", 9, (9, 10), 11), ("f2", 11, (11, 12), 13)]
    assert (
        result.validate(_brewery(3_000.0, 3_000.0, brewhouses=[Brewhouse("bh", 6_000, 1)], bottling_lines=[line])) == []
    )


def test_vessels_share_the_daily_volume_of_a_line(recipe: Recipe) -> None:
    line = BottlingLine.by_daily_volume("line", 2_000)
    brewery = _brewery(3_000.0, 1_000.0, 1_000.0, brewhouses=[Brewhouse("bh", 3_000, 3)], bottling_lines=[line])
    schedule = Schedule(
        batches={
            "BIG": Batch("BIG", "big", recipe, 0, volume=3_000.0),
            "S1": Batch("S1", "s1", recipe, 0, volume=500.0),
            "S2": Batch("S2", "s2", recipe, 0, volume=500.0),
        }
    )

    result = HeuristicScheduler().replan(brewery, schedule, current_day=0)

    runs = {a.batch_id: Counter(a.effective_bottling_runs) for a in result.assignments}
    assert runs == {"BIG": {9: 20, 10: 10}, "S1": {10: 5}, "S2": {10: 5}}
    assert result.validate(brewery) == []


def test_a_batch_beyond_the_daily_volume_bottles_its_vessels_one_after_another(recipe: Recipe) -> None:
    line = BottlingLine.by_daily_volume("line", 2_000)
    brewery = _brewery(2_000.0, 2_000.0, brewhouses=[Brewhouse("bh", 4_000, 1)], bottling_lines=[line])

    result = _plan(brewery, Batch("A", "a", recipe, 0, volume=4_000.0))

    assert sorted((a.fermenter.id, a.bottling_day, a.end_day) for a in result.assignments_of("A")) == [
        ("f1", 9, 10),
        ("f2", 10, 11),
    ]
    assert result.validate(brewery) == []


def test_a_vessel_waits_for_the_bottling_of_the_previous_batch(recipe: Recipe) -> None:
    line = BottlingLine("line", run_volume=2_000, max_runs_per_day=3)
    first, second = Batch("A", "a", recipe, 0, volume=10_000.0), Batch("B", "b", recipe, 10, volume=10_000.0)

    schedule = Schedule(batches={"A": first, "B": second})

    result = HeuristicScheduler().replan(_brewery(10_000.0, bottling_lines=[line]), schedule, current_day=0)

    assert result.assignments_of("A")[0].end_day == 11
    assert result.brew_day_of("B") == 11


def test_prefers_the_line_with_fewer_runs_when_both_finish_on_the_same_day(recipe: Recipe) -> None:
    lines = [BottlingLine("bottles", run_volume=1_000, max_runs_per_day=4), BottlingLine("cans", 3_000, 1)]
    result = _plan(_brewery(3_000.0, bottling_lines=lines), Batch("A", "a", recipe, 0, volume=3_000.0))

    [assignment] = result.assignments_of("A")
    assert (assignment.bottling_line, assignment.bottling_runs) == ("cans", ())


def test_bottling_ends_on_the_target_bottling_day(recipe: Recipe) -> None:
    line = BottlingLine("line", run_volume=2_000, max_runs_per_day=3)
    batch = Batch("BIG", "big", recipe, 0, volume=10_000.0, target_bottling_day=14)

    result = _plan(_brewery(10_000.0, bottling_lines=[line]), batch)

    [assignment] = result.assignments_of("BIG")
    assert (assignment.bottling_day, assignment.bottling_runs) == (13, (13, 13, 13, 14, 14))


def test_a_target_too_early_for_all_runs_is_infeasible(recipe: Recipe) -> None:
    line = BottlingLine("line", run_volume=2_000, max_runs_per_day=3)
    schedule = Schedule(batches={"BIG": Batch("BIG", "big", recipe, 0, volume=10_000.0, target_bottling_day=9)})

    with pytest.raises(SchedulingError):
        HeuristicScheduler(max_horizon_days=30).replan(_brewery(10_000.0, bottling_lines=[line]), schedule, 0)


def test_replanning_keeps_the_bottling_of_locked_batches_that_is_still_feasible() -> None:
    brewery = Brewery(
        name="Plant",
        vessels=[Vessel.unitank("f", 1, 1_000.0), Vessel.unitank("f", 2, 2_000.0)],
        brewhouses=[Brewhouse("brewhouse", kettle_yield=2_000, max_brews_per_day=2)],
        bottling_lines=[BottlingLine("line", run_volume=1_000, max_runs_per_day=2)],
    )
    early, late, next_in_f1 = Recipe("Early", 11), Recipe("Late", 12), Recipe("Next", 8)
    schedule = Schedule()
    for batch_id, recipe, day in (("A", late, -10), ("B", early, -10), ("C", next_in_f1, 2)):
        schedule.add_batch(Batch(batch_id, batch_id, recipe, planned_brew_day=day))
    # A ends fermentation after B but takes a run on day 1, so B is bottled over days 1 and 2.
    schedule.add_assignment(FermentationAssignment("A", brewery.vessels[0], 1_000.0, -10, 12, bottling_day=1))
    schedule.add_assignment(
        FermentationAssignment("B", brewery.vessels[1], 2_000.0, -10, 11, bottling_day=1, bottling_runs=(1, 2))
    )
    schedule.add_assignment(FermentationAssignment("C", brewery.vessels[0], 1_000.0, 2, 8, bottling_day=9))
    assert schedule.validate(brewery) == []

    result = HeuristicScheduler().replan(brewery, schedule, current_day=5)

    assert result.validate(brewery) == []
    assert result.assignment_for("A", "f1").effective_bottling_runs == (1,)
    assert result.assignment_for("B", "f2").effective_bottling_runs == (1, 2)
