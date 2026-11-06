"""Experiment 4: the effect of tank heterogeneity alone on the stability of a schedule.

Three fleets of 10 unitanks and 200 hl (``breweries.heterogeneity``) with the
same brewhouse and line get, for each set of ``--sets``, one ``OrderSet``:
recipes, volumes and days of availability drawn without looking at any tank.
The greedy scheduler plans each fleet from these orders alone, underfilling
and splitting where it needs to, so the occupancy is measured, not imposed.

Every batch that every fleet ends inside ``SIGNAL_DAYS`` gets, one at a
time, a ``Delay`` of each of ``EXTRA_DAYS`` in all its vessels, ``LEAD_DAYS``
before each fleet's planned end of that batch, so the signal day differs
between fleets but the warning does not. The method of ``--method`` repairs
each on a fresh copy of the baseline: the greedy scheduler, CP-SAT or
differential evolution, with the budgets of ``METHODS``; CP-SAT also records
its lower bound on the repair cost. Each baseline is also replanned
undisturbed by the greedy scheduler on each signal day as a control.

D_pack is the total absolute shift of the batches' last bottling starts.
``FleetComparison`` averages the measures per set and fleet and compares the
fleets paired by set; ``MethodComparison`` compares the fleets under every
method that has runs and every method's cost with CP-SAT's bound.

Runs go to the ``RunLog`` of ``results/exp4/<method>/runs/`` as they end, so
an interrupted run resumes. ``--orders-per-day`` and ``--first-set`` run a
robustness check on other loads and orders into a directory of their own.
"""

from __future__ import annotations

import argparse
import os
import random
import time
from collections import defaultdict
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from functools import cached_property
from itertools import combinations
from pathlib import Path
from statistics import mean

from brewery_schedule_digital_twin.domain import Batch, Brewery, Schedule
from brewery_schedule_digital_twin.scheduling import HeuristicScheduler
from brewery_schedule_digital_twin.signals import FermentationDelaySignal
from experiments.itch2027 import breweries
from experiments.itch2027.estimates import Estimate, PairedDifference
from experiments.itch2027.measures import (
    BaselineProfile,
    BatchChange,
    Row,
    fingerprint,
    replanning_problems,
    split_batches,
)
from experiments.itch2027.methods import RepairMethod, solver_columns
from experiments.itch2027.replanning import Repair
from experiments.itch2027.storage import RESULTS_DIR, RunLog, write_csv

SETS = 30
PILOT_SETS = 2
HORIZON_DAYS = 60
FIRST_ORDER_DAY = -18
BATCH_VOLUMES = tuple(500.0 * brews for brews in range(1, 8))
ORDERS_PER_DAY = 0.5
SIGNAL_DAYS = range(3, 21)
LEAD_DAYS = 3
EXTRA_DAYS = (1, 2, 3)
ALL_DELAYS = "all"

METHODS: dict[str, tuple[RepairMethod, ...]] = {
    "greedy": (RepairMethod("greedy"),),
    "cpsat": (RepairMethod("cpsat", 0, {"time_limit_seconds": 300.0, "workers": 4, "max_placements": 200}),),
    "de": (RepairMethod("de", 0, {"population_size": 30, "generations": 100, "time_limit_seconds": 600.0}),),
}
METRICS = (
    "d_pack",
    "d_pack_others",
    "d_pack_delayed",
    "shifted_bottlings",
    "retanked_batches",
    "changed_other_batches",
    "repair_cost",
    "baseline_split_batches",
    "repaired_split_batches",
    "added_split_batches",
    "delayed_vessels",
    "baseline_occupancy",
    "baseline_volume_occupancy",
    "repaired_occupancy",
    "baseline_mean_wait_days",
    "control_d_pack",
    "scheduler_seconds",
)
METHOD_METRICS = {
    "greedy": (),
    "cpsat": ("cpsat_best_bound", "cpsat_gap", "cpsat_optimal", "cpsat_returned_start"),
    "de": ("de_improved",),
}
REPORTED_PAIRS = ("d_pack", "d_pack_others", "repair_cost", "cpsat_best_bound", "retanked_batches")
FLEETS = tuple(breweries.heterogeneity())
FLEET_PAIRS = tuple(combinations(FLEETS, 2))


@dataclass(frozen=True)
class OrderSet:
    """The orders of one set, the same for every fleet, and the baseline each fleet plans from them.

    Orders arrive as a Poisson process of ``orders_per_day`` from
    ``FIRST_ORDER_DAY``, one longest fermentation before day 0, so the
    brewery is already running on day 0; each is a whole number of brews.
    """

    seed: int
    orders_per_day: float = ORDERS_PER_DAY

    @cached_property
    def orders(self) -> tuple[Batch, ...]:
        rng = random.Random(f"production {self.seed}")
        orders: list[Batch] = []
        elapsed = 0.0
        while True:
            elapsed += rng.expovariate(self.orders_per_day)
            volume = rng.choice(BATCH_VOLUMES)
            recipe = rng.choice(breweries.RECIPES)
            day = FIRST_ORDER_DAY + int(elapsed)
            if day >= HORIZON_DAYS:
                return tuple(orders)
            batch_id = f"P{len(orders) + 1:03d}"
            orders.append(Batch(batch_id, f"{recipe.name} #{batch_id}", recipe, day, volume=volume))

    def baseline(self, brewery: Brewery) -> Schedule:
        """The greedy scheduler places the orders by their day of availability."""
        schedule = Schedule(batches={batch.id: batch for batch in self.orders})
        return HeuristicScheduler().replan(brewery, schedule, FIRST_ORDER_DAY)

    @cached_property
    def baselines(self) -> dict[str, Schedule]:
        return {fleet: self.baseline(brewery) for fleet, brewery in breweries.heterogeneity().items()}

    def shared_batches(self) -> list[str]:
        """The batches every fleet ends so that their delay falls in the window."""
        return [
            batch.id
            for batch in sorted(self.orders, key=lambda b: b.id)
            if all(_batch_end(s, batch.id) - LEAD_DAYS in SIGNAL_DAYS for s in self.baselines.values())
        ]

    def delays(self) -> list[Delay]:
        return [
            Delay(self, fleet, batch_id, extra_days)
            for batch_id in self.shared_batches()
            for fleet in FLEETS
            for extra_days in EXTRA_DAYS
        ]


def _batch_end(schedule: Schedule, batch_id: str) -> int:
    return max(a.natural_end_day for a in schedule.assignments_of(batch_id))


@dataclass(frozen=True)
class Delay:
    """The delay of every vessel of one batch of one fleet: the same beer ferments longer however it was split."""

    orders: OrderSet
    fleet: str
    batch_id: str
    extra_days: int

    @property
    def scenario_id(self) -> str:
        return f"{self.orders.seed:02d}-{self.fleet}-{self.batch_id}-x{self.extra_days}"

    @property
    def brewery(self) -> Brewery:
        return breweries.heterogeneity()[self.fleet]

    @property
    def baseline(self) -> Schedule:
        return self.orders.baselines[self.fleet]

    @property
    def day(self) -> int:
        return _batch_end(self.baseline, self.batch_id) - LEAD_DAYS

    def signals(self) -> list[FermentationDelaySignal]:
        return [
            FermentationDelaySignal(self.day, self.batch_id, a.fermenter.id, self.extra_days)
            for a in self.baseline.assignments_of(self.batch_id)
        ]

    def run(self, method: RepairMethod) -> Row:
        """The row of ``method``'s repair of this delay, with the baseline and the undisturbed control."""
        brewery, baseline = self.brewery, self.baseline
        control = Repair.run(brewery, baseline, self.day)
        signals = self.signals()
        scheduler = method.scheduler()
        repair = Repair.run(brewery, baseline, self.day, signals, scheduler)
        row: Row = {
            "scenario_id": self.scenario_id,
            "method": method.name,
            "method_seed": method.seed,
            "seed": self.orders.seed,
            "fleet": self.fleet,
            "baseline_fingerprint": fingerprint(baseline),
            **_baseline_measures(brewery, baseline),
            "control_d_pack": control.change(HORIZON_DAYS).abs_bottling_shift_days if control.feasible else None,
            "delayed_batch": self.batch_id,
            "delayed_volume": sum(a.volume for a in baseline.assignments_of(self.batch_id)),
            "delayed_vessels": len(signals),
            "extra_days": self.extra_days,
            "lead_days": LEAD_DAYS,
            "signal_day": self.day,
        }
        if not repair.feasible:
            return {**row, "status": "infeasible", "error": repair.error, **solver_columns(scheduler)}
        repaired = repair.repaired()
        change = repair.change(HORIZON_DAYS)
        d_pack = change.abs_bottling_shift_days
        d_pack_delayed = abs(BatchChange.between(baseline, repaired, self.batch_id).bottling_shift or 0)
        problems = repair.problems()
        return {
            **row,
            "scheduler_seconds": repair.scheduler_seconds,
            "status": "solved" if not problems else "invalid",
            "validation_problems": len(problems),
            "d_pack": d_pack,
            "d_pack_others": d_pack - d_pack_delayed,
            "d_pack_delayed": d_pack_delayed,
            "shifted_bottlings": change.shifted_bottlings,
            "retanked_batches": change.retanked_batches,
            "changed_other_batches": change.changed_other_batches,
            "repair_cost": repair.cost(),
            "repaired_split_batches": split_batches(repaired),
            "added_split_batches": split_batches(repaired) - split_batches(baseline),
            "repaired_occupancy": change.replanned_occupancy,
            **solver_columns(scheduler),
        }


def _baseline_measures(brewery: Brewery, schedule: Schedule) -> Row:
    """The baseline profile, with its occupancy by volume and how long the orders wait to be brewed.

    An order brewed after its day of availability is no problem of a baseline planned from orders.
    """
    litre_days = sum(
        max(0, min(a.end_day, HORIZON_DAYS) - max(a.start_day, 0)) * a.volume for a in schedule.assignments
    )
    waits = [schedule.brew_day_of(batch_id) - batch.planned_brew_day for batch_id, batch in schedule.batches.items()]
    profile = BaselineProfile.of(brewery, schedule, HORIZON_DAYS).as_row()
    return {
        **profile,
        "baseline_problems": len(replanning_problems(brewery, schedule)),
        "baseline_volume_occupancy": litre_days / (sum(v.capacity for v in brewery.vessels) * HORIZON_DAYS),
        "baseline_mean_wait_days": mean(waits),
    }


def _run_delay(delay: Delay, method: RepairMethod, log: RunLog) -> None:
    log.append(delay.scenario_id, delay.run(method))


@dataclass(frozen=True)
class Design:
    """What one run of the experiment covers: its sets of orders and their load."""

    sets: range
    orders_per_day: float = ORDERS_PER_DAY

    def delays(self) -> list[Delay]:
        return [delay for seed in self.sets for delay in OrderSet(seed, self.orders_per_day).delays()]

    def results_root(self, pilot: bool) -> Path:
        if pilot:
            return RESULTS_DIR / "exp4_pilot"
        if self.sets.start == 0 and self.orders_per_day == ORDERS_PER_DAY:
            return RESULTS_DIR / "exp4"
        return RESULTS_DIR / f"exp4_orders{self.orders_per_day:g}_sets{self.sets.start}-{self.sets.stop - 1}"


def run_method(name: str, design: Design, log: RunLog, workers: int) -> list[Row]:
    """Every run of method ``name`` not yet in ``log``; then all its runs."""
    done = {(run["scenario_id"], run["method_seed"]) for run in log.read()}
    tasks = [
        (delay, method)
        for delay in design.delays()
        for method in METHODS[name]
        if (delay.scenario_id, method.seed) not in done
    ]
    print(f"{name}: {len(done)} runs done, {len(tasks)} to run on {workers} processes", flush=True)
    start = time.perf_counter()
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_run_delay, delay, method, log) for delay, method in tasks]
        for number, future in enumerate(as_completed(futures), start=1):
            future.result()
            if number % 50 == 0 or number == len(futures):
                print(f"  {number}/{len(futures)} runs, {(time.perf_counter() - start) / 60:.1f} min", flush=True)
    return log.read()


class FleetComparison:
    """The runs of one method averaged by set, fleet and delay, and the fleets compared set by set.

    A set with a repair that failed in any fleet is dropped for every fleet,
    so the fleets are always compared on the same delays.
    """

    def __init__(self, runs: Sequence[Row]) -> None:
        self.runs = sorted(runs, key=lambda run: (run["scenario_id"], run["method_seed"] or 0))
        methods = sorted({run["method"] for run in runs})
        self.metrics = METRICS + tuple(metric for name in methods for metric in METHOD_METRICS[name])
        self.sets = self._set_rows()

    def _set_rows(self) -> list[Row]:
        failed = {run["seed"] for run in self.runs if run["status"] != "solved"}
        groups: dict[tuple, list[Row]] = defaultdict(list)
        for run in self.runs:
            if run["seed"] not in failed:
                groups[run["seed"], run["fleet"], run["extra_days"]].append(run)
                groups[run["seed"], run["fleet"], ALL_DELAYS].append(run)
        return [
            {
                "seed": seed,
                "fleet": fleet,
                "extra_days": extra_days,
                "delays": len(group),
                **{metric: mean(float(run[metric]) for run in group) for metric in self.metrics},
            }
            for (seed, fleet, extra_days), group in sorted(groups.items(), key=lambda item: str(item[0]))
        ]

    def summary(self) -> list[Row]:
        """Means over the sets ± 95% CI by fleet and delay."""
        groups: dict[tuple[str, str], list[Row]] = defaultdict(list)
        for entry in self.sets:
            groups[entry["fleet"], str(entry["extra_days"])].append(entry)
        rows = []
        for (fleet, extra_days), group in sorted(groups.items()):
            row: Row = {"fleet": fleet, "extra_days": extra_days, "sets": len(group)}
            for metric in self.metrics:
                estimate = Estimate.of(entry[metric] for entry in group)
                row.update({metric: estimate.mean, f"{metric}_ci95": estimate.ci95})
            rows.append(row)
        return rows

    def paired(self) -> list[Row]:
        """The second fleet minus the first, set by set, for every delay and over all of them."""
        values = {(entry["seed"], entry["fleet"], str(entry["extra_days"])): entry for entry in self.sets}
        seeds = sorted({entry["seed"] for entry in self.sets})
        rows = []
        for extra_days in sorted({str(entry["extra_days"]) for entry in self.sets}):
            for first, second in FLEET_PAIRS:
                for metric in self.metrics:
                    difference = PairedDifference.of(
                        values[seed, second, extra_days][metric] - values[seed, first, extra_days][metric]
                        for seed in seeds
                    )
                    rows.append(
                        {
                            "extra_days": extra_days,
                            "metric": metric,
                            "first": first,
                            "second": second,
                            **difference.columns(),
                            "sets": difference.estimate.n,
                        }
                    )
        return rows

    def write(self, results_dir: Path) -> None:
        summary, paired = self.summary(), self.paired()
        write_csv(results_dir / "runs.csv", self.runs)
        write_csv(results_dir / "sets.csv", self.sets)
        write_csv(results_dir / "summary.csv", summary)
        write_csv(results_dir / "paired.csv", paired)
        failed = sum(run["status"] != "solved" for run in self.runs)
        moved = sum(1 for run in self.runs if run["control_d_pack"])
        problems = sum(run["baseline_problems"] for run in self.runs)
        print(f"\n{len(self.runs)} repairs, {failed} failed; invalid baselines {problems}; moved by a control {moved}")
        print("\nall delays, mean over sets ± 95% CI")
        for metric in self.metrics:
            cells = [
                f"{row['fleet']} {Estimate(row[metric], row[f'{metric}_ci95'], 0).format('{:.3f}')}"
                for row in summary
                if row["extra_days"] == ALL_DELAYS
            ]
            print(f"  {metric:26} " + "   ".join(cells))
        print("\nsecond fleet minus first, paired by set ± 95% CI [lower/equal/higher]")
        for row in paired:
            if row["metric"] in REPORTED_PAIRS:
                print(
                    f"  {row['metric']} delay {row['extra_days']} {row['second']}-{row['first']}: "
                    f"{row['mean_second_minus_first']:+.3f} ±{row['ci95'] or 0:.3f} "
                    f"[{row['second_lower']}/{row['equal']}/{row['second_higher']}]"
                )


class MethodComparison:
    """The fleets compared under every method that has runs, and every method's cost against CP-SAT's bound."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.runs = {name: RunLog(root / name / "runs").read() for name in METHODS if (root / name / "runs").exists()}

    def fleets(self) -> list[Row]:
        return [
            {"method": name} | row for name, runs in self.runs.items() if runs for row in FleetComparison(runs).paired()
        ]

    def bounds(self) -> list[Row]:
        """By method and fleet, on the scenarios CP-SAT solved: the mean cost, CP-SAT's bound and the gap to it."""
        bounds = {run["scenario_id"]: run for run in self.runs.get("cpsat", []) if run["status"] == "solved"}
        costs: dict[tuple[str, str], list[Row]] = defaultdict(list)
        for name, runs in self.runs.items():
            for run in runs:
                if run["status"] == "solved" and run["scenario_id"] in bounds:
                    costs[name, run["fleet"]].append(run)
        rows = []
        for (name, fleet), group in sorted(costs.items()):
            gaps = [run["repair_cost"] - bounds[run["scenario_id"]]["cpsat_best_bound"] for run in group]
            rows.append(
                {
                    "method": name,
                    "fleet": fleet,
                    "runs": len(group),
                    "repair_cost": mean(run["repair_cost"] for run in group),
                    "cpsat_best_bound": mean(bounds[run["scenario_id"]]["cpsat_best_bound"] for run in group),
                    "gap_to_bound": mean(gaps),
                    "at_bound": mean(gap <= 1e-9 for gap in gaps),
                    "below_bound": sum(gap < -1e-9 for gap in gaps),
                    "d_pack": mean(run["d_pack"] for run in group),
                }
            )
        return rows

    def write(self) -> None:
        write_csv(self.root / "methods.csv", self.fleets())
        rows = self.bounds()
        if not rows:
            return
        write_csv(self.root / "bounds.csv", rows)
        print("\nagainst the CP-SAT bound: repair cost | bound | gap | at bound | below bound | D_pack")
        for row in rows:
            print(
                f"  {row['method']:6} {row['fleet']}: {row['repair_cost']:.1f} | {row['cpsat_best_bound']:.1f} | "
                f"{row['gap_to_bound']:.1f} | {row['at_bound']:.0%} | {row['below_bound']} | {row['d_pack']:.2f}"
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--method", choices=METHODS, default="greedy", help="the repair method (default greedy)")
    parser.add_argument("--sets", type=int, default=SETS, help=f"sets of orders (default {SETS})")
    parser.add_argument("--first-set", type=int, default=0, help="seed of the first set of orders (default 0)")
    parser.add_argument("--orders-per-day", type=float, default=ORDERS_PER_DAY, help="order rate (default 0.5)")
    parser.add_argument("--pilot", action="store_true", help=f"only the first {PILOT_SETS} sets")
    parser.add_argument("--aggregate-only", action="store_true", help="aggregate the runs there are, run nothing")
    parser.add_argument("--workers", type=int, help="processes (default: the CPUs over the method's threads)")
    args = parser.parse_args()
    sets = PILOT_SETS if args.pilot else args.sets
    design = Design(range(args.first_set, args.first_set + sets), args.orders_per_day)
    root = design.results_root(args.pilot)
    log = RunLog(root / args.method / "runs")
    threads = max(method.threads for method in METHODS[args.method])
    workers = args.workers or max(1, (os.cpu_count() or 1) // threads)
    runs = log.read() if args.aggregate_only else run_method(args.method, design, log, workers)
    FleetComparison(runs).write(root / args.method)
    MethodComparison(root).write()


if __name__ == "__main__":
    main()
