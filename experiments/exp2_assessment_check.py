"""Agreement between agent assessments and replanning outcomes in the
single-signal sweeps (exp2-exp2e).

For every combination in ``results/<config>/exp2*_results.csv``, the
signal's own agent assesses it on the baseline on its arrival day, and the
outcome of replanning on that day is replayed:

    plan_changed    the replanned plan differs from the baseline (the
                    sweep's own column, including the signal's direct effect)
    replan_effect   the replanned plan differs from the plan just before
                    the replan, i.e. the scheduler changed something

KEEP agrees with an unchanged outcome, PENDING and REPLAN with a changed
one. For exp2, only combinations replanned on the signal day are included.

``--previous DIR`` compares against an earlier run's ``results/<config>/``
directory.

Writes to ``results/<config>/``:

    exp2_assessment_verdicts.csv   one row per combination
    exp2_assessment_check.csv      counts per (sweep, version, verdict)
    exp2_assessment_flips.csv      with --previous: combinations whose
                                   sweep outcome changed
    exp2_verdict_transitions.csv   with --previous holding verdicts:
                                   combinations whose verdict or
                                   replan_effect changed
"""

from __future__ import annotations

import csv
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

from brewery_agentic_replanning.agents import (
    Agent,
    Assessment,
    FermentationAgent,
    MaintenanceAgent,
    OperationsAgent,
    ProcurementAgent,
    SalesAgent,
)
from brewery_agentic_replanning.domain import Recipe
from brewery_agentic_replanning.scheduling import SchedulingError
from brewery_agentic_replanning.signals import (
    DayOffSignal,
    FermentationDelaySignal,
    FermenterMaintenanceSignal,
    IngredientShortageSignal,
    Signal,
)
from experiments import exp2e
from experiments.common import manual_twin, placements, write_csv
from experiments.config import ConfigError, ExperimentConfig, argument_parser, load_config

SignalBuilder = Callable[[dict, dict[str, Recipe]], Signal]

SWEEPS: dict[str, tuple[tuple[str, ...], SignalBuilder, type[Agent[Any]]]] = {
    "exp2": (
        ("signal_day", "off_day", "replan_day"),
        lambda r, recipes: DayOffSignal(day=int(r["signal_day"]), off_day=int(r["off_day"])),
        OperationsAgent,
    ),
    "exp2b": (
        ("day", "batch_id", "available_from_day"),
        lambda r, recipes: IngredientShortageSignal(
            day=int(r["day"]), batch_id=r["batch_id"], available_from_day=int(r["available_from_day"])
        ),
        ProcurementAgent,
    ),
    "exp2c": (
        ("day", "batch_id", "fermenter_id", "extra_days"),
        lambda r, recipes: FermentationDelaySignal(
            day=int(r["day"]),
            batch_id=r["batch_id"],
            fermenter_id=r["fermenter_id"],
            extra_days=int(r["extra_days"]),
        ),
        FermentationAgent,
    ),
    "exp2d": (
        ("day", "fermenter_id", "start_day", "duration_days"),
        lambda r, recipes: FermenterMaintenanceSignal(
            day=int(r["day"]),
            fermenter_id=r["fermenter_id"],
            start_day=int(r["start_day"]),
            duration_days=int(r["duration_days"]),
        ),
        MaintenanceAgent,
    ),
    "exp2e": (
        ("day", "planned_brew_day", "slack"),
        lambda r, recipes: exp2e.new_order(
            recipes, int(r["day"]), int(r["planned_brew_day"]), int(r["target_bottling_day"])
        ),
        SalesAgent,
    ),
}

FLIP_FIELDNAMES = [
    "sweep",
    "combination",
    "verdict",
    "previous_outcome",
    "current_outcome",
    "previous_changed_batches",
    "current_changed_batches",
]
TRANSITION_FIELDNAMES = [
    "sweep",
    "combination",
    "previous_verdict",
    "current_verdict",
    "previous_latest_safe_day",
    "current_latest_safe_day",
    "previous_affected_activity",
    "current_affected_activity",
    "previous_replan_effect",
    "current_replan_effect",
]


def verdict(cfg: ExperimentConfig, agent_type: type[Agent[Any]], signal: Signal) -> Assessment:
    brewery, schedule = cfg.baseline()
    agent = agent_type()
    agent.apply(brewery, schedule, signal)
    return agent.assess(brewery, schedule, signal, signal.day)


def replan_effect(cfg: ExperimentConfig, recipes: dict[str, Recipe], signal: Signal) -> str:
    twin = manual_twin(cfg, recipes, [signal], signal.day)
    before = placements(twin.schedule)
    try:
        twin.replan_now()
    except SchedulingError:
        return "infeasible"
    return "changed" if placements(twin.schedule) != before else "unchanged"


def outcome(row: dict) -> str:
    if row.get("feasible") == "False":
        return "infeasible"
    return "changed" if row["plan_changed"] == "True" else "unchanged"


def read_rows(path: Path, keys: tuple[str, ...]) -> dict[tuple, dict]:
    with path.open(newline="") as f:
        return {tuple(r[k] for k in keys): r for r in csv.DictReader(f)}


def counts(sweep: str, version: str, rows: list[dict]) -> list[dict]:
    by_verdict: dict[str, list[dict]] = {}
    for row in rows:
        by_verdict.setdefault(row["verdict"], []).append(row)
    summary = []
    for decision, group in sorted(by_verdict.items()):
        plan = Counter(r["plan_changed"] for r in group)
        effect = Counter(r["replan_effect"] for r in group) if "replan_effect" in group[0] else Counter()
        disagreeing = "changed" if decision == "KEEP" else "unchanged"
        summary.append(
            {
                "sweep": sweep,
                "version": version,
                "verdict": decision,
                "combinations": len(group),
                "plan_changed": plan["changed"],
                "plan_unchanged": plan["unchanged"],
                "replan_changed": effect["changed"],
                "replan_unchanged": effect["unchanged"],
                "infeasible": plan["infeasible"],
                "disagreements_plan_changed": plan[disagreeing],
                "disagreements_replan_effect": effect[disagreeing] if effect else "",
            }
        )
    return summary


def main() -> None:
    parser = argument_parser(__doc__.splitlines()[0])
    parser.add_argument("--previous", type=Path, help="results/<config>/ directory of an earlier run")
    args = parser.parse_args()
    try:
        cfg = load_config(args.config)
    except ConfigError as error:
        parser.error(str(error))
    recipes = cfg.build_recipes()

    previous_verdicts: dict[tuple[str, str], dict] = {}
    previous_verdicts_path = args.previous / "exp2_assessment_verdicts.csv" if args.previous else None
    if previous_verdicts_path and previous_verdicts_path.exists():
        with previous_verdicts_path.open(newline="") as f:
            previous_verdicts = {(r["sweep"], r["combination"]): r for r in csv.DictReader(f)}

    verdict_rows: list[dict] = []
    summary: list[dict] = []
    flips: list[dict] = []
    transitions: list[dict] = []
    for sweep, (keys, build, agent_type) in SWEEPS.items():
        current = read_rows(cfg.results_dir / f"{sweep}_results.csv", keys)
        previous = read_rows(args.previous / f"{sweep}_results.csv", keys) if args.previous else {}
        if previous and previous.keys() != current.keys():
            raise SystemExit(f"{sweep}: the two runs don't cover the same combinations")

        in_scope = [k for k, r in current.items() if sweep != "exp2" or r["replan_day"] == r["signal_day"]]
        rows = []
        for key in in_scope:
            signal = build(current[key], recipes)
            assessment = verdict(cfg, agent_type, signal)
            rows.append(
                {
                    "sweep": sweep,
                    "combination": ";".join(f"{k}={v}" for k, v in zip(keys, key, strict=True)),
                    "verdict": assessment.decision,
                    "latest_safe_day": assessment.latest_safe_decision_point,
                    "affected_activity": assessment.affected_activity or "",
                    "plan_changed": outcome(current[key]),
                    "replan_effect": replan_effect(cfg, recipes, signal),
                }
            )
        verdict_rows += rows
        summary += counts(sweep, "current", rows)
        if not previous:
            continue

        by_combination = {r["combination"]: r for r in rows}
        old_rows = []
        for key in in_scope:
            combination = ";".join(f"{k}={v}" for k, v in zip(keys, key, strict=True))
            new = by_combination[combination]
            old = previous_verdicts.get((sweep, combination))
            before, after = previous[key], current[key]
            old_rows.append(
                {
                    "verdict": old["verdict"] if old else new["verdict"],
                    "plan_changed": outcome(before),
                    **({"replan_effect": old["replan_effect"]} if old else {}),
                }
            )
            if outcome(before) != outcome(after) or before["changed_batches"] != after["changed_batches"]:
                flips.append(
                    {
                        "sweep": sweep,
                        "combination": combination,
                        "verdict": new["verdict"],
                        "previous_outcome": outcome(before),
                        "current_outcome": outcome(after),
                        "previous_changed_batches": before["changed_batches"],
                        "current_changed_batches": after["changed_batches"],
                    }
                )
            if old and (old["verdict"], old["replan_effect"]) != (new["verdict"], new["replan_effect"]):
                transitions.append(
                    {
                        "sweep": sweep,
                        "combination": combination,
                        "previous_verdict": old["verdict"],
                        "current_verdict": new["verdict"],
                        "previous_latest_safe_day": old["latest_safe_day"],
                        "current_latest_safe_day": new["latest_safe_day"],
                        "previous_affected_activity": old["affected_activity"],
                        "current_affected_activity": new["affected_activity"],
                        "previous_replan_effect": old["replan_effect"],
                        "current_replan_effect": new["replan_effect"],
                    }
                )
        summary += counts(sweep, "previous", old_rows)

    write_csv(cfg.results_path("exp2_assessment_verdicts.csv"), verdict_rows)
    write_csv(cfg.results_path("exp2_assessment_check.csv"), summary)
    if args.previous:
        write_csv(cfg.results_path("exp2_assessment_flips.csv"), flips, FLIP_FIELDNAMES)
    if previous_verdicts:
        write_csv(cfg.results_path("exp2_verdict_transitions.csv"), transitions, TRANSITION_FIELDNAMES)


if __name__ == "__main__":
    main()
