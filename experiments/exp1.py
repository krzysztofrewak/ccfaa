"""Single-signal examples: each of the configuration's exp1_scenarios is
applied alone to a fresh baseline, the brewery is replanned on day 4, and
the resulting metrics are printed.
"""

from __future__ import annotations

from brewery_agentic_replanning.metrics import compute_metrics
from experiments.common import manual_twin
from experiments.config import from_cli

REPLAN_DAY = 4


def main() -> None:
    cfg = from_cli(__doc__.splitlines()[0])
    recipes = cfg.build_recipes()
    for signal in cfg.hook("exp1_scenarios")(recipes):
        twin = manual_twin(cfg, recipes, [signal], REPLAN_DAY)
        twin.replan_now()
        metrics = compute_metrics(twin.brewery, twin.original_schedule, twin.schedule, twin.current_day)
        print(f"day {signal.day} {signal.kind}: {signal.description}")
        print(f"Changed batches: {metrics.changed_batches}")
        print(f"Schedule shift: {metrics.schedule_shift_days} days")
        print(f"Idle fermenter days: {metrics.idle_fermenter_days}")
        print(f"Shifted brews: {metrics.shifted_brews} ({metrics.total_brew_shift_days} days total)")
        print(f"Shifted bottlings: {metrics.shifted_bottlings} ({metrics.total_bottling_shift_days} days total)\n")


if __name__ == "__main__":
    main()
