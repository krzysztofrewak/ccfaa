from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from brewery_agentic_replanning.domain.equipment import Fermenter
    from brewery_agentic_replanning.domain.recipes import Recipe


@dataclass(frozen=True)
class Batch:
    """One brew, possibly split across several fermenters.

    The batch volume is the sum of its assignments' volumes and never
    exceeds the kettle yield. ``volume_hl`` is the requested volume of a
    batch that has no assignments yet; None means a full kettle yield.
    """

    id: str
    name: str
    recipe: Recipe
    planned_brew_day: int
    target_bottling_day: int | None = None
    volume_hl: float | None = None

    def __post_init__(self) -> None:
        if self.volume_hl is not None and self.volume_hl <= 0:
            raise ValueError(f"volume_hl must be positive, got {self.volume_hl}")


@dataclass
class FermentationAssignment:
    batch_id: str
    fermenter: Fermenter
    volume_hl: float
    start_day: int
    base_fermentation_days: int
    extra_delay_days: int = 0
    bottling_day: int | None = None
    locked: bool = False

    def __post_init__(self) -> None:
        if self.volume_hl <= 0:
            raise ValueError(f"volume_hl must be positive, got {self.volume_hl}")
        if self.volume_hl > self.fermenter.capacity_hl:
            raise ValueError(f"volume_hl {self.volume_hl} exceeds capacity of fermenter {self.fermenter.id}")

    def has_started(self, day: int) -> bool:
        return self.locked or self.start_day <= day

    def bottling_done(self, day: int) -> bool:
        return self.effective_bottling_day < day

    @property
    def natural_end_day(self) -> int:
        return self.start_day + self.base_fermentation_days + self.extra_delay_days

    @property
    def last_fermentation_day(self) -> int:
        return self.natural_end_day - 1

    @property
    def effective_bottling_day(self) -> int:
        return self.bottling_day if self.bottling_day is not None else self.last_fermentation_day

    @property
    def end_day(self) -> int:
        if self.bottling_day is None:
            return self.natural_end_day
        return max(self.bottling_day + 1, self.natural_end_day)

    def covers(self, day: int) -> bool:
        return self.start_day <= day < self.end_day
