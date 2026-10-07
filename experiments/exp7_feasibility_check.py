"""Coordinated replanning with a deferral feasibility check.

COORD_FC is COORD with one addition. At the end of every day
on which signals remain pending and no assessment requires a replan, the
coordinator runs trial reconstructions on isolated copies of the current
state, using only information available that day:

    tomorrow  the state with the activities starting tomorrow locked, as the
              digital twin locks them at the start of a day, replanned on
              the next day
    today     the current state replanned today

If tomorrow's trial is feasible, the signals stay pending. If it is not and
today's trial is, the coordinator replans today. If neither is feasible,
the event is recorded and the coordinator proceeds as COORD.

LF, COORD and COORD_FC, all with the current Sales rule, are run on the exp5 scenarios on
conf1-conf3 until the exp5 horizon, all in this process.

Writes to ``results/exp7/``:

    runs.csv                one row per (configuration, scenario, policy)
    feasibility.csv         feasibility of every policy per (configuration, scenario)
    check_events.csv        one row per feasibility check of COORD_FC
    cost_summary.csv        reconstructions, trial runs and computation time per policy
    common_summary.csv      schedule measures on combinations feasible under every policy
    pairwise_summary.csv    COORD_FC against the other policies on those combinations
"""

from __future__ import annotations

import copy
import re
import statistics
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from brewery_agentic_replanning.agents import Agent, default_agents
from brewery_agentic_replanning.metrics import compute_metrics
from brewery_agentic_replanning.policies import (
    AssessingPolicy,
    CoordinatedPolicy,
    LocallyFilteredPolicy,
)
from brewery_agentic_replanning.scheduling import HeuristicScheduler, SchedulingError
from brewery_agentic_replanning.signals import NewBatchRequirementSignal, Signal
from brewery_agentic_replanning.simulation import BreweryDigitalTwin
from experiments.common import placements, write_csv
from experiments.config import RESULTS_DIR, ExperimentConfig, load_config
from experiments.exp3_cross_functional import TimedScheduler
from experiments.exp5_random_scenarios import (
    CONFIGURATIONS,
    END_DAY,
    IDLE_FROM_DAY,
    build_signal,
    read_scenarios,
)

OUTPUT_DIR = RESULTS_DIR / "exp7"
CHECKED = "COORD_FC"
METRICS = ("reconstructions", "changed_batches", "abs_shift", "idle_tank_days", "net_shift")
RUN_FIELDNAMES = [
    "configuration",
    "scenario_id",
    "policy",
    "feasible",
    "failure_day",
    "failing_batch",
    "targets_met",
    "reconstructions",
    "reconstruction_seconds",
    "trial_runs",
    "trial_seconds",
    "run_seconds",
    "checks",
    "checks_replan_today",
    "checks_infeasible_both",
    "replanning_days",
    "changed_batches",
    "abs_shift",
    "net_shift",
    "idle_tank_days",
]


@dataclass(frozen=True)
class CheckEvent:
    day: int
    outcome: str
    pending: tuple[str, ...]


class FeasibilityCheckedPolicy(CoordinatedPolicy):
    def __init__(self) -> None:
        super().__init__()
        self.events: list[CheckEvent] = []
        self.trial_runs = 0
        self.trial_seconds = 0.0

    def should_replan_end_of_day(self, day: int) -> bool:
        if super().should_replan_end_of_day(day):
            return True
        if not self._active:
            return False
        if self._trial(day + 1):
            outcome = "defer"
        elif self._trial(day):
            outcome = "replan_today"
        else:
            outcome = "infeasible_today_and_tomorrow"
        pending = tuple(signal.kind for signal, _ in self._active.values())
        self.events.append(CheckEvent(day=day, outcome=outcome, pending=pending))
        return outcome == "replan_today"

    def _trial(self, day: int) -> bool:
        twin = self._twin
        assert twin is not None
        state_before = _state_signature(twin)
        brewery, schedule = copy.deepcopy(twin.brewery), copy.deepcopy(twin.schedule)
        schedule.lock_started_assignments(day)
        start = time.perf_counter()
        try:
            HeuristicScheduler().replan(brewery, schedule, day)
            feasible = True
        except SchedulingError:
            feasible = False
        self.trial_seconds += time.perf_counter() - start
        self.trial_runs += 1
        if _state_signature(twin) != state_before:
            raise RuntimeError("a trial reconstruction modified the simulation state")
        return feasible


def _state_signature(twin: BreweryDigitalTwin) -> tuple:
    schedule = twin.schedule
    return (
        frozenset(placements(schedule)),
        frozenset((a.batch_id, a.fermenter.id, a.locked, a.bottling_day) for a in schedule.assignments),
        frozenset(schedule.batches.items()),
        frozenset(twin.brewery.days_off),
        tuple(sorted((k, tuple(v)) for k, v in twin.brewery.fermenter_maintenance.items())),
    )


PolicySetup = tuple[Callable[[], AssessingPolicy], Callable[[], dict[type[Signal], Agent[Any]]]]
POLICIES: dict[str, PolicySetup] = {
    "LF": (LocallyFilteredPolicy, default_agents),
    "COORD": (CoordinatedPolicy, default_agents),
    CHECKED: (FeasibilityCheckedPolicy, default_agents),
}


def run(cfg: ExperimentConfig, scenario_id: str, rows: list[dict], policy_name: str) -> tuple[dict, list[dict]]:
    make_policy, make_agents = POLICIES[policy_name]
    recipes = cfg.build_recipes()
    signals = [build_signal(row, recipes) for row in rows]
    brewery, schedule = cfg.baseline()
    scheduler = TimedScheduler(HeuristicScheduler())
    policy = make_policy()
    twin = BreweryDigitalTwin(brewery, schedule, scheduler, policy, make_agents())
    for signal in signals:
        twin.inject_signal(signal)

    failure, failure_day = "", None
    start = time.perf_counter()
    for day in range(END_DAY + 1):
        try:
            twin.advance_to(day)
        except SchedulingError as error:
            failure, failure_day = str(error), day
            break
    run_seconds = time.perf_counter() - start

    events = policy.events if isinstance(policy, FeasibilityCheckedPolicy) else []
    match = re.search(r"batch '([^']+)'", failure)
    row: dict[str, Any] = {
        **dict.fromkeys(RUN_FIELDNAMES, ""),
        "configuration": cfg.name,
        "scenario_id": scenario_id,
        "policy": policy_name,
        "feasible": failure_day is None,
        "failure_day": "" if failure_day is None else failure_day,
        "failing_batch": match.group(1) if match else "",
        "reconstructions": len(twin.replan_log),
        "reconstruction_seconds": round(scheduler.total_seconds, 6),
        "trial_runs": policy.trial_runs if isinstance(policy, FeasibilityCheckedPolicy) else 0,
        "trial_seconds": round(policy.trial_seconds, 6) if isinstance(policy, FeasibilityCheckedPolicy) else 0.0,
        "run_seconds": round(run_seconds, 6),
        "checks": len(events),
        "checks_replan_today": sum(1 for e in events if e.outcome == "replan_today"),
        "checks_infeasible_both": sum(1 for e in events if e.outcome == "infeasible_today_and_tomorrow"),
        "replanning_days": ";".join(str(e.day) for e in twin.replan_log),
    }
    if failure_day is None:
        orders = [s.batch for s in signals if isinstance(s, NewBatchRequirementSignal)]
        metrics = compute_metrics(twin.brewery, twin.original_schedule, twin.schedule, IDLE_FROM_DAY)
        row.update(
            targets_met=all(
                twin.schedule.assignments_of(b.id)
                and all(a.bottling_day == b.target_bottling_day for a in twin.schedule.assignments_of(b.id))
                for b in orders
            ),
            changed_batches=metrics.changed_batches,
            abs_shift=metrics.total_brew_shift_days + metrics.total_bottling_shift_days,
            net_shift=metrics.schedule_shift_days,
            idle_tank_days=metrics.idle_fermenter_days,
        )
    event_rows = [
        {
            "configuration": cfg.name,
            "scenario_id": scenario_id,
            "day": e.day,
            "outcome": e.outcome,
            "pending": ";".join(e.pending),
            "run_feasible": failure_day is None,
        }
        for e in events
    ]
    return row, event_rows


def _cost_rows(runs: dict[tuple[str, str, str], dict]) -> list[dict]:
    rows = []
    for policy in POLICIES:
        group = [r for (_, _, p), r in runs.items() if p == policy]
        rows.append(
            {
                "policy": policy,
                "runs": len(group),
                "feasible": sum(1 for r in group if r["feasible"]),
                "reconstructions": sum(r["reconstructions"] for r in group),
                "trial_runs": sum(r["trial_runs"] for r in group),
                "scheduler_runs": sum(r["reconstructions"] + r["trial_runs"] for r in group),
                "reconstruction_seconds": round(sum(r["reconstruction_seconds"] for r in group), 3),
                "trial_seconds": round(sum(r["trial_seconds"] for r in group), 3),
                "scheduler_seconds": round(sum(r["reconstruction_seconds"] + r["trial_seconds"] for r in group), 3),
                "run_seconds": round(sum(r["run_seconds"] for r in group), 3),
            }
        )
    return rows


def _common_rows(runs: dict[tuple[str, str, str], dict], keys: list[tuple[str, str]]) -> list[dict]:
    rows = []
    for policy in POLICIES:
        group = [runs[(c, s, policy)] for c, s in keys]
        row: dict[str, Any] = {"policy": policy, "combinations": len(group)}
        for metric in (*METRICS, "trial_runs"):
            values = [r[metric] for r in group]
            row[f"{metric}_total"] = sum(values)
            row[f"{metric}_mean"] = round(statistics.mean(values), 2) if values else ""
        rows.append(row)
    return rows


def _pairwise_rows(runs: dict[tuple[str, str, str], dict], keys: list[tuple[str, str]]) -> list[dict]:
    rows = []
    for other in POLICIES:
        if other == CHECKED:
            continue
        row: dict[str, Any] = {"pair": f"{CHECKED} vs {other}", "combinations": len(keys)}
        for metric in METRICS:
            deltas = [runs[(c, s, CHECKED)][metric] - runs[(c, s, other)][metric] for c, s in keys]
            row[f"{metric}_lower"] = sum(1 for d in deltas if d < 0)
            row[f"{metric}_equal"] = sum(1 for d in deltas if d == 0)
            row[f"{metric}_higher"] = sum(1 for d in deltas if d > 0)
        rows.append(row)
    return rows


def main() -> None:
    scenarios = read_scenarios()
    configs = [load_config(name) for name in CONFIGURATIONS]
    runs: dict[tuple[str, str, str], dict] = {}
    events: list[dict] = []
    for cfg in configs:
        for scenario_id, rows in scenarios.items():
            for policy_name in POLICIES:
                row, event_rows = run(cfg, scenario_id, rows, policy_name)
                runs[(cfg.name, scenario_id, policy_name)] = row
                events += event_rows

    keys = sorted({(c, s) for c, s, _ in runs})
    feasibility = [
        {"configuration": c, "scenario_id": s, **{p: runs[(c, s, p)]["feasible"] for p in POLICIES}} for c, s in keys
    ]
    common_keys = [(c, s) for c, s in keys if all(runs[(c, s, p)]["feasible"] for p in POLICIES)]
    cost = _cost_rows(runs)
    common = _common_rows(runs, common_keys)
    pairwise = _pairwise_rows(runs, common_keys)

    write_csv(OUTPUT_DIR / "runs.csv", list(runs.values()), RUN_FIELDNAMES)
    write_csv(OUTPUT_DIR / "feasibility.csv", feasibility)
    write_csv(
        OUTPUT_DIR / "check_events.csv",
        events,
        ["configuration", "scenario_id", "day", "outcome", "pending", "run_feasible"],
    )
    write_csv(OUTPUT_DIR / "cost_summary.csv", cost)
    write_csv(OUTPUT_DIR / "common_summary.csv", common)
    write_csv(OUTPUT_DIR / "pairwise_summary.csv", pairwise)


if __name__ == "__main__":
    main()
