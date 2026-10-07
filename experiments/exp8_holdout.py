"""Hold-out robustness test of the final method.

A new set of N_SCENARIOS random cross-functional scenarios, drawn with a
seed not used before and with the generator of exp5 (same signal types,
parameter ranges and comparability constraints), is run on conf1-conf3
under UNC, LF and COORD with the production agents until END_DAY. The
scenarios were not used to design or adjust any rule.

``generate`` writes ``scenario_definitions.csv`` and its SHA-256 to
``scenario_definitions.sha256``. ``run`` refuses to start unless the
definitions still match that hash.

Writes to ``results/exp8/``:

    scenario_definitions.csv, scenario_definitions.sha256
    run_results.csv           one row per (configuration, scenario, policy)
    pairwise_differences.csv  one row per (configuration, scenario, policy pair)
    pairwise_summary.csv      lower/equal/higher counts per policy pair
    summary.csv               statistics per (configuration, policy) over feasible runs
    feasibility_differences.csv  every combination whose feasibility differs between policies
    coord_worse_than_lf.csv   every combination in which COORD is worse than LF on any criterion
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import re
import statistics
from collections.abc import Callable
from typing import Any

from brewery_agentic_replanning.agents import default_agents
from brewery_agentic_replanning.metrics import compute_metrics
from brewery_agentic_replanning.policies import (
    AssessingPolicy,
    CoordinatedPolicy,
    LocallyFilteredPolicy,
    UncoordinatedPolicy,
)
from brewery_agentic_replanning.scheduling import HeuristicScheduler, SchedulingError
from brewery_agentic_replanning.signals import NewBatchRequirementSignal
from brewery_agentic_replanning.simulation import BreweryDigitalTwin
from experiments.common import placements, write_csv
from experiments.config import RESULTS_DIR, ExperimentConfig, load_config
from experiments.exp5_random_scenarios import (
    DEFINITION_FIELDNAMES,
    ORDER_RECIPE,
    CommonBaselineReferences,
    build_signal,
    generate_scenarios,
    unresolved_pending_signals,
)

SEED = 20261001
N_SCENARIOS = 200
END_DAY = 60
IDLE_FROM_DAY = 15
CONFIGURATIONS = ("conf1", "conf2", "conf3")
OUTPUT_DIR = RESULTS_DIR / "exp8"
DEFINITIONS_PATH = OUTPUT_DIR / "scenario_definitions.csv"
HASH_PATH = OUTPUT_DIR / "scenario_definitions.sha256"

POLICIES: dict[str, Callable[[], AssessingPolicy]] = {
    "UNC": UncoordinatedPolicy,
    "LF": LocallyFilteredPolicy,
    "COORD": CoordinatedPolicy,
}
PAIRS = (("LF", "UNC"), ("COORD", "LF"), ("COORD", "UNC"))
LOWER_IS_BETTER = ("scheduler_calls", "changed_batches", "abs_shift", "idle_tank_days")
METRICS = (*LOWER_IS_BETTER, "net_shift")
RUN_FIELDNAMES = [
    "configuration",
    "scenario_id",
    "policy",
    "signals",
    "functions",
    "feasible",
    "failure_day",
    "failing_batch",
    "failing_batch_is_new_order",
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


def _digest() -> str:
    return hashlib.sha256(DEFINITIONS_PATH.read_bytes()).hexdigest()


def generate() -> None:
    if DEFINITIONS_PATH.exists():
        raise SystemExit(f"{DEFINITIONS_PATH} already exists; the scenarios are frozen")
    configs = [load_config(name) for name in CONFIGURATIONS]
    refs = CommonBaselineReferences(
        [cfg.baseline() for cfg in configs],
        order_fermentation_days=configs[0].build_recipes()[ORDER_RECIPE].fermentation_days,
    )
    write_csv(DEFINITIONS_PATH, generate_scenarios(refs, SEED, N_SCENARIOS), DEFINITION_FIELDNAMES)
    HASH_PATH.write_text(_digest() + "\n")
    print(f"sha256 {_digest()} -> {HASH_PATH}")


def read_scenarios() -> dict[str, list[dict]]:
    if _digest() != HASH_PATH.read_text().strip():
        raise SystemExit("scenario definitions do not match the recorded SHA-256")
    scenarios: dict[str, list[dict]] = {}
    with DEFINITIONS_PATH.open(newline="") as f:
        for row in csv.DictReader(f):
            scenarios.setdefault(row["scenario_id"], []).append(row)
    return scenarios


def run_one(cfg: ExperimentConfig, scenario_id: str, rows: list[dict], policy_name: str) -> tuple[dict, frozenset]:
    recipes = cfg.build_recipes()
    signals = [build_signal(row, recipes) for row in rows]
    brewery, schedule = cfg.baseline()
    policy = POLICIES[policy_name]()
    twin = BreweryDigitalTwin(brewery, schedule, HeuristicScheduler(), policy, default_agents())
    for signal in signals:
        twin.inject_signal(signal)

    failure, failure_day = "", None
    for day in range(END_DAY + 1):
        try:
            twin.advance_to(day)
        except SchedulingError as error:
            failure, failure_day = str(error), day
            break

    match = re.search(r"batch '([^']+)'", failure)
    failing_batch = match.group(1) if match else ""
    orders = [s.batch for s in signals if isinstance(s, NewBatchRequirementSignal)]
    row: dict[str, Any] = {
        **dict.fromkeys(RUN_FIELDNAMES, ""),
        "configuration": cfg.name,
        "scenario_id": scenario_id,
        "policy": policy_name,
        "signals": len(signals),
        "functions": len({r["function"] for r in rows}),
        "feasible": failure_day is None,
        "failure_day": "" if failure_day is None else failure_day,
        "failing_batch": failing_batch,
        "failing_batch_is_new_order": "" if failure_day is None else failing_batch in {b.id for b in orders},
        "scheduler_calls": len(twin.replan_log),
        "replanning_days": ";".join(str(e.day) for e in twin.replan_log),
    }
    if failure_day is not None:
        return row, frozenset()

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
    return row, frozenset(placements(twin.schedule))


def difference_rows(runs: dict[tuple[str, str, str], dict], finals: dict) -> list[dict]:
    rows = []
    for cfg_name, scenario_id in sorted({(c, s) for c, s, _ in runs}):
        for first, second in PAIRS:
            a, b = runs[(cfg_name, scenario_id, first)], runs[(cfg_name, scenario_id, second)]
            both = a["feasible"] and b["feasible"]
            row: dict[str, Any] = {
                "configuration": cfg_name,
                "scenario_id": scenario_id,
                "pair": f"{first}-{second}",
                "feasible_first": a["feasible"],
                "feasible_second": b["feasible"],
                "same_final_schedule": both
                and finals[(cfg_name, scenario_id, first)] == finals[(cfg_name, scenario_id, second)],
            }
            for metric in METRICS:
                row[f"delta_{metric}"] = a[metric] - b[metric] if both else ""
            rows.append(row)
    return rows


def pairwise_summary(differences: list[dict]) -> list[dict]:
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
                "combinations": len(group),
                "both_feasible": len(both),
                "only_first_feasible": sum(1 for d in group if d["feasible_first"] and not d["feasible_second"]),
                "only_second_feasible": sum(1 for d in group if d["feasible_second"] and not d["feasible_first"]),
                "same_final_schedule": sum(1 for d in both if d["same_final_schedule"]),
            }
            for metric in METRICS:
                deltas = [d[f"delta_{metric}"] for d in both]
                row[f"{metric}_lower"] = sum(1 for x in deltas if x < 0)
                row[f"{metric}_equal"] = sum(1 for x in deltas if x == 0)
                row[f"{metric}_higher"] = sum(1 for x in deltas if x > 0)
                row[f"{metric}_mean_delta"] = round(statistics.mean(deltas), 2) if deltas else ""
            rows.append(row)
    return rows


def summary_rows(runs: dict[tuple[str, str, str], dict]) -> list[dict]:
    rows = []
    for cfg_name in (*CONFIGURATIONS, "all"):
        for policy in POLICIES:
            group = [r for (c, _, p), r in runs.items() if p == policy and cfg_name in ("all", c)]
            feasible = [r for r in group if r["feasible"]]
            row: dict[str, Any] = {
                "configuration": cfg_name,
                "policy": policy,
                "runs": len(group),
                "feasible": len(feasible),
                "targets_met": sum(1 for r in feasible if r["targets_met"]),
                "unresolved_pending": sum(r["unresolved_pending"] for r in feasible),
                "scheduler_calls_all_runs": sum(r["scheduler_calls"] for r in group),
            }
            for metric in METRICS:
                values = [r[metric] for r in feasible]
                row[f"{metric}_mean"] = round(statistics.mean(values), 2)
                row[f"{metric}_median"] = statistics.median(values)
                row[f"{metric}_max"] = max(values)
            rows.append(row)
    return rows


def feasibility_rows(runs: dict[tuple[str, str, str], dict]) -> list[dict]:
    rows = []
    for cfg_name, scenario_id in sorted({(c, s) for c, s, _ in runs}):
        results = {p: runs[(cfg_name, scenario_id, p)] for p in POLICIES}
        if len({r["feasible"] for r in results.values()}) > 1:
            rows.append(
                {
                    "configuration": cfg_name,
                    "scenario_id": scenario_id,
                    **{f"feasible_{p}": r["feasible"] for p, r in results.items()},
                    **{f"failure_day_{p}": r["failure_day"] for p, r in results.items()},
                    **{f"failing_batch_{p}": r["failing_batch"] for p, r in results.items()},
                }
            )
    return rows


def coord_worse_rows(runs: dict[tuple[str, str, str], dict]) -> list[dict]:
    rows = []
    for cfg_name, scenario_id in sorted({(c, s) for c, s, _ in runs}):
        coord, lf = runs[(cfg_name, scenario_id, "COORD")], runs[(cfg_name, scenario_id, "LF")]
        both = coord["feasible"] and lf["feasible"]
        criteria = {
            "infeasible_where_lf_feasible": lf["feasible"] and not coord["feasible"],
            "target_missed_where_lf_met": both and lf["targets_met"] and not coord["targets_met"],
            **{f"more_{m}": both and coord[m] > lf[m] for m in LOWER_IS_BETTER},
        }
        if any(criteria.values()):
            rows.append(
                {
                    "configuration": cfg_name,
                    "scenario_id": scenario_id,
                    **criteria,
                    **{f"{m}_COORD": coord[m] for m in METRICS},
                    **{f"{m}_LF": lf[m] for m in METRICS},
                }
            )
    return rows


def run() -> None:
    scenarios = read_scenarios()
    runs: dict[tuple[str, str, str], dict] = {}
    finals: dict[tuple[str, str, str], frozenset] = {}
    for cfg in (load_config(name) for name in CONFIGURATIONS):
        for scenario_id, rows in scenarios.items():
            for policy_name in POLICIES:
                key = (cfg.name, scenario_id, policy_name)
                runs[key], finals[key] = run_one(cfg, scenario_id, rows, policy_name)

    differences = difference_rows(runs, finals)
    summary = summary_rows(runs)
    pairwise = pairwise_summary(differences)
    feasibility = feasibility_rows(runs)
    coord_worse = coord_worse_rows(runs)
    write_csv(OUTPUT_DIR / "run_results.csv", list(runs.values()), RUN_FIELDNAMES)
    write_csv(OUTPUT_DIR / "pairwise_differences.csv", differences)
    write_csv(OUTPUT_DIR / "pairwise_summary.csv", pairwise)
    write_csv(OUTPUT_DIR / "summary.csv", summary)
    if feasibility:
        write_csv(OUTPUT_DIR / "feasibility_differences.csv", feasibility)
    if coord_worse:
        write_csv(OUTPUT_DIR / "coord_worse_than_lf.csv", coord_worse)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("stage", choices=("generate", "run"))
    if parser.parse_args().stage == "generate":
        generate()
    else:
        run()


if __name__ == "__main__":
    main()
