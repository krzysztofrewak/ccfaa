"""Cross-functional scenarios repeated on several brewery configurations.

    conf1  reference configuration
    conf2  reduced production load: batches removed, same brewery
    conf3  alternative fermenter fleet with the same total capacity

Stages, written to ``results/combined/``:

1. configuration_summary.csv and baseline_validation.csv: fleet, load and
   utilisation over UTILISATION_HORIZON for every configuration, and
   PASS/FAIL checks of its baseline, including that replanning without a
   signal reproduces it.
2. scenario_definitions.csv and signal_validity_original.csv: every
   scenario signal with its agent's assessment on the configuration's
   baseline, compared with the corresponding conf1 signal on conf1. A
   signal that differs from conf1 must be documented in CF_ADAPTATIONS.
3. input_identity.csv, then the exp3 outputs of every configuration
   (exp3_results.csv, exp3_assessment_log.csv, exp3_decision_log.csv).
4. policy_comparison.csv and volume_preservation.csv: the policies side by
   side, whether their final schedules are identical, and whether every
   batch kept its volume.

The run stops if a baseline check fails, an adaptation is undocumented,
the policies would receive different inputs or a batch volume changes.
"""

from __future__ import annotations

import csv
import hashlib
from collections import Counter, defaultdict
from itertools import pairwise
from pathlib import Path

from brewery_agentic_replanning.agents import default_agents
from brewery_agentic_replanning.domain import Brewery, Schedule
from brewery_agentic_replanning.metrics import compute_metrics
from brewery_agentic_replanning.scheduling import HeuristicScheduler
from brewery_agentic_replanning.signals import (
    FermentationDelaySignal,
    FermenterMaintenanceSignal,
    IngredientShortageSignal,
    NewBatchRequirementSignal,
    Signal,
)
from brewery_agentic_replanning.simulation import BreweryDigitalTwin
from experiments import exp3_cross_functional as exp3
from experiments.common import placements, write_csv
from experiments.config import RESULTS_DIR, ExperimentConfig, load_config

CONFIGURATIONS = ["conf1", "conf2", "conf3"]
REFERENCE = "conf1"
UTILISATION_HORIZON = (0, 42)
COMBINED_DIR = RESULTS_DIR / "combined"
AGENTS = default_agents()


def _days_in_horizon(start_day: int, end_day: int) -> int:
    lo, hi = UTILISATION_HORIZON
    return max(0, min(end_day, hi) - max(start_day, lo))


def configuration_summary(cfg: ExperimentConfig) -> dict:
    brewery, schedule = cfg.baseline()
    lo, hi = UTILISATION_HORIZON
    horizon = hi - lo
    capacity = sum(f.capacity_hl for f in brewery.fermenters)
    tank_days = sum(_days_in_horizon(a.start_day, a.end_day) for a in schedule.assignments)
    volume_days = sum(a.volume_hl * _days_in_horizon(a.start_day, a.end_day) for a in schedule.assignments)
    distribution = Counter(f.capacity_hl for f in brewery.fermenters)
    return {
        "configuration": cfg.name,
        "unique_batches": len(schedule.batches),
        "fermenter_assignments": len(schedule.assignments),
        "split_batches": sum(1 for b in schedule.batches if len(schedule.assignments_of(b)) > 1),
        "fermenter_count": len(brewery.fermenters),
        "capacity_distribution": " + ".join(
            f"{n} x {cap:g} hl" for cap, n in sorted(distribution.items(), reverse=True)
        ),
        "fermenters": " ".join(f"{f.id}:{f.capacity_hl:g}" for f in brewery.fermenters),
        "total_capacity_hl": capacity,
        "kettle_yield_hl": brewery.kettle_yield_hl,
        "max_brews_per_day": brewery.max_brews_per_day,
        "max_bottling_runs_per_day": brewery.max_bottling_runs_per_day,
        "days_off": " ".join(str(d) for d in sorted(brewery.days_off)),
        "planned_volume_hl": sum(a.volume_hl for a in schedule.assignments),
        "horizon": f"days {lo}-{hi - 1}",
        "occupied_tank_days": tank_days,
        "available_tank_days": len(brewery.fermenters) * horizon,
        "time_utilisation": round(tank_days / (len(brewery.fermenters) * horizon), 4),
        "occupied_hl_days": volume_days,
        "available_hl_days": capacity * horizon,
        "volumetric_utilisation": round(volume_days / (capacity * horizon), 4),
        "last_baseline_end_day": schedule.horizon_days(),
    }


def _allocation_problems(brewery: Brewery, schedule: Schedule) -> list[str]:
    problems = []
    for batch_id in schedule.batches:
        rows = schedule.assignments_of(batch_id)
        total = sum(a.volume_hl for a in rows)
        if total > brewery.kettle_yield_hl:
            problems.append(f"{batch_id}: {total} hl > kettle yield {brewery.kettle_yield_hl}")
        if len({a.fermenter.id for a in rows}) != len(rows):
            problems.append(f"{batch_id}: same tank used twice")
        if len({a.start_day for a in rows}) != 1 or len({a.effective_bottling_day for a in rows}) != 1:
            problems.append(f"{batch_id}: split tanks don't share brew and bottling day")
    for fermenter in brewery.fermenters:
        on_tank = sorted(schedule.assignments_on_fermenter(fermenter.id), key=lambda a: a.start_day)
        for earlier, later in pairwise(on_tank):
            if earlier.end_day > later.start_day:
                problems.append(f"{fermenter.id} double-booked: {earlier.batch_id}/{later.batch_id}")
    unknown = {a.fermenter.id for a in schedule.assignments} - {f.id for f in brewery.fermenters}
    problems += [f"unknown tank {t}" for t in sorted(unknown)]
    return problems


def baseline_validation(cfg: ExperimentConfig) -> list[dict]:
    brewery, schedule = cfg.baseline()
    checks: list[tuple[str, bool, str]] = []

    problems = schedule.validate(brewery)
    checks.append(("schedule_validate", not problems, "; ".join(problems) or "no problems reported"))

    over = [a for a in schedule.assignments if a.volume_hl > a.fermenter.capacity_hl]
    checks.append(
        (
            "tank_capacity",
            not over,
            "; ".join(f"{a.batch_id} {a.volume_hl} hl > {a.fermenter.id}" for a in over)
            or f"every assignment <= its tank's capacity ({len(schedule.assignments)} checked)",
        )
    )

    allocation_problems = _allocation_problems(brewery, schedule)
    checks.append(
        (
            "fermenter_allocation",
            not allocation_problems,
            "; ".join(allocation_problems)
            or "batch volume <= kettle yield, no tank double-booked, split tanks share brew/bottling day",
        )
    )

    brews = Counter(schedule.brew_day_of(batch_id) for batch_id in schedule.batches)
    bottlings = Counter(a.effective_bottling_day for a in schedule.assignments)
    peak_brew_day, peak_brews = max(brews.items(), key=lambda kv: (kv[1], -kv[0]))
    peak_bottling_day, peak_bottlings = max(bottlings.items(), key=lambda kv: (kv[1], -kv[0]))
    checks.append(
        (
            "brewing_throughput",
            peak_brews <= brewery.max_brews_per_day,
            f"peak {peak_brews} brew(s) on day {peak_brew_day} (limit {brewery.max_brews_per_day})",
        )
    )
    checks.append(
        (
            "bottling_throughput",
            peak_bottlings <= brewery.max_bottling_runs_per_day,
            f"peak {peak_bottlings} bottling run(s) on day {peak_bottling_day} "
            f"(limit {brewery.max_bottling_runs_per_day}; one run per tank)",
        )
    )

    brew_off = sorted({b for b in schedule.batches if not brewery.is_working_day(schedule.brew_day_of(b))})
    bottle_off = sorted(
        {a.batch_id for a in schedule.assignments if not brewery.is_working_day(a.effective_bottling_day)}
    )
    checks.append(
        (
            "non_working_days",
            not brew_off and not bottle_off,
            (
                f"brewed on a day off: {brew_off}; bottled on a day off: {bottle_off}"
                if brew_off or bottle_off
                else "no brewing and no bottling on any day off"
            ),
        )
    )

    changed_days = []
    for day in range(exp3.END_DAY + 1):
        replanned = HeuristicScheduler().replan(brewery, schedule.clone(), day)
        metrics = compute_metrics(brewery, schedule, replanned, day)
        if placements(replanned) != placements(schedule) or metrics.changed_batches or metrics.schedule_shift_days:
            changed_days.append(day)
    checks.append(
        (
            "no_signal_scheduler_replan",
            not changed_days,
            (
                f"replan without any signal changed the baseline on day(s) {changed_days}"
                if changed_days
                else f"HeuristicScheduler.replan() with no signal on each day 0-{exp3.END_DAY} reproduces "
                f"the baseline exactly (same tank, volume, brew, bottling and end day for every assignment)"
            ),
        )
    )

    policy_notes = []
    policy_ok = True
    for policy_name, factory in exp3.POLICY_FACTORIES.items():
        brewery_copy, schedule_copy = cfg.baseline()
        twin = BreweryDigitalTwin(
            brewery=brewery_copy,
            schedule=schedule_copy,
            scheduler=HeuristicScheduler(),
            policy=factory(),
            agents=AGENTS,
        )
        twin.advance_to(exp3.END_DAY)
        unchanged = placements(twin.schedule) == placements(schedule)
        policy_ok &= unchanged and not twin.replan_log
        policy_notes.append(
            f"{policy_name}: {len(twin.replan_log)} replan(s), schedule {'unchanged' if unchanged else 'CHANGED'}"
        )
    checks.append(("no_signal_policy_runs", policy_ok, f"days 0-{exp3.END_DAY}: " + "; ".join(policy_notes)))

    return [
        {"configuration": cfg.name, "check": name, "result": "PASS" if ok else "FAIL", "detail": detail}
        for name, ok, detail in checks
    ]


def _signal_fields(signal: Signal) -> dict:
    fields = {"batch_id": signal.related_batch_id or "", "fermenter_id": "", "parameters": ""}
    if isinstance(signal, FermentationDelaySignal):
        fields.update(fermenter_id=signal.fermenter_id, parameters=f"extra_days={signal.extra_days}")
    elif isinstance(signal, IngredientShortageSignal):
        fields["parameters"] = f"available_from_day={signal.available_from_day}"
    elif isinstance(signal, NewBatchRequirementSignal):
        b = signal.batch
        fields["parameters"] = (
            f"recipe={b.recipe.name}; planned_brew_day={b.planned_brew_day}; "
            f"target_bottling_day={b.target_bottling_day}"
        )
    elif isinstance(signal, FermenterMaintenanceSignal):
        fields.update(
            fermenter_id=signal.fermenter_id,
            parameters=f"start_day={signal.start_day}; duration_days={signal.duration_days}",
        )
    else:
        fields["parameters"] = f"off_day={getattr(signal, 'off_day', '')}"
    return fields


def _missing_references(brewery: Brewery, schedule: Schedule, signal: Signal) -> str:
    missing = []
    fermenter_ids = {f.id for f in brewery.fermenters}
    if (
        isinstance(signal, (FermentationDelaySignal, FermenterMaintenanceSignal))
        and signal.fermenter_id not in fermenter_ids
    ):
        missing.append(f"tank {signal.fermenter_id}")
    if (
        isinstance(signal, (FermentationDelaySignal, IngredientShortageSignal))
        and signal.batch_id not in schedule.batches
    ):
        missing.append(f"batch {signal.batch_id}")
    if isinstance(signal, FermentationDelaySignal) and not missing:
        try:
            schedule.assignment_for(signal.batch_id, signal.fermenter_id)
        except KeyError:
            missing.append(f"batch {signal.batch_id} is not in tank {signal.fermenter_id}")
    return ", ".join(missing)


def isolated_assessment(cfg: ExperimentConfig, signal: Signal) -> dict:
    brewery, schedule = cfg.baseline()
    missing = _missing_references(brewery, schedule, signal)
    if missing:
        return {
            "references_exist": False,
            "missing": missing,
            "decision": "INVALID",
            "latest_safe_day": "",
            "affected": "",
            "reason": "",
        }
    agent = AGENTS[type(signal)]
    agent.apply(brewery, schedule, signal)
    assessment = agent.assess(brewery, schedule, signal, signal.day)
    affected = ";".join(x for x in (assessment.affected_activity, assessment.affected_resource) if x)
    return {
        "references_exist": True,
        "missing": "",
        "decision": assessment.decision,
        "latest_safe_day": assessment.latest_safe_decision_point,
        "affected": affected,
        "reason": assessment.reason,
    }


def scenario_rows(cfg: ExperimentConfig, reference: ExperimentConfig) -> tuple[list[dict], list[dict]]:
    recipes = cfg.build_recipes()
    reference_recipes = reference.build_recipes()
    reference_scenarios = reference.hook("CF_SCENARIOS")
    targets = cfg.hook("CF_TARGETS")
    notes: dict = getattr(cfg.module, "CF_ADAPTATIONS", {})

    definitions, validity = [], []
    for scenario_name, build in cfg.hook("CF_SCENARIOS").items():
        signals = build(recipes)
        originals = (
            reference_scenarios[scenario_name](reference_recipes) if scenario_name in reference_scenarios else []
        )
        for index, signal in enumerate(signals, start=1):
            signal_id = f"{scenario_name}-S{index}"
            original = originals[index - 1] if index <= len(originals) else None
            own = isolated_assessment(cfg, signal)
            ref = isolated_assessment(reference, original) if original is not None else None
            adapted = original is None or repr(signal) != repr(original)
            same = ref is not None and (own["decision"], own["latest_safe_day"]) == (
                ref["decision"],
                ref["latest_safe_day"],
            )
            target = targets.get(scenario_name)
            definitions.append(
                {
                    "configuration": cfg.name,
                    "scenario": scenario_name,
                    "signal_id": signal_id,
                    "arrival_day": signal.day,
                    "signal_type": signal.kind,
                    "function": AGENTS[type(signal)].organizational_function,
                    "description": signal.description,
                    **_signal_fields(signal),
                    "sales_target": f"{target[0]} bottled on day {target[1]}" if target else "",
                    "adapted": adapted,
                    "adaptation_note": notes.get(signal_id, "UNDOCUMENTED" if adapted else ""),
                    "assessment_on_baseline": own["decision"],
                    "latest_safe_day": own["latest_safe_day"],
                    "affected": own["affected"],
                    "assessment_reason": own["reason"],
                    "conf1_assessment": ref["decision"] if ref else "",
                    "conf1_latest_safe_day": ref["latest_safe_day"] if ref else "",
                    "same_situation_as_conf1": same,
                }
            )
        for index, original in enumerate(originals, start=1):
            here = isolated_assessment(cfg, original)
            ref = isolated_assessment(reference, original)
            validity.append(
                {
                    "configuration": cfg.name,
                    "scenario": scenario_name,
                    "signal_id": f"{scenario_name}-S{index}",
                    "original_signal": original.description,
                    "references_exist": here["references_exist"],
                    "missing": here["missing"],
                    "assessment_here": here["decision"],
                    "latest_safe_day_here": here["latest_safe_day"],
                    "affected_here": here["affected"],
                    "conf1_assessment": ref["decision"],
                    "conf1_latest_safe_day": ref["latest_safe_day"],
                    "conf1_affected": ref["affected"],
                    "valid_and_relevant_unchanged": here["references_exist"]
                    and (here["decision"], here["latest_safe_day"]) == (ref["decision"], ref["latest_safe_day"]),
                }
            )
    return definitions, validity


def _fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def input_identity(cfg: ExperimentConfig) -> list[dict]:
    recipes = cfg.build_recipes()
    rows = []
    for scenario_name in cfg.hook("CF_SCENARIOS"):
        seen = {}
        for policy_name in exp3.POLICY_FACTORIES:
            twin, events = exp3.build_twin(cfg, scenario_name, policy_name, recipes)
            brewery = twin.brewery
            brewery_state = (
                tuple((f.id, f.capacity_hl) for f in brewery.fermenters),
                sorted(brewery.days_off),
                brewery.max_brews_per_day,
                brewery.max_bottling_runs_per_day,
                sorted(brewery.fermenter_maintenance.items()),
            )
            seen[policy_name] = (
                _fingerprint(repr((sorted(placements(twin.original_schedule)), brewery_state))),
                _fingerprint(repr([(e.day, repr(e)) for e in events])),
            )
        if len(set(seen.values())) != 1:
            raise SystemExit(f"{cfg.name}/{scenario_name}: policies would receive different inputs: {seen}")
        baseline_fingerprint, signals_fingerprint = next(iter(seen.values()))
        rows.append(
            {
                "configuration": cfg.name,
                "scenario": scenario_name,
                "policies": ";".join(seen),
                "baseline_fingerprint": baseline_fingerprint,
                "signal_sequence_fingerprint": signals_fingerprint,
                "identical_inputs": True,
            }
        )
    return rows


def _final_snapshot(cfg: ExperimentConfig, result: dict) -> Path:
    scenario_dir = cfg.results_dir / "exp3" / result["scenario"]
    if not result["replanning_days"]:
        return scenario_dir / "baseline.csv"
    last_day = int(str(result["replanning_days"]).split(";")[-1])
    return scenario_dir / result["policy"].lower() / f"day{last_day:02d}_after_replan.csv"


def _snapshot_placements(path: Path) -> set[tuple]:
    with path.open() as f:
        return {
            (r["batch_id"], r["fermenter_id"], r["volume_hl"], r["start_day"], r["bottling_day"], r["end_day"])
            for r in csv.DictReader(f)
        }


def _snapshot_volumes(path: Path) -> dict[str, float]:
    totals: defaultdict[str, float] = defaultdict(float)
    with path.open() as f:
        for r in csv.DictReader(f):
            totals[r["batch_id"]] += float(r["volume_hl"])
    return totals


def volume_preservation(cfg: ExperimentConfig, result_rows: list[dict]) -> list[dict]:
    kettle_yield = cfg.build_brewery().kettle_yield_hl
    rows = []
    for result in result_rows:
        baseline = _snapshot_volumes(cfg.results_dir / "exp3" / result["scenario"] / "baseline.csv")
        final = _snapshot_volumes(_final_snapshot(cfg, result))
        changed = sorted(b for b in baseline if baseline[b] != final.get(b))
        out_of_range = sorted(b for b, v in final.items() if not 0 < v <= kettle_yield)
        rows.append(
            {
                "configuration": cfg.name,
                "scenario": result["scenario"],
                "policy": result["policy"],
                "baseline_volume_hl": sum(baseline.values()),
                "final_volume_of_baseline_batches_hl": sum(final.get(b, 0.0) for b in baseline),
                "new_batch_volume_hl": sum(v for b, v in final.items() if b not in baseline),
                "batches_with_changed_volume": ";".join(changed),
                "batches_outside_kettle_yield": ";".join(out_of_range),
                "volume_preserved": not changed and not out_of_range,
            }
        )
    return rows


def policy_comparison(cfg: ExperimentConfig, result_rows: list[dict]) -> list[dict]:
    metrics = (
        "scheduler_invocations",
        "replanning_days",
        "changed_batches",
        "pct_changed_batches",
        "total_abs_schedule_shift",
        "net_schedule_shift",
        "idle_fermenter_days",
        "target_feasible",
    )
    rows = []
    for scenario_name in cfg.hook("CF_SCENARIOS"):
        by_policy = {r["policy"]: r for r in result_rows if r["scenario"] == scenario_name}
        finals = {p: _snapshot_placements(_final_snapshot(cfg, r)) for p, r in by_policy.items()}
        row = {"configuration": cfg.name, "scenario": scenario_name}
        for metric in metrics:
            for policy_name in exp3.POLICY_FACTORIES:
                row[f"{metric}_{policy_name}"] = by_policy[policy_name][metric]
        row["same_final_UNC_vs_LF"] = finals["UNC"] == finals["LF"]
        row["same_final_LF_vs_COORD"] = finals["LF"] == finals["COORD"]
        row["same_final_UNC_vs_COORD"] = finals["UNC"] == finals["COORD"]
        rows.append(row)
    return rows


def main() -> None:
    configs = [load_config(name) for name in CONFIGURATIONS]
    reference = load_config(REFERENCE)

    validation = [row for cfg in configs for row in baseline_validation(cfg)]
    definitions: list[dict] = []
    validity: list[dict] = []
    for cfg in configs:
        cfg_definitions, cfg_validity = scenario_rows(cfg, reference)
        definitions += cfg_definitions
        validity += cfg_validity

    write_csv(COMBINED_DIR / "configuration_summary.csv", [configuration_summary(cfg) for cfg in configs])
    write_csv(COMBINED_DIR / "baseline_validation.csv", validation)
    write_csv(COMBINED_DIR / "scenario_definitions.csv", definitions)
    write_csv(COMBINED_DIR / "signal_validity_original.csv", validity)
    write_csv(COMBINED_DIR / "input_identity.csv", [row for cfg in configs for row in input_identity(cfg)])

    failed = [f"{r['configuration']}:{r['check']}" for r in validation if r["result"] != "PASS"]
    undocumented = [
        f"{r['configuration']}:{r['signal_id']}" for r in definitions if r["adaptation_note"] == "UNDOCUMENTED"
    ]
    if failed or undocumented:
        raise SystemExit(f"not running policies - failed checks: {failed}; undocumented adaptations: {undocumented}")

    assessments: list[dict] = []
    decisions: list[dict] = []
    results: list[dict] = []
    comparison: list[dict] = []
    volumes: list[dict] = []
    for cfg in configs:
        cfg_assessments, cfg_decisions, cfg_results = exp3.run_configuration(cfg)
        assessments += cfg_assessments
        decisions += cfg_decisions
        results += cfg_results
        comparison += policy_comparison(cfg, cfg_results)
        volumes += volume_preservation(cfg, cfg_results)

    write_csv(COMBINED_DIR / "exp3_results.csv", results, exp3.RESULT_FIELDNAMES)
    write_csv(COMBINED_DIR / "exp3_assessment_log.csv", assessments, exp3.ASSESSMENT_FIELDNAMES)
    write_csv(COMBINED_DIR / "exp3_decision_log.csv", decisions, exp3.DECISION_FIELDNAMES)
    write_csv(COMBINED_DIR / "policy_comparison.csv", comparison)
    write_csv(COMBINED_DIR / "volume_preservation.csv", volumes)
    broken = [f"{v['configuration']}/{v['scenario']}/{v['policy']}" for v in volumes if not v["volume_preserved"]]
    if broken:
        raise SystemExit(f"batch volume not preserved in: {broken}")


if __name__ == "__main__":
    main()
