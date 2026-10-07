from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from itertools import pairwise
from typing import TYPE_CHECKING

from brewery_agentic_replanning.domain.batches import Batch, FermentationAssignment

if TYPE_CHECKING:
    from brewery_agentic_replanning.domain.equipment import Brewery


@dataclass
class Schedule:
    batches: dict[str, Batch] = field(default_factory=dict)
    assignments: list[FermentationAssignment] = field(default_factory=list)

    def add_batch(self, batch: Batch) -> None:
        self.batches[batch.id] = batch

    def add_assignment(self, assignment: FermentationAssignment) -> None:
        self.assignments.append(assignment)

    def assignments_of(self, batch_id: str) -> list[FermentationAssignment]:
        return [a for a in self.assignments if a.batch_id == batch_id]

    def assignments_on_fermenter(self, fermenter_id: str) -> list[FermentationAssignment]:
        return [a for a in self.assignments if a.fermenter.id == fermenter_id]

    def assignment_for(self, batch_id: str, fermenter_id: str) -> FermentationAssignment:
        for assignment in self.assignments:
            if assignment.batch_id == batch_id and assignment.fermenter.id == fermenter_id:
                return assignment
        raise KeyError(f"no assignment for batch {batch_id!r} on fermenter {fermenter_id!r}")

    def brew_day_of(self, batch_id: str) -> int:
        assignments = self.assignments_of(batch_id)
        if not assignments:
            raise KeyError(f"batch {batch_id!r} has no assignments yet")
        return min(a.start_day for a in assignments)

    def unscheduled_batches(self) -> list[Batch]:
        scheduled_ids = {a.batch_id for a in self.assignments}
        return [batch for batch in self.batches.values() if batch.id not in scheduled_ids]

    def has_scheduled_work_on(self, day: int) -> bool:
        return any(a.start_day == day or a.effective_bottling_day == day for a in self.assignments)

    def horizon_days(self) -> int:
        if not self.assignments:
            return 0
        return max(a.end_day for a in self.assignments)

    def lock_started_assignments(self, current_day: int) -> None:
        for assignment in self.assignments:
            if assignment.has_started(current_day):
                assignment.locked = True

    def clone(self) -> Schedule:
        return Schedule(
            batches=dict(self.batches),
            assignments=[dataclasses.replace(a) for a in self.assignments],
        )

    def validate(self, brewery: Brewery) -> list[str]:
        problems: list[str] = []

        for batch_id, batch in self.batches.items():
            assignments = self.assignments_of(batch_id)
            if not assignments:
                problems.append(f"batch {batch_id!r} has no fermenter assignment")
                continue
            start_days = {a.start_day for a in assignments}
            if start_days != {batch.planned_brew_day}:
                problems.append(
                    f"batch {batch_id!r} assignment start day(s) {sorted(start_days)} "
                    f"don't match its declared brew day {batch.planned_brew_day}"
                )
            base_days = {a.base_fermentation_days for a in assignments}
            if len(base_days) > 1:
                problems.append(
                    f"batch {batch_id!r} has inconsistent fermentation days across its "
                    f"tanks: {sorted(base_days)} (likely a wrong recipe on one of its rows)"
                )
            if not brewery.is_working_day(batch.planned_brew_day):
                problems.append(f"batch {batch_id!r} is brewed on day {batch.planned_brew_day}, a day off")
            total_volume = sum(a.volume_hl for a in assignments)
            if not 0 < total_volume <= brewery.kettle_yield_hl:
                problems.append(
                    f"batch {batch_id!r} totals {total_volume:g} hl, outside (0, kettle yield "
                    f"{brewery.kettle_yield_hl:g}]"
                )
            if batch.volume_hl is not None and total_volume != batch.volume_hl:
                problems.append(
                    f"batch {batch_id!r} requested {batch.volume_hl:g} hl but its assignments total {total_volume:g} hl"
                )
            for assignment in assignments:
                if not 0 < assignment.volume_hl <= assignment.fermenter.capacity_hl:
                    problems.append(
                        f"batch {batch_id!r} puts {assignment.volume_hl:g} hl into {assignment.fermenter.id!r} "
                        f"(capacity {assignment.fermenter.capacity_hl:g} hl)"
                    )

        for fermenter in brewery.fermenters:
            on_tank = sorted(self.assignments_on_fermenter(fermenter.id), key=lambda a: a.start_day)
            for earlier, later in pairwise(on_tank):
                if earlier.end_day > later.start_day:
                    problems.append(
                        f"fermenter {fermenter.id!r} is double-booked: batch {earlier.batch_id!r} "
                        f"(days {earlier.start_day}-{earlier.end_day}) overlaps batch {later.batch_id!r} "
                        f"(days {later.start_day}-{later.end_day})"
                    )

        brews_by_day: dict[int, set[str]] = {}
        for assignment in self.assignments:
            brews_by_day.setdefault(assignment.start_day, set()).add(assignment.batch_id)
        for day, batch_ids in sorted(brews_by_day.items()):
            if len(batch_ids) > brewery.max_brews_per_day:
                problems.append(
                    f"day {day} has {len(batch_ids)} batches brewing "
                    f"(max {brewery.max_brews_per_day}): {sorted(batch_ids)}"
                )

        bottling_runs_by_day: dict[int, int] = {}
        for assignment in self.assignments:
            day = assignment.effective_bottling_day
            bottling_runs_by_day[day] = bottling_runs_by_day.get(day, 0) + 1
        for day, runs in sorted(bottling_runs_by_day.items()):
            if runs > brewery.max_bottling_runs_per_day:
                problems.append(f"day {day} would need {runs} bottling runs (max {brewery.max_bottling_runs_per_day})")

        return problems
