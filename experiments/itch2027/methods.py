"""The repair methods the itch2027 experiments compare and what their schedulers report of a search."""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from dataclasses import dataclass, field

from brewery_schedule_digital_twin.scheduling import (
    CpSatScheduler,
    DifferentialEvolutionScheduler,
    HeuristicScheduler,
    Scheduler,
)
from experiments.itch2027.measures import Row

SCHEDULERS: dict[str, type[Scheduler]] = {
    "greedy": HeuristicScheduler,
    "cpsat": CpSatScheduler,
    "de": DifferentialEvolutionScheduler,
}


@dataclass(frozen=True)
class RepairMethod:
    """A scheduler of ``SCHEDULERS`` with its settings and, for a stochastic one, its seed."""

    name: str
    seed: int | None = None
    settings: Mapping[str, object] = field(default_factory=dict, hash=False)

    def __post_init__(self) -> None:
        if self.name not in SCHEDULERS:
            raise ValueError(f"unknown method {self.name!r}, expected one of {sorted(SCHEDULERS)}")
        object.__setattr__(self, "settings", dict(self.settings))

    @property
    def key(self) -> str:
        return self.name if self.seed is None else f"{self.name}:{self.seed}"

    @property
    def threads(self) -> int:
        workers = self.parameters().get("workers", 1)
        return workers if isinstance(workers, int) else 1

    def scheduler(self) -> Scheduler:
        settings = dict(self.settings)
        if self.seed is not None:
            settings["seed"] = self.seed
        return SCHEDULERS[self.name](**settings)

    def parameters(self) -> dict[str, object]:
        """Every parameter of the scheduler, its defaults included."""
        signature = inspect.signature(SCHEDULERS[self.name])
        defaults = {
            name: parameter.default
            for name, parameter in signature.parameters.items()
            if parameter.default is not inspect.Parameter.empty
        }
        return {**defaults, **self.settings, **({} if self.seed is None else {"seed": self.seed})}

    def description(self) -> dict[str, object]:
        return {"class": SCHEDULERS[self.name].__name__, **self.parameters()}


def solver_columns(scheduler: Scheduler) -> Row:
    """What CP-SAT and differential evolution report of their last search; nothing for the greedy scheduler."""
    if isinstance(scheduler, CpSatScheduler) and scheduler.last_report is not None:
        cpsat = scheduler.last_report
        return {
            "solver_status": cpsat.status,
            "cpsat_objective": cpsat.objective,
            "cpsat_best_bound": cpsat.best_bound,
            "cpsat_gap": cpsat.gap,
            "cpsat_optimal": cpsat.optimal,
            "cpsat_hint_completed": cpsat.hint_completed,
            "cpsat_returned_start": cpsat.returned_start,
            "cpsat_model_seconds": cpsat.model_seconds,
        }
    if isinstance(scheduler, DifferentialEvolutionScheduler) and scheduler.last_report is not None:
        evolution = scheduler.last_report
        return {
            "solver_status": "time_limit" if evolution.stopped_by_limit else "generations",
            "de_cost": evolution.cost,
            "de_greedy_cost": evolution.heuristic_cost,
            "de_improved": evolution.improved,
            "de_evaluations": evolution.evaluations,
            "de_generations": evolution.generations,
            "de_stopped_by_limit": evolution.stopped_by_limit,
        }
    return {}
