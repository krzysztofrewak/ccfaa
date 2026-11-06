"""Means with t confidence intervals and paired differences, as the itch2027 experiments report them."""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from statistics import mean, stdev

T_975 = {
    1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228,
    11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145, 15: 2.131, 16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093, 20: 2.086,
    25: 2.060, 30: 2.042, 40: 2.021, 60: 2.000, 120: 1.980,
}  # fmt: skip


def t_975(df: int) -> float:
    """The two-sided 95% quantile of Student's t, of the nearest tabulated degrees of freedom not above ``df``."""
    return T_975[max(d for d in T_975 if d <= df)] if df >= 1 else math.nan


@dataclass(frozen=True)
class Estimate:
    """The mean of some values with the half-width of its 95% confidence interval, None for fewer than two."""

    mean: float | None
    ci95: float | None
    n: int

    @classmethod
    def of(cls, values: Iterable[float | None]) -> Estimate:
        present = [float(v) for v in values if v is not None and not math.isnan(v)]
        if not present:
            return cls(None, None, 0)
        if len(present) == 1:
            return cls(present[0], None, 1)
        half_width = t_975(len(present) - 1) * stdev(present) / math.sqrt(len(present))
        return cls(mean(present), half_width, len(present))

    def columns(self, name: str) -> dict[str, float | int | None]:
        return {name: self.mean, f"{name}_ci95": self.ci95, f"{name}_n": self.n}

    def format(self, pattern: str = "{:.1f}") -> str:
        if self.mean is None:
            return "-"
        return pattern.format(self.mean) + ("" if self.ci95 is None else " ±" + pattern.format(self.ci95))


@dataclass(frozen=True)
class PairedDifference:
    """Differences of a second measure minus a first, with how many are below, at and above zero."""

    estimate: Estimate
    lower: int
    equal: int
    higher: int

    @classmethod
    def of(cls, differences: Iterable[float], counted: Iterable[float] | None = None) -> PairedDifference:
        """The estimate over ``differences``; the counts over ``counted``, the same differences by default."""
        values = list(differences)
        signs = values if counted is None else list(counted)
        return cls(
            Estimate.of(values),
            lower=sum(d < 0 for d in signs),
            equal=sum(d == 0 for d in signs),
            higher=sum(d > 0 for d in signs),
        )

    def columns(self) -> dict[str, float | int | None]:
        return {
            "mean_second_minus_first": self.estimate.mean,
            "ci95": self.estimate.ci95,
            "second_lower": self.lower,
            "equal": self.equal,
            "second_higher": self.higher,
        }
