from __future__ import annotations

import math
import random
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from brewery_schedule_digital_twin.domain import Batch, Brew, FermentationAssignment, Fill, Schedule, VesselRole

if TYPE_CHECKING:
    from brewery_schedule_digital_twin.domain import Brewery, Recipe, Vessel


def occupancy(brewery: Brewery, schedule: Schedule, horizon_days: int) -> float:
    """The share of vessel-days in days 0 to ``horizon_days`` - 1 that hold a batch."""
    occupied = sum(max(0, min(a.end_day, horizon_days) - max(a.start_day, 0)) for a in schedule.assignments)
    return occupied / (len(brewery.vessels) * horizon_days)


@dataclass(frozen=True)
class _Tank:
    """One vessel of a generated batch: the (brew index, volume) of its fills, its line and the days of its runs."""

    vessel: Vessel
    fills: tuple[tuple[int, float], ...]
    line: int
    runs: tuple[int, ...]


@dataclass(frozen=True)
class _Planned:
    """A generated batch before it is numbered: the day and brewhouse of each brew and the vessels they fill."""

    recipe: Recipe
    brew_days: tuple[int, ...]
    brewhouses: tuple[int, ...]
    tanks: tuple[_Tank, ...]

    def start_day(self, tank: _Tank) -> int:
        return min(self.brew_days[index] for index, _ in tank.fills)

    def end_day(self, tank: _Tank) -> int:
        last_fill_day = max(self.brew_days[index] for index, _ in tank.fills)
        return max(tank.runs[-1] + 1, last_fill_day + self.recipe.fermentation_days)


class ScheduleGenerator:
    """Fills a running brewery with random batches up to a target occupancy.

    The schedule is a snapshot of a brewery in operation over days 0 to
    ``horizon_days`` - 1: every unitank first gets a chain of batches, each
    brewed into it on the day the previous one leaves it, from a batch
    already fermenting on day 0 to one that ends on or after the horizon.
    Each batch is of a recipe drawn from those whose brews and bottling fit
    the brewhouse and the bottling line; only if none does is the batch
    moved to the next day, leaving the tank empty for a day. Then batches
    are removed in random order, each one only if the occupancy stays at or
    above ``fill_ratio``, so the occupancy ends at ``fill_ratio`` or just
    above it, and a ``fill_ratio`` of 1 removes nothing.

    Only FULL vessels are filled, every one to its capacity. A tank larger
    than the kettle yield is filled from the fewest equal brews, merged in
    it, as close together as the brewhouse allows and at most
    ``Brewery.max_brew_spread_days`` apart. A smaller tank is filled from
    one brew, split with other tanks that are free on the same day, taken
    in random order, while the brew still fills them all. Each tank is
    bottled from its last fermentation day, which must have a free run, in
    as many runs a day as the line has free, so a tank of several runs or
    the further tanks of a split brew may be bottled over later days and
    stay occupied until their last run. Removing a batch frees its runs,
    so the kept batches are bottled again, in the order they were
    generated, before the occupancy is checked.

    In a brewery of several brewhouses each brew is made, day by day, in
    the brewhouses that make it, in the order they are listed, while they
    have free slots, so the brews of one batch may come from several. In
    one of several bottling lines each tank is bottled on the line that
    finishes it soonest, the first listed on a tie. Batches are numbered by
    brew day and their planned brew day is the day of their first brew, so
    the schedule validates as a baseline.
    """

    def fill(
        self,
        brewery: Brewery,
        recipes: Sequence[Recipe],
        fill_ratio: float,
        horizon_days: int,
        rng: random.Random,
    ) -> Schedule:
        if not 0 < fill_ratio <= 1:
            raise ValueError(f"fill_ratio must be in (0, 1], got {fill_ratio}")
        if horizon_days <= 0:
            raise ValueError(f"horizon_days must be positive, got {horizon_days}")
        if not recipes:
            raise ValueError("at least one recipe is needed")

        chains = self._chains(brewery, recipes, horizon_days, rng)
        kept = self._thin_out(brewery, chains, fill_ratio, horizon_days, rng)
        return self._schedule(brewery, kept)

    @staticmethod
    def _chains(brewery: Brewery, recipes: Sequence[Recipe], horizon_days: int, rng: random.Random) -> list[_Planned]:
        """Every batch of the back-to-back chains."""
        tanks = brewery.vessels_with(VesselRole.FULL)
        if not tanks:
            raise ValueError(f"brewery {brewery.name!r} has no FULL vessels to fill")
        shortest = min(recipe.fermentation_days for recipe in recipes)
        brews: Counter[tuple[int, int]] = Counter()
        runs: Counter[tuple[int, int]] = Counter()

        def split_with(vessel: Vessel, day: int) -> list[Vessel]:
            """The vessel and the tanks free on the same day that one brew still fills with it."""
            chosen = [vessel]
            volume = vessel.capacity
            partners = [tank for tank in tanks if tank is not vessel and free_from[tank.id] == day]
            for tank in rng.sample(partners, len(partners)):
                if volume + tank.capacity <= brewery.kettle_yield + 1e-9:
                    chosen.append(tank)
                    volume += tank.capacity
            return chosen

        def brew_slots(day: int, volume: float) -> tuple[tuple[int, int], ...] | None:
            """The (day, brewhouse) of the fewest brews of ``volume``, the first on ``day``, as early as possible."""
            count = max(1, math.ceil(volume / brewery.kettle_yield - 1e-9))
            able = [
                (index, brewhouse)
                for index, brewhouse in enumerate(brewery.brewhouses)
                if brewhouse.kettle_yield + 1e-9 >= volume / count
            ]
            if not brewery.is_working_day(day) or all(brews[index, day] >= b.max_brews_per_day for index, b in able):
                return None
            slots: list[tuple[int, int]] = []
            for candidate in range(day, day + brewery.max_brew_spread_days + 1):
                if brewery.is_working_day(candidate):
                    for index, brewhouse in able:
                        free = brewhouse.max_brews_per_day - brews[index, candidate]
                        slots += [(candidate, index)] * min(free, count - len(slots))
                if len(slots) == count:
                    return tuple(slots)
            return None

        def fits(days: tuple[int, ...], recipe: Recipe) -> bool:
            bottling_day = days[-1] + recipe.fermentation_days - 1
            return brewery.is_working_day(bottling_day) and any(
                runs[index, bottling_day] < line.max_runs_per_day for index, line in enumerate(brewery.bottling_lines)
            )

        # The first batch of every tank is already fermenting on day 0: if it
        # does not fit, it is brewed a day earlier rather than later.
        longest = max(recipe.fermentation_days for recipe in recipes)
        free_from = {vessel.id: -rng.randrange(shortest) for vessel in tanks}
        first_pending = {vessel.id for vessel in tanks}
        batches: list[_Planned] = []
        while True:
            open_tanks = [vessel for vessel in tanks if free_from[vessel.id] < horizon_days]
            if not open_tanks:
                return batches
            vessel = min(open_tanks, key=lambda v: (free_from[v.id], v.id))
            day = free_from[vessel.id]
            group = split_with(vessel, day)
            first = any(tank.id in first_pending for tank in group)
            volume = sum(tank.capacity for tank in group)
            slots = brew_slots(day, volume)
            days = None if slots is None else tuple(slot_day for slot_day, _ in slots)
            candidates = rng.sample(list(recipes), len(recipes))
            recipe = next(
                (
                    r
                    for r in candidates
                    if days is not None and fits(days, r) and (not first or days[-1] + r.fermentation_days > 0)
                ),
                None,
            )
            if slots is None or days is None or recipe is None:
                pending = vessel.id in first_pending
                if pending and day - 1 + longest > 0:
                    free_from[vessel.id] = day - 1
                else:
                    first_pending.discard(vessel.id)
                    free_from[vessel.id] = max(day, 0) + 1 if pending else day + 1
                continue
            for slot in slots:
                brews[slot[1], slot[0]] += 1
            bottling_day = days[-1] + recipe.fermentation_days - 1
            planned_tanks = []
            for tank in group:
                if len(days) > 1:
                    fills = tuple((index, volume / len(days)) for index in range(len(days)))
                else:
                    fills = ((0, tank.capacity),)
                planned_tanks.append(_Tank(tank, fills, *_bottle(brewery, runs, bottling_day, tank.capacity)))
            batch = _Planned(recipe, days, tuple(brewhouse for _, brewhouse in slots), tuple(planned_tanks))
            batches.append(batch)
            for planned in batch.tanks:
                first_pending.discard(planned.vessel.id)
                free_from[planned.vessel.id] = batch.end_day(planned)

    @staticmethod
    def _thin_out(
        brewery: Brewery,
        batches: list[_Planned],
        fill_ratio: float,
        horizon_days: int,
        rng: random.Random,
    ) -> list[_Planned]:
        capacity = len(brewery.vessels) * horizon_days

        def days_in_horizon(batch: _Planned) -> int:
            return sum(
                max(0, min(batch.end_day(tank), horizon_days) - max(batch.start_day(tank), 0)) for tank in batch.tanks
            )

        def occupied(kept: list[_Planned]) -> int:
            return sum(days_in_horizon(batch) for batch in kept)

        # Each removal is tried on the kept batches bottled again, so the
        # runs it frees count before the occupancy is checked.
        removed: set[int] = set()
        kept = ScheduleGenerator._rebottle(brewery, batches)
        for index in rng.sample(range(len(batches)), len(batches)):
            trial = ScheduleGenerator._rebottle(
                brewery, [batch for i, batch in enumerate(batches) if i != index and i not in removed]
            )
            if occupied(trial) / capacity >= fill_ratio:
                removed.add(index)
                kept = trial
        return kept

    @staticmethod
    def _rebottle(brewery: Brewery, batches: list[_Planned]) -> list[_Planned]:
        """Bottles the kept batches again in the order they were generated, into the runs removed batches freed.

        Every tank stays on its line, so with fewer batches on each line
        every run falls on the same day or earlier and no tank is occupied
        longer than before. Choosing the lines again could move a tank later.
        """
        runs: Counter[tuple[int, int]] = Counter()
        rebottled = []
        for batch in batches:
            bottling_day = batch.brew_days[-1] + batch.recipe.fermentation_days - 1
            tanks = []
            for tank in batch.tanks:
                _, days = _bottle(brewery, runs, bottling_day, tank.vessel.capacity, tank.line)
                tanks.append(replace(tank, runs=days))
            rebottled.append(replace(batch, tanks=tuple(tanks)))
        return rebottled

    @staticmethod
    def _schedule(brewery: Brewery, batches: list[_Planned]) -> Schedule:
        several_brewhouses = len(brewery.brewhouses) > 1
        several_lines = len(brewery.bottling_lines) > 1
        schedule = Schedule()
        ordered = sorted(batches, key=lambda batch: (batch.brew_days[0], batch.tanks[0].vessel.id))
        for number, batch in enumerate(ordered, start=1):
            batch_id = f"B{number:03d}"
            recipe = batch.recipe
            brews = [
                Brew(batch_id, index, day, brewhouse=brewery.brewhouses[brewhouse].name if several_brewhouses else None)
                for index, (day, brewhouse) in enumerate(zip(batch.brew_days, batch.brewhouses, strict=True), start=1)
            ]
            schedule.add_batch(
                Batch(
                    batch_id,
                    f"{recipe.name} #{batch_id}",
                    recipe,
                    planned_brew_day=batch.brew_days[0],
                    volume=sum(tank.vessel.capacity for tank in batch.tanks),
                )
            )
            for tank in batch.tanks:
                schedule.add_assignment(
                    FermentationAssignment(
                        batch_id=batch_id,
                        fermenter=tank.vessel,
                        volume=tank.vessel.capacity,
                        start_day=batch.start_day(tank),
                        base_fermentation_days=recipe.fermentation_days,
                        bottling_day=tank.runs[0],
                        fills=tuple(Fill(brews[index], volume) for index, volume in tank.fills),
                        bottling_runs=tank.runs if len(tank.runs) > 1 else (),
                        bottling_line=brewery.bottling_lines[tank.line].name if several_lines else None,
                    )
                )
        return schedule


def _bottle(
    brewery: Brewery, runs: Counter[tuple[int, int]], day: int, volume: float, line: int | None = None
) -> tuple[int, tuple[int, ...]]:
    """Takes the runs that bottle ``volume`` from ``day`` on, on the line that finishes soonest; that line and the days.

    Each day takes as many runs as the line has free. ``line`` keeps the
    tank on that line.
    """

    def plan(index: int) -> tuple[int, ...]:
        line = brewery.bottling_lines[index]
        needed = line.runs_for(volume)
        days: list[int] = []
        current = day
        while len(days) < needed:
            if brewery.is_working_day(current):
                days += [current] * min(line.max_runs_per_day - runs[index, current], needed - len(days))
            current += 1
        return tuple(days)

    candidates = range(len(brewery.bottling_lines)) if line is None else [line]
    plans = {index: plan(index) for index in candidates}
    index = min(plans, key=lambda i: (plans[i][-1], i))
    for run_day in plans[index]:
        runs[index, run_day] += 1
    return index, plans[index]
