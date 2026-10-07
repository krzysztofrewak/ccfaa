"""Single-signal sweep: fermenter maintenance (FermenterMaintenanceSignal).

For every signal day in 0-13 and every fermenter, a maintenance window
starting 0-13 days after the signal and lasting 1-14 days. Each
combination replays the baseline from scratch and replans on the signal
day.
"""

from __future__ import annotations

from brewery_agentic_replanning.domain import Brewery, Recipe, Schedule
from brewery_agentic_replanning.signals import FermenterMaintenanceSignal
from experiments.common import manual_twin, metric_columns, report_sweep, write_csv
from experiments.config import ExperimentConfig, from_cli

DAY_RANGE = range(0, 14)
START_OFFSETS = range(0, 14)
DURATIONS = range(1, 15)


def _occupied(schedule: Schedule, fermenter_id: str, start_day: int, end_day: int) -> bool:
    return any(a.start_day < end_day and start_day < a.end_day for a in schedule.assignments_on_fermenter(fermenter_id))


def run_combination(
    cfg: ExperimentConfig,
    recipes: dict[str, Recipe],
    day: int,
    fermenter_id: str,
    start_day: int,
    duration_days: int,
) -> dict:
    signal = FermenterMaintenanceSignal(
        day=day, fermenter_id=fermenter_id, start_day=start_day, duration_days=duration_days
    )
    end_day = start_day + duration_days
    twin = manual_twin(cfg, recipes, [signal], day)
    occupied = _occupied(twin.original_schedule, fermenter_id, start_day, end_day)
    twin.replan_now()
    return {
        "day": day,
        "fermenter_id": fermenter_id,
        "start_day": start_day,
        "duration_days": duration_days,
        "end_day": end_day,
        "fermenter_occupied_during_window": occupied,
        **metric_columns(twin),
    }


def combinations(brewery: Brewery) -> list[tuple[int, str, int, int]]:
    return [
        (day, fermenter.id, day + offset, duration_days)
        for day in DAY_RANGE
        for fermenter in brewery.fermenters
        for offset in START_OFFSETS
        for duration_days in DURATIONS
    ]


def main() -> None:
    cfg = from_cli(__doc__.splitlines()[0])
    recipes = cfg.build_recipes()
    rows = [run_combination(cfg, recipes, *combination) for combination in combinations(cfg.build_brewery())]
    write_csv(cfg.results_path("exp2d_results.csv"), rows)
    report_sweep(rows)


if __name__ == "__main__":
    main()
