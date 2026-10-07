import pytest

from brewery_agentic_replanning.agents import (
    FermentationAgent,
    MaintenanceAgent,
    OperationsAgent,
    ProcurementAgent,
)
from brewery_agentic_replanning.domain import Batch, Brewery, FermentationAssignment, Recipe, Schedule
from brewery_agentic_replanning.policies import ManualPolicy
from brewery_agentic_replanning.scheduling import HeuristicScheduler
from brewery_agentic_replanning.signals import (
    DayOffSignal,
    FermentationDelaySignal,
    FermenterMaintenanceSignal,
    IngredientShortageSignal,
)
from brewery_agentic_replanning.simulation import BreweryDigitalTwin


def _assignment(
    brewery: Brewery, batch_id: str, start_day: int, fermenter: int = 0, **kwargs
) -> FermentationAssignment:
    return FermentationAssignment(
        batch_id=batch_id,
        fermenter=brewery.fermenters[fermenter],
        volume_hl=10.0,
        start_day=start_day,
        base_fermentation_days=10,
        **kwargs,
    )


def _schedule(brewery: Brewery, recipe: Recipe, *starts: tuple[str, int, int]) -> Schedule:
    schedule = Schedule()
    for batch_id, start_day, fermenter in starts:
        schedule.add_batch(Batch(batch_id, batch_id, recipe, planned_brew_day=start_day))
        schedule.add_assignment(_assignment(brewery, batch_id, start_day, fermenter))
    return schedule


def _assess(agent, brewery: Brewery, schedule: Schedule, signal):
    agent.apply(brewery, schedule, signal)
    return agent.assess(brewery, schedule, signal, signal.day)


def test_has_started_from_the_brew_day_on(brewery: Brewery) -> None:
    assignment = _assignment(brewery, "001", start_day=5)
    assert not assignment.has_started(4)
    assert assignment.has_started(5)
    assert assignment.has_started(20)


def test_a_locked_assignment_counts_as_started_whatever_the_day(brewery: Brewery) -> None:
    assert _assignment(brewery, "001", start_day=9, locked=True).has_started(4)


def test_bottling_is_still_ahead_until_its_day_has_passed(brewery: Brewery) -> None:
    assignment = _assignment(brewery, "001", start_day=0)
    bottling_day = assignment.effective_bottling_day
    assert not assignment.bottling_done(bottling_day)
    assert assignment.bottling_done(bottling_day + 1)


def test_scheduler_locks_by_the_same_rule(brewery: Brewery) -> None:
    schedule = Schedule(assignments=[_assignment(brewery, "a", 3), _assignment(brewery, "b", 4, fermenter=1)])
    schedule.lock_started_assignments(3)
    assert [a.locked for a in schedule.assignments] == [a.has_started(3) for a in schedule.assignments] == [True, False]


def test_twin_marks_started_work_as_each_day_begins(brewery: Brewery, recipe: Recipe) -> None:
    schedule = _schedule(brewery, recipe, ("001", 0, 0), ("002", 3, 1))
    twin = BreweryDigitalTwin(
        brewery=brewery,
        schedule=schedule,
        scheduler=HeuristicScheduler(),
        policy=ManualPolicy(),
        agents={},
        current_day=0,
    )
    twin.advance_to(2)
    assert [a.locked for a in twin.schedule.assignments] == [True, False]
    twin.advance_to(3)
    assert [a.locked for a in twin.schedule.assignments] == [True, True]
    assert not twin.replan_log


def test_maintenance_over_an_ongoing_unlocked_fermentation_is_keep(brewery: Brewery, recipe: Recipe) -> None:
    schedule = _schedule(brewery, recipe, ("001", 0, 0))
    signal = FermenterMaintenanceSignal(day=2, fermenter_id="f1", start_day=3, duration_days=2)

    assessment = _assess(MaintenanceAgent(), brewery, schedule, signal)

    assert assessment.decision == "KEEP"


def test_maintenance_over_a_fermentation_brewed_today_is_keep(brewery: Brewery, recipe: Recipe) -> None:
    schedule = _schedule(brewery, recipe, ("001", 2, 0))
    signal = FermenterMaintenanceSignal(day=2, fermenter_id="f1", start_day=2, duration_days=1)

    assert _assess(MaintenanceAgent(), brewery, schedule, signal).decision == "KEEP"


def test_maintenance_over_ongoing_and_future_work_is_timed_by_the_future_one(brewery: Brewery, recipe: Recipe) -> None:
    schedule = _schedule(brewery, recipe, ("001", 0, 0), ("002", 10, 0))
    signal = FermenterMaintenanceSignal(day=2, fermenter_id="f1", start_day=8, duration_days=5)

    assessment = _assess(MaintenanceAgent(), brewery, schedule, signal)

    assert assessment.decision == "PENDING"
    assert assessment.latest_safe_decision_point == 9
    assert assessment.affected_activity == "002"


def test_maintenance_over_a_future_allocation_is_still_not_keep(brewery: Brewery, recipe: Recipe) -> None:
    schedule = _schedule(brewery, recipe, ("002", 3, 0))
    signal = FermenterMaintenanceSignal(day=2, fermenter_id="f1", start_day=3, duration_days=1)

    assessment = _assess(MaintenanceAgent(), brewery, schedule, signal)

    assert assessment.decision == "REPLAN"
    assert assessment.affected_activity == "002"


def test_day_off_on_a_brew_that_already_happened_today_is_keep(brewery: Brewery, recipe: Recipe) -> None:
    schedule = _schedule(brewery, recipe, ("001", 4, 0))
    signal = DayOffSignal(day=4, off_day=4)

    assert _assess(OperationsAgent(), brewery, schedule, signal).decision == "KEEP"


def test_day_off_on_a_started_fermentations_bottling_is_not_keep(brewery: Brewery, recipe: Recipe) -> None:
    schedule = _schedule(brewery, recipe, ("001", 0, 0))
    signal = DayOffSignal(day=2, off_day=schedule.assignments[0].effective_bottling_day)

    assessment = _assess(OperationsAgent(), brewery, schedule, signal)

    assert assessment.decision == "PENDING"
    assert assessment.latest_safe_decision_point == 8
    assert assessment.affected_activity == "001"


def test_day_off_on_a_locked_fermentations_bottling_is_not_keep(brewery: Brewery, recipe: Recipe) -> None:
    schedule = _schedule(brewery, recipe, ("001", 0, 0))
    schedule.lock_started_assignments(2)
    signal = DayOffSignal(day=2, off_day=9)

    assert _assess(OperationsAgent(), brewery, schedule, signal).decision == "PENDING"


def test_day_off_on_a_future_brew_is_still_not_keep(brewery: Brewery, recipe: Recipe) -> None:
    schedule = _schedule(brewery, recipe, ("002", 6, 0))
    signal = DayOffSignal(day=2, off_day=6)

    assert _assess(OperationsAgent(), brewery, schedule, signal).decision == "PENDING"


def test_fermentation_delay_ignores_the_completed_previous_tenant(brewery: Brewery, recipe: Recipe) -> None:
    schedule = _schedule(brewery, recipe, ("000", -12, 0), ("001", 0, 0), ("002", 11, 0))
    signal = FermentationDelaySignal(day=4, batch_id="001", fermenter_id="f1", extra_days=3)

    assessment = _assess(FermentationAgent(), brewery, schedule, signal)

    assert assessment.decision == "PENDING"
    assert assessment.latest_safe_decision_point == 10
    assert assessment.affected_activity == "002"


def test_shortage_for_a_batch_already_brewed_is_keep(brewery: Brewery, recipe: Recipe) -> None:
    schedule = _schedule(brewery, recipe, ("001", 3, 0))
    signal = IngredientShortageSignal(day=3, batch_id="001", available_from_day=8)

    assert _assess(ProcurementAgent(), brewery, schedule, signal).decision == "KEEP"


@pytest.mark.parametrize("current_day", [2, 3, 4])
def test_assessment_agrees_before_and_after_the_scheduler_ran(
    brewery: Brewery, recipe: Recipe, current_day: int
) -> None:
    verdicts = []
    for after_the_scheduler_ran in (False, True):
        schedule = _schedule(brewery, recipe, ("001", 3, 0), ("002", 14, 0))
        if after_the_scheduler_ran:
            HeuristicScheduler().replan(brewery, schedule, current_day)
        signal = FermenterMaintenanceSignal(day=current_day, fermenter_id="f1", start_day=5, duration_days=10)
        assessment = _assess(MaintenanceAgent(), brewery, schedule, signal)
        verdicts.append((assessment.decision, assessment.latest_safe_decision_point, assessment.affected_activity))
    assert verdicts[0] == verdicts[1]
