from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass, field
from itertools import pairwise
from typing import TYPE_CHECKING

from brewery_schedule_digital_twin.domain.batches import Batch, Brew, FermentationAssignment

if TYPE_CHECKING:
    from brewery_schedule_digital_twin.domain.equipment import Brewery
    from brewery_schedule_digital_twin.domain.recipes import Recipe


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

    def brew_volumes(self) -> dict[Brew, float]:
        volumes: dict[Brew, float] = {}
        for assignment in self.assignments:
            for fill in assignment.effective_fills:
                volumes[fill.brew] = volumes.get(fill.brew, 0.0) + fill.volume_hl
        return volumes

    def brews_on(self, day: int) -> int:
        brews: set[tuple[str, int]] = set()
        for assignment in self.assignments:
            if not assignment.fills:
                if assignment.start_day == day:
                    brews.add((assignment.batch_id, 1))
                continue
            brews.update((assignment.batch_id, fill.brew.number) for fill in assignment.fills if fill.brew.day == day)
        return len(brews)

    def brews_in(self, brewery: Brewery, brewhouse: str, day: int) -> int:
        """Brews made in ``brewhouse`` on ``day``."""
        if len(brewery.brewhouses) == 1:
            return self.brews_on(day) if brewery.brewhouses[0].name == brewhouse else 0
        brews: set[tuple[str, int]] = set()
        for assignment in self.assignments:
            if not assignment.fills:
                if assignment.start_day == day and brewery.brewhouse(None).name == brewhouse:
                    brews.add((assignment.batch_id, 1))
                continue
            brews.update(
                (assignment.batch_id, fill.brew.number)
                for fill in assignment.fills
                if fill.brew.day == day and brewery.brewhouse(fill.brew.brewhouse).name == brewhouse
            )
        return len(brews)

    def bottling_runs_on(self, brewery: Brewery, line: str, day: int) -> int:
        """Bottling runs on ``line`` on ``day``, counting only assignments with a bottling day set."""
        if len(brewery.bottling_lines) == 1:
            if brewery.bottling_lines[0].name != line:
                return 0
            return sum(
                a.bottling_runs.count(day) if a.bottling_runs else a.bottling_day == day for a in self.assignments
            )
        runs = 0
        for assignment in self.assignments:
            if assignment.bottling_runs:
                if day in assignment.bottling_runs and brewery.bottling_line(assignment.bottling_line).name == line:
                    runs += assignment.bottling_runs.count(day)
            elif assignment.bottling_day == day and brewery.bottling_line(assignment.bottling_line).name == line:
                runs += 1
        return runs

    def unscheduled_batches(self) -> list[Batch]:
        scheduled_ids = {a.batch_id for a in self.assignments}
        return [batch for batch in self.batches.values() if batch.id not in scheduled_ids]

    def has_scheduled_work_on(self, day: int) -> bool:
        return any(day in a.fill_days or day in a.effective_bottling_runs for a in self.assignments)

    def horizon_days(self) -> int:
        if not self.assignments:
            return 0
        return max(a.end_day for a in self.assignments)

    def lock_started_assignments(self, current_day: int) -> None:
        """Locks every assignment of a batch once any of them has started."""
        started = {a.batch_id for a in self.assignments if a.has_started(current_day)}
        for assignment in self.assignments:
            if assignment.batch_id in started:
                assignment.locked = True

    def clone(self) -> Schedule:
        return Schedule(
            batches=dict(self.batches),
            assignments=[dataclasses.replace(a) for a in self.assignments],
        )

    def validate(self, brewery: Brewery) -> list[str]:
        problems: list[str] = []

        assignments_by_batch: dict[str, list[FermentationAssignment]] = {}
        for assignment in self.assignments:
            assignments_by_batch.setdefault(assignment.batch_id, []).append(assignment)

        for batch_id, batch in self.batches.items():
            assignments = assignments_by_batch.get(batch_id, [])
            if not assignments:
                problems.append(f"batch {batch_id!r} has no fermenter assignment")
                continue
            brew_days = sorted({day for a in assignments for day in a.fill_days})
            if brew_days[0] != batch.planned_brew_day:
                problems.append(
                    f"batch {batch_id!r} brew day(s) {brew_days} "
                    f"don't start on its declared brew day {batch.planned_brew_day}"
                )
            if brew_days[-1] - brew_days[0] > brewery.max_brew_spread_days:
                problems.append(
                    f"batch {batch_id!r} is brewed over days {brew_days[0]}-{brew_days[-1]}, more than "
                    f"{brewery.max_brew_spread_days} day(s) apart"
                )
            problems += self._brew_identity_problems(assignments)
            base_days = {a.base_fermentation_days for a in assignments}
            if len(base_days) > 1:
                problems.append(
                    f"batch {batch_id!r} has inconsistent fermentation days across its "
                    f"tanks: {sorted(base_days)} (likely a wrong recipe on one of its rows)"
                )
            for day in brew_days:
                if not brewery.is_working_day(day):
                    problems.append(f"batch {batch_id!r} is brewed on day {day}, a day off")
            total_volume = sum(a.volume_hl for a in assignments)
            if batch.volume_hl is not None and not math.isclose(total_volume, batch.volume_hl, abs_tol=1e-9):
                problems.append(
                    f"batch {batch_id!r} requested {batch.volume_hl:g} hl but its assignments total {total_volume:g} hl"
                )
            problems += self._recipe_problems(batch, assignments)
            for assignment in assignments:
                if not 0 < assignment.volume_hl <= assignment.fermenter.capacity_hl:
                    problems.append(
                        f"batch {batch_id!r} puts {assignment.volume_hl:g} hl into {assignment.fermenter.id!r} "
                        f"(capacity {assignment.fermenter.capacity_hl:g} hl)"
                    )

        for fermenter in brewery.vessels:
            on_tank = sorted(self.assignments_on_fermenter(fermenter.id), key=lambda a: a.start_day)
            for earlier, later in pairwise(on_tank):
                if earlier.end_day > later.start_day:
                    problems.append(
                        f"fermenter {fermenter.id!r} is double-booked: batch {earlier.batch_id!r} "
                        f"(days {earlier.start_day}-{earlier.end_day}) overlaps batch {later.batch_id!r} "
                        f"(days {later.start_day}-{later.end_day})"
                    )

        brew_volumes: dict[tuple[str, int, int, str | None], float] = {}
        for assignment in self.assignments:
            if not assignment.fills:
                implicit = (assignment.batch_id, 1, assignment.start_day, None)
                brew_volumes[implicit] = brew_volumes.get(implicit, 0.0) + assignment.volume_hl
            for fill in assignment.fills:
                key = (assignment.batch_id, fill.brew.number, fill.brew.day, fill.brew.brewhouse)
                brew_volumes[key] = brew_volumes.get(key, 0.0) + fill.volume_hl
        brews_by_day: dict[tuple[str, int], set[str]] = {}
        for (batch_id, number, day, name), volume in sorted(
            brew_volumes.items(), key=lambda item: (f"{item[0][0]}/{item[0][1]}", item[0][2])
        ):
            brew_id = f"{batch_id}/{number}"
            try:
                brewhouse = brewery.brewhouse(name)
            except KeyError as error:
                problems.append(f"brew {brew_id!r}: {error.args[0]}")
                continue
            if volume > brewhouse.yield_hl + 1e-9:
                problems.append(
                    f"brew {brew_id!r} totals {volume:g} hl, more than the yield of brewhouse "
                    f"{brewhouse.name!r} ({brewhouse.yield_hl:g} hl)"
                )
            brews_by_day.setdefault((brewhouse.name, day), set()).add(brew_id)
        for (name, day), brew_ids in sorted(brews_by_day.items(), key=lambda item: (item[0][1], item[0][0])):
            limit = brewery.brewhouse(name).max_brews_per_day
            if len(brew_ids) > limit:
                problems.append(
                    f"brewhouse {name!r} has {len(brew_ids)} brews on day {day} (max {limit}): {sorted(brew_ids)}"
                )

        runs_by_day: dict[tuple[str, int], int] = {}
        for assignment in self.assignments:
            try:
                line = brewery.bottling_line(assignment.bottling_line)
            except KeyError as error:
                problems.append(f"batch {assignment.batch_id!r} in {assignment.fermenter.id!r}: {error.args[0]}")
                continue
            needed = line.runs_for(assignment.volume_hl)
            if len(assignment.effective_bottling_runs) < needed:
                problems.append(
                    f"batch {assignment.batch_id!r} in {assignment.fermenter.id!r} needs {needed} bottling runs "
                    f"on line {line.name!r} but has {len(assignment.effective_bottling_runs)}"
                )
            for day in assignment.effective_bottling_runs:
                runs_by_day[(line.name, day)] = runs_by_day.get((line.name, day), 0) + 1
        for (name, day), runs in sorted(runs_by_day.items(), key=lambda item: (item[0][1], item[0][0])):
            limit = brewery.bottling_line(name).max_runs_per_day
            if runs > limit:
                problems.append(f"line {name!r} would need {runs} bottling runs on day {day} (max {limit})")

        return problems

    @staticmethod
    def _brew_identity_problems(assignments: list[FermentationAssignment]) -> list[str]:
        """Brews poured into several vessels on different days or from different brewhouses."""
        if len(assignments) == 1 and not assignments[0].fills:
            return []
        days_by_brew: dict[str, set[int]] = {}
        brewhouses_by_brew: dict[str, set[str | None]] = {}
        for assignment in assignments:
            for fill in assignment.effective_fills:
                days_by_brew.setdefault(fill.brew.id, set()).add(fill.brew.day)
                brewhouses_by_brew.setdefault(fill.brew.id, set()).add(fill.brew.brewhouse)
        problems = [
            f"brew {brew_id!r} is poured on different days {sorted(days)}"
            for brew_id, days in sorted(days_by_brew.items())
            if len(days) > 1
        ]
        problems += [
            f"brew {brew_id!r} comes from different brewhouses {sorted(map(str, names))}"
            for brew_id, names in sorted(brewhouses_by_brew.items())
            if len(names) > 1
        ]
        return problems

    @staticmethod
    def _recipe_problems(batch: Batch, assignments: list[FermentationAssignment]) -> list[str]:
        """Brews of other recipes than the batch's own, unless declared in its blend."""
        if batch.blend is None:
            others = {
                fill.brew.recipe: None
                for assignment in assignments
                for fill in assignment.fills
                if fill.brew.recipe is not None and fill.brew.recipe != batch.recipe
            }
            return [
                f"batch {batch.id!r} of {batch.recipe.name!r} has brews of {recipe.name!r} but declares no blend"
                for recipe in others
            ]

        volumes: dict[Recipe, float] = {}
        for assignment in assignments:
            for fill in assignment.effective_fills:
                recipe = batch.recipe_of(fill.brew)
                volumes[recipe] = volumes.get(recipe, 0.0) + fill.volume_hl

        problems = [
            f"batch {batch.id!r} has brews of {recipe.name!r}, which is not in its blend"
            for recipe in volumes
            if recipe not in batch.blend
        ]
        for recipe, declared in batch.blend.items():
            brewed = volumes.get(recipe, 0.0)
            if not math.isclose(brewed, declared, abs_tol=1e-9):
                problems.append(f"batch {batch.id!r} blends {brewed:g} hl of {recipe.name!r}, declared {declared:g} hl")
        return problems
