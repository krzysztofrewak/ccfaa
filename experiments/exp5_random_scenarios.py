"""Robustness check on randomly generated cross-functional scenarios.

``generate`` draws N_SCENARIOS scenarios with a fixed seed and writes them
to ``results/exp5/scenario_definitions.csv``. Each scenario has 2-5
signals from at least two organizational functions, arriving on days 0-13,
with the parameter ranges of the single-signal sweeps (exp2-exp2e). Signals
only name batches, assignments and fermenters present in every
configuration, so each scenario is valid on conf1-conf3.

``run`` reads the scenarios back from that file and simulates each of them
on conf1-conf3 under the three policies until END_DAY, late enough for
every PENDING assessment to be resolved. Every run starts from a fresh
baseline and the same signals. New orders are assessed with
:class:`~experiments.legacy.SalesAgentLegacy`, the Sales rule in force when
this experiment was run.

Writes to ``results/exp5/``:

    scenario_definitions.csv   one row per signal
    run_results.csv            one row per (configuration, scenario, policy)
    policy_differences.csv     one row per (configuration, scenario, policy pair)
    summary.csv                statistics per (configuration, policy)
    summary_common_feasible.csv  the same, over scenarios feasible under all policies
    pairwise_summary.csv       better/equal/worse counts per policy pair
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import random
import statistics
from collections.abc import Callable
from typing import Any

from brewery_agentic_replanning.domain import Batch, Brewery, Recipe, Schedule
from brewery_agentic_replanning.metrics import compute_metrics
from brewery_agentic_replanning.policies import (
    AssessingPolicy,
    CoordinatedPolicy,
    LocallyFilteredPolicy,
    UncoordinatedPolicy,
)
from brewery_agentic_replanning.scheduling import HeuristicScheduler, SchedulingError
from brewery_agentic_replanning.signals import (
    DayOffSignal,
    FermentationDelaySignal,
    FermenterMaintenanceSignal,
    IngredientShortageSignal,
    NewBatchRequirementSignal,
    Signal,
)
from brewery_agentic_replanning.simulation import BreweryDigitalTwin
from experiments.common import placements, write_csv
from experiments.config import RESULTS_DIR, ExperimentConfig, load_config
from experiments.legacy import legacy_agents

SEED = 20260930
N_SCENARIOS = 200
SIGNAL_COUNTS = range(2, 6)
SIGNAL_DAYS = range(0, 14)
END_DAY = 60
IDLE_FROM_DAY = 15
CONFIGURATIONS = ("conf1", "conf2", "conf3")
OUTPUT_DIR = RESULTS_DIR / "exp5"
DEFINITIONS_PATH = OUTPUT_DIR / "scenario_definitions.csv"

POLICIES: dict[str, Callable[[], AssessingPolicy]] = {
    "UNC": UncoordinatedPolicy,
    "LF": LocallyFilteredPolicy,
    "COORD": CoordinatedPolicy,
}
PAIRS = (("LF", "UNC"), ("COORD", "LF"), ("COORD", "UNC"))
LOWER_IS_BETTER = ("scheduler_calls", "changed_batches", "abs_shift", "idle_tank_days")
METRICS = (*LOWER_IS_BETTER, "net_shift")

FUNCTIONS = {
    "Production": ("FermentationDelaySignal",),
    "Procurement": ("IngredientShortageSignal",),
    "Sales": ("NewBatchRequirementSignal",),
    "Operations": ("DayOffSignal", "FermenterMaintenanceSignal"),
}
DEVIATIONS = [extra_days for extra_days in range(-3, 8) if extra_days != 0]
SHORTAGE_OFFSETS = range(1, 14)
MAINTENANCE_START_OFFSETS = range(0, 14)
MAINTENANCE_DURATIONS = range(1, 15)
ORDER_BREW_OFFSETS = range(0, 14)
ORDER_SLACKS = (0, 3, 7, 14)
ORDER_RECIPE = "new_england_ipa"

DEFINITION_FIELDNAMES = [
    "scenario_id",
    "signal_index",
    "day",
    "signal_type",
    "function",
    "batch_id",
    "fermenter_id",
    "extra_days",
    "available_from_day",
    "off_day",
    "start_day",
    "duration_days",
    "planned_brew_day",
    "target_bottling_day",
]
RUN_FIELDNAMES = [
    "configuration",
    "scenario_id",
    "policy",
    "signals",
    "functions",
    "feasible",
    "failure",
    "failure_day",
    "failure_at_order_safe_point",
    "targets_met",
    "unresolved_pending",
    "scheduler_calls",
    "replanning_days",
    "changed_batches",
    "pct_changed_batches",
    "abs_shift",
    "net_shift",
    "idle_tank_days",
]


class CommonBaselineReferences:
    def __init__(self, baselines: list[tuple[Brewery, Schedule]], order_fermentation_days: int) -> None:
        brewery, schedule = baselines[0]
        self.days_off = set(brewery.days_off)
        self.order_fermentation_days = order_fermentation_days
        self.fermenter_ids = sorted(set.intersection(*({f.id for f in b.fermenters} for b, _ in baselines)))
        common = set.intersection(*(_assignment_keys(s) for _, s in baselines))
        self.locked_assignments = sorted(
            (
                a
                for a in schedule.assignments
                if a.has_started(0) and (a.batch_id, a.fermenter.id, a.start_day) in common
            ),
            key=lambda a: (a.batch_id, a.fermenter.id),
        )
        batch_ids = set.intersection(*(set(s.batches) for _, s in baselines))
        self.batches = sorted((schedule.batches[b] for b in batch_ids), key=lambda b: b.id)


def _assignment_keys(schedule: Schedule) -> set[tuple[str, str, int]]:
    return {(a.batch_id, a.fermenter.id, a.start_day) for a in schedule.assignments}


def _draw_signal(rng: random.Random, refs: CommonBaselineReferences, used: set[tuple[str, str]]) -> dict | None:
    function = rng.choice(sorted(FUNCTIONS))
    signal_type = rng.choice(FUNCTIONS[function])
    day = rng.choice(SIGNAL_DAYS)
    row: dict[str, Any] = {"day": day, "signal_type": signal_type, "function": function}

    if signal_type == "FermentationDelaySignal":
        assignments = [a for a in refs.locked_assignments if a.covers(day) and (a.batch_id, a.fermenter.id) not in used]
        if not assignments:
            return None
        assignment = rng.choice(assignments)
        extra_days = rng.choice([d for d in DEVIATIONS if assignment.natural_end_day + d > day])
        used.add((assignment.batch_id, assignment.fermenter.id))
        row.update(batch_id=assignment.batch_id, fermenter_id=assignment.fermenter.id, extra_days=extra_days)
    elif signal_type == "IngredientShortageSignal":
        batches = [b for b in refs.batches if b.planned_brew_day > day]
        if not batches:
            return None
        batch = rng.choice(batches)
        row.update(batch_id=batch.id, available_from_day=day + rng.choice(SHORTAGE_OFFSETS))
    elif signal_type == "NewBatchRequirementSignal":
        brew_days = [day + offset for offset in ORDER_BREW_OFFSETS if day + offset not in refs.days_off]
        planned_brew_day = rng.choice(brew_days)
        row.update(
            planned_brew_day=planned_brew_day,
            target_bottling_day=planned_brew_day + refs.order_fermentation_days + rng.choice(ORDER_SLACKS),
        )
    elif signal_type == "DayOffSignal":
        row.update(off_day=rng.choice(range(day, max(SIGNAL_DAYS) + 1)))
    else:
        row.update(
            fermenter_id=rng.choice(refs.fermenter_ids),
            start_day=day + rng.choice(MAINTENANCE_START_OFFSETS),
            duration_days=rng.choice(MAINTENANCE_DURATIONS),
        )
    return row


def generate_scenarios(refs: CommonBaselineReferences, seed: int, count: int) -> list[dict]:
    rng = random.Random(seed)
    rows: list[dict] = []
    scenarios = 0
    while scenarios < count:
        size = rng.choice(SIGNAL_COUNTS)
        signals: list[dict] = []
        used: set[tuple[str, str]] = set()
        while len(signals) < size:
            drawn = _draw_signal(rng, refs, used)
            if drawn is not None:
                signals.append(drawn)
        if len({s["function"] for s in signals}) < 2:
            continue
        scenarios += 1
        scenario_id = f"R{scenarios:03d}"
        signals.sort(key=lambda s: s["day"])
        orders = [s for s in signals if s["signal_type"] == "NewBatchRequirementSignal"]
        for number, order in enumerate(orders, start=1):
            order["batch_id"] = f"NEW{number}"
        for index, signal in enumerate(signals, start=1):
            rows.append(
                {
                    **dict.fromkeys(DEFINITION_FIELDNAMES, ""),
                    **signal,
                    "scenario_id": scenario_id,
                    "signal_index": index,
                }
            )
    return rows


def build_signal(row: dict, recipes: dict[str, Recipe]) -> Signal:
    day = int(row["day"])
    kind = row["signal_type"]
    if kind == "FermentationDelaySignal":
        return FermentationDelaySignal(
            day=day, batch_id=row["batch_id"], fermenter_id=row["fermenter_id"], extra_days=int(row["extra_days"])
        )
    if kind == "IngredientShortageSignal":
        return IngredientShortageSignal(
            day=day, batch_id=row["batch_id"], available_from_day=int(row["available_from_day"])
        )
    if kind == "NewBatchRequirementSignal":
        batch = Batch(
            row["batch_id"],
            f"New England IPA - order {row['batch_id']}",
            recipes[ORDER_RECIPE],
            planned_brew_day=int(row["planned_brew_day"]),
            target_bottling_day=int(row["target_bottling_day"]),
        )
        return NewBatchRequirementSignal(day=day, batch=batch)
    if kind == "DayOffSignal":
        return DayOffSignal(day=day, off_day=int(row["off_day"]))
    return FermenterMaintenanceSignal(
        day=day,
        fermenter_id=row["fermenter_id"],
        start_day=int(row["start_day"]),
        duration_days=int(row["duration_days"]),
    )


def read_scenarios() -> dict[str, list[dict]]:
    scenarios: dict[str, list[dict]] = {}
    with DEFINITIONS_PATH.open(newline="") as f:
        for row in csv.DictReader(f):
            scenarios.setdefault(row["scenario_id"], []).append(row)
    return scenarios


def unresolved_pending_signals(policy: AssessingPolicy) -> int:
    latest = {id(record.signal): record for record in policy.assessments}
    unresolved = 0
    for record in latest.values():
        if record.assessment.decision != "PENDING":
            continue
        if not any(
            d.decision == "REPLAN" and d.day >= record.day and any(s is record.signal for s in d.signals)
            for d in policy.decisions
        ):
            unresolved += 1
    return unresolved


def _order_safe_points(rows: list[dict], recipes: dict[str, Recipe]) -> set[int]:
    fermentation_days = recipes[ORDER_RECIPE].fermentation_days
    return {
        int(row["target_bottling_day"]) - fermentation_days
        for row in rows
        if row["signal_type"] == "NewBatchRequirementSignal"
    }


def run_one(cfg: ExperimentConfig, rows: list[dict], policy_name: str) -> tuple[dict, set[tuple] | None]:
    recipes = cfg.build_recipes()
    signals = [build_signal(row, recipes) for row in rows]
    brewery, schedule = cfg.baseline()
    policy = POLICIES[policy_name]()
    twin = BreweryDigitalTwin(
        brewery=brewery,
        schedule=schedule,
        scheduler=HeuristicScheduler(),
        policy=policy,
        agents=legacy_agents(),
    )
    for signal in signals:
        twin.inject_signal(signal)

    failure = ""
    failure_day: int | str = ""
    for day in range(END_DAY + 1):
        try:
            twin.advance_to(day)
        except SchedulingError as error:
            failure, failure_day = str(error), day
            break

    row: dict[str, Any] = {
        **dict.fromkeys(RUN_FIELDNAMES, ""),
        "configuration": cfg.name,
        "scenario_id": rows[0]["scenario_id"],
        "policy": policy_name,
        "signals": len(signals),
        "functions": len({r["function"] for r in rows}),
        "feasible": not failure,
        "failure": failure,
        "failure_day": failure_day,
        "failure_at_order_safe_point": failure_day in _order_safe_points(rows, recipes),
        "scheduler_calls": len(twin.replan_log),
        "replanning_days": ";".join(str(day) for day in sorted({e.day for e in twin.replan_log})),
    }
    if failure:
        return row, None

    orders = [s.batch for s in signals if isinstance(s, NewBatchRequirementSignal)]
    metrics = compute_metrics(twin.brewery, twin.original_schedule, twin.schedule, IDLE_FROM_DAY)
    plan_batches = len(set(twin.original_schedule.batches) | set(twin.schedule.batches))
    row.update(
        targets_met=all(
            twin.schedule.assignments_of(b.id)
            and all(a.bottling_day == b.target_bottling_day for a in twin.schedule.assignments_of(b.id))
            for b in orders
        ),
        unresolved_pending=unresolved_pending_signals(policy),
        changed_batches=metrics.changed_batches,
        pct_changed_batches=round(100 * metrics.changed_batches / plan_batches, 1),
        abs_shift=metrics.total_brew_shift_days + metrics.total_bottling_shift_days,
        net_shift=metrics.schedule_shift_days,
        idle_tank_days=metrics.idle_fermenter_days,
    )
    return row, placements(twin.schedule)


def _difference_rows(results: dict[tuple[str, str, str], dict], finals: dict) -> list[dict]:
    rows = []
    for cfg_name, scenario_id in sorted({(c, s) for c, s, _ in results}):
        for first, second in PAIRS:
            a = results[(cfg_name, scenario_id, first)]
            b = results[(cfg_name, scenario_id, second)]
            both = a["feasible"] and b["feasible"]
            row = {
                "configuration": cfg_name,
                "scenario_id": scenario_id,
                "pair": f"{first}-{second}",
                "feasible_first": a["feasible"],
                "feasible_second": b["feasible"],
                "same_final_schedule": both
                and finals[(cfg_name, scenario_id, first)] == finals[(cfg_name, scenario_id, second)],
            }
            for metric in METRICS:
                comparable = both or metric == "scheduler_calls"
                row[f"delta_{metric}"] = a[metric] - b[metric] if comparable else ""
            rows.append(row)
    return rows


def _quantile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(q * (len(ordered) - 1)))]


def _feasible_under_all(run_rows: list[dict]) -> set[tuple[str, str]]:
    infeasible = {(r["configuration"], r["scenario_id"]) for r in run_rows if not r["feasible"]}
    return {(r["configuration"], r["scenario_id"]) for r in run_rows} - infeasible


def _summary_rows(run_rows: list[dict], scenarios: set[tuple[str, str]] | None = None) -> list[dict]:
    rows = []
    for cfg_name in CONFIGURATIONS:
        for policy_name in POLICIES:
            group = [
                r
                for r in run_rows
                if r["configuration"] == cfg_name
                and r["policy"] == policy_name
                and (scenarios is None or (cfg_name, r["scenario_id"]) in scenarios)
            ]
            feasible = [r for r in group if r["feasible"]]
            row: dict[str, Any] = {
                "configuration": cfg_name,
                "policy": policy_name,
                "runs": len(group),
                "feasible": len(feasible),
                "targets_met": sum(1 for r in feasible if r["targets_met"]),
                "unresolved_pending": sum(r["unresolved_pending"] for r in feasible),
                "total_scheduler_calls": sum(r["scheduler_calls"] for r in group),
            }
            for metric in METRICS:
                values = [r[metric] for r in (group if metric == "scheduler_calls" else feasible)]
                row[f"{metric}_mean"] = round(statistics.mean(values), 2)
                row[f"{metric}_median"] = _quantile(values, 0.5)
                row[f"{metric}_p90"] = _quantile(values, 0.9)
                row[f"{metric}_max"] = max(values)
            rows.append(row)
    return rows


def _pairwise_rows(differences: list[dict]) -> list[dict]:
    rows = []
    for cfg_name in (*CONFIGURATIONS, "all"):
        for first, second in PAIRS:
            group = [
                d for d in differences if d["pair"] == f"{first}-{second}" and cfg_name in ("all", d["configuration"])
            ]
            both = [d for d in group if d["feasible_first"] and d["feasible_second"]]
            row: dict[str, Any] = {
                "configuration": cfg_name,
                "pair": f"{first} vs {second}",
                "scenarios": len(group),
                "both_feasible": len(both),
                "only_first_feasible": sum(1 for d in group if d["feasible_first"] and not d["feasible_second"]),
                "only_second_feasible": sum(1 for d in group if d["feasible_second"] and not d["feasible_first"]),
                "same_final_schedule": sum(1 for d in both if d["same_final_schedule"]),
            }
            for metric in METRICS:
                deltas = [d[f"delta_{metric}"] for d in (group if metric == "scheduler_calls" else both)]
                row[f"{metric}_lower"] = sum(1 for x in deltas if x < 0)
                row[f"{metric}_equal"] = sum(1 for x in deltas if x == 0)
                row[f"{metric}_higher"] = sum(1 for x in deltas if x > 0)
            rows.append(row)
    return rows


def generate() -> None:
    configs = [load_config(name) for name in CONFIGURATIONS]
    refs = CommonBaselineReferences(
        [cfg.baseline() for cfg in configs],
        order_fermentation_days=configs[0].build_recipes()[ORDER_RECIPE].fermentation_days,
    )
    write_csv(DEFINITIONS_PATH, generate_scenarios(refs, SEED, N_SCENARIOS), DEFINITION_FIELDNAMES)
    print(f"sha256 {hashlib.sha256(DEFINITIONS_PATH.read_bytes()).hexdigest()}")


def run() -> None:
    scenarios = read_scenarios()
    configs = [load_config(name) for name in CONFIGURATIONS]
    results: dict[tuple[str, str, str], dict] = {}
    finals: dict[tuple[str, str, str], set[tuple] | None] = {}
    for cfg in configs:
        for scenario_id, rows in scenarios.items():
            for policy_name in POLICIES:
                key = (cfg.name, scenario_id, policy_name)
                results[key], finals[key] = run_one(cfg, rows, policy_name)

    run_rows = list(results.values())
    differences = _difference_rows(results, finals)
    summary = _summary_rows(run_rows)
    common_summary = _summary_rows(run_rows, _feasible_under_all(run_rows))
    pairwise = _pairwise_rows(differences)
    write_csv(OUTPUT_DIR / "run_results.csv", run_rows, RUN_FIELDNAMES)
    write_csv(OUTPUT_DIR / "policy_differences.csv", differences)
    write_csv(OUTPUT_DIR / "summary.csv", summary)
    write_csv(OUTPUT_DIR / "summary_common_feasible.csv", common_summary)
    write_csv(OUTPUT_DIR / "pairwise_summary.csv", pairwise)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("stage", choices=("generate", "run"))
    args = parser.parse_args()
    if args.stage == "generate":
        generate()
    else:
        run()


if __name__ == "__main__":
    main()
