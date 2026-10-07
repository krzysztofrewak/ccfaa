import pytest

from brewery_agentic_replanning.agents import FermentationAgent, OperationsAgent
from brewery_agentic_replanning.domain import Batch, Brewery, FermentationAssignment, Recipe, Schedule
from brewery_agentic_replanning.policies import ManualPolicy, ReplanningPolicy, UncoordinatedPolicy
from brewery_agentic_replanning.scheduling import HeuristicScheduler
from brewery_agentic_replanning.signals import DayOffSignal, FermentationDelaySignal
from brewery_agentic_replanning.simulation import BreweryDigitalTwin


class ReplanOnEffect(ReplanningPolicy):
    def should_replan_immediately(self, day: int) -> bool:
        return True

    def should_replan_end_of_day(self, day: int) -> bool:
        return False


def _initial_schedule(brewery: Brewery, recipe: Recipe) -> Schedule:
    schedule = Schedule()
    schedule.add_batch(Batch("001", "B1", recipe, planned_brew_day=0))
    schedule.add_assignment(
        FermentationAssignment(
            batch_id="001",
            fermenter=brewery.fermenters[0],
            volume_hl=10.0,
            start_day=0,
            base_fermentation_days=10,
            locked=True,
        )
    )
    return schedule


def _twin(brewery: Brewery, recipe: Recipe, policy: ReplanningPolicy, agents: dict | None = None) -> BreweryDigitalTwin:
    return BreweryDigitalTwin(
        brewery=brewery,
        schedule=_initial_schedule(brewery, recipe),
        scheduler=HeuristicScheduler(),
        policy=policy,
        agents={FermentationDelaySignal: FermentationAgent()} if agents is None else agents,
    )


def test_signal_has_no_effect_before_its_day(brewery: Brewery, recipe: Recipe) -> None:
    twin = _twin(brewery, recipe, ReplanOnEffect())
    twin.inject_signal(FermentationDelaySignal(day=4, batch_id="001", fermenter_id="f1", extra_days=3))

    twin.advance_to(3)

    assert twin.schedule.assignment_for("001", "f1").extra_delay_days == 0
    assert twin.signal_log == []
    assert twin.replan_log == []


def test_signal_is_applied_and_triggers_a_replan_on_its_day(brewery: Brewery, recipe: Recipe) -> None:
    twin = _twin(brewery, recipe, ReplanOnEffect())
    twin.inject_signal(FermentationDelaySignal(day=4, batch_id="001", fermenter_id="f1", extra_days=3))

    twin.advance_to(4)

    assert twin.schedule.assignment_for("001", "f1").extra_delay_days == 3
    assert [event.day for event in twin.replan_log] == [4]
    assert [signal.day for signal in twin.signal_log] == [4]


def test_a_signal_without_effect_is_not_offered_to_the_policy(brewery: Brewery, recipe: Recipe) -> None:
    twin = _twin(brewery, recipe, ReplanOnEffect(), agents={DayOffSignal: OperationsAgent()})
    twin.inject_signal(DayOffSignal(day=4, off_day=20))

    twin.advance_to(4)

    assert not brewery.is_working_day(20)
    assert [signal.day for signal in twin.signal_log] == [4]
    assert twin.replan_log == []


def test_a_policy_can_opt_in_to_signals_without_effect(brewery: Brewery, recipe: Recipe) -> None:
    twin = _twin(brewery, recipe, UncoordinatedPolicy(), agents={DayOffSignal: OperationsAgent()})
    twin.inject_signal(DayOffSignal(day=4, off_day=20))

    twin.advance_to(4)

    assert [event.day for event in twin.replan_log] == [4]


def test_original_days_off_snapshot_is_unaffected_by_later_signals(brewery: Brewery, recipe: Recipe) -> None:
    brewery.mark_day_off(7)
    twin = _twin(brewery, recipe, ReplanOnEffect(), agents={DayOffSignal: OperationsAgent()})
    twin.inject_signal(DayOffSignal(day=2, off_day=20))

    twin.advance_to(2)

    assert twin.original_days_off == {7}
    assert brewery.days_off == {7, 20}


def test_manual_policy_only_replans_when_explicitly_requested(brewery: Brewery, recipe: Recipe) -> None:
    twin = _twin(brewery, recipe, ManualPolicy())
    twin.inject_signal(FermentationDelaySignal(day=4, batch_id="001", fermenter_id="f1", extra_days=3))
    twin.inject_signal(FermentationDelaySignal(day=5, batch_id="001", fermenter_id="f1", extra_days=1))

    twin.advance_to(5)
    assert twin.schedule.assignment_for("001", "f1").extra_delay_days == 4
    assert twin.replan_log == []

    twin.replan_now()
    assert [event.day for event in twin.replan_log] == [5]


def test_injecting_a_past_signal_raises(brewery: Brewery, recipe: Recipe) -> None:
    twin = _twin(brewery, recipe, ReplanOnEffect())
    twin.advance_to(5)

    with pytest.raises(ValueError):
        twin.inject_signal(FermentationDelaySignal(day=3, batch_id="001", fermenter_id="f1", extra_days=1))


def test_advancing_backwards_raises(brewery: Brewery, recipe: Recipe) -> None:
    twin = _twin(brewery, recipe, ReplanOnEffect())
    twin.advance_to(5)

    with pytest.raises(ValueError):
        twin.advance_to(3)


def test_unregistered_signal_type_raises(brewery: Brewery, recipe: Recipe) -> None:
    twin = _twin(brewery, recipe, ReplanOnEffect(), agents={})
    twin.inject_signal(FermentationDelaySignal(day=1, batch_id="001", fermenter_id="f1", extra_days=1))

    with pytest.raises(KeyError):
        twin.advance_to(1)
