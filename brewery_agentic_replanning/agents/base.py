from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, Generic, Literal, TypeVar

if TYPE_CHECKING:
    from brewery_agentic_replanning.domain import Brewery, Schedule
    from brewery_agentic_replanning.signals import Signal

Decision = Literal["KEEP", "PENDING", "REPLAN"]
SignalT = TypeVar("SignalT", bound="Signal")


@dataclass(frozen=True)
class Assessment:
    """A domain agent's judgment of one signal against the current state.

    - KEEP: the signal requires no schedule reconstruction.
    - PENDING: reconstruction is required, but can be deferred until
      ``latest_safe_decision_point``.
    - REPLAN: reconstruction is required now.
    """

    source_agent: str
    decision: Decision
    reason: str
    latest_safe_decision_point: int | None = None
    affected_activity: str | None = None
    affected_resource: str | None = None


class Agent(ABC, Generic[SignalT]):
    signal_type: ClassVar[type[Signal]]

    @property
    def organizational_function(self) -> str:
        return type(self).__name__

    @abstractmethod
    def apply(self, brewery: Brewery, schedule: Schedule, signal: SignalT) -> bool:
        """Update the brewery and schedule state with ``signal``.

        Returns whether the signal changed anything a replan could act on.
        """
        raise NotImplementedError

    def assess(self, brewery: Brewery, schedule: Schedule, signal: SignalT, current_day: int) -> Assessment:
        """Judge ``signal`` against the current state without modifying it.

        Called after :meth:`apply` and again on every reassessment, so an
        implementation must not rely on state that :meth:`apply` itself sets.
        The default requires an immediate replan.
        """
        return Assessment(
            source_agent=self.organizational_function,
            decision="REPLAN",
            reason="no domain-specific assessment rule defined for this signal type",
            latest_safe_decision_point=current_day,
        )
