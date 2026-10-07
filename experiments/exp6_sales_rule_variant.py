"""Coordinated replanning with the current Sales rule against the legacy rule.

The current :class:`SalesAgent` sets the latest decision point of a new
order to the day before its requested brewing day; exp5 used
:class:`~experiments.legacy.SalesAgentLegacy`. COORD with the current rule
(COORD_BD) is run on the exp5 scenarios (``results/exp5/scenario_definitions.csv``) on
conf1-conf3 until the exp5 horizon, and compared with the exp5 results of
UNC, LF and COORD with the legacy rule, which are read from
``results/exp5/run_results.csv``.
Every failure of COORD_BD in a scenario feasible under LF is diagnosed.

Writes to ``results/exp6/``:

    variant_runs.csv        one row per (configuration, scenario), COORD_BD
    feasibility.csv         feasibility of all four policies per (configuration, scenario)
    common_summary.csv      statistics over combinations feasible under all four policies
    pairwise_summary.csv    COORD_BD against UNC, LF and COORD on those combinations
    failure_diagnosis.csv   one row per COORD_BD failure in a combination feasible under LF
"""

from __future__ import annotations

import copy
import csv
import re
import statistics
from typing import Any

from brewery_agentic_replanning.agents import Agent, default_agents
from brewery_agentic_replanning.domain import Brewery, Schedule
from brewery_agentic_replanning.metrics import compute_metrics
from brewery_agentic_replanning.policies import CoordinatedPolicy, LocallyFilteredPolicy, ReplanningPolicy
from brewery_agentic_replanning.scheduling import HeuristicScheduler, Scheduler, SchedulingError
from brewery_agentic_replanning.signals import NewBatchRequirementSignal, Signal
from brewery_agentic_replanning.simulation import BreweryDigitalTwin
from experiments.common import write_csv
from experiments.config import RESULTS_DIR, ExperimentConfig, load_config
from experiments.exp5_random_scenarios import (
    CONFIGURATIONS,
    END_DAY,
    IDLE_FROM_DAY,
    build_signal,
    read_scenarios,
)
from experiments.exp5_random_scenarios import OUTPUT_DIR as EXP5_DIR

OUTPUT_DIR = RESULTS_DIR / "exp6"
VARIANT = "COORD_BD"
POLICIES = ("UNC", "LF", "COORD", VARIANT)
LOWER_IS_BETTER = ("scheduler_calls", "changed_batches", "abs_shift", "idle_tank_days")
METRICS = (*LOWER_IS_BETTER, "net_shift")
RUN_FIELDNAMES = [
    "configuration",
    "scenario_id",
    "policy",
    "feasible",
    "failure",
    "failure_day",
    "failing_batch",
    "targets_met",
    "scheduler_calls",
    "replanning_days",
    "changed_batches",
    "pct_changed_batches",
    "abs_shift",
    "net_shift",
    "idle_tank_days",
]


class CapturingScheduler(Scheduler):
    def __init__(self) -> None:
        self._scheduler = HeuristicScheduler()
        self.last_input: tuple[Brewery, Schedule, int] | None = None

    def replan(self, brewery: Brewery, schedule: Schedule, current_day: int) -> Schedule:
        self.last_input = (copy.deepcopy(brewery), copy.deepcopy(schedule), current_day)
        return self._scheduler.replan(brewery, schedule, current_day)


class Run:
    def __init__(
        self, cfg: ExperimentConfig, rows: list[dict], policy: ReplanningPolicy, agents: dict[type[Signal], Agent[Any]]
    ) -> None:
        recipes = cfg.build_recipes()
        self.signals = [build_signal(row, recipes) for row in rows]
        brewery, schedule = cfg.baseline()
        self.scheduler = CapturingScheduler()
        self.policy = policy
        self.twin = BreweryDigitalTwin(brewery, schedule, self.scheduler, policy, agents)
        for signal in self.signals:
            self.twin.inject_signal(signal)
        self.failure = ""
        self.failure_day: int | None = None
        for day in range(END_DAY + 1):
            try:
                self.twin.advance_to(day)
            except SchedulingError as error:
                self.failure, self.failure_day = str(error), day
                break

    @property
    def feasible(self) -> bool:
        return self.failure_day is None

    @property
    def failing_batch(self) -> str:
        match = re.search(r"batch '([^']+)'", self.failure)
        return match.group(1) if match else ""


def run_row(cfg: ExperimentConfig, scenario_id: str, run: Run) -> dict:
    twin = run.twin
    row: dict[str, Any] = {
        **dict.fromkeys(RUN_FIELDNAMES, ""),
        "configuration": cfg.name,
        "scenario_id": scenario_id,
        "policy": VARIANT,
        "feasible": run.feasible,
        "failure": run.failure,
        "failure_day": "" if run.feasible else run.failure_day,
        "failing_batch": run.failing_batch,
        "scheduler_calls": len(twin.replan_log),
        "replanning_days": ";".join(str(day) for day in sorted({e.day for e in twin.replan_log})),
    }
    if not run.feasible:
        return row
    orders = [s.batch for s in run.signals if isinstance(s, NewBatchRequirementSignal)]
    metrics = compute_metrics(twin.brewery, twin.original_schedule, twin.schedule, IDLE_FROM_DAY)
    plan_batches = len(set(twin.original_schedule.batches) | set(twin.schedule.batches))
    row.update(
        targets_met=all(
            twin.schedule.assignments_of(b.id)
            and all(a.bottling_day == b.target_bottling_day for a in twin.schedule.assignments_of(b.id))
            for b in orders
        ),
        changed_batches=metrics.changed_batches,
        pct_changed_batches=round(100 * metrics.changed_batches / plan_batches, 1),
        abs_shift=metrics.total_brew_shift_days + metrics.total_bottling_shift_days,
        net_shift=metrics.schedule_shift_days,
        idle_tank_days=metrics.idle_fermenter_days,
    )
    return row


def _active_at_failure(run: Run) -> list[Signal]:
    policy = run.policy
    assert isinstance(policy, CoordinatedPolicy)
    latest = {id(record.signal): record for record in policy.assessments}
    return [
        record.signal
        for record in latest.values()
        if record.assessment.decision != "KEEP"
        and not any(
            d.decision == "REPLAN" and d.day >= record.day and any(s is record.signal for s in d.signals)
            for d in policy.decisions
        )
    ]


def _feasible_with_movable_commitments(run: Run, since_day: int) -> bool:
    assert run.scheduler.last_input is not None
    brewery, schedule, _ = run.scheduler.last_input
    schedule = copy.deepcopy(schedule)
    for assignment in schedule.assignments:
        if assignment.start_day > since_day:
            assignment.locked = False
    try:
        HeuristicScheduler().replan(copy.deepcopy(brewery), schedule, since_day)
    except SchedulingError:
        return False
    return True


def diagnose(cfg: ExperimentConfig, scenario_id: str, rows: list[dict], run: Run) -> dict:
    batch_id = run.failing_batch
    order = next(
        (r for r in rows if r["signal_type"] == "NewBatchRequirementSignal" and r["batch_id"] == batch_id), None
    )
    others_active = [s for s in _active_at_failure(run) if s.related_batch_id != batch_id]
    row: dict[str, Any] = {
        "configuration": cfg.name,
        "scenario_id": scenario_id,
        "failure_day": run.failure_day,
        "failing_batch": batch_id,
        "failing_batch_is_new_order": order is not None,
        "other_signals_active_at_failure": ";".join(s.kind for s in others_active),
        "failure_after_order_decision_day": "",
        "order_arrival_day": "",
        "order_requested_brew_day": "",
        "order_alone_feasible": "",
        "feasible_with_movable_commitments": "",
        "lf_brew_day": "",
    }
    if order is None:
        row["mechanism"] = "existing batch cannot be placed"
        return row

    arrival, requested = int(order["day"]), int(order["planned_brew_day"])
    alone = Run(cfg, [order], CoordinatedPolicy(), default_agents())
    lf = Run(cfg, rows, LocallyFilteredPolicy(), default_agents())
    movable = _feasible_with_movable_commitments(run, arrival)
    row.update(
        failure_after_order_decision_day=run.failure_day is not None and run.failure_day > requested - 1,
        order_arrival_day=arrival,
        order_requested_brew_day=requested,
        order_alone_feasible=alone.feasible,
        feasible_with_movable_commitments=movable,
        lf_brew_day=min(a.start_day for a in lf.twin.schedule.assignments_of(batch_id)),
    )
    if not alone.feasible:
        row["mechanism"] = "order infeasible on its own under the variant rule"
    elif others_active:
        row["mechanism"] = "interaction with other pending signals"
    else:
        row["mechanism"] = "interaction with signals already incorporated"
    return row


def _read_exp5_runs() -> dict[tuple[str, str, str], dict]:
    runs = {}
    with (EXP5_DIR / "run_results.csv").open(newline="") as f:
        for r in csv.DictReader(f):
            r["feasible"] = r["feasible"] == "True"
            for metric in METRICS:
                r[metric] = int(r[metric]) if r[metric] != "" else ""
            runs[(r["configuration"], r["scenario_id"], r["policy"])] = r
    return runs


def _summary(runs: dict[tuple[str, str, str], dict], keys: list[tuple[str, str]]) -> list[dict]:
    rows = []
    for cfg_name in (*CONFIGURATIONS, "all"):
        selected = [k for k in keys if cfg_name in ("all", k[0])]
        for policy in POLICIES:
            group = [runs[(c, s, policy)] for c, s in selected]
            row: dict[str, Any] = {"configuration": cfg_name, "policy": policy, "combinations": len(group)}
            for metric in METRICS:
                values = [r[metric] for r in group]
                row[f"{metric}_total"] = sum(values)
                row[f"{metric}_mean"] = round(statistics.mean(values), 2) if values else ""
            rows.append(row)
    return rows


def _pairwise(runs: dict[tuple[str, str, str], dict], keys: list[tuple[str, str]]) -> list[dict]:
    rows = []
    for cfg_name in (*CONFIGURATIONS, "all"):
        selected = [k for k in keys if cfg_name in ("all", k[0])]
        for other in ("UNC", "LF", "COORD"):
            row: dict[str, Any] = {
                "configuration": cfg_name,
                "pair": f"{VARIANT} vs {other}",
                "combinations": len(selected),
            }
            for metric in METRICS:
                deltas = [runs[(c, s, VARIANT)][metric] - runs[(c, s, other)][metric] for c, s in selected]
                row[f"{metric}_lower"] = sum(1 for d in deltas if d < 0)
                row[f"{metric}_equal"] = sum(1 for d in deltas if d == 0)
                row[f"{metric}_higher"] = sum(1 for d in deltas if d > 0)
            rows.append(row)
    return rows


def main() -> None:
    scenarios = read_scenarios()
    configs = [load_config(name) for name in CONFIGURATIONS]
    runs = _read_exp5_runs()

    variant_rows: list[dict] = []
    diagnoses: list[dict] = []
    for cfg in configs:
        for scenario_id, rows in scenarios.items():
            run = Run(cfg, rows, CoordinatedPolicy(), default_agents())
            row = run_row(cfg, scenario_id, run)
            variant_rows.append(row)
            runs[(cfg.name, scenario_id, VARIANT)] = row
            if not run.feasible and runs[(cfg.name, scenario_id, "LF")]["feasible"]:
                diagnoses.append(diagnose(cfg, scenario_id, rows, run))

    keys = sorted({(c, s) for c, s, _ in runs})
    feasibility = [
        {"configuration": c, "scenario_id": s, **{p: runs[(c, s, p)]["feasible"] for p in POLICIES}} for c, s in keys
    ]
    common = [(c, s) for c, s in keys if all(runs[(c, s, p)]["feasible"] for p in POLICIES)]
    summary = _summary(runs, common)
    pairwise = _pairwise(runs, common)

    write_csv(OUTPUT_DIR / "variant_runs.csv", variant_rows, RUN_FIELDNAMES)
    write_csv(OUTPUT_DIR / "feasibility.csv", feasibility)
    write_csv(OUTPUT_DIR / "common_summary.csv", summary)
    write_csv(OUTPUT_DIR / "pairwise_summary.csv", pairwise)
    if diagnoses:
        write_csv(OUTPUT_DIR / "failure_diagnosis.csv", diagnoses)


if __name__ == "__main__":
    main()
