from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Fermenter:
    name: str
    index: int
    capacity_hl: float

    def __post_init__(self) -> None:
        if self.capacity_hl <= 0:
            raise ValueError(f"capacity_hl must be positive, got {self.capacity_hl}")

    @property
    def id(self) -> str:
        return f"{self.name}{self.index}"


@dataclass
class Brewery:
    name: str
    fermenters: list[Fermenter]
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

    def fermenter_by_id(self, fermenter_id: str) -> Fermenter:
        for fermenter in self.fermenters:
            if fermenter.id == fermenter_id:
                return fermenter
        raise KeyError(f"no fermenter with id {fermenter_id!r}")

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
