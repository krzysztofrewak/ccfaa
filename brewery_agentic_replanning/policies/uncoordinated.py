from __future__ import annotations

from typing import TYPE_CHECKING

from brewery_agentic_replanning.policies.assessing import AssessingPolicy

if TYPE_CHECKING:
    from brewery_agentic_replanning.signals import Signal


class UncoordinatedPolicy(AssessingPolicy):
    """Uncoordinated event-driven replanning: every signal invokes the
    scheduler on arrival, including signals whose agent reported no effect.

    Each signal's assessment is still logged for reporting, but it plays
    no part in the decision.
    """

    always_consider_signals = True

    def __init__(self) -> None:
        super().__init__()
        self._current: Signal | None = None

    def observe_signal(self, day: int, signal: Signal, had_effect: bool) -> None:
        self._assess(signal, day)
        self._current = signal

    def should_replan_immediately(self, day: int) -> bool:
        return True

    def should_replan_end_of_day(self, day: int) -> bool:
        return False

    def on_replanned(self, day: int) -> None:
        if self._current is not None:
            self._record_decision(
                day,
                "REPLAN",
                [self._current],
                "no assessment - every signal replans unconditionally",
                scheduler_invoked=True,
            )
        self._current = None
