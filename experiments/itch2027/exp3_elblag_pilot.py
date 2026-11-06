"""A pilot of CP-SAT with a longer limit and several workers on the Elbląg scenarios of Experiment 3.

With 10 seconds and one worker CP-SAT found no schedule in 309 of the 360
Elbląg scenarios of Experiment 3 and only its starting solution in the other
51. The pilot runs the greedy scheduler and CP-SAT with ``--seconds``,
``--solver-workers``, ``--max-placements`` and any ``--param`` of the solver
on the scenarios of schedules ``--indices`` of Elbląg (72 by default), or on
the 12 of ``representative``, through the runner of Experiment 3, so the
baselines, delays, validation and costs are the same. Each setting goes to
``results/exp3_elblag_pilot/<setting>/``, and ``report.csv`` gives, per
variant and occupancy, the share of valid schedules, whether CP-SAT's search
found a schedule at all, how often it returned only its starting solution or
proved optimality, and how its cost and D_pack compare with the greedy
scheduler's. ``comparison/`` holds the tables of Experiment 3's
``Comparison`` for the same scenarios, with the differential evolution runs
of Experiment 3 (10 s, three seeds) next to the pilot's greedy and CP-SAT
runs, so the three methods are compared on one sample without running
differential evolution again.
"""

from __future__ import annotations

import argparse
import os
from collections import defaultdict
from collections.abc import Iterable, Sequence
from statistics import mean, median

from experiments.itch2027.exp3 import EXTRA_DAYS, METHODS, Comparison, Manifest, ScenarioRunner, run_key
from experiments.itch2027.measures import Row
from experiments.itch2027.methods import RepairMethod
from experiments.itch2027.storage import RESULTS_DIR, RunLog, write_csv

INDICES = (0, 1)
SECONDS = 60.0
SOLVER_WORKERS = 4
MAX_PLACEMENTS = 40


def representative(manifest: Manifest) -> Manifest:
    """Schedule 0 of every Elbląg variant and occupancy once, each variant and occupancy with every delay."""
    elblag = manifest.where(experiment="elblag", index=0)
    variants = sorted({s.variant for s in elblag.scenarios})
    occupancies = sorted({s.target_occupancy for s in elblag.scenarios})
    wanted = {
        (variant, occupancy, EXTRA_DAYS[(v + o) % len(EXTRA_DAYS)])
        for v, variant in enumerate(variants)
        for o, occupancy in enumerate(occupancies)
    }
    return Manifest(s for s in elblag.scenarios if (s.variant, s.target_occupancy, s.extra_days) in wanted)


def with_exp3_runs(runs: Sequence[Row], exp3_runs: Iterable[Row], method: str = "de") -> list[Row]:
    """The pilot's runs and Experiment 3's runs of ``method`` on the same scenarios."""
    ids = {run["scenario_id"] for run in runs}
    expected = {(scenario_id, m.name, m.seed) for scenario_id in ids for m in METHODS if m.name == method}
    added = [run for run in exp3_runs if run_key(run) in expected]
    if len(added) < len(expected):
        print(f"warning: {len(expected) - len(added)} of {len(expected)} {method} runs of Experiment 3 are missing")
    return [*runs, *added]


def report_rows(runs: Sequence[Row]) -> list[Row]:
    """Per variant and occupancy, and in all: CP-SAT's outcomes and how it compares with the greedy scheduler."""
    greedy = {run["scenario_id"]: run for run in runs if run["method"] == "greedy"}
    groups: dict[tuple, list[Row]] = defaultdict(list)
    for run in runs:
        if run["method"] == "cpsat" and run["scenario_id"] in greedy:
            groups[run["variant"], run["target_occupancy"]].append(run)
            groups["all", "all"].append(run)
    rows = []
    for (variant, occupancy), group in sorted(groups.items(), key=lambda item: str(item[0])):
        valid = [r for r in group if r["status"] == "solved"]
        paired = [(greedy[r["scenario_id"]], r) for r in valid if greedy[r["scenario_id"]]["status"] == "solved"]
        seconds = [r["scheduler_seconds"] for r in group if r.get("scheduler_seconds") is not None]
        gaps = [r for r in valid if r["cpsat_gap"] is not None]
        rows.append(
            {
                "variant": variant,
                "target_occupancy": occupancy,
                "scenarios": len(group),
                "valid": len(valid),
                "hint_completed": sum(bool(r["cpsat_hint_completed"]) for r in group),
                "search_found": sum(r["solver_status"] in ("OPTIMAL", "FEASIBLE") for r in group),
                "returned_start": sum(bool(r["cpsat_returned_start"]) for r in valid),
                "optimal": sum(r["solver_status"] == "OPTIMAL" for r in group),
                "cost_lower": sum(c["repair_cost"] < g["repair_cost"] for g, c in paired),
                "d_pack_lower": sum(c["abs_bottling_shift_days"] < g["abs_bottling_shift_days"] for g, c in paired),
                "d_pack_higher": sum(c["abs_bottling_shift_days"] > g["abs_bottling_shift_days"] for g, c in paired),
                "greedy_repair_cost": _mean(g["repair_cost"] for g, _ in paired),
                "cpsat_repair_cost": _mean(c["repair_cost"] for _, c in paired),
                "greedy_d_pack": _mean(g["abs_bottling_shift_days"] for g, _ in paired),
                "cpsat_d_pack": _mean(c["abs_bottling_shift_days"] for _, c in paired),
                "mean_gap": _mean(r["cpsat_gap"] for r in gaps),
                "mean_relative_gap": _mean(r["cpsat_gap"] / r["repair_cost"] for r in gaps if r["repair_cost"]),
                "mean_scheduler_seconds": _mean(seconds),
                "median_scheduler_seconds": median(seconds) if seconds else None,
                "max_scheduler_seconds": max(seconds, default=None),
                "mean_model_seconds": _mean(r["cpsat_model_seconds"] or 0 for r in group),
            }
        )
    return rows


def _mean(values: Iterable[float]) -> float | None:
    present = list(values)
    return mean(present) if present else None


def _format(value: float | None, pattern: str = "{:.1f}") -> str:
    return "-" if value is None else pattern.format(value)


def print_report(rows: Sequence[Row]) -> None:
    print(
        "\nvariant occ | valid | hint ok | search found | start returned | optimal | cost < greedy"
        " | D_pack < / > greedy | cost greedy -> cpsat | D_pack greedy -> cpsat | gap (rel) | seconds mean/median/max"
    )
    for r in rows:
        occupancy = r["target_occupancy"] if r["target_occupancy"] == "all" else f"{r['target_occupancy']:.0%}"
        print(
            f"  {r['variant']:>3} {occupancy:>4} | {r['valid']}/{r['scenarios']} | {r['hint_completed']}"
            f" | {r['search_found']} | {r['returned_start']} | {r['optimal']} | {r['cost_lower']}"
            f" | {r['d_pack_lower']} / {r['d_pack_higher']}"
            f" | {_format(r['greedy_repair_cost'])} -> {_format(r['cpsat_repair_cost'])}"
            f" | {_format(r['greedy_d_pack'])} -> {_format(r['cpsat_d_pack'])}"
            f" | {_format(r['mean_gap'])} ({_format(r['mean_relative_gap'], '{:.0%}')})"
            f" | {_format(r['mean_scheduler_seconds'])}/{_format(r['median_scheduler_seconds'])}"
            f"/{_format(r['max_scheduler_seconds'])}"
        )


def _parameter(text: str) -> tuple[str, int]:
    name, value = text.split("=")
    return name, int(value)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seconds", type=float, default=SECONDS, help="CP-SAT's limit a repair")
    parser.add_argument("--solver-workers", type=int, default=SOLVER_WORKERS, help="CP-SAT's workers a repair")
    parser.add_argument("--max-placements", type=int, default=MAX_PLACEMENTS, help="CP-SAT's candidates a batch")
    parser.add_argument("--param", type=_parameter, action="append", default=[], help="e.g. symmetry_level=0")
    parser.add_argument("--indices", type=int, nargs="+", default=list(INDICES), help="the schedules of the sample")
    parser.add_argument("--representative", action="store_true", help="only the 12 scenarios of representative()")
    parser.add_argument("--aggregate-only", action="store_true", help="report the runs there are, run nothing")
    parser.add_argument("--workers", type=int, help="processes (default: the CPUs over the solver workers)")
    args = parser.parse_args()
    settings = {
        "time_limit_seconds": args.seconds,
        "workers": args.solver_workers,
        "max_placements": args.max_placements,
        "solver_parameters": dict(args.param),
    }
    methods = (RepairMethod("greedy"), RepairMethod("cpsat", 0, settings))
    manifest = Manifest.read(RESULTS_DIR / "exp3" / "manifest.csv")
    if args.representative:
        manifest = representative(manifest)
    else:
        manifest = manifest.where(experiment="elblag", index=tuple(args.indices))
    label = f"cpsat-{args.seconds:g}s-{args.solver_workers}w-{args.max_placements}p"
    label += "".join(f"-{name}{value}" for name, value in args.param)
    results_dir = RESULTS_DIR / "exp3_elblag_pilot" / label
    log = RunLog(results_dir / "runs")
    print(f"{len(manifest.scenarios)} Elbląg scenarios, {label}, into {results_dir}")
    if not args.aggregate_only:
        workers = args.workers or max(1, (os.cpu_count() or 2) // args.solver_workers)
        ScenarioRunner(methods, log).run(manifest, workers)
    ids = {scenario.scenario_id for scenario in manifest.scenarios}
    runs = sorted(
        (run for run in log.read() if run["scenario_id"] in ids), key=lambda r: (r["scenario_id"], r["method"])
    )
    write_csv(results_dir / "runs.csv", runs)
    rows = report_rows(runs)
    write_csv(results_dir / "report.csv", rows)
    print_report(rows)
    Comparison(with_exp3_runs(runs, RunLog(RESULTS_DIR / "exp3" / "runs").read())).write(results_dir / "comparison")


if __name__ == "__main__":
    main()
