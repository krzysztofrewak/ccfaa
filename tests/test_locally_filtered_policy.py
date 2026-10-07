from collections.abc import Callable
from itertools import pairwise

import pytest

from brewery_agentic_replanning.domain import Batch, Recipe, Schedule
from brewery_agentic_replanning.policies import (
    AssessingPolicy,
    CoordinatedPolicy,
    LocallyFilteredPolicy,
    UncoordinatedPolicy,
)
from brewery_agentic_replanning.signals import (
    DayOffSignal,
    FermenterMaintenanceSignal,
    IngredientShortageSignal,
    NewBatchRequirementSignal,
    Signal,
)
from brewery_agentic_replanning.simulation import BreweryDigitalTwin

RunTwin = Callable[..., BreweryDigitalTwin]

POLICIES: dict[str, Callable[[], AssessingPolicy]] = {
    "UNC": UncoordinatedPolicy,
    "LF": LocallyFilteredPolicy,
    "COORD": CoordinatedPolicy,
}


def _run_all(run_twin: RunTwin, signals: Callable[[], list[Signal]]) -> dict[str, BreweryDigitalTwin]:
    return {name: run_twin(factory(), signals()) for name, factory in POLICIES.items()}


def _policy(twin: BreweryDigitalTwin) -> AssessingPolicy:
    assert isinstance(twin.policy, AssessingPolicy)
    return twin.policy


def _replan_days(twin: BreweryDigitalTwin) -> list[int]:
    return [event.day for event in twin.replan_log]


def _decisions(twin: BreweryDigitalTwin) -> list[str]:
    return [record.decision for record in _policy(twin).decisions]


def _verdicts(twin: BreweryDigitalTwin) -> list[str]:
    return [record.assessment.decision for record in _policy(twin).assessments]


def _placements(schedule: Schedule) -> set[tuple]:
    return {
        (a.batch_id, a.fermenter.id, a.volume_hl, a.start_day, a.end_day, a.effective_bottling_day)
        for a in schedule.assignments
    }


def _irrelevant_day_off() -> list[Signal]:
    return [DayOffSignal(day=1, off_day=5)]


def _deferrable_shortage() -> list[Signal]:
    return [IngredientShortageSignal(day=1, batch_id="B", available_from_day=20)]


def _urgent_maintenance() -> list[Signal]:
    return [FermenterMaintenanceSignal(day=8, fermenter_id="f1", start_day=9, duration_days=3)]


def _mixed_signals() -> list[Signal]:
    return [
        FermenterMaintenanceSignal(day=1, fermenter_id="f1", start_day=9, duration_days=3),
        IngredientShortageSignal(day=2, batch_id="B", available_from_day=20),
        DayOffSignal(day=3, off_day=20),
    ]


def test_irrelevant_signal_replans_only_under_the_uncoordinated_policy(run_twin: RunTwin) -> None:
    twins = _run_all(run_twin, _irrelevant_day_off)

    assert _replan_days(twins["UNC"]) == [1]
    assert _replan_days(twins["LF"]) == []
    assert _decisions(twins["LF"]) == ["KEEP"]
    assert _replan_days(twins["COORD"]) == []
    assert _decisions(twins["COORD"]) == ["KEEP"]
    assert _placements(twins["LF"].schedule) == _placements(twins["LF"].original_schedule)


def test_deferrable_signal_replans_on_arrival_where_coordination_defers(run_twin: RunTwin) -> None:
    twins = _run_all(run_twin, _deferrable_shortage)

    assessment = _policy(twins["LF"]).assessments[0].assessment
    assert (assessment.decision, assessment.latest_safe_decision_point) == ("PENDING", 14)
    assert _replan_days(twins["LF"]) == [1]
    assert _decisions(twins["LF"]) == ["REPLAN"]
    assert _replan_days(twins["COORD"]) == [14]
    assert _decisions(twins["COORD"]) == ["PENDING", "REPLAN"]


def test_locally_filtered_policy_never_defers(run_twin: RunTwin, recipe: Recipe) -> None:
    order = Batch("D", "BD", recipe, planned_brew_day=25, target_bottling_day=40)
    twin = run_twin(LocallyFilteredPolicy(), [*_mixed_signals(), NewBatchRequirementSignal(day=4, batch=order)])

    assert set(_decisions(twin)) <= {"KEEP", "REPLAN"}
    for record in _policy(twin).decisions:
        if record.decision == "REPLAN":
            assert record.day == record.signals[0].day


def test_urgent_signal_replans_at_once_under_both_assessing_policies(run_twin: RunTwin) -> None:
    twins = _run_all(run_twin, _urgent_maintenance)

    assert _verdicts(twins["LF"]) == ["REPLAN"]
    assert _replan_days(twins["LF"]) == [8]
    assert _replan_days(twins["COORD"]) == [8]
    assert _decisions(twins["COORD"]) == ["REPLAN"]
    assert _placements(twins["LF"].schedule) == _placements(twins["COORD"].schedule)


def test_second_signal_is_assessed_against_the_replanned_schedule(run_twin: RunTwin) -> None:
    signals = [
        FermenterMaintenanceSignal(day=8, fermenter_id="f1", start_day=9, duration_days=3),
        DayOffSignal(day=9, off_day=20),
    ]
    twin = run_twin(LocallyFilteredPolicy(), signals)

    assert _replan_days(twin) == [8, 9]
    assert _decisions(twin) == ["REPLAN", "REPLAN"]
    assert _policy(twin).assessments[1].assessment.affected_activity == "C"


def test_signal_resolved_by_an_earlier_replan_is_kept(run_twin: RunTwin) -> None:
    signals = [
        IngredientShortageSignal(day=14, batch_id="B", available_from_day=20),
        DayOffSignal(day=15, off_day=15),
    ]
    twin = run_twin(LocallyFilteredPolicy(), signals)

    assert any(a.start_day == 15 for a in twin.original_schedule.assignments)
    assert _replan_days(twin) == [14]
    assert _decisions(twin) == ["REPLAN", "KEEP"]


def test_overlapping_shortage_resolved_by_an_earlier_replan_is_kept(run_twin: RunTwin) -> None:
    signals = [
        IngredientShortageSignal(day=14, batch_id="B", available_from_day=20),
        IngredientShortageSignal(day=15, batch_id="B", available_from_day=18),
    ]
    twin = run_twin(LocallyFilteredPolicy(), signals)

    assert _replan_days(twin) == [14]
    assert _decisions(twin) == ["REPLAN", "KEEP"]


def test_locally_filtered_policy_assesses_each_signal_once(run_twin: RunTwin) -> None:
    locally_filtered = run_twin(LocallyFilteredPolicy(), _mixed_signals())
    coordinated = run_twin(CoordinatedPolicy(), _mixed_signals())

    assert len(_policy(locally_filtered).assessments) == 3
    assert len({id(record.signal) for record in _policy(locally_filtered).assessments}) == 3
    assert len(_policy(coordinated).assessments) > 3


def test_no_signals_means_no_replan(run_twin: RunTwin) -> None:
    for name, factory in POLICIES.items():
        twin = run_twin(factory(), [])
        assert _replan_days(twin) == [], name
        assert _placements(twin.schedule) == _placements(twin.original_schedule), name


def test_replanning_without_new_information_reproduces_the_baseline(run_twin: RunTwin) -> None:
    twin = run_twin(LocallyFilteredPolicy(), [], until=0)
    twin.replan_now()

    assert _placements(twin.schedule) == _placements(twin.original_schedule)
    assert _policy(twin).decisions == []


def test_policies_differ_as_expected_on_mixed_signals(run_twin: RunTwin) -> None:
    def signals() -> list[Signal]:
        return [
            DayOffSignal(day=1, off_day=5),
            IngredientShortageSignal(day=2, batch_id="B", available_from_day=20),
        ]

    twins = _run_all(run_twin, signals)

    assert _replan_days(twins["UNC"]) == [1, 2]
    assert _verdicts(twins["UNC"]) == ["KEEP", "PENDING"]
    assert _replan_days(twins["LF"]) == [2]
    assert _decisions(twins["LF"]) == ["KEEP", "REPLAN"]
    assert _replan_days(twins["COORD"]) == [14]
    assert _decisions(twins["COORD"]) == ["KEEP", "PENDING", "REPLAN"]


@pytest.mark.parametrize("policy_name", list(POLICIES))
def test_every_policy_produces_a_feasible_schedule(run_twin: RunTwin, policy_name: str) -> None:
    twin = run_twin(POLICIES[policy_name](), _mixed_signals())
    brewery, schedule = twin.brewery, twin.schedule

    for fermenter in brewery.fermenters:
        windows = sorted((a.start_day, a.end_day) for a in schedule.assignments_on_fermenter(fermenter.id))
        for earlier, later in pairwise(windows):
            assert earlier[1] <= later[0], f"{fermenter.id} double-booked: {earlier} vs {later}"

    for assignment in schedule.assignments:
        if not assignment.locked:
            assert brewery.is_working_day(assignment.start_day)
            assert brewery.is_working_day(assignment.effective_bottling_day)

    for day in range(60):
        assert sum(1 for a in schedule.assignments if a.start_day == day) <= brewery.max_brews_per_day
        assert sum(1 for a in schedule.assignments if a.bottling_day == day) <= brewery.max_bottling_runs_per_day


def test_target_bottling_day_is_met_exactly(run_twin: RunTwin, recipe: Recipe) -> None:
    order = Batch("SPEC", "Rush order", recipe, planned_brew_day=25, target_bottling_day=40)
    twin = run_twin(LocallyFilteredPolicy(), [NewBatchRequirementSignal(day=1, batch=order)], until=40)

    assignments = twin.schedule.assignments_of("SPEC")
    assert assignments
    assert all(a.bottling_day == 40 for a in assignments)
