from __future__ import annotations

from typing import TYPE_CHECKING

from brewery_agentic_replanning.policies.assessing import AssessingPolicy

if TYPE_CHECKING:
    from brewery_agentic_replanning.agents import Assessment
    from brewery_agentic_replanning.signals import Signal


class LocallyFilteredPolicy(AssessingPolicy):
    """Locally filtered event-driven replanning.

    Each signal is assessed by its own agent on arrival. KEEP retains the
    current plan; PENDING and REPLAN both invoke the scheduler at once.
    Nothing is deferred, so the policy keeps no pending set and never
    reassesses a signal.
    """

    always_consider_signals = True

    def __init__(self) -> None:
        super().__init__()
        self._replan_immediately = False
        self._relevant: tuple[Signal, Assessment] | None = None

    def observe_signal(self, day: int, signal: Signal, had_effect: bool) -> None:
        assessment = self._assess(signal, day)
        if assessment.decision == "KEEP":
            self._record_decision(day, "KEEP", [signal], assessment.reason, scheduler_invoked=False)
            return
        self._relevant = (signal, assessment)
        self._replan_immediately = True

    def should_replan_immediately(self, day: int) -> bool:
        replan, self._replan_immediately = self._replan_immediately, False
        return replan

    def should_replan_end_of_day(self, day: int) -> bool:
        return False

    def on_replanned(self, day: int) -> None:
        if self._relevant is not None:
            signal, assessment = self._relevant
            if assessment.decision == "REPLAN":
                reason = f"local assessment requires plan revision: {assessment.reason}"
            else:
                reason = (
                    f"local assessment requires plan revision (safely deferrable to day "
                    f"{assessment.latest_safe_decision_point}, not deferred): {assessment.reason}"
                )
            self._record_decision(day, "REPLAN", [signal], reason, scheduler_invoked=True, urgent_signals=[signal])
        self._relevant = None
        self._replan_immediately = False
