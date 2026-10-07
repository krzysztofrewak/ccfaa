from __future__ import annotations

from typing import TYPE_CHECKING

from brewery_agentic_replanning.domain.batches import FermentationAssignment
from brewery_agentic_replanning.domain.schedule import Schedule
from brewery_agentic_replanning.scheduling.base import Scheduler, SchedulingError

if TYPE_CHECKING:
    from brewery_agentic_replanning.domain import Batch, Brewery, Fermenter


class HeuristicScheduler(Scheduler):
    """Greedy list scheduler.

    Locked assignments keep their fermenter and brew day; only their
    bottling day is moved if it has become infeasible. The remaining
    batches are placed in priority order: batches with a target bottling
    day first, by target, then the rest by planned brew day. A batch keeps
    its existing placement while that placement is still feasible.
    Otherwise it is placed on the earliest feasible brew day, in the
    smallest fermenter that holds its volume or, failing that, in the
    largest-first combination of fermenters. Bottling takes place on the
    last fermentation day, or on the first later day on which it is
    feasible.
    """

    def __init__(self, max_horizon_days: int = 3650) -> None:
        self._max_horizon_days = max_horizon_days

    def replan(self, brewery: Brewery, schedule: Schedule, current_day: int) -> Schedule:
        schedule.lock_started_assignments(current_day)

        locked = [a for a in schedule.assignments if a.locked]
        self._reconcile_locked_bottling_days(brewery, locked)

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
                volume_hl = self._batch_volume(brewery, batch, existing)
                self._schedule_batch(brewery, new_schedule, batch, current_day, fermentation_days, volume_hl)

        return new_schedule

    @staticmethod
    def _batch_volume(brewery: Brewery, batch: Batch, existing: list[FermentationAssignment]) -> float:
        if existing:
            volume_hl = sum(a.volume_hl for a in existing)
        elif batch.volume_hl is not None:
            volume_hl = batch.volume_hl
        else:
            volume_hl = brewery.kettle_yield_hl
        if not 0 < volume_hl <= brewery.kettle_yield_hl:
            raise SchedulingError(
                f"batch {batch.id!r} has volume {volume_hl} hl, outside (0, kettle yield "
                f"{brewery.kettle_yield_hl}] - one brewing operation can't produce that"
            )
        return float(volume_hl)

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

        brew_day = existing[0].start_day
        if brew_day < max(current_day, batch.planned_brew_day):
            return False
        if not brewery.is_working_day(brew_day):
            return False
        if self._brews_on(schedule, brew_day) >= brewery.max_brews_per_day:
            return False

        fermenters = [a.fermenter for a in existing]
        natural_end_day = brew_day + fermentation_days
        if not self._still_free(brewery, schedule, fermenters, brew_day, natural_end_day):
            return False

        bottling_day = self._bottling_day_for_fixed_fermenters(
            brewery, schedule, fermenters, brew_day, natural_end_day, batch.target_bottling_day
        )
        if bottling_day is None:
            return False

        for assignment in existing:
            schedule.add_assignment(
                FermentationAssignment(
                    batch_id=batch.id,
                    fermenter=assignment.fermenter,
                    volume_hl=assignment.volume_hl,
                    start_day=brew_day,
                    base_fermentation_days=fermentation_days,
                    bottling_day=bottling_day,
                )
            )
        return True

    def _bottling_day_for_fixed_fermenters(
        self,
        brewery: Brewery,
        schedule: Schedule,
        fermenters: list[Fermenter],
        brew_day: int,
        natural_end_day: int,
        target_bottling_day: int | None,
    ) -> int | None:
        last_fermentation_day = natural_end_day - 1

        if target_bottling_day is not None:
            if target_bottling_day < last_fermentation_day or not brewery.is_working_day(target_bottling_day):
                return None
            if target_bottling_day > last_fermentation_day and not self._still_free(
                brewery, schedule, fermenters, brew_day, target_bottling_day + 1
            ):
                return None
            if (
                self._bottling_runs_on(schedule, target_bottling_day) + len(fermenters)
                > brewery.max_bottling_runs_per_day
            ):
                return None
            return target_bottling_day

        bottling_day = last_fermentation_day
        for _ in range(self._max_horizon_days):
            if bottling_day > last_fermentation_day and not self._still_free(
                brewery, schedule, fermenters, brew_day, bottling_day + 1
            ):
                return None
            if (
                brewery.is_working_day(bottling_day)
                and self._bottling_runs_on(schedule, bottling_day) + len(fermenters)
                <= brewery.max_bottling_runs_per_day
            ):
                return bottling_day
            bottling_day += 1
        return None

    def _reconcile_locked_bottling_days(self, brewery: Brewery, locked: list[FermentationAssignment]) -> None:
        reconciled: list[FermentationAssignment] = []
        for assignment in sorted(locked, key=lambda a: a.natural_end_day):
            earliest = assignment.last_fermentation_day
            if assignment.bottling_day is not None:
                earliest = max(earliest, assignment.bottling_day)
            for day in range(earliest, earliest + self._max_horizon_days):
                if not brewery.is_working_day(day):
                    continue
                if self._locked_bottling_runs_on(reconciled, day) >= brewery.max_bottling_runs_per_day:
                    continue
                assignment.bottling_day = day
                break
            else:
                raise SchedulingError(
                    f"could not find a feasible bottling day for already-fermenting batch {assignment.batch_id!r}"
                )
            reconciled.append(assignment)

    @staticmethod
    def _locked_bottling_runs_on(reconciled: list[FermentationAssignment], day: int) -> int:
        return sum(1 for a in reconciled if a.bottling_day == day)

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
        volume_hl: float,
    ) -> None:
        earliest_day = max(current_day, batch.planned_brew_day)

        for brew_day in range(earliest_day, earliest_day + self._max_horizon_days):
            if not brewery.is_working_day(brew_day):
                continue
            if self._brews_on(schedule, brew_day) >= brewery.max_brews_per_day:
                continue

            natural_end_day = brew_day + fermentation_days
            slot = self._find_bottling_slot(
                brewery, schedule, volume_hl, brew_day, natural_end_day, batch.target_bottling_day
            )
            if slot is None:
                continue
            bottling_day, fermenters = slot

            self._commit(schedule, batch, fermenters, volume_hl, brew_day, bottling_day, fermentation_days)
            return

        raise SchedulingError(f"could not find a feasible schedule for batch {batch.id!r}")

    @staticmethod
    def _brews_on(schedule: Schedule, day: int) -> int:
        return len({a.batch_id for a in schedule.assignments if a.start_day == day})

    @staticmethod
    def _bottling_runs_on(schedule: Schedule, day: int) -> int:
        return sum(1 for a in schedule.assignments if a.bottling_day == day)

    def _find_bottling_slot(
        self,
        brewery: Brewery,
        schedule: Schedule,
        volume_hl: float,
        brew_day: int,
        natural_end_day: int,
        target_bottling_day: int | None,
    ) -> tuple[int, list[Fermenter]] | None:
        last_fermentation_day = natural_end_day - 1
        fermenters = self._find_fermenters(brewery, schedule, volume_hl, brew_day, natural_end_day)
        if fermenters is None:
            return None

        if target_bottling_day is not None:
            if target_bottling_day < last_fermentation_day or not brewery.is_working_day(target_bottling_day):
                return None
            if target_bottling_day > last_fermentation_day and not self._still_free(
                brewery, schedule, fermenters, brew_day, target_bottling_day + 1
            ):
                fermenters = self._find_fermenters(brewery, schedule, volume_hl, brew_day, target_bottling_day + 1)
                if fermenters is None:
                    return None
            if (
                self._bottling_runs_on(schedule, target_bottling_day) + len(fermenters)
                > brewery.max_bottling_runs_per_day
            ):
                return None
            return target_bottling_day, fermenters

        bottling_day = last_fermentation_day
        for _ in range(self._max_horizon_days):
            if bottling_day > last_fermentation_day and not self._still_free(
                brewery, schedule, fermenters, brew_day, bottling_day + 1
            ):
                fermenters = self._find_fermenters(brewery, schedule, volume_hl, brew_day, bottling_day + 1)
                if fermenters is None:
                    return None
            if (
                brewery.is_working_day(bottling_day)
                and self._bottling_runs_on(schedule, bottling_day) + len(fermenters)
                <= brewery.max_bottling_runs_per_day
            ):
                return bottling_day, fermenters
            bottling_day += 1
        return None

    @staticmethod
    def _still_free(
        brewery: Brewery, schedule: Schedule, fermenters: list[Fermenter], start_day: int, end_day: int
    ) -> bool:
        return all(
            HeuristicScheduler._is_free(brewery, schedule, fermenter, start_day, end_day) for fermenter in fermenters
        )

    @staticmethod
    def _is_free(brewery: Brewery, schedule: Schedule, fermenter: Fermenter, start_day: int, end_day: int) -> bool:
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
        volume_hl: float,
        start_day: int,
        end_day: int,
    ) -> list[Fermenter] | None:
        available = [
            fermenter
            for fermenter in brewery.fermenters
            if self._is_free(brewery, schedule, fermenter, start_day, end_day)
        ]

        single_fits = [f for f in available if f.capacity_hl >= volume_hl]
        if single_fits:
            return [min(single_fits, key=lambda f: f.capacity_hl)]

        combination: list[Fermenter] = []
        remaining = volume_hl
        for fermenter in sorted(available, key=lambda f: f.capacity_hl, reverse=True):
            if remaining <= 0:
                break
            combination.append(fermenter)
            remaining -= fermenter.capacity_hl
        if remaining > 0:
            return None
        return combination

    @staticmethod
    def _commit(
        schedule: Schedule,
        batch: Batch,
        fermenters: list[Fermenter],
        volume_hl: float,
        brew_day: int,
        bottling_day: int,
        fermentation_days: int,
    ) -> None:
        remaining = volume_hl
        for fermenter in fermenters:
            allocated = min(remaining, fermenter.capacity_hl)
            schedule.add_assignment(
                FermentationAssignment(
                    batch_id=batch.id,
                    fermenter=fermenter,
                    volume_hl=allocated,
                    start_day=brew_day,
                    base_fermentation_days=fermentation_days,
                    bottling_day=bottling_day,
                )
            )
            remaining -= allocated
