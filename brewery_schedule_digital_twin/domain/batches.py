from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from brewery_schedule_digital_twin.domain.equipment import Vessel
    from brewery_schedule_digital_twin.domain.recipes import Recipe


@dataclass(frozen=True)
class Batch:
    """A quantity of one beer, brewed in one or more brews.

    The batch volume is the sum of its assignments' volumes. A batch up to
    the kettle yield is one brew, possibly split across several vessels; a
    larger batch takes several brews, possibly merged into one vessel.
    ``volume`` is the requested volume of a batch that has no
    assignments yet; None means a full kettle yield.

    ``blend`` declares a blend: the volume in litres brewed from each recipe.
    Each brew is of one recipe and the brews are mixed in the vessels. The
    batch's ``recipe`` still sets the fermentation time. A blend's volume
    is the sum of its components, so ``volume`` may be left out. A batch
    without ``blend`` takes brews of its own recipe only.
    """

    id: str
    name: str
    recipe: Recipe
    planned_brew_day: int
    target_bottling_day: int | None = None
    volume: float | None = None
    blend: Mapping[Recipe, float] | None = field(default=None, hash=False)

    def __post_init__(self) -> None:
        if self.blend is not None:
            if len(self.blend) < 2:
                raise ValueError(f"a blend needs at least two recipes, got {len(self.blend)}")
            if any(volume <= 0 for volume in self.blend.values()):
                raise ValueError(f"blend volumes must be positive, got {dict(self.blend)}")
            object.__setattr__(self, "blend", MappingProxyType(dict(self.blend)))
            total = sum(self.blend.values())
            if self.volume is None:
                object.__setattr__(self, "volume", total)
            elif not math.isclose(self.volume, total, abs_tol=1e-9):
                raise ValueError(f"volume {self.volume} differs from the blend total {total}")
        if self.volume is not None and self.volume <= 0:
            raise ValueError(f"volume must be positive, got {self.volume}")

    def recipe_of(self, brew: Brew) -> Recipe:
        return brew.recipe if brew.recipe is not None else self.recipe


@dataclass(frozen=True)
class Brew:
    """One brewhouse operation of a batch, numbered from 1 within the batch.

    ``recipe`` is None for a brew of the batch's own recipe. A brew of a
    different recipe is allowed only in a batch that declares a ``blend``.
    ``brewhouse`` names the brewhouse; None means the brewery's only one.
    """

    batch_id: str
    number: int
    day: int
    recipe: Recipe | None = None
    brewhouse: str | None = None

    def __post_init__(self) -> None:
        if self.number < 1:
            raise ValueError(f"number must be at least 1, got {self.number}")

    @property
    def id(self) -> str:
        return f"{self.batch_id}/{self.number}"


@dataclass(frozen=True)
class Fill:
    """The part of a brew that is poured into one vessel."""

    brew: Brew
    volume: float

    def __post_init__(self) -> None:
        if self.volume <= 0:
            raise ValueError(f"volume must be positive, got {self.volume}")


@dataclass
class FermentationAssignment:
    """A batch's beer in one vessel.

    Without ``fills`` the vessel is filled from a single brew of the batch
    on ``start_day``. With ``fills`` it is filled from the listed brews:
    fermentation starts with the first fill on ``start_day`` and ends
    ``base_fermentation_days`` after the last fill.

    Bottling starts on ``bottling_day``, on ``bottling_line`` (None means
    the brewery's only line). Without ``bottling_runs`` the vessel is
    bottled in one run that day. With ``bottling_runs`` it is bottled in
    one run per listed day, starting on ``bottling_day``; the vessel is
    occupied until the last run.
    """

    batch_id: str
    fermenter: Vessel
    volume: float
    start_day: int
    base_fermentation_days: int
    extra_delay_days: int = 0
    bottling_day: int | None = None
    locked: bool = False
    fills: tuple[Fill, ...] = ()
    bottling_line: str | None = None
    bottling_runs: tuple[int, ...] = ()
    _implicit_fills: tuple[tuple[str, int, float], tuple[Fill, ...]] | None = field(
        default=None, init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        if self.volume <= 0:
            raise ValueError(f"volume must be positive, got {self.volume}")
        if self.volume > self.fermenter.capacity:
            raise ValueError(f"volume {self.volume} exceeds capacity of fermenter {self.fermenter.id}")
        if self.bottling_runs and (self.bottling_day is None or min(self.bottling_runs) != self.bottling_day):
            raise ValueError(f"bottling runs of batch {self.batch_id!r} must start on bottling_day {self.bottling_day}")
        if not self.fills:
            return
        if any(fill.brew.batch_id != self.batch_id for fill in self.fills):
            raise ValueError(f"fills of batch {self.batch_id!r} must come from its own brews")
        if min(fill.brew.day for fill in self.fills) != self.start_day:
            raise ValueError(f"the first fill of batch {self.batch_id!r} must be on start_day {self.start_day}")
        if not math.isclose(sum(fill.volume for fill in self.fills), self.volume, abs_tol=1e-9):
            raise ValueError(f"fills of batch {self.batch_id!r} must add up to volume {self.volume}")

    @property
    def effective_fills(self) -> tuple[Fill, ...]:
        if self.fills:
            return self.fills
        key = (self.batch_id, self.start_day, self.volume)
        if self._implicit_fills is None or self._implicit_fills[0] != key:
            # Cached: schedulers ask for the implicit fill of every assignment many times.
            self._implicit_fills = (key, (Fill(Brew(self.batch_id, 1, self.start_day), self.volume),))
        return self._implicit_fills[1]

    @property
    def fill_days(self) -> set[int]:
        if not self.fills:
            return {self.start_day}
        return {fill.brew.day for fill in self.fills}

    @property
    def last_fill_day(self) -> int:
        if not self.fills:
            return self.start_day
        return max(fill.brew.day for fill in self.fills)

    def has_started(self, day: int) -> bool:
        return self.locked or self.start_day <= day

    def bottling_done(self, day: int) -> bool:
        return self.last_bottling_day < day

    @property
    def natural_end_day(self) -> int:
        return self.last_fill_day + self.base_fermentation_days + self.extra_delay_days

    @property
    def last_fermentation_day(self) -> int:
        return self.natural_end_day - 1

    @property
    def effective_bottling_day(self) -> int:
        return self.bottling_day if self.bottling_day is not None else self.last_fermentation_day

    @property
    def effective_bottling_runs(self) -> tuple[int, ...]:
        """The day of each bottling run."""
        return self.bottling_runs or (self.effective_bottling_day,)

    @property
    def last_bottling_day(self) -> int:
        if not self.bottling_runs:
            return self.effective_bottling_day
        return max(self.bottling_runs)

    @property
    def end_day(self) -> int:
        if self.bottling_day is None:
            return self.natural_end_day
        last_bottling_day = max(self.bottling_runs) if self.bottling_runs else self.bottling_day
        return max(last_bottling_day + 1, self.natural_end_day)

    def covers(self, day: int) -> bool:
        return self.start_day <= day < self.end_day
