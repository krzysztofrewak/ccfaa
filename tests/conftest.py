from collections.abc import Callable, Sequence

import pytest

from brewery_agentic_replanning.agents import default_agents
from brewery_agentic_replanning.domain import Batch, Brewery, FermentationAssignment, Fermenter, Recipe, Schedule
from brewery_agentic_replanning.policies import ReplanningPolicy
from brewery_agentic_replanning.scheduling import HeuristicScheduler
from brewery_agentic_replanning.signals import Signal
from brewery_agentic_replanning.simulation import BreweryDigitalTwin


@pytest.fixture
def recipe() -> Recipe:
    return Recipe(name="Test Pale Ale", fermentation_days=10)


@pytest.fixture
def brewery() -> Brewery:
    return Brewery(
        name="Test Brewery",
        fermenters=[Fermenter("f", 1, 10.0), Fermenter("f", 2, 10.0)],
        kettle_yield_hl=10.0,
        max_brews_per_day=1,
        max_bottling_runs_per_day=1,
    )


def _three_tank_brewery() -> Brewery:
    return Brewery(
        name="Test Brewery",
        fermenters=[Fermenter("f", 1, 10.0), Fermenter("f", 2, 10.0), Fermenter("f", 3, 10.0)],
        kettle_yield_hl=10.0,
        max_brews_per_day=3,
        max_bottling_runs_per_day=3,
    )


@pytest.fixture
def three_tank_brewery() -> Brewery:
    return _three_tank_brewery()


def _three_batch_schedule(brewery: Brewery, recipe: Recipe) -> Schedule:
    schedule = Schedule()
    for batch_id, fermenter, brew_day in (("A", 0, 9), ("B", 1, 15), ("C", 2, 20)):
        schedule.add_batch(Batch(batch_id, f"B{batch_id}", recipe, planned_brew_day=brew_day))
        schedule.add_assignment(
            FermentationAssignment(
                batch_id=batch_id,
                fermenter=brewery.fermenters[fermenter],
                volume_hl=10.0,
                start_day=brew_day,
                base_fermentation_days=recipe.fermentation_days,
            )
        )
    return schedule


@pytest.fixture
def run_twin(recipe: Recipe) -> Callable[..., BreweryDigitalTwin]:

    def run(
        policy: ReplanningPolicy,
        signals: Sequence[Signal],
        until: int = 30,
        brewery: Brewery | None = None,
        schedule: Schedule | None = None,
    ) -> BreweryDigitalTwin:
        brewery = brewery or _three_tank_brewery()
        twin = BreweryDigitalTwin(
            brewery=brewery,
            schedule=schedule if schedule is not None else _three_batch_schedule(brewery, recipe),
            scheduler=HeuristicScheduler(),
            policy=policy,
            agents=default_agents(),
        )
        for signal in signals:
            twin.inject_signal(signal)
        twin.advance_to(until)
        return twin

    return run
