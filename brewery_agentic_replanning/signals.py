from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from brewery_agentic_replanning.domain import Batch


@dataclass(frozen=True)
class Signal:
    day: int

    @property
    def kind(self) -> str:
        return type(self).__name__

    @property
    def description(self) -> str:
        raise NotImplementedError

    @property
    def related_batch_id(self) -> str | None:
        return None


@dataclass(frozen=True)
class FermentationDelaySignal(Signal):
    batch_id: str
    fermenter_id: str
    extra_days: int

    def __post_init__(self) -> None:
        if self.extra_days == 0:
            raise ValueError("extra_days must not be zero")

    @property
    def description(self) -> str:
        if self.extra_days > 0:
            return f"Fermentation of batch {self.batch_id} in {self.fermenter_id} delayed by {self.extra_days} day(s)"
        return f"Fermentation of batch {self.batch_id} in {self.fermenter_id} shortened by {-self.extra_days} day(s)"

    @property
    def related_batch_id(self) -> str | None:
        return self.batch_id


@dataclass(frozen=True)
class IngredientShortageSignal(Signal):
    batch_id: str
    available_from_day: int

    def __post_init__(self) -> None:
        if self.available_from_day < self.day:
            raise ValueError(
                f"available_from_day ({self.available_from_day}) cannot precede "
                f"the day the signal is observed ({self.day})"
            )

    @property
    def description(self) -> str:
        return f"Ingredients for batch {self.batch_id} missing, available again from day {self.available_from_day}"

    @property
    def related_batch_id(self) -> str | None:
        return self.batch_id


@dataclass(frozen=True)
class NewBatchRequirementSignal(Signal):
    batch: Batch

    def __post_init__(self) -> None:
        if self.batch.target_bottling_day is None:
            raise ValueError("batch must declare a target_bottling_day")

    @property
    def description(self) -> str:
        return (
            f"New order: batch {self.batch.id} ({self.batch.recipe.name}) "
            f"must be bottled by day {self.batch.target_bottling_day}"
        )

    @property
    def related_batch_id(self) -> str | None:
        return self.batch.id


@dataclass(frozen=True)
class DayOffSignal(Signal):
    off_day: int

    @property
    def description(self) -> str:
        return f"Day {self.off_day} declared a day off"


@dataclass(frozen=True)
class FermenterMaintenanceSignal(Signal):
    fermenter_id: str
    start_day: int
    duration_days: int

    def __post_init__(self) -> None:
        if self.duration_days <= 0:
            raise ValueError(f"duration_days must be positive, got {self.duration_days}")

    @property
    def description(self) -> str:
        return (
            f"Fermenter {self.fermenter_id} out for maintenance from day {self.start_day} "
            f"for {self.duration_days} day(s)"
        )
