"""Measures of a baseline schedule and of the changes a replan makes to it."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any

from brewery_schedule_digital_twin.domain import Brewery, FermentationAssignment, Schedule
from brewery_schedule_digital_twin.generation import occupancy
from brewery_schedule_digital_twin.metrics import compute_metrics
from brewery_schedule_digital_twin.signals import FermentationDelaySignal

Row = dict[str, Any]


def fingerprint(schedule: Schedule) -> str:
    """A digest of every assignment, which tells a regenerated baseline from another."""
    rows = sorted(
        (a.batch_id, a.fermenter.id, round(a.volume, 6), a.start_day, a.end_day, a.effective_bottling_day)
        for a in schedule.assignments
    )
    return hashlib.sha256(repr(rows).encode()).hexdigest()[:16]


def replanning_problems(brewery: Brewery, schedule: Schedule) -> list[str]:
    """The problems of a replanned schedule; a brew moved from its batch's declared day is none."""
    return [problem for problem in schedule.validate(brewery) if "declared brew day" not in problem]


def moved_locked_batches(baseline: Schedule, replanned: Schedule, day: int) -> list[str]:
    """The batches started by ``day`` whose vessels, volumes, start days or brew days the replan changed."""
    started = sorted({a.batch_id for a in baseline.assignments if a.has_started(day)})

    def frozen(schedule: Schedule, batch_id: str) -> list[tuple]:
        return sorted(
            (a.fermenter.id, round(a.volume, 6), a.start_day, tuple(sorted(f.brew.day for f in a.effective_fills)))
            for a in schedule.assignments_of(batch_id)
        )

    return [
        f"locked batch {batch_id!r} was moved"
        for batch_id in started
        if frozen(baseline, batch_id) != frozen(replanned, batch_id)
    ]


def split_batches(schedule: Schedule) -> int:
    return sum(1 for batch_id in schedule.batches if len(schedule.assignments_of(batch_id)) > 1)


@dataclass(frozen=True)
class BaselineProfile:
    tanks: int
    capacity: float
    occupancy: float
    problems: int
    batches: int
    brews: int
    volume: float
    split_batches: int
    merged_tanks: int
    makespan: int
    idle_fermenter_days: int

    @classmethod
    def of(cls, brewery: Brewery, schedule: Schedule, horizon_days: int) -> BaselineProfile:
        assignments = schedule.assignments
        return cls(
            tanks=len(brewery.vessels),
            capacity=sum(vessel.capacity for vessel in brewery.vessels),
            occupancy=occupancy(brewery, schedule, horizon_days),
            problems=len(schedule.validate(brewery)),
            batches=len(schedule.batches),
            brews=len(schedule.brew_volumes()),
            volume=sum(a.volume for a in assignments),
            split_batches=split_batches(schedule),
            merged_tanks=sum(1 for a in assignments if len({fill.brew for fill in a.effective_fills}) > 1),
            makespan=max(a.end_day for a in assignments),
            idle_fermenter_days=compute_metrics(brewery, schedule, schedule, 0).idle_fermenter_days,
        )

    def as_row(self) -> Row:
        row = {f"baseline_{name}": value for name, value in asdict(self).items()}
        return {"tanks": row.pop("baseline_tanks"), "capacity": row.pop("baseline_capacity"), **row}


@dataclass(frozen=True)
class BatchChange:
    """How a replan moved one batch: its first brew and its last bottling start, in days."""

    batch_id: str
    changed: bool
    brew_shift: int | None
    bottling_shift: int | None
    tanks_changed: bool

    @classmethod
    def between(cls, original: Schedule, replanned: Schedule, batch_id: str) -> BatchChange:
        before, after = original.assignments_of(batch_id), replanned.assignments_of(batch_id)
        (brew_before, bottling_before), (brew_after, bottling_after) = _days(before), _days(after)
        return cls(
            batch_id=batch_id,
            changed=_signature(before) != _signature(after),
            brew_shift=None if brew_before is None or brew_after is None else brew_after - brew_before,
            bottling_shift=None
            if bottling_before is None or bottling_after is None
            else bottling_after - bottling_before,
            tanks_changed=_tanks(before) != _tanks(after),
        )


@dataclass(frozen=True)
class ScheduleChange:
    """The changes a replan on one day made to the original schedule, with the delayed batch singled out.

    ``abs_bottling_shift_days`` is D_pack: the absolute shift of every batch's
    last bottling start, so a batch split over several vessels counts once.
    """

    replanned_problems: int
    plan_changed: bool
    changed_batches: int
    pct_changed_batches: float
    changed_other_batches: int
    net_schedule_shift: int
    total_abs_schedule_shift: int
    shifted_brews: int
    shifted_bottlings: int
    abs_brew_shift_days: int
    abs_bottling_shift_days: int
    max_brew_shift_days: int
    max_bottling_shift_days: int
    retanked_batches: int
    unplaced_batches: int
    idle_fermenter_days_before: int
    idle_fermenter_days: int
    idle_fermenter_days_change: int
    replanned_occupancy: float
    replanned_makespan: int
    delayed_bottling_day: int | None
    delayed_end_day: int | None
    next_batch_in_tank: str | None

    @classmethod
    def of(
        cls,
        brewery: Brewery,
        original: Schedule,
        replanned: Schedule,
        day: int,
        horizon_days: int,
        delay: FermentationDelaySignal | None,
    ) -> ScheduleChange:
        metrics = compute_metrics(brewery, original, replanned, day)
        idle_before = compute_metrics(brewery, original, original, day).idle_fermenter_days
        changes = [BatchChange.between(original, replanned, batch_id) for batch_id in original.batches]
        others = [change for change in changes if delay is None or change.batch_id != delay.batch_id]
        delayed = None if delay is None else replanned.assignment_for(delay.batch_id, delay.fermenter_id)
        brew_shifts = [abs(c.brew_shift) for c in changes if c.brew_shift is not None]
        bottling_shifts = [abs(c.bottling_shift) for c in changes if c.bottling_shift is not None]
        return cls(
            replanned_problems=len(replanning_problems(brewery, replanned)),
            plan_changed=metrics.changed_batches > 0,
            changed_batches=metrics.changed_batches,
            pct_changed_batches=round(
                100 * metrics.changed_batches / len(set(original.batches) | set(replanned.batches)), 1
            ),
            changed_other_batches=sum(1 for change in others if change.changed),
            net_schedule_shift=metrics.schedule_shift_days,
            total_abs_schedule_shift=metrics.total_brew_shift_days + metrics.total_bottling_shift_days,
            shifted_brews=metrics.shifted_brews,
            shifted_bottlings=metrics.shifted_bottlings,
            abs_brew_shift_days=metrics.total_brew_shift_days,
            abs_bottling_shift_days=metrics.total_bottling_shift_days,
            max_brew_shift_days=max(brew_shifts, default=0),
            max_bottling_shift_days=max(bottling_shifts, default=0),
            retanked_batches=sum(1 for change in changes if change.tanks_changed),
            unplaced_batches=sum(1 for batch_id in replanned.batches if not replanned.assignments_of(batch_id)),
            idle_fermenter_days_before=idle_before,
            idle_fermenter_days=metrics.idle_fermenter_days,
            idle_fermenter_days_change=metrics.idle_fermenter_days - idle_before,
            replanned_occupancy=occupancy(brewery, replanned, horizon_days),
            replanned_makespan=max(a.end_day for a in replanned.assignments),
            delayed_bottling_day=None if delayed is None else delayed.effective_bottling_day,
            delayed_end_day=None if delayed is None else delayed.end_day,
            next_batch_in_tank=None if delay is None else _next_batch_on(original, delay.fermenter_id, delay.batch_id),
        )

    def as_row(self) -> Row:
        return asdict(self)


def _next_batch_on(schedule: Schedule, tank_id: str, batch_id: str) -> str | None:
    ids = [a.batch_id for a in sorted(schedule.assignments_on_fermenter(tank_id), key=lambda a: a.start_day)]
    position = ids.index(batch_id)
    return ids[position + 1] if position + 1 < len(ids) else None


def _days(assignments: Sequence[FermentationAssignment]) -> tuple[int | None, int | None]:
    if not assignments:
        return None, None
    first_brew = min(fill.brew.day for a in assignments for fill in a.effective_fills)
    return first_brew, max(a.effective_bottling_day for a in assignments)


def _tanks(assignments: Sequence[FermentationAssignment]) -> list[str]:
    return sorted(a.fermenter.id for a in assignments)


def _signature(assignments: Sequence[FermentationAssignment]) -> frozenset[tuple[str, float, int, int]]:
    return frozenset((a.fermenter.id, a.volume, a.start_day, a.end_day) for a in assignments)
