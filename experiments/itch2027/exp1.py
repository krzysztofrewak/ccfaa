"""Experiment 1: a sweep of fermentation delays over every tank of a brewery, in each of its variants.

An ``Experiment`` holds everything the run varies: the variants of the
brewery, the recipes, the target occupancies, the horizon and the sweep.
``EXPERIMENTS`` names the experiments ``--experiment`` chooses from.

For every variant and target occupancy, ``--count`` schedules are generated
with seeds ``--seed`` to ``--seed`` + ``--count`` - 1. Each schedule gets,
one at a time, every signal of the sweep: on each signal day, the batch in
a tank whose fermentation would end in one of the lead days
(``natural_end_day`` - signal day) has it extended by each of the extra
days. Every signal is applied to a fresh copy of the baseline, the twin
advances to the signal day and the greedy scheduler
(``HeuristicScheduler``) replans on that day.

As a control, every baseline is also replanned without any signal on
every signal day, so the changes the greedy scheduler makes to a generated
schedule on its own can be told apart from the effect of a delay.

Writes ``experiments/itch2027/results/exp1/<experiment>/runs.csv``, one row
per signal, with the ``control_*`` measures of the undisturbed replan on
the signal day, and ``controls.csv``, one row per undisturbed replan.
"""

from __future__ import annotations

import argparse
import csv
import os
import random
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from brewery_schedule_digital_twin.agents import default_agents
from brewery_schedule_digital_twin.domain import (
    BottlingLine,
    Brewery,
    Brewhouse,
    FermentationAssignment,
    Recipe,
    Schedule,
    Vessel,
)
from brewery_schedule_digital_twin.generation import ScheduleGenerator, occupancy
from brewery_schedule_digital_twin.metrics import compute_metrics
from brewery_schedule_digital_twin.policies import ManualPolicy
from brewery_schedule_digital_twin.scheduling import HeuristicScheduler, SchedulingError
from brewery_schedule_digital_twin.signals import FermentationDelaySignal
from brewery_schedule_digital_twin.simulation import BreweryDigitalTwin

RESULTS_DIR = Path(__file__).resolve().parent / "results" / "exp1"
CONTROL_COLUMNS = (
    "feasible",
    "plan_changed",
    "changed_batches",
    "net_schedule_shift",
    "total_abs_schedule_shift",
    "shifted_brews",
    "shifted_bottlings",
    "abs_brew_shift_days",
    "abs_bottling_shift_days",
    "retanked_batches",
    "idle_fermenter_days_change",
)


@dataclass(frozen=True)
class Experiment:
    """The variants of one brewery and the sweep they are compared on."""

    name: str
    variants: Mapping[str, Brewery]
    recipes: tuple[Recipe, ...]
    occupancies: tuple[float, ...]
    horizon_days: int
    signal_days: range
    lead_days: range
    extra_days: range


def wolow() -> Experiment:
    """Browar Wołów with 100 hl of unitanks in three fleets.

    One 6 hl brewhouse of 4 brews a day and manual bottling of up to 20 hl
    a day, so the largest tank is bottled in one day. On signal days 3-20,
    fermentations ending in 1-3 days are extended by 1-3 days.
    """
    fleets = {
        "A": [1_000.0] * 4 + [2_000.0] * 3,
        "B": [1_000.0] * 10,
        "C": [2_000.0] * 5,
        "D": [10_000.0 / 7] * 7,
    }
    return Experiment(
        name="wolow",
        variants={
            variant: Brewery(
                name=f"Browar Wołów {variant}",
                vessels=[Vessel.unitank("f", index, capacity) for index, capacity in enumerate(tanks, start=1)],
                brewhouses=[Brewhouse("brewhouse", kettle_yield=600, max_brews_per_day=4)],
                bottling_lines=[BottlingLine.by_daily_volume("manual", volume_per_day=2_000)],
            )
            for variant, tanks in fleets.items()
        },
        recipes=(
            Recipe(name="Hefeweizen", fermentation_days=8),
            Recipe(name="Witbier", fermentation_days=10),
            Recipe(name="New England IPA", fermentation_days=12),
            Recipe(name="Session Pale Ale", fermentation_days=15),
            Recipe(name="Dry Stout", fermentation_days=18),
        ),
        occupancies=(1.0, 0.97, 0.95, 0.93, 0.90, 0.85),
        horizon_days=60,
        signal_days=range(3, 21),
        lead_days=range(1, 4),
        extra_days=range(1, 4),
    )


EXPERIMENTS: dict[str, Callable[[], Experiment]] = {"wolow": wolow}


def sweep_signals(experiment: Experiment, schedule: Schedule) -> list[FermentationDelaySignal]:
    """Every signal of the sweep, by tank, signal day, lead and extra days."""
    return [
        FermentationDelaySignal(day, a.batch_id, a.fermenter.id, extra_days)
        for a in sorted(schedule.assignments, key=lambda a: (a.fermenter.index, a.start_day))
        for day in experiment.signal_days
        if a.natural_end_day - day in experiment.lead_days
        for extra_days in experiment.extra_days
    ]


def run_schedule(task: tuple[Experiment, str, float, int, int]) -> tuple[list[dict], list[dict]]:
    """The signal rows and the control rows of one baseline."""
    experiment, variant, target, index, seed = task
    horizon_days = experiment.horizon_days
    brewery, baseline = generate(experiment, variant, target, seed)
    row = {
        **schedule_columns(experiment, variant, target, index, seed),
        **baseline_columns(brewery, baseline, horizon_days),
    }
    controls = {
        day: replan_undisturbed(brewery, baseline.clone(), day, horizon_days, row)[0] for day in experiment.signal_days
    }
    signals = []
    for signal in sweep_signals(experiment, baseline):
        signal_row, _ = replan_after(brewery, baseline.clone(), signal, horizon_days, row)
        control = controls[signal.day]
        signals.append({**signal_row, **{f"control_{key}": control.get(key) for key in CONTROL_COLUMNS}})
    return signals, list(controls.values())


def generate(experiment: Experiment, variant: str, target: float, seed: int) -> tuple[Brewery, Schedule]:
    brewery = experiment.variants[variant]
    generator = ScheduleGenerator()
    return brewery, generator.fill(brewery, experiment.recipes, target, experiment.horizon_days, random.Random(seed))


def schedule_columns(experiment: Experiment, variant: str, target: float, index: int, seed: int) -> dict:
    return {
        "experiment": experiment.name,
        "variant": variant,
        "target_occupancy": target,
        "index": index,
        "seed": seed,
        "horizon_days": experiment.horizon_days,
    }


def replan_after(
    brewery: Brewery, baseline: Schedule, signal: FermentationDelaySignal, horizon_days: int, row: dict
) -> tuple[dict, BreweryDigitalTwin | None]:
    """The row extended with the signal and the effect of replanning on its day; the twin unless infeasible.

    The twin changes ``baseline``, so a baseline that gets several signals is passed as a clone.
    """
    original = baseline.assignment_for(signal.batch_id, signal.fermenter_id)
    row = {
        **row,
        "signal": True,
        "signal_day": signal.day,
        "delayed_tank": signal.fermenter_id,
        "delayed_tank_capacity": original.fermenter.capacity,
        "extra_days": signal.extra_days,
        "lead_days": original.natural_end_day - signal.day,
        "delayed_batch": signal.batch_id,
        "delayed_recipe": baseline.batches[signal.batch_id].recipe.name,
        "delayed_volume": original.volume,
        "delayed_original_natural_end_day": original.natural_end_day,
        "delayed_original_bottling_day": original.effective_bottling_day,
        "delayed_original_end_day": original.end_day,
    }

    return _replan(brewery, baseline, signal, signal.day, horizon_days, row)


def replan_undisturbed(
    brewery: Brewery, baseline: Schedule, day: int, horizon_days: int, row: dict
) -> tuple[dict, BreweryDigitalTwin | None]:
    """The row extended with the effect of replanning on ``day`` without any signal, as in ``replan_after``."""
    return _replan(brewery, baseline, None, day, horizon_days, {**row, "signal": False, "signal_day": day})


def _replan(
    brewery: Brewery,
    baseline: Schedule,
    signal: FermentationDelaySignal | None,
    day: int,
    horizon_days: int,
    row: dict,
) -> tuple[dict, BreweryDigitalTwin | None]:
    twin = BreweryDigitalTwin(brewery, baseline, HeuristicScheduler(), ManualPolicy(), default_agents())
    if signal is not None:
        twin.inject_signal(signal)
    twin.advance_to(day)
    start = time.perf_counter()
    try:
        twin.replan_now()
    except SchedulingError as error:
        return {**row, "feasible": False, "error": str(error)}, None
    row["scheduler_seconds"] = time.perf_counter() - start
    row.update(feasible=True, **replanning_columns(twin, horizon_days))
    return row, twin


def baseline_columns(brewery: Brewery, schedule: Schedule, horizon_days: int) -> dict:
    assignments = schedule.assignments
    return {
        "tanks": len(brewery.vessels),
        "capacity": sum(vessel.capacity for vessel in brewery.vessels),
        "baseline_occupancy": occupancy(brewery, schedule, horizon_days),
        "baseline_problems": len(schedule.validate(brewery)),
        "baseline_batches": len(schedule.batches),
        "baseline_brews": len(schedule.brew_volumes()),
        "baseline_volume": sum(a.volume for a in assignments),
        "baseline_split_batches": sum(1 for batch_id in schedule.batches if len(schedule.assignments_of(batch_id)) > 1),
        "baseline_merged_tanks": sum(1 for a in assignments if len({fill.brew for fill in a.effective_fills}) > 1),
        "baseline_makespan": max(a.end_day for a in assignments),
        "baseline_idle_fermenter_days": compute_metrics(brewery, schedule, schedule, 0).idle_fermenter_days,
    }


def replanning_columns(twin: BreweryDigitalTwin, horizon_days: int) -> dict:
    brewery, original, replanned, day = twin.brewery, twin.original_schedule, twin.schedule, twin.current_day
    metrics = compute_metrics(brewery, original, replanned, day)
    idle_before = compute_metrics(brewery, original, original, day).idle_fermenter_days
    changes = [placement_change(original, replanned, batch_id) for batch_id in original.batches]
    delayed = twin.signal_log[0] if twin.signal_log else None
    assert delayed is None or isinstance(delayed, FermentationDelaySignal)
    delayed_after = None if delayed is None else replanned.assignment_for(delayed.batch_id, delayed.fermenter_id)
    moved = [change for change in changes if delayed is None or change["batch_id"] != delayed.batch_id]
    plan_batches = len(set(original.batches) | set(replanned.batches))
    return {
        "replanned_problems": len(replanning_problems(brewery, replanned)),
        "plan_changed": metrics.changed_batches > 0,
        "changed_batches": metrics.changed_batches,
        "pct_changed_batches": round(100 * metrics.changed_batches / plan_batches, 1),
        "changed_other_batches": sum(1 for change in moved if change["changed"]),
        "net_schedule_shift": metrics.schedule_shift_days,
        "total_abs_schedule_shift": metrics.total_brew_shift_days + metrics.total_bottling_shift_days,
        "shifted_brews": metrics.shifted_brews,
        "shifted_bottlings": metrics.shifted_bottlings,
        "abs_brew_shift_days": metrics.total_brew_shift_days,
        "abs_bottling_shift_days": metrics.total_bottling_shift_days,
        "max_brew_shift_days": max((abs(c["brew_shift"]) for c in changes if c["brew_shift"] is not None), default=0),
        "max_bottling_shift_days": max(
            (abs(c["bottling_shift"]) for c in changes if c["bottling_shift"] is not None), default=0
        ),
        "retanked_batches": sum(1 for change in changes if change["tanks_changed"]),
        "unplaced_batches": sum(1 for batch_id in replanned.batches if not replanned.assignments_of(batch_id)),
        "idle_fermenter_days_before": idle_before,
        "idle_fermenter_days": metrics.idle_fermenter_days,
        "idle_fermenter_days_change": metrics.idle_fermenter_days - idle_before,
        "replanned_occupancy": occupancy(brewery, replanned, horizon_days),
        "replanned_makespan": max(a.end_day for a in replanned.assignments),
        "delayed_bottling_day": None if delayed_after is None else delayed_after.effective_bottling_day,
        "delayed_end_day": None if delayed_after is None else delayed_after.end_day,
        "next_batch_in_tank": None
        if delayed is None
        else next_batch_on(original, delayed.fermenter_id, delayed.batch_id),
    }


def replanning_problems(brewery: Brewery, schedule: Schedule) -> list[str]:
    """The problems of a replanned schedule, without brews moved from a batch's declared brew day."""
    return [problem for problem in schedule.validate(brewery) if "declared brew day" not in problem]


def next_batch_on(schedule: Schedule, tank_id: str, batch_id: str) -> str | None:
    """The batch brewed into the tank right after ``batch_id``."""
    on_tank = sorted(schedule.assignments_on_fermenter(tank_id), key=lambda a: a.start_day)
    ids = [a.batch_id for a in on_tank]
    position = ids.index(batch_id)
    return ids[position + 1] if position + 1 < len(ids) else None


def placement_change(original: Schedule, replanned: Schedule, batch_id: str) -> dict:
    before = original.assignments_of(batch_id)
    after = replanned.assignments_of(batch_id)
    brew_before, bottling_before = _days(before)
    brew_after, bottling_after = _days(after)
    return {
        "batch_id": batch_id,
        "changed": _signature(before) != _signature(after),
        "brew_shift": None if brew_after is None or brew_before is None else brew_after - brew_before,
        "bottling_shift": (
            None if bottling_after is None or bottling_before is None else bottling_after - bottling_before
        ),
        "tanks_changed": _tanks(before) != _tanks(after),
    }


def _days(assignments: Sequence[FermentationAssignment]) -> tuple[int | None, int | None]:
    """The first brew day and the last bottling start of a batch."""
    if not assignments:
        return None, None
    return (
        min(fill.brew.day for a in assignments for fill in a.effective_fills),
        max(a.effective_bottling_day for a in assignments),
    )


def _tanks(assignments: Sequence[FermentationAssignment]) -> list[str]:
    return sorted(a.fermenter.id for a in assignments)


def _signature(assignments: Sequence[FermentationAssignment]) -> frozenset[tuple[str, float, int, int]]:
    return frozenset((a.fermenter.id, a.volume, a.start_day, a.end_day) for a in assignments)


def write_csv(path: Path, rows: Sequence[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"{len(rows)} rows -> {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--experiment", choices=EXPERIMENTS, default="wolow", help="experiment (default wolow)")
    parser.add_argument("--count", type=int, default=10, help="schedules per variant and occupancy (default 10)")
    parser.add_argument("--seed", type=int, default=0, help="seed of the first schedule (default 0)")
    parser.add_argument("--workers", type=int, default=os.cpu_count(), help="processes (default: all CPUs)")
    args = parser.parse_args()
    experiment = EXPERIMENTS[args.experiment]()

    tasks = [
        (experiment, variant, target, index, args.seed + index)
        for variant in experiment.variants
        for target in experiment.occupancies
        for index in range(args.count)
    ]
    runs: list[dict] = []
    controls: list[dict] = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for number, (rows, control_rows) in enumerate(pool.map(run_schedule, tasks, chunksize=4), start=1):
            runs += rows
            controls += control_rows
            if number % args.count == 0:
                variant, target = tasks[number - 1][1:3]
                print(f"{variant} {target:.0%}: {number}/{len(tasks)} schedules, {len(runs)} signals so far")
    infeasible = sum(1 for row in runs if not row["feasible"])
    changed = sum(1 for row in controls if row.get("plan_changed"))
    print(
        f"{len(runs)} signals, {infeasible} infeasible; {changed}/{len(controls)} undisturbed replans changed the plan"
    )
    write_csv(RESULTS_DIR / experiment.name / "runs.csv", runs)
    write_csv(RESULTS_DIR / experiment.name / "controls.csv", controls)


if __name__ == "__main__":
    main()
