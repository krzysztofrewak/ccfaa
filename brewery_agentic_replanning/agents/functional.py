from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any

from brewery_agentic_replanning.agents.base import Agent, Assessment
from brewery_agentic_replanning.signals import (
    DayOffSignal,
    FermentationDelaySignal,
    FermenterMaintenanceSignal,
    IngredientShortageSignal,
    NewBatchRequirementSignal,
    Signal,
)

if TYPE_CHECKING:
    from brewery_agentic_replanning.domain import Brewery, FermentationAssignment, Schedule


def _deadline_assessment(
    agent: Agent[Any],
    safe_point: int,
    current_day: int,
    reason: str,
    affected_activity: str | None = None,
    affected_resource: str | None = None,
) -> Assessment:
    return Assessment(
        source_agent=agent.organizational_function,
        decision="REPLAN" if current_day >= safe_point else "PENDING",
        reason=reason,
        latest_safe_decision_point=safe_point,
        affected_activity=affected_activity,
        affected_resource=affected_resource,
    )


def _last_movable_day(start_day: int) -> int:
    return start_day - 1


class FermentationAgent(Agent[FermentationDelaySignal]):
    signal_type = FermentationDelaySignal

    @property
    def organizational_function(self) -> str:
        return "ProductionAgent"

    def apply(self, brewery: Brewery, schedule: Schedule, signal: FermentationDelaySignal) -> bool:
        assignment = schedule.assignment_for(signal.batch_id, signal.fermenter_id)
        assignment.extra_delay_days += signal.extra_days
        return True

    def assess(
        self, brewery: Brewery, schedule: Schedule, signal: FermentationDelaySignal, current_day: int
    ) -> Assessment:
        assignment = schedule.assignment_for(signal.batch_id, signal.fermenter_id)
        if signal.extra_days < 0:
            conflict = self._bottling_conflict(brewery, schedule, assignment)
            if conflict is None:
                return Assessment(
                    source_agent=self.organizational_function,
                    decision="KEEP",
                    reason="earlier completion frees the fermenter sooner, doesn't invalidate the plan",
                )
            return _deadline_assessment(
                self,
                safe_point=assignment.effective_bottling_day - 1,
                current_day=current_day,
                reason=f"earlier completion moves bottling onto {conflict}",
                affected_activity=signal.batch_id,
                affected_resource=signal.fermenter_id,
            )

        colliding = [
            other
            for other in schedule.assignments_on_fermenter(signal.fermenter_id)
            if other is not assignment and not other.has_started(current_day) and other.start_day < assignment.end_day
        ]
        if not colliding:
            return Assessment(
                source_agent=self.organizational_function,
                decision="KEEP",
                reason="later completion doesn't collide with any future modifiable allocation",
                affected_resource=signal.fermenter_id,
            )
        return _deadline_assessment(
            self,
            safe_point=_last_movable_day(min(other.start_day for other in colliding)),
            current_day=current_day,
            reason=f"later completion collides with a future modifiable allocation on {signal.fermenter_id}",
            affected_activity=",".join(sorted({other.batch_id for other in colliding})),
            affected_resource=signal.fermenter_id,
        )

    @staticmethod
    def _bottling_conflict(brewery: Brewery, schedule: Schedule, assignment: FermentationAssignment) -> str | None:
        day = assignment.effective_bottling_day
        if not brewery.is_working_day(day):
            return f"day {day}, a non-working day"
        others = sum(1 for a in schedule.assignments if a is not assignment and a.effective_bottling_day == day)
        if others >= brewery.max_bottling_runs_per_day:
            return f"day {day}, already at its bottling limit ({brewery.max_bottling_runs_per_day} runs)"
        return None


class ProcurementAgent(Agent[IngredientShortageSignal]):
    signal_type = IngredientShortageSignal

    def apply(self, brewery: Brewery, schedule: Schedule, signal: IngredientShortageSignal) -> bool:
        batch = schedule.batches[signal.batch_id]
        if batch.planned_brew_day >= signal.available_from_day:
            return False
        schedule.add_batch(dataclasses.replace(batch, planned_brew_day=signal.available_from_day))
        return True

    def assess(
        self, brewery: Brewery, schedule: Schedule, signal: IngredientShortageSignal, current_day: int
    ) -> Assessment:
        brewed_before_availability = [
            a
            for a in schedule.assignments_of(signal.batch_id)
            if not a.has_started(current_day) and a.start_day < signal.available_from_day
        ]
        if not brewed_before_availability:
            return Assessment(
                source_agent=self.organizational_function,
                decision="KEEP",
                reason="the batch isn't scheduled before the shortage resolves",
                affected_activity=signal.batch_id,
            )
        return _deadline_assessment(
            self,
            safe_point=_last_movable_day(min(a.start_day for a in brewed_before_availability)),
            current_day=current_day,
            reason="shortage outlasts the batch's currently scheduled brew day",
            affected_activity=signal.batch_id,
        )


class SalesAgent(Agent[NewBatchRequirementSignal]):
    signal_type = NewBatchRequirementSignal

    def apply(self, brewery: Brewery, schedule: Schedule, signal: NewBatchRequirementSignal) -> bool:
        schedule.add_batch(signal.batch)
        return True

    def assess(
        self, brewery: Brewery, schedule: Schedule, signal: NewBatchRequirementSignal, current_day: int
    ) -> Assessment:
        batch = signal.batch
        return _deadline_assessment(
            self,
            safe_point=_last_movable_day(batch.planned_brew_day),
            current_day=current_day,
            reason="new order must be scheduled before its requested brewing day",
            affected_activity=batch.id,
        )


class OperationsAgent(Agent[DayOffSignal]):
    signal_type = DayOffSignal

    def apply(self, brewery: Brewery, schedule: Schedule, signal: DayOffSignal) -> bool:
        already_off = not brewery.is_working_day(signal.off_day)
        had_work = schedule.has_scheduled_work_on(signal.off_day)
        brewery.mark_day_off(signal.off_day)
        return had_work and not already_off

    def assess(self, brewery: Brewery, schedule: Schedule, signal: DayOffSignal, current_day: int) -> Assessment:
        colliding = [
            a
            for a in schedule.assignments
            if (a.start_day == signal.off_day and not a.has_started(current_day))
            or (a.effective_bottling_day == signal.off_day and not a.bottling_done(current_day))
        ]
        if not colliding:
            return Assessment(
                source_agent=self.organizational_function,
                decision="KEEP",
                reason="no modifiable brewing or bottling scheduled that day",
            )
        return _deadline_assessment(
            self,
            safe_point=signal.off_day - 1,
            current_day=current_day,
            reason="day off collides with modifiable scheduled brewing or bottling",
            affected_activity=",".join(sorted({a.batch_id for a in colliding})),
        )


class MaintenanceAgent(Agent[FermenterMaintenanceSignal]):
    signal_type = FermenterMaintenanceSignal

    @property
    def organizational_function(self) -> str:
        return "OperationsAgent"

    def apply(self, brewery: Brewery, schedule: Schedule, signal: FermenterMaintenanceSignal) -> bool:
        brewery.mark_fermenter_maintenance(signal.fermenter_id, signal.start_day, signal.duration_days)
        return True

    def assess(
        self, brewery: Brewery, schedule: Schedule, signal: FermenterMaintenanceSignal, current_day: int
    ) -> Assessment:
        window_end = signal.start_day + signal.duration_days
        colliding = [
            a
            for a in schedule.assignments_on_fermenter(signal.fermenter_id)
            if not a.has_started(current_day) and a.start_day < window_end and signal.start_day < a.end_day
        ]
        if not colliding:
            return Assessment(
                source_agent=self.organizational_function,
                decision="KEEP",
                reason="no future modifiable allocation overlaps the maintenance window",
                affected_resource=signal.fermenter_id,
            )
        return _deadline_assessment(
            self,
            safe_point=_last_movable_day(min(a.start_day for a in colliding)),
            current_day=current_day,
            reason=f"maintenance window collides with a future modifiable allocation on {signal.fermenter_id}",
            affected_activity=",".join(sorted({a.batch_id for a in colliding})),
            affected_resource=signal.fermenter_id,
        )


def default_agents() -> dict[type[Signal], Agent[Any]]:
    agents: list[Agent[Any]] = [
        FermentationAgent(),
        ProcurementAgent(),
        SalesAgent(),
        OperationsAgent(),
        MaintenanceAgent(),
    ]
    return {agent.signal_type: agent for agent in agents}
