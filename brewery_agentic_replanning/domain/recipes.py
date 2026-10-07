from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Recipe:
    name: str
    fermentation_days: int

    def __post_init__(self) -> None:
        if self.fermentation_days <= 0:
            raise ValueError(f"fermentation_days must be positive, got {self.fermentation_days}")
