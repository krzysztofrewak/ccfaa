from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Recipe:
    """Fermentation times of a beer.

    ``fermentation_days`` is the time a batch spends in a FULL vessel,
    without a transfer. ``primary_fermentation_days`` and
    ``secondary_fermentation_days`` are the times in a PRIMARY vessel and,
    after the transfer, in a SECONDARY vessel. The three are independent:
    the stage times need not add up to ``fermentation_days``. None means
    the stage time is not known for this recipe.
    """

    name: str
    fermentation_days: int
    primary_fermentation_days: int | None = None
    secondary_fermentation_days: int | None = None

    def __post_init__(self) -> None:
        for attribute in ("fermentation_days", "primary_fermentation_days", "secondary_fermentation_days"):
            days = getattr(self, attribute)
            if days is not None and days <= 0:
                raise ValueError(f"{attribute} must be positive, got {days}")
