"""Cross-functional scenarios under three replanning policies.

Every scenario of the configuration (CF_SCENARIOS) runs under:

    UNC    UncoordinatedPolicy: every signal invokes the scheduler
    LF     LocallyFilteredPolicy: KEEP is filtered out, PENDING and REPLAN
           invoke the scheduler on arrival
    COORD  CoordinatedPolicy: PENDING is deferred until a latest safe
           decision point, or until another signal forces an earlier
           reconstruction

Each run starts from a fresh baseline and simulates days 0-END_DAY.

Writes to ``results/<config>/``:

    exp3_results.csv          one row per (scenario, policy)
    exp3_assessment_log.csv   one row per agent assessment
    exp3_decision_log.csv     one row per decision cycle
    exp3/<scenario>/baseline.csv and
    exp3/<scenario>/<policy>/dayNN_after_replan.csv
                              schedule snapshots after every scheduler call
"""

from __future__ import annotations

import csv
import time
from pathlib import Path

from brewery_agentic_replanning.agents import default_agents
from brewery_agentic_replanning.domain import Brewery, Recipe, Schedule
from brewery_agentic_replanning.metrics import compute_metrics
from brewery_agentic_replanning.policies import (
    AssessingPolicy,
    CoordinatedPolicy,
    LocallyFilteredPolicy,
    UncoordinatedPolicy,
)
from brewery_agentic_replanning.scheduling import HeuristicScheduler, Scheduler, SchedulingError
from brewery_agentic_replanning.signals import (
    DayOffSignal,
    FermentationDelaySignal,
    FermenterMaintenanceSignal,
    IngredientShortageSignal,
    NewBatchRequirementSignal,
    Signal,
)
from brewery_agentic_replanning.simulation import BreweryDigitalTwin
from experiments.common import write_csv
from experiments.config import ExperimentConfig, from_cli

END_DAY = 15

POLICY_FACTORIES: dict[str, type[AssessingPolicy]] = {
    "UNC": UncoordinatedPolicy,
    "LF": LocallyFilteredPolicy,
    "COORD": CoordinatedPolicy,
}

SHORT_LABELS: dict[type[Signal], str] = {
    FermentationDelaySignal: "fermentation",
    IngredientShortageSignal: "ingredient_shortage",
    NewBatchRequirementSignal: "sales",
    DayOffSignal: "day_off",
    FermenterMaintenanceSignal: "maintenance",
}

ASSESSMENT_FIELDNAMES = [
    "configuration",
    "scenario",
    "policy",
    "simulation_day",
    "assessment_index",
    "signal_id",
    "signal_type",
    "signal_description",
    "source_agent",
    "local_assessment",
    "affects_plan",
    "requires_immediate_replan",
    "latest_safe_day",
    "status",
]
DECISION_FIELDNAMES = [
    "configuration",
    "scenario",
    "policy",
    "simulation_day",
    "decision_cycle",
    "active_signals",
    "coordination_decision",
    "trigger_reason",
    "scheduler_invoked",
    "schedule_version",
]
RESULT_FIELDNAMES = [
    "configuration",
    "scenario",
    "policy",
    "signals_received",
    "replanning_actions",
    "scheduler_invocations",
    "replanning_days",
    "changed_batches",
    "net_schedule_shift",
    "idle_fermenter_days",
    "shifted_brews",
    "shifted_bottlings",
    "abs_brew_shift_days",
    "abs_bottling_shift_days",
    "total_abs_schedule_shift",
    "plan_batches",
    "pct_changed_batches",
    "feasible",
    "target_feasible",
]
SNAPSHOT_FIELDNAMES = [
    "batch_id",
    "fermenter_id",
    "volume_hl",
    "start_day",
    "base_fermentation_days",
    "extra_delay_days",
    "bottling_day",
    "end_day",
    "locked",
]


class TimedScheduler(Scheduler):
    def __init__(self, scheduler: Scheduler) -> None:
        self._scheduler = scheduler
        self.total_seconds = 0.0
        self.call_count = 0

    def replan(self, brewery: Brewery, schedule: Schedule, current_day: int) -> Schedule:
        start = time.perf_counter()
        try:
            return self._scheduler.replan(brewery, schedule, current_day)
        finally:
            self.total_seconds += time.perf_counter() - start
            self.call_count += 1


def write_schedule_snapshot(path: Path, schedule: Schedule) -> None:
    rows = sorted(
        (
            {
                "batch_id": a.batch_id,
                "fermenter_id": a.fermenter.id,
                "volume_hl": a.volume_hl,
                "start_day": a.start_day,
                "base_fermentation_days": a.base_fermentation_days,
                "extra_delay_days": a.extra_delay_days,
                "bottling_day": a.bottling_day,
                "end_day": a.end_day,
                "locked": a.locked,
            }
            for a in schedule.assignments
        ),
        key=lambda r: (r["start_day"], r["batch_id"], r["fermenter_id"]),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=SNAPSHOT_FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def build_twin(
    cfg: ExperimentConfig, scenario_name: str, policy_name: str, recipes: dict[str, Recipe]
) -> tuple[BreweryDigitalTwin, list[Signal]]:
    events = cfg.hook("CF_SCENARIOS")[scenario_name](recipes)
    brewery = cfg.build_brewery()
    twin = BreweryDigitalTwin(
        brewery=brewery,
        schedule=cfg.declare_initial_schedule(brewery, recipes),
        scheduler=TimedScheduler(HeuristicScheduler()),
        policy=POLICY_FACTORIES[policy_name](),
        agents=default_agents(),
    )
    for event in events:
        twin.inject_signal(event)
    return twin, events


def _label(signals: tuple[Signal, ...], separator: str) -> str:
    return separator.join(SHORT_LABELS.get(type(s), s.kind) for s in signals)


def _policy_action(policy_name: str, decision: str) -> str:
    if policy_name == "UNC":
        return "REPLAN"
    if policy_name == "LF":
        return "KEEP" if decision == "KEEP" else "REPLAN"
    return decision


def log_rows(
    scenario_name: str, policy_name: str, policy: AssessingPolicy, signal_ids: dict[int, str]
) -> tuple[list[dict], list[dict]]:
    assessment_rows = []
    for index, record in enumerate(policy.assessments, start=1):
        decision = record.assessment.decision
        status = _policy_action(policy_name, decision)
        assessment_rows.append(
            {
                "scenario": scenario_name,
                "policy": policy_name,
                "simulation_day": record.day,
                "assessment_index": index,
                "signal_id": signal_ids[id(record.signal)],
                "signal_type": record.signal.kind,
                "signal_description": record.signal.description,
                "source_agent": record.assessment.source_agent,
                "local_assessment": "NO_OP" if decision == "KEEP" else "AFFECTS",
                "affects_plan": decision != "KEEP",
                "requires_immediate_replan": status == "REPLAN",
                "latest_safe_day": None if policy_name == "UNC" else record.assessment.latest_safe_decision_point,
                "status": status,
            }
        )

    decision_rows = []
    version = 0
    for cycle in policy.decisions:
        if cycle.decision == "REPLAN":
            version += 1
        if policy_name == "COORD" and cycle.urgent_signals:
            trigger_reason = f"latest safe point reached: {_label(cycle.urgent_signals, '/')}"
        else:
            trigger_reason = cycle.reason
        decision_rows.append(
            {
                "scenario": scenario_name,
                "policy": policy_name,
                "simulation_day": cycle.day,
                "decision_cycle": f"cycle_{cycle.cycle}",
                "active_signals": _label(cycle.signals, ";"),
                "coordination_decision": cycle.decision,
                "trigger_reason": trigger_reason,
                "scheduler_invoked": cycle.scheduler_invoked,
                "schedule_version": version,
            }
        )
    return assessment_rows, decision_rows


def _target_met(twin: BreweryDigitalTwin, target: tuple[str, int] | None, feasible: bool) -> bool:
    if target is None:
        return True
    if not feasible:
        return False
    batch_id, target_bottling_day = target
    assignments = twin.schedule.assignments_of(batch_id)
    return bool(assignments) and all(a.bottling_day == target_bottling_day for a in assignments)


def run_scenario(
    cfg: ExperimentConfig, scenario_name: str, policy_name: str, recipes: dict[str, Recipe]
) -> tuple[list[dict], list[dict], dict, float]:
    twin, events = build_twin(cfg, scenario_name, policy_name, recipes)
    signal_ids = {id(event): f"{scenario_name}-S{index}" for index, event in enumerate(events, start=1)}

    scenario_dir = cfg.results_dir / "exp3" / scenario_name
    write_schedule_snapshot(scenario_dir / "baseline.csv", twin.original_schedule)
    feasible = True
    try:
        for day in range(END_DAY + 1):
            replans_before = len(twin.replan_log)
            twin.advance_to(day)
            if len(twin.replan_log) > replans_before:
                write_schedule_snapshot(
                    scenario_dir / policy_name.lower() / f"day{day:02d}_after_replan.csv", twin.schedule
                )
    except SchedulingError:
        feasible = False

    assert isinstance(twin.policy, AssessingPolicy)
    assessment_rows, decision_rows = (
        log_rows(scenario_name, policy_name, twin.policy, signal_ids) if feasible else ([], [])
    )

    replanning_days = sorted({event.day for event in twin.replan_log})
    result_row: dict = {
        **dict.fromkeys(RESULT_FIELDNAMES),
        "configuration": cfg.name,
        "scenario": scenario_name,
        "policy": policy_name,
        "signals_received": len(events),
        "replanning_actions": len(replanning_days),
        "scheduler_invocations": len(replanning_days),
        "replanning_days": ";".join(str(day) for day in replanning_days),
        "feasible": feasible,
        "target_feasible": _target_met(twin, cfg.hook("CF_TARGETS")[scenario_name], feasible),
    }
    if feasible:
        metrics = compute_metrics(twin.brewery, twin.original_schedule, twin.schedule, twin.current_day)
        plan_batches = len(set(twin.original_schedule.batches) | set(twin.schedule.batches))
        result_row.update(
            changed_batches=metrics.changed_batches,
            net_schedule_shift=metrics.schedule_shift_days,
            idle_fermenter_days=metrics.idle_fermenter_days,
            shifted_brews=metrics.shifted_brews,
            shifted_bottlings=metrics.shifted_bottlings,
            abs_brew_shift_days=metrics.total_brew_shift_days,
            abs_bottling_shift_days=metrics.total_bottling_shift_days,
            total_abs_schedule_shift=metrics.total_brew_shift_days + metrics.total_bottling_shift_days,
            plan_batches=plan_batches,
            pct_changed_batches=round(100 * metrics.changed_batches / plan_batches, 1),
        )

    assert isinstance(twin.scheduler, TimedScheduler)
    print(f"  rescheduling: {twin.scheduler.total_seconds * 1000:.2f} ms across {twin.scheduler.call_count} call(s)")
    return (
        [{"configuration": cfg.name, **row} for row in assessment_rows],
        [{"configuration": cfg.name, **row} for row in decision_rows],
        result_row,
        twin.scheduler.total_seconds,
    )


def run_configuration(cfg: ExperimentConfig) -> tuple[list[dict], list[dict], list[dict]]:
    scenarios = cfg.hook("CF_SCENARIOS")
    cfg.hook("CF_TARGETS")
    recipes = cfg.build_recipes()

    assessment_rows: list[dict] = []
    decision_rows: list[dict] = []
    result_rows: list[dict] = []
    total_seconds = 0.0
    for scenario_name in scenarios:
        for policy_name in POLICY_FACTORIES:
            print(f"Running {cfg.name} / {scenario_name} / {policy_name}...")
            assessments, decisions, result, seconds = run_scenario(cfg, scenario_name, policy_name, recipes)
            assessment_rows += assessments
            decision_rows += decisions
            result_rows.append(result)
            total_seconds += seconds

    print(f"Total rescheduling time: {total_seconds * 1000:.2f} ms")
    write_csv(cfg.results_path("exp3_assessment_log.csv"), assessment_rows, ASSESSMENT_FIELDNAMES)
    write_csv(cfg.results_path("exp3_decision_log.csv"), decision_rows, DECISION_FIELDNAMES)
    write_csv(cfg.results_path("exp3_results.csv"), result_rows, RESULT_FIELDNAMES)
    return assessment_rows, decision_rows, result_rows


def main() -> None:
    run_configuration(from_cli(__doc__.splitlines()[0]))


if __name__ == "__main__":
    main()
