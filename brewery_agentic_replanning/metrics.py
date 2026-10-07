from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from brewery_agentic_replanning.domain import Brewery, Schedule


@dataclass(frozen=True)
class ReplanningMetrics:
    changed_batches: int
    schedule_shift_days: int
    idle_fermenter_days: int
    shifted_brews: int
    shifted_bottlings: int
    total_brew_shift_days: int
    total_bottling_shift_days: int


def compute_metrics(brewery: Brewery, original: Schedule, replanned: Schedule, current_day: int) -> ReplanningMetrics:
    brew_shifts = _brew_day_shifts(original, replanned)
    bottling_shifts = _bottling_day_shifts(original, replanned)
    return ReplanningMetrics(
        changed_batches=_count_changed_batches(original, replanned),
        schedule_shift_days=_schedule_shift_days(brewery, original, replanned),
        idle_fermenter_days=_idle_fermenter_days(brewery, replanned, current_day),
        shifted_brews=sum(1 for d in brew_shifts if d != 0),
        shifted_bottlings=sum(1 for d in bottling_shifts if d != 0),
        total_brew_shift_days=sum(abs(d) for d in brew_shifts),
        total_bottling_shift_days=sum(abs(d) for d in bottling_shifts),
    )


def _batch_signature(schedule: Schedule, batch_id: str) -> frozenset[tuple[str, float, int, int]]:
    return frozenset((a.fermenter.id, a.volume_hl, a.start_day, a.end_day) for a in schedule.assignments_of(batch_id))


def _count_changed_batches(original: Schedule, replanned: Schedule) -> int:
    batch_ids = set(original.batches) | set(replanned.batches)
    return sum(
        1 for batch_id in batch_ids if _batch_signature(original, batch_id) != _batch_signature(replanned, batch_id)
    )


def _last_end_day(schedule: Schedule, fermenter_id: str, default: int) -> int:
    assignments = schedule.assignments_on_fermenter(fermenter_id)
    if not assignments:
        return default
    return max(a.end_day for a in assignments)


def _schedule_shift_days(brewery: Brewery, original: Schedule, replanned: Schedule) -> int:
    original_total = sum(_last_end_day(original, f.id, 0) for f in brewery.fermenters)
    replanned_total = sum(_last_end_day(replanned, f.id, 0) for f in brewery.fermenters)
    return replanned_total - original_total


def _idle_fermenter_days(brewery: Brewery, schedule: Schedule, current_day: int) -> int:
    idle_days = 0
    for fermenter in brewery.fermenters:
        assignments = schedule.assignments_on_fermenter(fermenter.id)
        last_end = max((a.end_day for a in assignments), default=current_day)
        for day in range(current_day, last_end):
            if not any(a.covers(day) for a in assignments):
                idle_days += 1
    return idle_days


def _bottling_day(schedule: Schedule, batch_id: str) -> int | None:
    assignments = schedule.assignments_of(batch_id)
    if not assignments:
        return None
    return max(a.effective_bottling_day for a in assignments)


def _brew_day_shifts(original: Schedule, replanned: Schedule) -> list[int]:
    shifts = []
    for batch_id in set(original.batches) & set(replanned.batches):
        if not original.assignments_of(batch_id) or not replanned.assignments_of(batch_id):
            continue
        shifts.append(replanned.brew_day_of(batch_id) - original.brew_day_of(batch_id))
    return shifts


def _bottling_day_shifts(original: Schedule, replanned: Schedule) -> list[int]:
    shifts = []
    for batch_id in set(original.batches) & set(replanned.batches):
        original_day = _bottling_day(original, batch_id)
        replanned_day = _bottling_day(replanned, batch_id)
        if original_day is None or replanned_day is None:
            continue
        shifts.append(replanned_day - original_day)
    return shifts
