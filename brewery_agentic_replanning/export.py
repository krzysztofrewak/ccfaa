from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING

from brewery_agentic_replanning.metrics import compute_metrics

if TYPE_CHECKING:
    from brewery_agentic_replanning.domain import Brewery, FermentationAssignment, Fermenter, Schedule
    from brewery_agentic_replanning.signals import Signal


class CalendarExporter:
    def export(
        self,
        path: str | Path,
        brewery: Brewery,
        original: Schedule,
        replanned: Schedule,
        current_day: int,
        signals: Sequence[Signal] = (),
        replan_count: int = 0,
        original_days_off: set[int] | None = None,
    ) -> None:
        start_day, end_day = self._day_bounds(original, replanned, current_day)
        horizon = end_day - start_day
        metrics = compute_metrics(brewery, original, replanned, current_day)
        batch_ids = sorted(set(original.batches) | set(replanned.batches))

        payload = {
            "current_day": current_day,
            "start_day": start_day,
            "horizon_days": horizon,
            "original_days_off": sorted(original_days_off if original_days_off is not None else brewery.days_off),
            "days_off": sorted(brewery.days_off),
            "replan_count": replan_count,
            "fermenters": [
                self._fermenter_payload(brewery, fermenter, original, replanned, start_day, horizon, current_day)
                for fermenter in brewery.fermenters
            ],
            "batches": [self._batch_summary(batch_id, original, replanned) for batch_id in batch_ids],
            "signals": [self._signal_payload(signal) for signal in signals],
            "metrics": asdict(metrics),
        }

        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(payload, indent=2))

    @staticmethod
    def _day_bounds(original: Schedule, replanned: Schedule, current_day: int) -> tuple[int, int]:
        days = [current_day, current_day + 1]
        for schedule in (original, replanned):
            for assignment in schedule.assignments:
                days.append(assignment.start_day)
                days.append(assignment.end_day)
        return max(min(days), 0), max(days)

    @staticmethod
    def _signal_payload(signal: Signal) -> dict:
        return {
            "day": signal.day,
            "kind": signal.kind,
            "description": signal.description,
            "related_batch_id": signal.related_batch_id,
        }

    @staticmethod
    def _assignment_status(assignment: FermentationAssignment, current_day: int) -> str:
        if assignment.end_day <= current_day:
            return "completed"
        if assignment.start_day <= current_day:
            return "ongoing"
        return "planned"

    @classmethod
    def _slots_and_status(
        cls, schedule: Schedule, fermenter_id: str, start_day: int, horizon: int, current_day: int
    ) -> tuple[list[str | None], list[str | None], list[bool]]:
        slots: list[str | None] = [None] * horizon
        status: list[str | None] = [None] * horizon
        brew_starts: list[bool] = [False] * horizon
        for assignment in schedule.assignments_on_fermenter(fermenter_id):
            assignment_status = cls._assignment_status(assignment, current_day)
            for day in range(max(assignment.start_day, start_day), assignment.end_day):
                slots[day - start_day] = assignment.batch_id
                status[day - start_day] = assignment_status
            if assignment.start_day >= start_day:
                brew_starts[assignment.start_day - start_day] = True
        return slots, status, brew_starts

    def _fermenter_payload(
        self,
        brewery: Brewery,
        fermenter: Fermenter,
        original: Schedule,
        replanned: Schedule,
        start_day: int,
        horizon: int,
        current_day: int,
    ) -> dict:
        original_slots, original_status, original_brew_starts = self._slots_and_status(
            original, fermenter.id, start_day, horizon, current_day
        )
        new_slots, new_status, new_brew_starts = self._slots_and_status(
            replanned, fermenter.id, start_day, horizon, current_day
        )
        return {
            "name": fermenter.name,
            "index": fermenter.index,
            "capacity": f"{fermenter.capacity_hl:g}hl",
            "maintenance": [
                {"start": start, "end": end} for start, end in brewery.fermenter_maintenance_windows(fermenter.id)
            ],
            "original_slots": original_slots,
            "original_status": original_status,
            "original_brew_starts": original_brew_starts,
            "new_slots": new_slots,
            "new_status": new_status,
            "new_brew_starts": new_brew_starts,
            "changed_slots": [o != n for o, n in zip(original_slots, new_slots, strict=True)],
        }

    @classmethod
    def _batch_summary(cls, batch_id: str, original: Schedule, replanned: Schedule) -> dict:
        batch = replanned.batches.get(batch_id) or original.batches[batch_id]
        original_tanks = cls._tank_summaries(original.assignments_of(batch_id))
        new_tanks = cls._tank_summaries(replanned.assignments_of(batch_id))

        return {
            "id": batch.id,
            "name": batch.name,
            "recipe": batch.recipe.name,
            "expected_fermentation_days": batch.recipe.fermentation_days,
            "original": original_tanks,
            "new": new_tanks,
            "changed": original_tanks != new_tanks,
        }

    @staticmethod
    def _tank_summaries(assignments: list[FermentationAssignment]) -> list[dict]:
        ordered = sorted(assignments, key=lambda a: (a.fermenter.name, a.fermenter.index))
        return [{"fermenter": a.fermenter.id, "start": a.start_day, "end": a.end_day} for a in ordered]
