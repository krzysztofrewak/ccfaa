from __future__ import annotations

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
    name: str
    index: int
    capacity_hl: float
    roles: frozenset[VesselRole]

    def __post_init__(self) -> None:
        if self.capacity_hl <= 0:
            raise ValueError(f"capacity_hl must be positive, got {self.capacity_hl}")
        object.__setattr__(self, "roles", frozenset(self.roles))
        if not self.roles:
            raise ValueError(f"vessel {self.id} must have at least one role")

    @classmethod
    def unitank(cls, name: str, index: int, capacity_hl: float) -> Vessel:
        return cls(name, index, capacity_hl, frozenset({VesselRole.FULL}))

    @classmethod
    def fermenter(cls, name: str, index: int, capacity_hl: float) -> Vessel:
        return cls(name, index, capacity_hl, frozenset({VesselRole.PRIMARY}))

    @classmethod
    def lagering_tank(cls, name: str, index: int, capacity_hl: float) -> Vessel:
        return cls(name, index, capacity_hl, frozenset({VesselRole.SECONDARY}))

    @classmethod
    def general_vessel(cls, name: str, index: int, capacity_hl: float) -> Vessel:
        return cls(name, index, capacity_hl, frozenset({VesselRole.PRIMARY, VesselRole.SECONDARY}))

    @property
    def id(self) -> str:
        return f"{self.name}{self.index}"

    def has_role(self, role: VesselRole) -> bool:
        return role in self.roles


@dataclass
class Brewery:
    name: str
    vessels: list[Vessel]
    kettle_yield_hl: float
    max_brews_per_day: int
    max_bottling_runs_per_day: int
    days_off: set[int] = field(default_factory=set)
    fermenter_maintenance: dict[str, list[tuple[int, int]]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kettle_yield_hl <= 0:
            raise ValueError(f"kettle_yield_hl must be positive, got {self.kettle_yield_hl}")
        if self.max_brews_per_day <= 0:
            raise ValueError(f"max_brews_per_day must be positive, got {self.max_brews_per_day}")
        if self.max_bottling_runs_per_day <= 0:
            raise ValueError(f"max_bottling_runs_per_day must be positive, got {self.max_bottling_runs_per_day}")
        ids = [vessel.id for vessel in self.vessels]
        duplicates = sorted({vessel_id for vessel_id in ids if ids.count(vessel_id) > 1})
        if duplicates:
            raise ValueError(f"vessel ids must be unique, got duplicates {duplicates}")

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
