from __future__ import annotations

from typing import Any

from brewery_agentic_replanning.agents import Agent, Assessment, SalesAgent, default_agents
from brewery_agentic_replanning.domain import Brewery, Schedule
from brewery_agentic_replanning.signals import NewBatchRequirementSignal, Signal


class SalesAgentLegacy(SalesAgent):
    def assess(
        self, brewery: Brewery, schedule: Schedule, signal: NewBatchRequirementSignal, current_day: int
    ) -> Assessment:
        batch = signal.batch
        assert batch.target_bottling_day is not None
        latest_brew_day_meeting_target = batch.target_bottling_day - batch.recipe.fermentation_days
        return Assessment(
            source_agent=self.organizational_function,
            decision="REPLAN" if current_day >= latest_brew_day_meeting_target else "PENDING",
            reason="new order must be scheduled in time to hit its target bottling day",
            latest_safe_decision_point=latest_brew_day_meeting_target,
            affected_activity=batch.id,
        )


def legacy_agents() -> dict[type[Signal], Agent[Any]]:
    agents = default_agents()
    agents[NewBatchRequirementSignal] = SalesAgentLegacy()
    return agents
