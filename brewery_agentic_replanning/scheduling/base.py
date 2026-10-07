from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from brewery_agentic_replanning.domain import Brewery, Schedule


class SchedulingError(Exception):
    pass


class Scheduler(ABC):
    """Produces a new schedule from the current brewery state.

    Implementations must not move assignments marked as ``locked``.
    """

    @abstractmethod
    def replan(self, brewery: Brewery, schedule: Schedule, current_day: int) -> Schedule:
        raise NotImplementedError
