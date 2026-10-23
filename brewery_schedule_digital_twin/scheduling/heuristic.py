from __future__ import annotations

import math
from collections import Counter
from typing import TYPE_CHECKING

from brewery_schedule_digital_twin.domain.batches import Brew, FermentationAssignment, Fill
from brewery_schedule_digital_twin.domain.equipment import VesselRole
from brewery_schedule_digital_twin.domain.schedule import Schedule
from brewery_schedule_digital_twin.scheduling.base import Scheduler, SchedulingError

if TYPE_CHECKING:
    from brewery_schedule_digital_twin.domain import Batch, BottlingLine, Brewery, Brewhouse, Recipe, Vessel

BottlingPlan = list[tuple[str | None, tuple[int, ...]]]
"""The bottling line (None for a brewery's only one) and run days of each vessel."""


class HeuristicScheduler(Scheduler):
    """Greedy list scheduler.

    Locked assignments keep their fermenter and brew day; only their
    bottling is moved if it has become infeasible. The remaining batches
    are placed in priority order: batches with a target bottling day
    first, by target, then the rest by planned brew day. A batch keeps its
    existing placement while that placement is still feasible. Otherwise
    it is placed on the earliest feasible brew day, in the smallest
    fermenter that holds its volume or, failing that, in the largest-first
    combination of fermenters. Bottling starts on the last fermentation
    day, or on the first later day on which it is feasible.

    The whole fermentation takes place in one vessel, so only vessels that
    have the FULL role are used.

    A batch larger than the largest brew is brewed in the fewest equal
    brews, on the earliest first brew day from which all of them fit, as
    close together as the brewhouses allow and at most
    ``Brewery.max_brew_spread_days`` apart. Each brew goes to the smallest
    brewhouse that makes it and has a free slot that day. Its vessels are
    chosen as for a single brew and filled with the brews in order, so
    several brews can be merged into one vessel and a brew can be split
    between two.

    A blend is brewed recipe by recipe, each in the fewest equal brews, and
    every brew is spread over the batch's vessels in proportion to their
    volumes, so each vessel holds the declared blend.

    Each vessel is bottled on the line that finishes it soonest (in the
    fewest runs on a tie), in as many runs a day as the line has free, so bottling is as short as
    possible. Bottling starts on the same day for every vessel of a batch,
    unless the batch needs more runs than all lines take a day: then only
    its first vessel starts that day and each further vessel starts on the
    first day its line has a free run.
    A target bottling day is the day bottling ends: bottling starts on the
    latest day from which its last run falls on the target.

    A locked vessel keeps its bottling if it has started or still follows
    the end of fermentation, on working days and within the line's runs;
    the others are rebottled in order of fermentation end, each on the
    first day it fits.
    """

    def __init__(self, max_horizon_days: int = 3650) -> None:
        self._max_horizon_days = max_horizon_days

    def replan(self, brewery: Brewery, schedule: Schedule, current_day: int) -> Schedule:
        schedule.lock_started_assignments(current_day)

        locked = [a for a in schedule.assignments if a.locked]
        self._reconcile_locked_bottling_days(brewery, locked, current_day)

        new_schedule = Schedule(batches=dict(schedule.batches))
        for assignment in locked:
            new_schedule.add_assignment(assignment)

        locked_batch_ids = {a.batch_id for a in locked}
        pending_batches = [b for b in schedule.batches.values() if b.id not in locked_batch_ids]
        pending = sorted(pending_batches, key=self._priority_key)
        for batch in pending:
            existing = schedule.assignments_of(batch.id)
            fermentation_days = existing[0].base_fermentation_days if existing else batch.recipe.fermentation_days
            if not self._keep_existing_placement(
                brewery, new_schedule, batch, existing, fermentation_days, current_day
            ):
                volume = self._batch_volume(batch, existing, brewery.kettle_yield)
                brews = self._brew_plan(batch, volume, brewery.kettle_yield)
                self._schedule_batch(brewery, new_schedule, batch, current_day, fermentation_days, brews)

        return new_schedule

    @staticmethod
    def _batch_volume(batch: Batch, existing: list[FermentationAssignment], kettle_yield: float) -> float:
        if existing:
            volume = sum(a.volume for a in existing)
        elif batch.volume is not None:
            volume = batch.volume
        else:
            volume = kettle_yield
        return float(volume)

    @staticmethod
    def _brew_plan(batch: Batch, volume: float, kettle_yield: float) -> list[tuple[Recipe | None, float]]:
        """The recipe and volume of each brew: the fewest equal brews per recipe."""
        components: dict[Recipe | None, float] = {None: volume}
        if batch.blend is not None:
            components = {recipe: part for recipe, part in batch.blend.items()}
        plan: list[tuple[Recipe | None, float]] = []
        for recipe, component in components.items():
            count = math.ceil(component / kettle_yield - 1e-9)
            plan += [(recipe, component / count)] * count
        return plan

    def _keep_existing_placement(
        self,
        brewery: Brewery,
        schedule: Schedule,
        batch: Batch,
        existing: list[FermentationAssignment],
        fermentation_days: int,
        current_day: int,
    ) -> bool:
        if not existing:
            return False

        brews = {fill.brew.number: fill.brew for a in existing for fill in a.effective_fills}
        try:
            brews_per_slot = Counter((brewery.brewhouse(brew.brewhouse), brew.day) for brew in brews.values())
        except KeyError:
            return False
        brew_days = {day for _, day in brews_per_slot}
        brew_day = min(brew_days)
        if brew_day < max(current_day, batch.planned_brew_day):
            return False
        for (brewhouse, day), count in brews_per_slot.items():
            if not brewery.is_working_day(day):
                return False
            if schedule.brews_in(brewery, brewhouse.name, day) + count > brewhouse.max_brews_per_day:
                return False

        fermenters = [a.fermenter for a in existing]
        natural_end_day = max(brew_days) + fermentation_days
        if not self._still_free(brewery, schedule, fermenters, brew_day, natural_end_day):
            return False

        bottling = self._bottling_for_fixed_fermenters(
            brewery,
            schedule,
            [(a.fermenter, a.volume) for a in existing],
            brew_day,
            natural_end_day,
            batch.target_bottling_day,
        )
        if bottling is None:
            return False
        _, plan = bottling

        for assignment, (line, runs) in zip(existing, plan, strict=True):
            schedule.add_assignment(
                FermentationAssignment(
                    batch_id=batch.id,
                    fermenter=assignment.fermenter,
                    volume=assignment.volume,
                    start_day=assignment.start_day,
                    base_fermentation_days=fermentation_days,
                    bottling_day=runs[0],
                    fills=assignment.fills,
                    bottling_line=line,
                    bottling_runs=self._stored_runs(runs),
                )
            )
        return True

    def _bottling_for_fixed_fermenters(
        self,
        brewery: Brewery,
        schedule: Schedule,
        vessels: list[tuple[Vessel, float]],
        brew_day: int,
        natural_end_day: int,
        target_bottling_day: int | None,
    ) -> tuple[int, BottlingPlan] | None:
        fermenters = [vessel for vessel, _ in vessels]
        last_fermentation_day = natural_end_day - 1

        if target_bottling_day is not None:
            if target_bottling_day < last_fermentation_day or not brewery.is_working_day(target_bottling_day):
                return None
            if target_bottling_day > last_fermentation_day and not self._still_free(
                brewery, schedule, fermenters, brew_day, target_bottling_day + 1
            ):
                return None
            return self._bottling_ending_on(brewery, schedule, vessels, last_fermentation_day, target_bottling_day)

        bottling_day = last_fermentation_day
        for _ in range(self._max_horizon_days):
            if bottling_day > last_fermentation_day and not self._still_free(
                brewery, schedule, fermenters, brew_day, bottling_day + 1
            ):
                return None
            if brewery.is_working_day(bottling_day):
                plan = self._plan_bottling(brewery, schedule, vessels, bottling_day)
                if plan is not None:
                    if not self._free_until_bottled(brewery, schedule, fermenters, brew_day, plan):
                        return None
                    return bottling_day, plan
            bottling_day += 1
        return None

    def _reconcile_locked_bottling_days(
        self, brewery: Brewery, locked: list[FermentationAssignment], current_day: int
    ) -> None:
        """Keeps the bottling of locked assignments that started or is still feasible and replans the rest."""
        reconciled = Schedule()
        replanned = []
        for assignment in sorted(locked, key=lambda a: a.natural_end_day):
            if self._keeps_bottling(brewery, reconciled, assignment, current_day):
                reconciled.add_assignment(assignment)
            else:
                replanned.append(assignment)
        for assignment in replanned:
            earliest = assignment.last_fermentation_day
            if assignment.bottling_day is not None:
                earliest = max(earliest, assignment.bottling_day)
            for day in range(earliest, earliest + self._max_horizon_days):
                if not brewery.is_working_day(day):
                    continue
                plan = self._plan_bottling(brewery, reconciled, [(assignment.fermenter, assignment.volume)], day)
                if plan is None:
                    continue
                [(line, runs)] = plan
                assignment.bottling_day = day
                assignment.bottling_line = line
                assignment.bottling_runs = self._stored_runs(runs)
                break
            else:
                raise SchedulingError(
                    f"could not find a feasible bottling day for already-fermenting batch {assignment.batch_id!r}"
                )
            reconciled.add_assignment(assignment)

    @staticmethod
    def _keeps_bottling(
        brewery: Brewery, reconciled: Schedule, assignment: FermentationAssignment, current_day: int
    ) -> bool:
        """Whether the bottling already started, or still follows fermentation and fits the line and working days."""
        if assignment.bottling_day is None:
            return False
        if assignment.bottling_day < current_day:
            return True
        if assignment.bottling_day < assignment.last_fermentation_day:
            return False
        try:
            line = brewery.bottling_line(assignment.bottling_line)
        except KeyError:
            return False
        runs = assignment.effective_bottling_runs
        return (
            len(runs) >= line.runs_for(assignment.volume)
            and all(brewery.is_working_day(day) for day in runs)
            and all(
                reconciled.bottling_runs_on(brewery, line.name, day) + runs.count(day) <= line.max_runs_per_day
                for day in set(runs)
            )
        )

    @staticmethod
    def _priority_key(batch: Batch) -> tuple[int, int, int]:
        has_deadline = 0 if batch.target_bottling_day is not None else 1
        deadline = batch.target_bottling_day if batch.target_bottling_day is not None else 0
        return (has_deadline, deadline, batch.planned_brew_day)

    def _schedule_batch(
        self,
        brewery: Brewery,
        schedule: Schedule,
        batch: Batch,
        current_day: int,
        fermentation_days: int,
        brew_plan: list[tuple[Recipe | None, float]],
    ) -> None:
        earliest_day = max(current_day, batch.planned_brew_day)
        total = sum(volume for _, volume in brew_plan)

        for first_day in range(earliest_day, earliest_day + self._max_horizon_days):
            slots = self._brew_slots(brewery, schedule, first_day, [volume for _, volume in brew_plan])
            if slots is None:
                continue

            natural_end_day = max(day for day, _ in slots) + fermentation_days
            slot = self._find_bottling_slot(
                brewery, schedule, total, first_day, natural_end_day, batch.target_bottling_day
            )
            if slot is None:
                continue
            _, fermenters, plan = slot

            brews = [
                (Brew(batch.id, number, day, recipe, brewhouse), volume)
                for number, ((day, brewhouse), (recipe, volume)) in enumerate(
                    zip(slots, brew_plan, strict=True), start=1
                )
            ]
            self._commit(schedule, batch, fermenters, brews, plan, fermentation_days)
            return

        raise SchedulingError(f"could not find a feasible schedule for batch {batch.id!r}")

    @staticmethod
    def _brew_slots(
        brewery: Brewery, schedule: Schedule, first_day: int, volumes: list[float]
    ) -> list[tuple[int, str | None]] | None:
        """The day and brewhouse of each brew, or None if the brews don't fit from ``first_day`` on.

        Each brew takes the earliest day within the spread on which a
        brewhouse that makes it has a free slot, in the smallest such
        brewhouse; the first brew day must be ``first_day``. The brewhouse
        is None in a brewery with only one.
        """
        if not brewery.is_working_day(first_day):
            return None
        if len(volumes) == 1 and len(brewery.brewhouses) == 1:
            # The common case: one brew in the only brewhouse, which makes any planned brew.
            if schedule.brews_on(first_day) >= brewery.brewhouses[0].max_brews_per_day:
                return None
            return [(first_day, None)]
        free: dict[tuple[str, int], int] = {}

        def has_room(brewhouse: Brewhouse, day: int) -> bool:
            if (brewhouse.name, day) not in free:
                free[(brewhouse.name, day)] = brewhouse.max_brews_per_day - schedule.brews_in(
                    brewery, brewhouse.name, day
                )
            return free[(brewhouse.name, day)] > 0

        if not any(has_room(brewhouse, first_day) for brewhouse in brewery.brewhouses):
            return None
        days = [first_day]
        if len(volumes) > 1:
            last_day = first_day + brewery.max_brew_spread_days
            days += [d for d in range(first_day + 1, last_day + 1) if brewery.is_working_day(d)]
        slots: list[tuple[int, str | None]] = []
        for volume in volumes:
            brewhouses = sorted(
                (b for b in brewery.brewhouses if b.kettle_yield >= volume - 1e-9), key=lambda b: b.kettle_yield
            )
            slot = next(
                ((day, brewhouse) for day in days for brewhouse in brewhouses if has_room(brewhouse, day)), None
            )
            if slot is None:
                return None
            day, brewhouse = slot
            free[(brewhouse.name, day)] -= 1
            slots.append((day, brewhouse.name if len(brewery.brewhouses) > 1 else None))
        if min(day for day, _ in slots) != first_day:
            return None
        return slots

    def _plan_bottling(
        self, brewery: Brewery, schedule: Schedule, vessels: list[tuple[Vessel, float]], start_day: int
    ) -> BottlingPlan | None:
        """The line and run days of each vessel, starting on ``start_day``, or None.

        Each vessel goes to the line that finishes it soonest, in the fewest
        runs on a tie, taking every free run of that line on each working
        day. Every vessel starts on ``start_day`` unless the vessels need
        more runs than the lines take a day; then only the first one does.
        The line is None in a brewery with only one.
        """
        used: Counter[tuple[str, int]] = Counter()
        fewest_runs = sum(min(line.runs_for(volume) for line in brewery.bottling_lines) for _, volume in vessels)
        together = fewest_runs <= brewery.max_bottling_runs_per_day

        def free(line: BottlingLine, day: int) -> int:
            return line.max_runs_per_day - schedule.bottling_runs_on(brewery, line.name, day) - used[(line.name, day)]

        plan: BottlingPlan = []
        for index, (_, volume) in enumerate(vessels):
            best: tuple[BottlingLine, list[int]] | None = None
            for line in brewery.bottling_lines:
                if (together or index == 0) and free(line, start_day) <= 0:
                    continue
                needed = line.runs_for(volume)
                runs: list[int] = []
                day = start_day
                while len(runs) < needed and day < start_day + self._max_horizon_days:
                    if brewery.is_working_day(day):
                        runs += [day] * min(max(0, free(line, day)), needed - len(runs))
                    day += 1
                if len(runs) == needed and (best is None or (runs[-1], len(runs)) < (best[1][-1], len(best[1]))):
                    best = (line, runs)
            if best is None:
                return None
            line, runs = best
            for day in runs:
                used[(line.name, day)] += 1
            plan.append((line.name if len(brewery.bottling_lines) > 1 else None, tuple(runs)))
        return plan

    def _bottling_ending_on(
        self,
        brewery: Brewery,
        schedule: Schedule,
        vessels: list[tuple[Vessel, float]],
        last_fermentation_day: int,
        target_bottling_day: int,
    ) -> tuple[int, BottlingPlan] | None:
        """The latest start from which bottling ends on ``target_bottling_day``, or None.

        The vessels are already known to be free until the target.
        """
        for start in range(target_bottling_day, last_fermentation_day - 1, -1):
            if not brewery.is_working_day(start):
                continue
            plan = self._plan_bottling(brewery, schedule, vessels, start)
            if plan is not None and self._end_of(plan) == target_bottling_day + 1:
                return start, plan
        return None

    @staticmethod
    def _end_of(plan: BottlingPlan) -> int:
        """The day after the last bottling run."""
        return max(runs[-1] for _, runs in plan) + 1

    @staticmethod
    def _free_until_bottled(
        brewery: Brewery, schedule: Schedule, fermenters: list[Vessel], brew_day: int, plan: BottlingPlan
    ) -> bool:
        """Whether the vessels stay free until bottling ends.

        Callers have already checked them up to the day after the first
        run, so only bottling over several days needs a check.
        """
        first_day = min(runs[0] for _, runs in plan)
        end = HeuristicScheduler._end_of(plan)
        return end <= first_day + 1 or HeuristicScheduler._still_free(brewery, schedule, fermenters, brew_day, end)

    @staticmethod
    def _stored_runs(runs: tuple[int, ...]) -> tuple[int, ...]:
        """Run days as stored on an assignment: none for a single run on the bottling day."""
        return runs if len(runs) > 1 else ()

    @staticmethod
    def _allocations(fermenters: list[Vessel], volume: float) -> list[float]:
        allocations: list[float] = []
        remaining = volume
        for fermenter in fermenters:
            allocations.append(min(remaining, fermenter.capacity))
            remaining -= allocations[-1]
        return allocations

    @staticmethod
    def _filled(fermenters: list[Vessel], volume: float) -> list[tuple[Vessel, float]]:
        return list(zip(fermenters, HeuristicScheduler._allocations(fermenters, volume), strict=True))

    def _find_bottling_slot(
        self,
        brewery: Brewery,
        schedule: Schedule,
        volume: float,
        brew_day: int,
        natural_end_day: int,
        target_bottling_day: int | None,
    ) -> tuple[int, list[Vessel], BottlingPlan] | None:
        last_fermentation_day = natural_end_day - 1
        fermenters = self._find_fermenters(brewery, schedule, volume, brew_day, natural_end_day)
        if fermenters is None:
            return None

        if target_bottling_day is not None:
            if target_bottling_day < last_fermentation_day or not brewery.is_working_day(target_bottling_day):
                return None
            if target_bottling_day > last_fermentation_day and not self._still_free(
                brewery, schedule, fermenters, brew_day, target_bottling_day + 1
            ):
                fermenters = self._find_fermenters(brewery, schedule, volume, brew_day, target_bottling_day + 1)
                if fermenters is None:
                    return None
            ending = self._bottling_ending_on(
                brewery, schedule, self._filled(fermenters, volume), last_fermentation_day, target_bottling_day
            )
            if ending is None:
                return None
            return ending[0], fermenters, ending[1]

        bottling_day = last_fermentation_day
        for _ in range(self._max_horizon_days):
            if bottling_day > last_fermentation_day and not self._still_free(
                brewery, schedule, fermenters, brew_day, bottling_day + 1
            ):
                fermenters = self._find_fermenters(brewery, schedule, volume, brew_day, bottling_day + 1)
                if fermenters is None:
                    return None
            if brewery.is_working_day(bottling_day):
                slot = self._bottle_from(brewery, schedule, fermenters, volume, brew_day, bottling_day)
                if slot is not None:
                    return slot
            bottling_day += 1
        return None

    def _bottle_from(
        self,
        brewery: Brewery,
        schedule: Schedule,
        fermenters: list[Vessel],
        volume: float,
        brew_day: int,
        bottling_day: int,
    ) -> tuple[int, list[Vessel], BottlingPlan] | None:
        """Bottling of ``fermenters`` from ``bottling_day``; other vessels if these are busy until it ends."""
        plan = self._plan_bottling(brewery, schedule, self._filled(fermenters, volume), bottling_day)
        if plan is None:
            return None
        if self._free_until_bottled(brewery, schedule, fermenters, brew_day, plan):
            return bottling_day, fermenters, plan
        alternative = self._find_fermenters(brewery, schedule, volume, brew_day, self._end_of(plan))
        if alternative is None:
            return None
        plan = self._plan_bottling(brewery, schedule, self._filled(alternative, volume), bottling_day)
        if plan is None or not self._free_until_bottled(brewery, schedule, alternative, brew_day, plan):
            return None
        return bottling_day, alternative, plan

    @staticmethod
    def _still_free(
        brewery: Brewery, schedule: Schedule, fermenters: list[Vessel], start_day: int, end_day: int
    ) -> bool:
        return all(
            HeuristicScheduler._is_free(brewery, schedule, fermenter, start_day, end_day) for fermenter in fermenters
        )

    @staticmethod
    def _is_free(brewery: Brewery, schedule: Schedule, fermenter: Vessel, start_day: int, end_day: int) -> bool:
        for window_start, window_end in brewery.fermenter_maintenance_windows(fermenter.id):
            if window_start < end_day and start_day < window_end:
                return False
        for assignment in schedule.assignments_on_fermenter(fermenter.id):
            if assignment.start_day < end_day and start_day < assignment.end_day:
                return False
        return True

    def _find_fermenters(
        self,
        brewery: Brewery,
        schedule: Schedule,
        volume: float,
        start_day: int,
        end_day: int,
    ) -> list[Vessel] | None:
        busy = {a.fermenter.id for a in schedule.assignments if a.start_day < end_day and start_day < a.end_day}
        available = [
            fermenter
            for fermenter in brewery.vessels
            if fermenter.has_role(VesselRole.FULL)
            and fermenter.id not in busy
            and not any(
                window_start < end_day and start_day < window_end
                for window_start, window_end in brewery.fermenter_maintenance_windows(fermenter.id)
            )
        ]

        single_fits = [f for f in available if f.capacity >= volume]
        if single_fits:
            return [min(single_fits, key=lambda f: f.capacity)]

        combination: list[Vessel] = []
        remaining = volume
        for fermenter in sorted(available, key=lambda f: f.capacity, reverse=True):
            if remaining <= 0:
                break
            combination.append(fermenter)
            remaining -= fermenter.capacity
        if remaining > 0:
            return None
        return combination

    @staticmethod
    def _commit(
        schedule: Schedule,
        batch: Batch,
        fermenters: list[Vessel],
        brews: list[tuple[Brew, float]],
        bottling: BottlingPlan,
        fermentation_days: int,
    ) -> None:
        total = sum(volume for _, volume in brews)
        allocations = HeuristicScheduler._allocations(fermenters, total)

        if len(brews) == 1 and brews[0][0].brewhouse is None:
            # One brew in the only brewhouse: the implicit fill on start_day says it all.
            fills_by_vessel: list[list[Fill]] = [[] for _ in fermenters]
        elif batch.blend is not None:
            fills_by_vessel = [
                [Fill(brew, volume * allocated / total) for brew, volume in brews] for allocated in allocations
            ]
        else:
            fills_by_vessel = HeuristicScheduler._pour_in_order(brews, allocations)

        brew_day = min(brew.day for brew, _ in brews)
        for fermenter, allocated, fills, (line, runs) in zip(
            fermenters, allocations, fills_by_vessel, bottling, strict=True
        ):
            schedule.add_assignment(
                FermentationAssignment(
                    batch_id=batch.id,
                    fermenter=fermenter,
                    volume=allocated,
                    start_day=min((fill.brew.day for fill in fills), default=brew_day),
                    base_fermentation_days=fermentation_days,
                    bottling_day=runs[0],
                    fills=tuple(fills),
                    bottling_line=line,
                    bottling_runs=HeuristicScheduler._stored_runs(runs),
                )
            )

    @staticmethod
    def _pour_in_order(brews: list[tuple[Brew, float]], allocations: list[float]) -> list[list[Fill]]:
        """Fills each vessel with the brews in order, so a brew may be split between two vessels."""
        unpoured = [volume for _, volume in brews]
        current = 0
        fills_by_vessel: list[list[Fill]] = []
        for room in allocations:
            fills: list[Fill] = []
            while current < len(brews) and room > 1e-9:
                poured = min(unpoured[current], room)
                fills.append(Fill(brews[current][0], poured))
                room -= poured
                unpoured[current] -= poured
                if unpoured[current] <= 1e-9:
                    current += 1
            fills_by_vessel.append(fills)
        return fills_by_vessel
