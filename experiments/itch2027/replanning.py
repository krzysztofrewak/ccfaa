"""The replan of a baseline after fermentation delays, as the digital twin makes it."""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass

from brewery_schedule_digital_twin.agents import default_agents
from brewery_schedule_digital_twin.domain import Brewery, Schedule
from brewery_schedule_digital_twin.policies import ManualPolicy
from brewery_schedule_digital_twin.scheduling import HeuristicScheduler, Scheduler, SchedulingError, repair_cost
from brewery_schedule_digital_twin.signals import FermentationDelaySignal
from brewery_schedule_digital_twin.simulation import BreweryDigitalTwin
from experiments.itch2027.measures import ScheduleChange, moved_locked_batches, replanning_problems


@dataclass(frozen=True)
class Repair:
    """A replan on ``day`` of a copy of ``baseline`` after ``delays``.

    ``before`` is the schedule as it stood on that day, delays included;
    ``replanned`` is None if the scheduler found no schedule, with its
    ``error``. ``scheduler_seconds`` times the scheduler alone,
    ``wall_seconds`` also the twin and the delays.
    """

    brewery: Brewery
    baseline: Schedule
    delays: tuple[FermentationDelaySignal, ...]
    day: int
    before: Schedule
    replanned: Schedule | None
    error: str | None
    scheduler_seconds: float
    wall_seconds: float

    @classmethod
    def run(
        cls,
        brewery: Brewery,
        baseline: Schedule,
        day: int,
        delays: Sequence[FermentationDelaySignal] = (),
        scheduler: Scheduler | None = None,
    ) -> Repair:
        start = time.perf_counter()
        twin = BreweryDigitalTwin(
            brewery, baseline.clone(), scheduler or HeuristicScheduler(), ManualPolicy(), default_agents()
        )
        for delay in delays:
            twin.inject_signal(delay)
        twin.advance_to(day)
        before = twin.schedule.clone()
        solve_start = time.perf_counter()
        replanned: Schedule | None = None
        message: str | None = None
        try:
            twin.replan_now()
        except SchedulingError as error:
            message = str(error)
        else:
            replanned = twin.schedule
        end = time.perf_counter()
        return cls(brewery, baseline, tuple(delays), day, before, replanned, message, end - solve_start, end - start)

    @property
    def feasible(self) -> bool:
        return self.replanned is not None

    def repaired(self) -> Schedule:
        if self.replanned is None:
            raise ValueError(f"the replan on day {self.day} found no schedule: {self.error}")
        return self.replanned

    def problems(self) -> list[str]:
        """What makes the replanned schedule invalid, locked batches moved included."""
        repaired = self.repaired()
        return replanning_problems(self.brewery, repaired) + moved_locked_batches(self.baseline, repaired, self.day)

    def cost(self) -> int:
        return repair_cost(self.before, self.repaired(), self.day)

    def change(self, horizon_days: int) -> ScheduleChange:
        delay = self.delays[0] if self.delays else None
        return ScheduleChange.of(self.brewery, self.baseline, self.repaired(), self.day, horizon_days, delay)
