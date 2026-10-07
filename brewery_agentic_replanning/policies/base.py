from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from brewery_agentic_replanning.signals import Signal
    from brewery_agentic_replanning.simulation import BreweryDigitalTwin


class ReplanningPolicy(ABC):
    """Decides when the digital twin invokes the scheduler.

    For every signal due on a simulation day, the twin applies it through
    its agent and calls :meth:`observe_signal`. If the agent reported an
    effect, or ``always_consider_signals`` is set, it then calls
    :meth:`register_signal` and :meth:`should_replan_immediately`. After
    the day's signals it calls :meth:`should_replan_end_of_day`, and after
    every scheduler invocation :meth:`on_replanned`.
    """

    always_consider_signals: ClassVar[bool] = False

    def bind_twin(self, twin: BreweryDigitalTwin) -> None:
        return None

    def observe_signal(self, day: int, signal: Signal, had_effect: bool) -> None:
        return None

    def register_signal(self, day: int) -> None:
        return None

    @abstractmethod
    def should_replan_immediately(self, day: int) -> bool:
        raise NotImplementedError

    @abstractmethod
    def should_replan_end_of_day(self, day: int) -> bool:
        raise NotImplementedError

    def on_replanned(self, day: int) -> None:
        return None
