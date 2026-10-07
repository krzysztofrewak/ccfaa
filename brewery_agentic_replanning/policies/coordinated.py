from __future__ import annotations

from typing import TYPE_CHECKING

from brewery_agentic_replanning.policies.assessing import AssessingPolicy

if TYPE_CHECKING:
    from brewery_agentic_replanning.agents import Assessment
    from brewery_agentic_replanning.signals import Signal


class CoordinatedPolicy(AssessingPolicy):
    """Coordinated replanning.

    Every signal that is not KEEP joins the active set. The active set is
    reassessed whenever a new signal arrives and at the end of every day;
    a signal reassessed as KEEP leaves it. The scheduler is invoked as
    soon as any active assessment is REPLAN, either on arrival or once its
    latest safe decision point is reached, and the active set is cleared
    after every reconstruction.
    """

    def __init__(self) -> None:
        super().__init__()
        self._active: dict[int, tuple[Signal, Assessment]] = {}
        self._replan_immediately = False
        self._last_reassessed_day: int | None = None
        self._last_reassessed_replan = False

    def observe_signal(self, day: int, signal: Signal, had_effect: bool) -> None:
        assessment = self._assess(signal, day)
        if assessment.decision == "KEEP":
            self._record_decision(day, "KEEP", [signal], assessment.reason, scheduler_invoked=False)
        else:
            self._active[id(signal)] = (signal, assessment)

        replan = assessment.decision == "REPLAN"
        if self._reassess_active(day, exclude=id(signal)):
            replan = True
        self._last_reassessed_day = day
        self._last_reassessed_replan = replan

        if replan:
            self._replan_immediately = True
        elif assessment.decision == "PENDING":
            self._record_decision(
                day,
                "PENDING",
                [signal],
                f"deferred until day {assessment.latest_safe_decision_point}",
                scheduler_invoked=False,
            )

    def should_replan_immediately(self, day: int) -> bool:
        replan, self._replan_immediately = self._replan_immediately, False
        return replan

    def should_replan_end_of_day(self, day: int) -> bool:
        if self._last_reassessed_day == day:
            return self._last_reassessed_replan
        replan = self._reassess_active(day)
        self._last_reassessed_day = day
        self._last_reassessed_replan = replan
        return replan

    def on_replanned(self, day: int) -> None:
        if self._active:
            signals = [signal for signal, _ in self._active.values()]
            urgent = [signal for signal, assessment in self._active.values() if assessment.decision == "REPLAN"]
            if urgent:
                reason = "latest safe point reached: " + ", ".join(signal.kind for signal in urgent)
            else:
                reason = "reassessment found an active constraint newly urgent"
            self._record_decision(day, "REPLAN", signals, reason, scheduler_invoked=True, urgent_signals=urgent)
        self._active.clear()
        self._replan_immediately = False
        self._last_reassessed_day = None
        self._last_reassessed_replan = False

    def _reassess_active(self, day: int, exclude: int | None = None) -> bool:
        still_active: dict[int, tuple[Signal, Assessment]] = {}
        any_replan = False
        for key, (signal, previous) in self._active.items():
            if key == exclude:
                still_active[key] = (signal, previous)
                continue
            assessment = self._assess(signal, day)
            if assessment.decision != "KEEP":
                still_active[key] = (signal, assessment)
                any_replan = any_replan or assessment.decision == "REPLAN"
        self._active = still_active
        return any_replan
