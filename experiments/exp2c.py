"""Single-signal sweep: fermentation completion (FermentationDelaySignal).

For every signal day in 0-13, every (batch, fermenter) assignment
fermenting on that day, with a deviation of -3..-1 or +1..+7 days. An
earlier completion that would end fermentation by the signal day is
skipped. Each combination replays the baseline from scratch and replans on
the signal day.
"""

from __future__ import annotations

from brewery_agentic_replanning.domain import Recipe, Schedule
from brewery_agentic_replanning.signals import FermentationDelaySignal
from experiments.common import manual_twin, metric_columns, report_sweep, write_csv
from experiments.config import ExperimentConfig, from_cli

DAY_RANGE = range(0, 14)
DEVIATIONS = [extra_days for extra_days in range(-3, 8) if extra_days != 0]


def active_assignments(schedule: Schedule, day: int) -> list[tuple[str, str]]:
    return sorted({(a.batch_id, a.fermenter.id) for a in schedule.assignments if a.covers(day)})


def run_combination(
    cfg: ExperimentConfig, recipes: dict[str, Recipe], day: int, batch_id: str, fermenter_id: str, extra_days: int
) -> dict:
    signal = FermentationDelaySignal(day=day, batch_id=batch_id, fermenter_id=fermenter_id, extra_days=extra_days)
    twin = manual_twin(cfg, recipes, [signal], day)
    original = twin.original_schedule.assignment_for(batch_id, fermenter_id)
    twin.replan_now()
    return {
        "day": day,
        "batch_id": batch_id,
        "fermenter_id": fermenter_id,
        "extra_days": extra_days,
        "original_end_day": original.end_day,
        "original_natural_end_day": original.natural_end_day,
        **metric_columns(twin),
    }


def combinations(baseline: Schedule) -> list[tuple[int, str, str, int]]:
    return [
        (day, batch_id, fermenter_id, extra_days)
        for day in DAY_RANGE
        for batch_id, fermenter_id in active_assignments(baseline, day)
        for extra_days in DEVIATIONS
        if baseline.assignment_for(batch_id, fermenter_id).natural_end_day + extra_days > day
    ]


def main() -> None:
    cfg = from_cli(__doc__.splitlines()[0])
    recipes = cfg.build_recipes()
    _, baseline = cfg.baseline()
    rows = [run_combination(cfg, recipes, *combination) for combination in combinations(baseline)]
    write_csv(cfg.results_path("exp2c_results.csv"), rows)
    report_sweep(rows)


if __name__ == "__main__":
    main()
