from collections.abc import Callable

from brewery_schedule_digital_twin.scheduling.base import Scheduler, SchedulingError
from brewery_schedule_digital_twin.scheduling.cpsat import CpSatReport, CpSatScheduler
from brewery_schedule_digital_twin.scheduling.differential_evolution import (
    DifferentialEvolutionScheduler,
    EvolutionReport,
)
from brewery_schedule_digital_twin.scheduling.heuristic import HeuristicScheduler
from brewery_schedule_digital_twin.scheduling.objective import RepairWeights, repair_cost

SCHEDULERS: dict[str, Callable[..., Scheduler]] = {
    "heuristic": HeuristicScheduler,
    "cpsat": CpSatScheduler,
    "de": DifferentialEvolutionScheduler,
}

__all__ = [
    "SCHEDULERS",
    "CpSatReport",
    "CpSatScheduler",
    "DifferentialEvolutionScheduler",
    "EvolutionReport",
    "HeuristicScheduler",
    "RepairWeights",
    "Scheduler",
    "SchedulingError",
    "repair_cost",
]
