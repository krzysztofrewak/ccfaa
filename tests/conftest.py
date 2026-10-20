import math
from collections.abc import Callable, Sequence

import pytest

from brewery_schedule_digital_twin.agents import default_agents
from brewery_schedule_digital_twin.domain import (
    Batch,
    BottlingLine,
    Brewery,
    Brewhouse,
    FermentationAssignment,
    Recipe,
    Schedule,
    Vessel,
)
from brewery_schedule_digital_twin.policies import ReplanningPolicy
from brewery_schedule_digital_twin.scheduling import HeuristicScheduler
from brewery_schedule_digital_twin.signals import Signal
from brewery_schedule_digital_twin.simulation import BreweryDigitalTwin


@pytest.fixture
def recipe() -> Recipe:
    return Recipe(name="Test Pale Ale", fermentation_days=10)


@pytest.fixture
def brewery() -> Brewery:
    return Brewery(
        name="Test Brewery",
        vessels=[Vessel.unitank("f", 1, 10.0), Vessel.unitank("f", 2, 10.0)],
        brewhouses=[Brewhouse("brewhouse", yield_l=1_000, max_brews_per_day=1)],
        bottling_lines=[BottlingLine("line", run_volume_l=math.inf, max_runs_per_day=1)],
    )


def _three_tank_brewery() -> Brewery:
    return Brewery(
        name="Test Brewery",
        vessels=[Vessel.unitank("f", 1, 10.0), Vessel.unitank("f", 2, 10.0), Vessel.unitank("f", 3, 10.0)],
        brewhouses=[Brewhouse("brewhouse", yield_l=1_000, max_brews_per_day=3)],
        bottling_lines=[BottlingLine("line", run_volume_l=math.inf, max_runs_per_day=3)],
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
                fermenter=brewery.vessels[fermenter],
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
