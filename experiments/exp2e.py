"""Single-signal sweep: new production requirement (NewBatchRequirementSignal).

For every signal day in 0-13, a new New England IPA batch planned for
brewing 0-13 days after the signal (working days only), with a target
bottling day of planned brew day + fermentation duration + slack, slack in
{0, 3, 7, 14}. Each combination replays the baseline from scratch and
replans on the signal day; a combination the scheduler cannot fit is
recorded as infeasible.
"""

from __future__ import annotations

from brewery_agentic_replanning.domain import Batch, Brewery, Recipe
from brewery_agentic_replanning.scheduling import SchedulingError
from brewery_agentic_replanning.signals import NewBatchRequirementSignal
from experiments.common import METRIC_COLUMNS, manual_twin, metric_columns, report_sweep, write_csv
from experiments.config import ExperimentConfig, from_cli

DAY_RANGE = range(0, 14)
BREW_DAY_OFFSETS = range(0, 14)
SLACK_VALUES = (0, 3, 7, 14)
RECIPE_KEY = "new_england_ipa"
NEW_BATCH_ID = "SPEC"


def new_order(
    recipes: dict[str, Recipe], day: int, planned_brew_day: int, target_bottling_day: int
) -> NewBatchRequirementSignal:
    batch = Batch(
        NEW_BATCH_ID,
        "New England IPA - rush order",
        recipes[RECIPE_KEY],
        planned_brew_day=planned_brew_day,
        target_bottling_day=target_bottling_day,
    )
    return NewBatchRequirementSignal(day=day, batch=batch)


def run_combination(
    cfg: ExperimentConfig, recipes: dict[str, Recipe], day: int, planned_brew_day: int, slack: int
) -> dict:
    target_bottling_day = planned_brew_day + recipes[RECIPE_KEY].fermentation_days + slack
    twin = manual_twin(cfg, recipes, [new_order(recipes, day, planned_brew_day, target_bottling_day)], day)
    row = {
        "day": day,
        "planned_brew_day": planned_brew_day,
        "slack": slack,
        "target_bottling_day": target_bottling_day,
    }
    try:
        twin.replan_now()
    except SchedulingError:
        return {
            **row,
            "feasible": False,
            "new_batch_start_day": None,
            "new_batch_bottling_day": None,
            **dict.fromkeys(METRIC_COLUMNS),
        }

    new_assignments = twin.schedule.assignments_of(NEW_BATCH_ID)
    return {
        **row,
        "feasible": True,
        "new_batch_start_day": min(a.start_day for a in new_assignments),
        "new_batch_bottling_day": max(a.effective_bottling_day for a in new_assignments),
        **metric_columns(twin),
    }


def combinations(brewery: Brewery) -> list[tuple[int, int, int]]:
    return [
        (day, day + offset, slack)
        for day in DAY_RANGE
        for offset in BREW_DAY_OFFSETS
        if brewery.is_working_day(day + offset)
        for slack in SLACK_VALUES
    ]


def main() -> None:
    cfg = from_cli(__doc__.splitlines()[0])
    recipes = cfg.build_recipes()
    rows = [run_combination(cfg, recipes, *combination) for combination in combinations(cfg.build_brewery())]
    write_csv(cfg.results_path("exp2e_results.csv"), rows)
    infeasible = sum(1 for row in rows if not row["feasible"])
    print(f"{infeasible}/{len(rows)} combinations were infeasible.")
    report_sweep(rows)


if __name__ == "__main__":
    main()
