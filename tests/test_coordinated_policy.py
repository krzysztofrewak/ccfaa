from collections.abc import Callable

from brewery_agentic_replanning.domain import Brewery, Schedule
from brewery_agentic_replanning.policies import CoordinatedPolicy, UncoordinatedPolicy
from brewery_agentic_replanning.signals import DayOffSignal, FermenterMaintenanceSignal, IngredientShortageSignal
from brewery_agentic_replanning.simulation import BreweryDigitalTwin

RunTwin = Callable[..., BreweryDigitalTwin]


def _replan_days(twin: BreweryDigitalTwin) -> list[int]:
    return [event.day for event in twin.replan_log]


def _three_independent_signals() -> list:
    return [
        FermenterMaintenanceSignal(day=1, fermenter_id="f1", start_day=9, duration_days=3),
        IngredientShortageSignal(day=1, batch_id="B", available_from_day=20),
        DayOffSignal(day=1, off_day=20),
    ]


def test_uncoordinated_policy_replans_once_per_signal(run_twin: RunTwin) -> None:
    twin = run_twin(UncoordinatedPolicy(), _three_independent_signals())

    assert _replan_days(twin) == [1, 1, 1]


def test_coordinating_policy_consolidates_pending_signals_into_one_replan(run_twin: RunTwin) -> None:
    twin = run_twin(CoordinatedPolicy(), _three_independent_signals())

    assert _replan_days(twin) == [8]
    policy = twin.policy
    assert isinstance(policy, CoordinatedPolicy)
    assert [d.decision for d in policy.decisions] == ["PENDING", "PENDING", "PENDING", "REPLAN"]
    replan = policy.decisions[-1]
    assert replan.scheduler_invoked
    assert {s.kind for s in replan.signals} == {
        "FermenterMaintenanceSignal",
        "IngredientShortageSignal",
        "DayOffSignal",
    }
    assert {s.kind for s in replan.urgent_signals} == {"FermenterMaintenanceSignal"}


def test_coordinating_policy_reassesses_a_signal_at_most_once_a_day(run_twin: RunTwin) -> None:
    signals = [
        FermenterMaintenanceSignal(day=1, fermenter_id="f1", start_day=9, duration_days=3),
        IngredientShortageSignal(day=2, batch_id="B", available_from_day=20),
        DayOffSignal(day=3, off_day=20),
    ]
    twin = run_twin(CoordinatedPolicy(), signals)

    policy = twin.policy
    assert isinstance(policy, CoordinatedPolicy)
    seen = [(record.day, id(record.signal)) for record in policy.assessments]
    assert len(seen) == len(set(seen))


def test_coordinating_policy_ignores_a_redundant_signal(run_twin: RunTwin, three_tank_brewery: Brewery) -> None:
    three_tank_brewery.mark_day_off(20)

    twin = run_twin(
        CoordinatedPolicy(), [DayOffSignal(day=1, off_day=20)], brewery=three_tank_brewery, schedule=Schedule()
    )

    assert twin.replan_log == []


def test_coordinating_policy_replans_at_the_latest_safe_point_without_new_signals(run_twin: RunTwin) -> None:
    twin = run_twin(CoordinatedPolicy(), [DayOffSignal(day=1, off_day=20)])

    assert _replan_days(twin) == [19]
