import pytest

from brewery_schedule_digital_twin.domain import Batch, Brewery, FermentationAssignment, Recipe, Schedule
from brewery_schedule_digital_twin.scheduling import CpSatScheduler, HeuristicScheduler, SchedulingError, repair_cost


def _planned(brewery: Brewery, recipe: Recipe) -> Schedule:
    """Batch 001 fermenting in f1 on days 0-9, batch 002 planned into f1 on day 10."""
    schedule = Schedule()
    schedule.add_batch(Batch("001", "B1", recipe, planned_brew_day=0))
    schedule.add_batch(Batch("002", "B2", recipe, planned_brew_day=10))
    schedule.add_assignment(
        FermentationAssignment(
            "001", brewery.vessels[0], 1_000.0, start_day=0, base_fermentation_days=10, bottling_day=9
        )
    )
    schedule.add_assignment(
        FermentationAssignment(
            "002", brewery.vessels[0], 1_000.0, start_day=10, base_fermentation_days=10, bottling_day=19
        )
    )
    return schedule


def test_leaves_a_feasible_plan_unchanged(brewery: Brewery, recipe: Recipe) -> None:
    schedule = _planned(brewery, recipe)
    before = [(a.batch_id, a.fermenter.id, a.start_day, a.bottling_day) for a in schedule.assignments]

    result = CpSatScheduler().replan(brewery, schedule, current_day=3)

    assert [(a.batch_id, a.fermenter.id, a.start_day, a.bottling_day) for a in result.assignments] == before


def test_moves_the_next_batch_to_a_free_tank_rather_than_delaying_it(brewery: Brewery, recipe: Recipe) -> None:
    schedule = _planned(brewery, recipe)
    schedule.assignment_for("001", "f1").extra_delay_days = 2

    result = CpSatScheduler().replan(brewery, schedule, current_day=3)

    delayed = result.assignment_for("001", "f1")
    assert delayed.start_day == 0 and delayed.bottling_day == 11
    assert [(a.fermenter.id, a.start_day, a.bottling_day) for a in result.assignments_of("002")] == [("f2", 10, 19)]
    assert result.validate(brewery) == []


def test_keeps_a_target_bottling_day_by_delaying_the_brew_into_the_freed_tank(brewery: Brewery, recipe: Recipe) -> None:
    schedule = _planned(brewery, recipe)
    schedule.batches["002"] = Batch("002", "B2", recipe, planned_brew_day=10, target_bottling_day=21)
    schedule.assignment_for("001", "f1").extra_delay_days = 2

    result = CpSatScheduler().replan(brewery, schedule, current_day=3)

    assert max(a.last_bottling_day for a in result.assignments_of("002")) == 21


def test_reports_the_objective_the_bound_and_the_starting_solution(brewery: Brewery, recipe: Recipe) -> None:
    schedule = _planned(brewery, recipe)
    schedule.assignment_for("001", "f1").extra_delay_days = 2
    schedule.lock_started_assignments(3)
    before = schedule.clone()
    scheduler = CpSatScheduler()

    result = scheduler.replan(brewery, schedule, current_day=3)

    report = scheduler.last_report
    assert report is not None and report.optimal and report.gap == 0
    assert report.objective == report.best_bound == repair_cost(before, result, 3)
    assert report.hint_completed is True and report.returned_start is False
    assert report.model_seconds >= 0


def test_disturbs_the_plan_no_more_than_the_heuristic(brewery: Brewery, recipe: Recipe) -> None:
    def shift(result: Schedule) -> int:
        return sum(abs(result.brew_day_of(batch_id) - day) for batch_id, day in (("001", 0), ("002", 10)))

    heuristic, cpsat = _planned(brewery, recipe), _planned(brewery, recipe)
    for schedule in (heuristic, cpsat):
        schedule.assignment_for("001", "f1").extra_delay_days = 2

    assert shift(CpSatScheduler().replan(brewery, cpsat, 3)) <= shift(
        HeuristicScheduler().replan(brewery, heuristic, 3)
    )


def test_rebottles_a_locked_batch_whose_fermentation_overruns_its_bottling_day(
    brewery: Brewery, recipe: Recipe
) -> None:
    schedule = _planned(brewery, recipe)
    schedule.assignment_for("001", "f1").extra_delay_days = 3

    result = CpSatScheduler().replan(brewery, schedule, current_day=3)

    delayed = result.assignment_for("001", "f1")
    assert delayed.locked and delayed.bottling_day == delayed.last_fermentation_day == 12


def test_raises_when_no_vessel_holds_a_batch(brewery: Brewery, recipe: Recipe) -> None:
    schedule = Schedule()
    schedule.add_batch(Batch("BIG", "big", recipe, planned_brew_day=0, volume=5_000.0))

    with pytest.raises(SchedulingError):
        CpSatScheduler().replan(brewery, schedule, current_day=0)
