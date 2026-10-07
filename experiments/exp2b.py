"""Single-signal sweep: ingredient availability (IngredientShortageSignal).

For every signal day in 0-13, every batch not yet brewed by that day, with
ingredients available again 1-13 days after the signal. Each combination
replays the baseline from scratch and replans on the signal day.
"""

from __future__ import annotations

from brewery_agentic_replanning.domain import Recipe, Schedule
from brewery_agentic_replanning.signals import IngredientShortageSignal
from experiments.common import manual_twin, metric_columns, report_sweep, write_csv
from experiments.config import ExperimentConfig, from_cli

DAY_RANGE = range(0, 14)
SHORTAGE_OFFSETS = range(1, 14)


def pending_batch_ids(schedule: Schedule, day: int) -> list[str]:
    return sorted(batch.id for batch in schedule.batches.values() if batch.planned_brew_day > day)


def run_combination(
    cfg: ExperimentConfig, recipes: dict[str, Recipe], day: int, batch_id: str, available_from_day: int
) -> dict:
    signal = IngredientShortageSignal(day=day, batch_id=batch_id, available_from_day=available_from_day)
    twin = manual_twin(cfg, recipes, [signal], day)
    original_planned_brew_day = twin.original_schedule.batches[batch_id].planned_brew_day
    twin.replan_now()
    return {
        "day": day,
        "batch_id": batch_id,
        "original_planned_brew_day": original_planned_brew_day,
        "available_from_day": available_from_day,
        "shortage_forces_delay": available_from_day > original_planned_brew_day,
        **metric_columns(twin),
    }


def combinations(baseline: Schedule) -> list[tuple[int, str, int]]:
    return [
        (day, batch_id, day + offset)
        for day in DAY_RANGE
        for batch_id in pending_batch_ids(baseline, day)
        for offset in SHORTAGE_OFFSETS
    ]


def main() -> None:
    cfg = from_cli(__doc__.splitlines()[0])
    recipes = cfg.build_recipes()
    _, baseline = cfg.baseline()
    rows = [run_combination(cfg, recipes, *combination) for combination in combinations(baseline)]
    write_csv(cfg.results_path("exp2b_results.csv"), rows)
    report_sweep(rows)


if __name__ == "__main__":
    main()
