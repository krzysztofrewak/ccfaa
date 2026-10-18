from __future__ import annotations

from brewery_schedule_digital_twin.policies.base import ReplanningPolicy


class ManualPolicy(ReplanningPolicy):
    def should_replan_immediately(self, day: int) -> bool:
        return False

    def should_replan_end_of_day(self, day: int) -> bool:
        return False
