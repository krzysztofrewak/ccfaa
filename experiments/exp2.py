"""Single-signal sweep: non-working day (DayOffSignal).

Every combination of signal day, declared day off and replanning day in
0-13, with the day off and the replanning day not preceding the signal.
Each combination replays the baseline from scratch and replans once.
"""

from __future__ import annotations

from brewery_agentic_replanning.domain import Recipe, Schedule
from brewery_agentic_replanning.signals import DayOffSignal
from experiments.common import manual_twin, metric_columns, report_sweep, write_csv
from experiments.config import ExperimentConfig, from_cli

DAY_RANGE = range(0, 14)


def _work_on(schedule: Schedule, day: int) -> tuple[bool, bool]:
    had_brew = any(a.start_day == day for a in schedule.assignments)
    had_bottling = any(a.effective_bottling_day == day for a in schedule.assignments)
    return had_brew, had_bottling


def run_combination(
    cfg: ExperimentConfig, recipes: dict[str, Recipe], signal_day: int, off_day: int, replan_day: int
) -> dict:
    twin = manual_twin(cfg, recipes, [DayOffSignal(day=signal_day, off_day=off_day)], replan_day)
    had_brew, had_bottling = _work_on(twin.original_schedule, off_day)
    twin.replan_now()
    metrics = metric_columns(twin)
    return {
        "signal_day": signal_day,
        "off_day": off_day,
        "replan_day": replan_day,
        **metrics,
        "off_day_had_brew": had_brew,
        "off_day_had_bottling": had_bottling,
    }


def combinations() -> list[tuple[int, int, int]]:
    return [
        (signal_day, off_day, replan_day)
        for signal_day in DAY_RANGE
        for off_day in DAY_RANGE
        if off_day >= signal_day
        for replan_day in DAY_RANGE
        if replan_day >= signal_day
    ]


def main() -> None:
    cfg = from_cli(__doc__.splitlines()[0])
    recipes = cfg.build_recipes()
    rows = [run_combination(cfg, recipes, *combination) for combination in combinations()]
    write_csv(cfg.results_path("exp2_results.csv"), rows)
    report_sweep(rows)


if __name__ == "__main__":
    main()
