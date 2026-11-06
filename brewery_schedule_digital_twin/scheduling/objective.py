from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from brewery_schedule_digital_twin.domain import FermentationAssignment, Schedule


@dataclass(frozen=True)
class RepairWeights:
    """Weights of the repair objective.

    Per day a brew of an already planned batch moves (``brew_shift``) or
    the last bottling run of a planned batch or a locked vessel moves
    (``bottling_shift``); for a planned batch put into other vessels or
    volumes (``placement_change``); per brew moved to another brewhouse
    (``brewhouse_change``); and per day a batch without a placement is
    brewed or bottled after its earliest day (``lateness``).
    """

    brew_shift: int = 10
    bottling_shift: int = 10
    placement_change: int = 5
    brewhouse_change: int = 1
    lateness: int = 1


def repair_cost(original: Schedule, repaired: Schedule, current_day: int, weights: RepairWeights | None = None) -> int:
    """How far ``repaired`` departs from ``original``, the schedule being repaired on ``current_day``.

    The cost ``CpSatScheduler`` minimises: shifts of the brews and of the
    last bottling run of planned batches and of locked vessels, planned
    batches put into other vessels and brews moved to another brewhouse,
    and, for batches without a placement, the days they are brewed and
    bottled after their earliest day. A brew is matched by its number, or
    by the first brew day if the batch was brewed anew in other brews.
    """
    weights = weights or RepairWeights()
    cost = 0
    for batch in original.batches.values():
        before = original.assignments_of(batch.id)
        after = repaired.assignments_of(batch.id)
        if not after:
            raise ValueError(f"batch {batch.id!r} is not placed in the repaired schedule")
        if any(a.locked for a in before):
            vessels = {a.fermenter.id: a for a in after}
            cost += weights.bottling_shift * sum(
                abs(vessels[a.fermenter.id].last_bottling_day - a.last_bottling_day) for a in before
            )
            continue
        if not before:
            earliest = max(current_day, batch.planned_brew_day)
            first_brew = min(fill.brew.day for a in after for fill in a.effective_fills)
            last_bottling = max(a.last_bottling_day for a in after)
            fermentation_days = after[0].base_fermentation_days
            cost += weights.lateness * (first_brew - earliest + last_bottling - (earliest + fermentation_days - 1))
            continue

        brews_before = {fill.brew.number: fill.brew for a in before for fill in a.effective_fills}
        brews_after = {fill.brew.number: fill.brew for a in after for fill in a.effective_fills}
        first_after = min(brew.day for brew in brews_after.values())
        for number, brew in brews_before.items():
            moved = brews_after.get(number)
            cost += weights.brew_shift * abs((moved.day if moved is not None else first_after) - brew.day)
            if moved is not None and brew.brewhouse is not None and moved.brewhouse != brew.brewhouse:
                cost += weights.brewhouse_change
        cost += weights.bottling_shift * abs(
            max(a.last_bottling_day for a in after) - max(a.last_bottling_day for a in before)
        )
        if _placement(before) != _placement(after):
            cost += weights.placement_change
    return cost


def _placement(assignments: list[FermentationAssignment]) -> list[tuple[str, float]]:
    return sorted((a.fermenter.id, round(a.volume, 6)) for a in assignments)
