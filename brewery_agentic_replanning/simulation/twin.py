from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from brewery_agentic_replanning.agents import Agent
    from brewery_agentic_replanning.domain import Brewery, Schedule
    from brewery_agentic_replanning.policies import ReplanningPolicy
    from brewery_agentic_replanning.scheduling import Scheduler
    from brewery_agentic_replanning.signals import Signal


@dataclass(frozen=True)
class ReplanEvent:
    day: int


class BreweryDigitalTwin:
    def __init__(
        self,
        brewery: Brewery,
        schedule: Schedule,
        scheduler: Scheduler,
        policy: ReplanningPolicy,
        agents: dict[type[Signal], Agent[Any]],
        current_day: int = 0,
    ) -> None:
        self.brewery = brewery
        self.schedule = schedule
        self.original_schedule = schedule.clone()
        self.original_days_off = set(brewery.days_off)
        self.scheduler = scheduler
        self.policy = policy
        self.agents = agents
        self.current_day = current_day
        self.replan_log: list[ReplanEvent] = []
        self.signal_log: list[Signal] = []
        self._last_processed_day = current_day - 1
        self._pending_signals: dict[int, list[Signal]] = {}
        policy.bind_twin(self)

    def inject_signal(self, signal: Signal) -> None:
        if signal.day <= self._last_processed_day:
            raise ValueError(f"cannot inject a signal for day {signal.day}, already at day {self.current_day}")
        self._pending_signals.setdefault(signal.day, []).append(signal)

    def advance_to(self, target_day: int) -> None:
        if target_day <= self._last_processed_day:
            raise ValueError(f"cannot rewind from day {self.current_day} to {target_day}")
        for day in range(self._last_processed_day + 1, target_day + 1):
            self._process_day(day)
            self._last_processed_day = day
            self.current_day = day

    def replan_now(self) -> None:
        self._replan(self.current_day)

    def _process_day(self, day: int) -> None:
        self.schedule.lock_started_assignments(day)
        for signal in self._pending_signals.pop(day, []):
            had_effect = self._apply_signal(signal)
            self.policy.observe_signal(day, signal, had_effect)
            if had_effect or self.policy.always_consider_signals:
                self.policy.register_signal(day)
                if self.policy.should_replan_immediately(day):
                    self._replan(day)

        if self.policy.should_replan_end_of_day(day):
            self._replan(day)

    def _apply_signal(self, signal: Signal) -> bool:
        agent = self.agents.get(type(signal))
        if agent is None:
            raise KeyError(f"no agent registered for signal type {type(signal).__name__}")
        had_effect = agent.apply(self.brewery, self.schedule, signal)
        self.signal_log.append(signal)
        return had_effect

    def _replan(self, day: int) -> None:
        self.schedule = self.scheduler.replan(self.brewery, self.schedule, day)
        self.replan_log.append(ReplanEvent(day=day))
        self.policy.on_replanned(day)
