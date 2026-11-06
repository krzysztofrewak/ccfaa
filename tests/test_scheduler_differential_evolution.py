from collections.abc import Callable

import pytest

from brewery_schedule_digital_twin.domain import Batch, Brewery, FermentationAssignment, Recipe, Schedule
from brewery_schedule_digital_twin.scheduling import (
    CpSatScheduler,
    DifferentialEvolutionScheduler,
    HeuristicScheduler,
    Scheduler,
    repair_cost,
)


def _delayed(brewery: Brewery, recipe: Recipe) -> Schedule:
    """Batch 001 fermenting in f1 two days longer than planned, batch 002 planned into f1 on day 10."""
    schedule = Schedule()
    schedule.add_batch(Batch("001", "B1", recipe, planned_brew_day=0))
    schedule.add_batch(Batch("002", "B2", recipe, planned_brew_day=10))
    schedule.add_assignment(
        FermentationAssignment(
            "001",
            brewery.vessels[0],
            1_000.0,
            start_day=0,
            base_fermentation_days=10,
            bottling_day=9,
            extra_delay_days=2,
        )
    )
    schedule.add_assignment(
        FermentationAssignment(
            "002", brewery.vessels[0], 1_000.0, start_day=10, base_fermentation_days=10, bottling_day=19
        )
    )
    return schedule


def _cost(make_scheduler: Callable[[], Scheduler], brewery: Brewery, recipe: Recipe) -> int:
    schedule = _delayed(brewery, recipe)
    schedule.lock_started_assignments(3)
    before = schedule.clone()
    return repair_cost(before, make_scheduler().replan(brewery, schedule, current_day=3), 3)


def test_an_unchanged_schedule_costs_nothing(brewery: Brewery, recipe: Recipe) -> None:
    schedule = _delayed(brewery, recipe)
    schedule.assignment_for("001", "f1").extra_delay_days = 0

    assert repair_cost(schedule, schedule, current_day=3) == 0


def test_repairs_a_delay_no_worse_than_the_heuristic_and_as_well_as_cpsat(brewery: Brewery, recipe: Recipe) -> None:
    heuristic = _cost(HeuristicScheduler, brewery, recipe)
    de = _cost(DifferentialEvolutionScheduler, brewery, recipe)

    assert de <= heuristic
    assert de == _cost(CpSatScheduler, brewery, recipe)


def test_moves_the_next_batch_to_the_free_tank(brewery: Brewery, recipe: Recipe) -> None:
    result = DifferentialEvolutionScheduler().replan(brewery, _delayed(brewery, recipe), current_day=3)

    assert [(a.fermenter.id, a.start_day, a.bottling_day) for a in result.assignments_of("002")] == [("f2", 10, 19)]
    assert result.validate(brewery) == []


def test_the_same_seed_gives_the_same_schedule(brewery: Brewery, recipe: Recipe) -> None:
    def signature(result: Schedule) -> list[tuple[str, str, int, int | None]]:
        return [(a.batch_id, a.fermenter.id, a.start_day, a.bottling_day) for a in result.assignments]

    first = DifferentialEvolutionScheduler(seed=7).replan(brewery, _delayed(brewery, recipe), current_day=3)
    again = DifferentialEvolutionScheduler(seed=7).replan(brewery, _delayed(brewery, recipe), current_day=3)

    assert signature(first) == signature(again)


def test_reports_the_cost_of_its_schedule_and_of_the_heuristic(brewery: Brewery, recipe: Recipe) -> None:
    scheduler = DifferentialEvolutionScheduler()
    scheduler.replan(brewery, _delayed(brewery, recipe), current_day=3)

    report = scheduler.last_report
    assert report is not None and report.heuristic_cost is not None
    assert report.cost <= report.heuristic_cost
    assert report.evaluations >= 1


def test_a_spent_time_limit_decodes_only_the_heuristic_genome(brewery: Brewery, recipe: Recipe) -> None:
    scheduler = DifferentialEvolutionScheduler(time_limit_seconds=0)
    scheduler.replan(brewery, _delayed(brewery, recipe), current_day=3)

    report = scheduler.last_report
    assert report is not None
    assert (report.evaluations, report.generations, report.stopped_by_limit) == (1, 0, True)
    assert report.cost == report.heuristic_cost and not report.improved


def test_counts_the_generations_it_completes(brewery: Brewery, recipe: Recipe) -> None:
    scheduler = DifferentialEvolutionScheduler(generations=3)
    scheduler.replan(brewery, _delayed(brewery, recipe), current_day=3)

    assert scheduler.last_report is not None
    assert (scheduler.last_report.generations, scheduler.last_report.stopped_by_limit) == (3, False)


def test_needs_a_population_of_at_least_four() -> None:
    with pytest.raises(ValueError):
        DifferentialEvolutionScheduler(population_size=3)
