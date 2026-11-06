"""Sweeps of fermentation delays over generated schedules, the design of Experiments 1 and 2.

A ``DelaySweep`` holds the variants of one brewery and what is swept on
them. For every variant and target occupancy, ``count`` schedules are
generated with consecutive seeds. Each gets, one at a time, the delays of
the sweep: on each signal day, the batch in a tank whose fermentation would
end in one of the lead days is extended by each of the extra days; a sweep
with ``signals_per_schedule`` gives each schedule only that many of them,
drawn with its seed. Every delay is replanned by the greedy scheduler on a
fresh copy of the baseline on its signal day. As a control, every baseline
is also replanned undisturbed on every day it gets a delay on, which tells
the changes the greedy scheduler makes on its own from those of a delay.

``run`` writes ``<results_dir>/<sweep>/runs.csv``, one row per delay with
the ``control_*`` measures of its day, and ``controls.csv``, one row per
undisturbed replan.
"""

from __future__ import annotations

import argparse
import os
import random
from collections.abc import Callable, Mapping
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from brewery_schedule_digital_twin.domain import Brewery, Recipe, Schedule
from brewery_schedule_digital_twin.generation import ScheduleGenerator
from brewery_schedule_digital_twin.signals import FermentationDelaySignal
from experiments.itch2027.breweries import RECIPES
from experiments.itch2027.measures import BaselineProfile, Row
from experiments.itch2027.replanning import Repair
from experiments.itch2027.storage import write_csv

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
class DelaySweep:
    """The variants of one brewery and the delays they are compared on.

    By default every brewery is compared at the same occupancies over 60
    days: on signal days 3-20, fermentations ending in 1-3 days are
    extended by 1-3 days, all of them unless ``signals_per_schedule`` is set.
    """

    name: str
    variants: Mapping[str, Brewery]
    recipes: tuple[Recipe, ...] = RECIPES
    occupancies: tuple[float, ...] = (1.0, 0.97, 0.95, 0.93, 0.90, 0.85)
    horizon_days: int = 60
    signal_days: range = range(3, 21)
    lead_days: range = range(1, 4)
    extra_days: range = range(1, 4)
    signals_per_schedule: int | None = None

    def generate(self, variant: str, target: float, seed: int) -> tuple[Brewery, Schedule]:
        brewery = self.variants[variant]
        schedule = ScheduleGenerator().fill(brewery, self.recipes, target, self.horizon_days, random.Random(seed))
        return brewery, schedule

    def delays(self, schedule: Schedule) -> list[FermentationDelaySignal]:
        """Every delay of the sweep, by tank, signal day and extra days."""
        return [
            FermentationDelaySignal(day, a.batch_id, a.fermenter.id, extra_days)
            for a in sorted(schedule.assignments, key=lambda a: (a.fermenter.index, a.start_day))
            for day in self.signal_days
            if a.natural_end_day - day in self.lead_days
            for extra_days in self.extra_days
        ]

    def delays_of(self, schedule: Schedule, seed: int) -> list[FermentationDelaySignal]:
        """The delays one schedule gets: all of the sweep, or ``signals_per_schedule`` of them drawn with ``seed``."""
        delays = self.delays(schedule)
        count = self.signals_per_schedule
        if count is None or count >= len(delays):
            return delays
        chosen = set(random.Random(f"signals {seed}").sample(range(len(delays)), count))
        return [delay for index, delay in enumerate(delays) if index in chosen]

    def schedule_columns(self, variant: str, target: float, index: int, seed: int) -> Row:
        return {
            "experiment": self.name,
            "variant": variant,
            "target_occupancy": target,
            "index": index,
            "seed": seed,
            "horizon_days": self.horizon_days,
        }

    def run_schedule(self, variant: str, target: float, index: int, seed: int) -> tuple[list[Row], list[Row]]:
        """The delay rows and the control rows of one schedule."""
        brewery, baseline = self.generate(variant, target, seed)
        row = {
            **self.schedule_columns(variant, target, index, seed),
            **BaselineProfile.of(brewery, baseline, self.horizon_days).as_row(),
        }
        delays = self.delays_of(baseline, seed)
        controls = {
            day: {**row, "signal": False, "signal_day": day, **self.repair_columns(Repair.run(brewery, baseline, day))}
            for day in sorted({delay.day for delay in delays})
        }
        rows = []
        for delay in delays:
            repair = Repair.run(brewery, baseline, delay.day, [delay])
            control = controls[delay.day]
            rows.append(
                {
                    **row,
                    **delay_columns(baseline, delay),
                    **self.repair_columns(repair),
                    **{f"control_{key}": control.get(key) for key in CONTROL_COLUMNS},
                }
            )
        return rows, list(controls.values())

    def repair_columns(self, repair: Repair) -> Row:
        if not repair.feasible:
            return {"scheduler_seconds": repair.scheduler_seconds, "feasible": False, "error": repair.error}
        return {
            "scheduler_seconds": repair.scheduler_seconds,
            "repair_cost": repair.cost(),
            "feasible": True,
            **repair.change(self.horizon_days).as_row(),
        }

    def run(self, results_dir: Path, count: int, first_seed: int, workers: int) -> None:
        """Runs ``count`` schedules per variant and occupancy and writes the sweep's CSVs."""
        tasks = [
            (self, variant, target, index, first_seed + index)
            for variant in self.variants
            for target in self.occupancies
            for index in range(count)
        ]
        runs: list[Row] = []
        controls: list[Row] = []
        with ProcessPoolExecutor(max_workers=workers) as pool:
            for number, (rows, control_rows) in enumerate(pool.map(_run_schedule, tasks, chunksize=4), start=1):
                runs += rows
                controls += control_rows
                if number % count == 0:
                    _, variant, target, _, _ = tasks[number - 1]
                    print(f"{self.name} {variant} {target:.0%}: {number}/{len(tasks)} schedules, {len(runs)} delays")
        infeasible = sum(1 for row in runs if not row["feasible"])
        changed = sum(1 for row in controls if row.get("plan_changed"))
        print(
            f"{self.name}: {len(runs)} delays, {infeasible} infeasible; "
            f"{changed}/{len(controls)} undisturbed replans changed the plan"
        )
        write_csv(results_dir / self.name / "runs.csv", runs)
        write_csv(results_dir / self.name / "controls.csv", controls)


def delay_columns(baseline: Schedule, delay: FermentationDelaySignal) -> Row:
    original = baseline.assignment_for(delay.batch_id, delay.fermenter_id)
    return {
        "signal": True,
        "signal_day": delay.day,
        "delayed_tank": delay.fermenter_id,
        "delayed_tank_capacity": original.fermenter.capacity,
        "extra_days": delay.extra_days,
        "lead_days": original.natural_end_day - delay.day,
        "delayed_batch": delay.batch_id,
        "delayed_recipe": baseline.batches[delay.batch_id].recipe.name,
        "delayed_volume": original.volume,
        "delayed_original_natural_end_day": original.natural_end_day,
        "delayed_original_bottling_day": original.effective_bottling_day,
        "delayed_original_end_day": original.end_day,
    }


def _run_schedule(task: tuple[DelaySweep, str, float, int, int]) -> tuple[list[Row], list[Row]]:
    sweep, variant, target, index, seed = task
    return sweep.run_schedule(variant, target, index, seed)


def main(sweeps: Mapping[str, Callable[[], DelaySweep]], results_dir: Path, description: str) -> None:
    """Runs the sweeps that ``--experiment`` names, all by default, into ``results_dir``."""
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--experiment", nargs="+", choices=sweeps, default=list(sweeps), help="sweeps (default: all)")
    parser.add_argument("--count", type=int, default=10, help="schedules per variant and occupancy (default 10)")
    parser.add_argument("--seed", type=int, default=0, help="seed of the first schedule (default 0)")
    parser.add_argument("--workers", type=int, default=os.cpu_count(), help="processes (default: all CPUs)")
    args = parser.parse_args()
    for name in dict.fromkeys(args.experiment):
        sweeps[name]().run(results_dir, args.count, args.seed, args.workers)
