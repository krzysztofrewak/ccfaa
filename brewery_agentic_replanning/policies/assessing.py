from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from brewery_agentic_replanning.policies.base import ReplanningPolicy

if TYPE_CHECKING:
    from brewery_agentic_replanning.agents import Assessment, Decision
    from brewery_agentic_replanning.signals import Signal
    from brewery_agentic_replanning.simulation import BreweryDigitalTwin


@dataclass(frozen=True)
class AssessmentRecord:
    day: int
    signal: Signal
    assessment: Assessment


@dataclass(frozen=True)
class DecisionRecord:
    day: int
    cycle: int
    decision: Decision
    signals: tuple[Signal, ...]
    reason: str
    scheduler_invoked: bool
    urgent_signals: tuple[Signal, ...] = ()


class AssessingPolicy(ReplanningPolicy):
    def __init__(self) -> None:
        self._twin: BreweryDigitalTwin | None = None
        self.assessments: list[AssessmentRecord] = []
        self.decisions: list[DecisionRecord] = []

    def bind_twin(self, twin: BreweryDigitalTwin) -> None:
        self._twin = twin

    def _assess(self, signal: Signal, day: int) -> Assessment:
        if self._twin is None:
            raise RuntimeError(f"{type(self).__name__} is not bound to a digital twin")
        agent = self._twin.agents[type(signal)]
        assessment = agent.assess(self._twin.brewery, self._twin.schedule, signal, day)
        self.assessments.append(AssessmentRecord(day=day, signal=signal, assessment=assessment))
        return assessment

    def _record_decision(
        self,
        day: int,
        decision: Decision,
        signals: list[Signal],
        reason: str,
        scheduler_invoked: bool,
        urgent_signals: list[Signal] | None = None,
    ) -> None:
        self.decisions.append(
            DecisionRecord(
                day=day,
                cycle=len(self.decisions) + 1,
                decision=decision,
                signals=tuple(signals),
                reason=reason,
                scheduler_invoked=scheduler_invoked,
                urgent_signals=tuple(urgent_signals or ()),
            )
        )
