from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum


class VesselRole(Enum):
    """What a vessel can hold a batch for.

    FULL is the whole fermentation without a transfer. PRIMARY and
    SECONDARY are the two stages of a fermentation with a transfer from a
    PRIMARY vessel to a different SECONDARY vessel in between.
    """

    FULL = "full"
    PRIMARY = "primary"
    SECONDARY = "secondary"


@dataclass(frozen=True)
class Vessel:
    """Holds up to ``capacity`` litres of one batch."""

    name: str
    index: int
    capacity: float
    roles: frozenset[VesselRole]

    def __post_init__(self) -> None:
        if self.capacity <= 0:
            raise ValueError(f"capacity must be positive, got {self.capacity}")
        object.__setattr__(self, "roles", frozenset(self.roles))
        if not self.roles:
            raise ValueError(f"vessel {self.id} must have at least one role")

    @classmethod
    def unitank(cls, name: str, index: int, capacity: float) -> Vessel:
        return cls(name, index, capacity, frozenset({VesselRole.FULL}))

    @classmethod
    def fermenter(cls, name: str, index: int, capacity: float) -> Vessel:
        return cls(name, index, capacity, frozenset({VesselRole.PRIMARY}))

    @classmethod
    def lagering_tank(cls, name: str, index: int, capacity: float) -> Vessel:
        return cls(name, index, capacity, frozenset({VesselRole.SECONDARY}))

    @classmethod
    def general_vessel(cls, name: str, index: int, capacity: float) -> Vessel:
        return cls(name, index, capacity, frozenset({VesselRole.PRIMARY, VesselRole.SECONDARY}))

    @property
    def id(self) -> str:
        return f"{self.name}{self.index}"

    def has_role(self, role: VesselRole) -> bool:
        return role in self.roles


@dataclass(frozen=True)
class Brewhouse:
    """Brews one recipe at a time, up to ``kettle_yield`` litres per brew."""

    name: str
    kettle_yield: float
    max_brews_per_day: int

    def __post_init__(self) -> None:
        if self.kettle_yield <= 0:
            raise ValueError(f"kettle_yield must be positive, got {self.kettle_yield}")
        if self.max_brews_per_day <= 0:
            raise ValueError(f"max_brews_per_day must be positive, got {self.max_brews_per_day}")


@dataclass(frozen=True)
class BottlingLine:
    """Bottles or cans beer in runs of up to ``run_volume`` litres.

    A run takes beer from one vessel, so a vessel holding more than one run
    is bottled in several runs, possibly over several days. ``math.inf``
    makes a line that bottles any vessel in one run.
    """

    name: str
    run_volume: float
    max_runs_per_day: int

    def __post_init__(self) -> None:
        if self.run_volume <= 0:
            raise ValueError(f"run_volume must be positive, got {self.run_volume}")
        if self.max_runs_per_day <= 0:
            raise ValueError(f"max_runs_per_day must be positive, got {self.max_runs_per_day}")

    @classmethod
    def by_daily_volume(cls, name: str, volume_per_day: float, step: float = 100) -> BottlingLine:
        """A line limited only by the volume it bottles a day, from any number of vessels.

        Bottling is counted in runs of ``step``, so a vessel takes as many
        runs as it holds started steps and several vessels share the daily
        volume. With the default step of 100 litres the limit is exact for
        vessels holding whole hundreds of litres.
        """
        runs = volume_per_day / step
        if runs != int(runs):
            raise ValueError(f"volume_per_day {volume_per_day} is not a multiple of step {step}")
        return cls(name, run_volume=step, max_runs_per_day=int(runs))

    def runs_for(self, volume: float) -> int:
        """Runs needed to bottle one vessel holding ``volume``."""
        return max(1, math.ceil(volume / self.run_volume - 1e-9))


@dataclass
class Brewery:
    name: str
    vessels: list[Vessel]
    brewhouses: list[Brewhouse]
    bottling_lines: list[BottlingLine]
    days_off: set[int] = field(default_factory=set)
    fermenter_maintenance: dict[str, list[tuple[int, int]]] = field(default_factory=dict)
    max_brew_spread_days: int = 2
    """Most days between the first and the last brew of one batch."""

    def __post_init__(self) -> None:
        if not self.brewhouses:
            raise ValueError("a brewery needs at least one brewhouse")
        if not self.bottling_lines:
            raise ValueError("a brewery needs at least one bottling line")
        if self.max_brew_spread_days < 0:
            raise ValueError(f"max_brew_spread_days must not be negative, got {self.max_brew_spread_days}")
        for kind, ids in (
            ("vessel ids", [vessel.id for vessel in self.vessels]),
            ("brewhouse names", [brewhouse.name for brewhouse in self.brewhouses]),
            ("bottling line names", [line.name for line in self.bottling_lines]),
        ):
            duplicates = sorted({item for item in ids if ids.count(item) > 1})
            if duplicates:
                raise ValueError(f"{kind} must be unique, got duplicates {duplicates}")

    @property
    def kettle_yield(self) -> float:
        """The largest brew any brewhouse makes."""
        return max(brewhouse.kettle_yield for brewhouse in self.brewhouses)

    @property
    def max_brews_per_day(self) -> int:
        """Brews a day in all brewhouses together."""
        return sum(brewhouse.max_brews_per_day for brewhouse in self.brewhouses)

    @property
    def max_bottling_runs_per_day(self) -> int:
        """Bottling runs a day on all lines together."""
        return sum(line.max_runs_per_day for line in self.bottling_lines)

    def brewhouse(self, name: str | None) -> Brewhouse:
        """The brewhouse called ``name``; None names the only brewhouse of the brewery."""
        if name is None:
            if len(self.brewhouses) > 1:
                raise KeyError(f"brewery {self.name!r} has several brewhouses, so a brew must name one")
            return self.brewhouses[0]
        for brewhouse in self.brewhouses:
            if brewhouse.name == name:
                return brewhouse
        raise KeyError(f"no brewhouse called {name!r}")

    def bottling_line(self, name: str | None) -> BottlingLine:
        """The line called ``name``; None names the only line of the brewery."""
        if name is None:
            if len(self.bottling_lines) > 1:
                raise KeyError(f"brewery {self.name!r} has several bottling lines, so a bottling must name one")
            return self.bottling_lines[0]
        for line in self.bottling_lines:
            if line.name == name:
                return line
        raise KeyError(f"no bottling line called {name!r}")

    def vessel_by_id(self, vessel_id: str) -> Vessel:
        for vessel in self.vessels:
            if vessel.id == vessel_id:
                return vessel
        raise KeyError(f"no vessel with id {vessel_id!r}")

    def vessels_with(self, role: VesselRole) -> list[Vessel]:
        return [vessel for vessel in self.vessels if vessel.has_role(role)]

    def mark_day_off(self, day: int) -> None:
        self.days_off.add(day)

    def is_working_day(self, day: int) -> bool:
        return day not in self.days_off

    def mark_fermenter_maintenance(self, fermenter_id: str, start_day: int, duration_days: int) -> None:
        if duration_days <= 0:
            raise ValueError(f"duration_days must be positive, got {duration_days}")
        self.fermenter_maintenance.setdefault(fermenter_id, []).append((start_day, start_day + duration_days))

    def fermenter_maintenance_windows(self, fermenter_id: str) -> list[tuple[int, int]]:
        return self.fermenter_maintenance.get(fermenter_id, [])
