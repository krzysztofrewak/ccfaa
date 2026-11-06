"""Experiment 3: the greedy, CP-SAT and differential evolution schedulers repair the same delays of Experiment 2.

Does the ranking of the unitank variants hold when another method repairs
the schedule? Every variant of the three breweries of Experiment 2, at target
occupancies of 90, 97 and 100%, with each of its 10 schedules: of the delays
each schedule got in Experiment 2, one is drawn for each of 1, 2 and 3 extra
days, with a fixed seed and without looking at any result, 900 scenarios. A
schedule without a delay of some extra days is reported before anything
runs; no other delay stands in for it. The scenarios are written once to
``results/exp3/manifest.csv`` with a fingerprint of each baseline, so every
method gets the same ones.

Each scenario regenerates its baseline, checks its fingerprint and gives
every method of ``METHODS`` a fresh copy of it with the same delay, replanned
on the signal day. Every run goes to the ``RunLog`` of ``results/exp3/runs/``
as it ends. A run that finds no schedule, or an invalid one, is kept with its
status and without a cost. CP-SAT returns the greedy schedule it completed as
its starting solution when its search finds nothing better, marked
``fallback`` = ``cpsat_start``. ``metadata.json`` records the methods'
settings, the decisions each takes, what the times measure and, for every
session, the libraries and the hardware.

``Comparison`` averages the three differential evolution runs of a scenario,
then the scenarios of a schedule, and gives means and 95% confidence
intervals over the schedules of each brewery, variant, occupancy and method
(``summary.csv``), the methods compared pairwise on the same scenarios
(``paired.csv``) and each method's ranking of the variants (``ranking.csv``).
"""

from __future__ import annotations

import argparse
import os
import random
import time
import traceback
from collections import defaultdict
from collections.abc import Iterable, Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

from brewery_schedule_digital_twin.domain import Brewery, Schedule
from brewery_schedule_digital_twin.signals import FermentationDelaySignal
from experiments.itch2027 import exp2
from experiments.itch2027.estimates import Estimate, PairedDifference
from experiments.itch2027.measures import BaselineProfile, Row, fingerprint
from experiments.itch2027.methods import RepairMethod, solver_columns
from experiments.itch2027.replanning import Repair
from experiments.itch2027.storage import RESULTS_DIR, Metadata, RunLog, read_csv, write_csv
from experiments.itch2027.sweep import DelaySweep, delay_columns

OCCUPANCIES = (0.90, 0.97, 1.0)
EXTRA_DAYS = (1, 2, 3)
SCHEDULES = 10
TIME_LIMIT_SECONDS = 10.0
SAMPLE_SEED = "exp3"
EXPERIMENT_ORDER = ("wolow", "bury", "elblag")

METHODS = (
    RepairMethod("greedy"),
    RepairMethod("cpsat", 0, {"time_limit_seconds": TIME_LIMIT_SECONDS, "workers": 1}),
    *(RepairMethod("de", seed, {"time_limit_seconds": TIME_LIMIT_SECONDS}) for seed in (0, 1, 2)),
)
METHOD_NAMES = tuple(dict.fromkeys(method.name for method in METHODS))
METRICS = ("abs_bottling_shift_days", "abs_brew_shift_days", "repair_cost", "scheduler_seconds")
SHARES = ("solved", "fallback")
RANKED_METRICS = ("repair_cost", "abs_bottling_shift_days", "abs_brew_shift_days")
GROUP = ("experiment", "variant", "target_occupancy")

DECISIONS = {
    "greedy": (
        "HeuristicScheduler: places the batches one by one in priority order; a batch keeps its current vessels "
        "while they are still feasible, else goes into the smallest vessels that hold it; brews and bottling runs "
        "take the earliest feasible days, brewhouses and lines."
    ),
    "cpsat": (
        "CpSatScheduler: decides the day and brewhouse of every brew, one of up to max_placements candidate "
        "placements per batch (its current one, the greedy one, every single vessel holding it, minimal "
        "largest-first combinations), and the line and daily chunks of every vessel's bottling (up to "
        "extra_chunks chunks more than the line needs), also for locked vessels not yet bottled. The greedy "
        "schedule bounds the horizon and, completed by a first solve of up to half the limit, is the starting "
        "solution, returned (fallback cpsat_start) if the search finds nothing better; if that completion fails, "
        "the solver starts from a partial hint and may find no schedule at all."
    ),
    "de": (
        "DifferentialEvolutionScheduler: searches, for every batch not locked, its place in the greedy order, "
        "whether it keeps its current vessels and which single vessel holding it it goes into; brew days, "
        "brewhouses, splits over several vessels and bottling are left to the greedy decoder. The first "
        "population holds the genome of the greedy schedule."
    ),
    "common": (
        "All methods replan the same twin state on the signal day: assignments of batches already started are "
        "locked (vessels and brews kept), the cost of every result is repair_cost with the default RepairWeights "
        "against the state before replanning, and every result is validated with measures.replanning_problems "
        "and checked for moved locked batches."
    ),
}
TIMING = (
    "scheduler_seconds: wall time of Scheduler.replan alone, from the twin state on the signal day to the "
    "repaired schedule; wall_seconds: also building the twin, injecting the delay and advancing to its day, "
    "without generating the baseline, the cost or the measures. CP-SAT applies its limit to its two solves only: "
    "finding the greedy schedule and building the model come before it (cpsat_model_seconds). Differential "
    "evolution checks its limit before decoding each genome but the first, so it overruns it by at most one "
    "decoding. The greedy scheduler has no limit. Runs share the machine with the other workers."
)


def sweeps() -> dict[str, DelaySweep]:
    return {name: build() for name, build in exp2.SWEEPS.items()}


@dataclass(frozen=True)
class Scenario:
    """One delay of one Experiment 2 schedule, which every method repairs."""

    scenario_id: str
    experiment: str
    variant: str
    target_occupancy: float
    index: int
    seed: int
    signal_day: int
    delayed_tank: str
    delayed_batch: str
    extra_days: int
    lead_days: int
    candidates: int
    baseline_fingerprint: str

    @classmethod
    def from_row(cls, row: dict[str, str]) -> Scenario:
        types = {field.name: field.type for field in fields(cls)}
        convert = {"int": int, "float": float, "str": str}
        return cls(**{name: convert[str(types[name])](value) for name, value in row.items()})

    @staticmethod
    def make_id(experiment: str, variant: str, target: float, index: int, extra_days: int) -> str:
        return f"{experiment}-{variant}-{round(100 * target)}-{index}-x{extra_days}"

    @property
    def delay(self) -> FermentationDelaySignal:
        return FermentationDelaySignal(self.signal_day, self.delayed_batch, self.delayed_tank, self.extra_days)

    @property
    def sweep(self) -> DelaySweep:
        return exp2.SWEEPS[self.experiment]()

    def baseline(self) -> tuple[Brewery, Schedule]:
        """The regenerated baseline, checked against the fingerprint the scenario was drawn with."""
        brewery, baseline = self.sweep.generate(self.variant, self.target_occupancy, self.seed)
        if fingerprint(baseline) != self.baseline_fingerprint:
            raise RuntimeError(f"{self.scenario_id}: the regenerated baseline differs from the manifest's")
        return brewery, baseline

    def as_row(self) -> Row:
        return asdict(self)


class Manifest:
    """The scenarios of the experiment, drawn once from the delays of Experiment 2."""

    def __init__(self, scenarios: Iterable[Scenario]) -> None:
        self.scenarios = list(scenarios)

    @classmethod
    def draw(cls, workers: int, exp2_dir: Path) -> Manifest:
        """Every scenario of the sample; exits listing the gaps if a schedule lacks a delay of some extra days."""
        tasks = [
            (name, variant, target, index)
            for name, sweep in sweeps().items()
            for variant in sweep.variants
            for target in OCCUPANCIES
            for index in range(SCHEDULES)
        ]
        scenarios: list[Scenario] = []
        delays: dict[tuple, set[str]] = {}
        missing: list[str] = []
        with ProcessPoolExecutor(max_workers=workers) as pool:
            for task, (drawn, keys, gaps) in zip(tasks, pool.map(_draw_schedule, tasks), strict=True):
                scenarios += drawn
                delays[task] = keys
                missing += gaps
        if missing:
            raise SystemExit(f"Experiment 2 has no scenario to draw for {len(missing)} cases:\n" + "\n".join(missing))
        _check_against_exp2(delays, exp2_dir)
        return cls(scenarios)

    @classmethod
    def read(cls, path: Path) -> Manifest:
        return cls(Scenario.from_row(row) for row in read_csv(path))

    def write(self, path: Path) -> None:
        write_csv(path, [scenario.as_row() for scenario in self.scenarios])

    def where(self, **values: object) -> Manifest:
        """The scenarios whose fields have the given values, a value given as a tuple standing for any of them."""

        def matches(scenario: Scenario) -> bool:
            for name, wanted in values.items():
                options = wanted if isinstance(wanted, tuple) else (wanted,)
                if getattr(scenario, name) not in options:
                    return False
            return True

        return Manifest(s for s in self.scenarios if matches(s))

    def pilot(self) -> Manifest:
        """Schedule 0 at 100% of every variant, 30 scenarios."""
        return self.where(target_occupancy=1.0, index=0)


def _draw_schedule(task: tuple[str, str, float, int]) -> tuple[list[Scenario], set[str], list[str]]:
    """The scenarios drawn from one schedule's delays, those delays as keys, and the extra days it has none of."""
    name, variant, target, index = task
    sweep = sweeps()[name]
    seed = index
    _, baseline = sweep.generate(variant, target, seed)
    delays = sweep.delays_of(baseline, seed)
    scenarios, missing = [], []
    for extra_days in EXTRA_DAYS:
        candidates = [delay for delay in delays if delay.extra_days == extra_days]
        if not candidates:
            missing.append(f"  {name} {variant} {target:.0%} schedule {index}: no delay of {extra_days} extra days")
            continue
        delay = random.Random(f"{SAMPLE_SEED} {name} {variant} {target} {seed} {extra_days}").choice(candidates)
        original = baseline.assignment_for(delay.batch_id, delay.fermenter_id)
        scenarios.append(
            Scenario(
                scenario_id=Scenario.make_id(name, variant, target, index, extra_days),
                experiment=name,
                variant=variant,
                target_occupancy=target,
                index=index,
                seed=seed,
                signal_day=delay.day,
                delayed_tank=delay.fermenter_id,
                delayed_batch=delay.batch_id,
                extra_days=delay.extra_days,
                lead_days=original.natural_end_day - delay.day,
                candidates=len(candidates),
                baseline_fingerprint=fingerprint(baseline),
            )
        )
    return scenarios, {_delay_key(d.day, d.fermenter_id, d.batch_id, d.extra_days) for d in delays}, missing


def _delay_key(*values: object) -> str:
    return "/".join(str(value) for value in values)


def _check_against_exp2(delays: dict[tuple, set[str]], exp2_dir: Path) -> None:
    """Checks that the regenerated schedules got the very delays Experiment 2 recorded, where it has run."""
    for name in {task[0] for task in delays}:
        path = exp2_dir / name / "runs.csv"
        if not path.exists():
            print(f"warning: {path} is missing, the sample is not checked against Experiment 2")
            continue
        recorded: dict[tuple, set[str]] = defaultdict(set)
        for row in read_csv(path):
            key = (name, row["variant"], float(row["target_occupancy"]), int(row["index"]))
            recorded[key].add(
                _delay_key(row["signal_day"], row["delayed_tank"], row["delayed_batch"], row["extra_days"])
            )
        for task, keys in delays.items():
            if task in recorded and recorded[task] != keys:
                raise SystemExit(f"{task}: the regenerated delays differ from Experiment 2's")


class ScenarioRunner:
    """Runs methods on scenarios into a ``RunLog``, skipping the runs it already holds."""

    def __init__(self, methods: Sequence[RepairMethod], log: RunLog) -> None:
        self.methods = tuple(methods)
        self.log = log

    def pending(self, manifest: Manifest) -> list[tuple[Scenario, tuple[RepairMethod, ...]]]:
        done = {run_key(run) for run in self.log.read()}
        order = {"elblag": 0, "bury": 1, "wolow": 2}
        return [
            (scenario, methods)
            for scenario in sorted(manifest.scenarios, key=lambda s: order.get(s.experiment, len(order)))
            if (methods := tuple(m for m in self.methods if (scenario.scenario_id, m.name, m.seed) not in done))
        ]

    def run(self, manifest: Manifest, workers: int, metadata: Metadata | None = None) -> None:
        tasks = self.pending(manifest)
        total = sum(len(methods) for _, methods in tasks)
        print(f"{total} runs to make in {len(tasks)} scenarios on {workers} processes", flush=True)
        session = (
            metadata.start_session(protocol(self.methods), workers, len(self.log.read()), total) if metadata else ""
        )
        made = 0
        start = time.perf_counter()
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_run_scenario, scenario, methods, self.log) for scenario, methods in tasks]
            for number, future in enumerate(as_completed(futures), start=1):
                made += future.result()
                if number % 10 == 0 or number == len(futures):
                    elapsed = (time.perf_counter() - start) / 60
                    print(f"{number}/{len(futures)} scenarios, {made}/{total} runs, {elapsed:.1f} min", flush=True)
        if metadata:
            metadata.finish_session(session, made)


def _run_scenario(scenario: Scenario, methods: tuple[RepairMethod, ...], log: RunLog) -> int:
    brewery, baseline = scenario.baseline()
    sweep = scenario.sweep
    row = {
        "scenario_id": scenario.scenario_id,
        **sweep.schedule_columns(scenario.variant, scenario.target_occupancy, scenario.index, scenario.seed),
        **BaselineProfile.of(brewery, baseline, sweep.horizon_days).as_row(),
    }
    for method in methods:
        log.append(scenario.scenario_id, run_method(brewery, baseline, scenario.delay, sweep.horizon_days, row, method))
    return len(methods)


def run_method(
    brewery: Brewery,
    baseline: Schedule,
    delay: FermentationDelaySignal,
    horizon_days: int,
    row: Row,
    method: RepairMethod,
) -> Row:
    """The row of one method's repair of ``delay`` on a fresh copy of ``baseline``, whatever its outcome."""
    scheduler = method.scheduler()
    result: Row = {**row, "method": method.name, "method_seed": method.seed, **delay_columns(baseline, delay)}
    start = time.perf_counter()
    try:
        repair = Repair.run(brewery, baseline, delay.day, [delay], scheduler)
    except Exception as error:  # a crash is a result of the run too
        result.update(
            status="error",
            feasible=False,
            valid=None,
            error=f"{type(error).__name__}: {error}",
            traceback=traceback.format_exc(limit=5),
            wall_seconds=time.perf_counter() - start,
        )
    else:
        result.update(scheduler_seconds=repair.scheduler_seconds, wall_seconds=repair.wall_seconds)
        if repair.feasible:
            problems = repair.problems()
            result.update(
                feasible=True,
                repair_cost=repair.cost(),
                **repair.change(horizon_days).as_row(),
                validation_problems=len(problems),
                validation_sample=" | ".join(problems[:3]),
                valid=not problems,
                status="solved" if not problems else "invalid",
            )
        else:
            result.update(feasible=False, valid=None, error=repair.error, status="infeasible")
    columns = solver_columns(scheduler)
    result.update(columns, fallback="cpsat_start" if columns.get("cpsat_returned_start") else "none")
    result["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return result


def run_key(run: Row) -> tuple[object, object, object]:
    return run["scenario_id"], run["method"], run["method_seed"]


def protocol(methods: Sequence[RepairMethod]) -> dict:
    return {
        "protocol": {
            "occupancies": OCCUPANCIES,
            "extra_days": EXTRA_DAYS,
            "schedules": SCHEDULES,
            "sample_seed": SAMPLE_SEED,
            "exp2_signals_per_schedule": exp2.SIGNALS_PER_SCHEDULE,
            "methods": [method.key for method in methods],
            "fallback": (
                "a run without a valid schedule keeps its status and gets no cost; cpsat_start marks a CP-SAT run "
                "returning its completed starting solution (the greedy schedule) as its search found no better"
            ),
        },
        "solvers": {method.key: method.description() for method in methods},
        "decisions": DECISIONS,
        "timing": TIMING,
    }


class Comparison:
    """The runs of the methods aggregated by scenario, by schedule and by brewery, variant and occupancy."""

    def __init__(self, runs: Sequence[Row]) -> None:
        self.runs = sorted(
            runs, key=lambda r: (r["scenario_id"], METHOD_NAMES.index(r["method"]), r["method_seed"] or 0)
        )
        self.by_scenario = self._scenario_values()
        self.by_schedule = self._schedule_values()

    def _scenario_values(self) -> dict[tuple[str, str], Row]:
        """By scenario and method: the share of valid runs and each measure averaged over the runs, if all are valid.

        A method run several times gets a measure only when every run of the
        scenario found a valid schedule, so a mean is never taken over its
        luckier runs alone.
        """
        by_key: dict[tuple[str, str], list[Row]] = defaultdict(list)
        for run in self.runs:
            by_key[run["scenario_id"], run["method"]].append(run)
        values = {}
        for key, group in by_key.items():
            valid = [run["status"] == "solved" for run in group]
            entry: Row = {column: group[0][column] for column in GROUP}
            entry.update(index=group[0]["index"], runs=len(group), solved=mean(valid))
            entry["fallback"] = mean(run.get("fallback", "none") != "none" for run in group)
            for metric in METRICS:
                entry[metric] = mean(float(run[metric]) for run in group) if all(valid) else None
            values[key] = entry
        return values

    def _schedule_values(self) -> dict[tuple, Row]:
        """By schedule and method: the measures averaged over the scenarios that have them."""
        by_key: dict[tuple, list[Row]] = defaultdict(list)
        for (_, method), entry in self.by_scenario.items():
            key = (entry["experiment"], entry["variant"], entry["target_occupancy"], entry["index"], method)
            by_key[key].append(entry)
        return {
            key: {
                "scenarios": len(group),
                **{share: mean(entry[share] for entry in group) for share in SHARES},
                **{metric: _mean_present(entry[metric] for entry in group) for metric in METRICS},
            }
            for key, group in by_key.items()
        }

    def summary(self) -> list[Row]:
        """Means over the schedules ± 95% CI by brewery, variant, occupancy and method."""
        groups: dict[tuple, list[Row]] = defaultdict(list)
        for (experiment, variant, target, _, method), entry in self.by_schedule.items():
            groups[experiment, variant, target, method].append(entry)
        rows = []
        for (experiment, variant, target, method), group in sorted(groups.items(), key=lambda item: _order(*item[0])):
            row: Row = {"experiment": experiment, "variant": variant, "target_occupancy": target, "method": method}
            row["schedules"] = len(group)
            for metric in (*SHARES, *METRICS):
                row.update(Estimate.of(entry[metric] for entry in group).columns(metric))
            rows.append(row)
        return rows

    def paired(self) -> list[Row]:
        """The second method minus the first on the scenarios both have a measure of, averaged by schedule first.

        For each brewery, variant and occupancy and for each brewery as a whole.
        """
        rows = []
        scenario_ids = sorted({scenario for scenario, _ in self.by_scenario})
        pairs = [(a, b) for i, a in enumerate(METHOD_NAMES) for b in METHOD_NAMES[i + 1 :]]
        for first, second in pairs:
            for metric in METRICS:
                by_group: dict[tuple, dict[tuple, list[float]]] = defaultdict(lambda: defaultdict(list))
                for scenario in scenario_ids:
                    a, b = self.by_scenario.get((scenario, first)), self.by_scenario.get((scenario, second))
                    if a is None or b is None or a[metric] is None or b[metric] is None:
                        continue
                    group = tuple(a[c] for c in GROUP)
                    schedule = (*group, a["index"])
                    for key in (group, (a["experiment"], "all", "all")):
                        by_group[key][schedule].append(b[metric] - a[metric])
                for (experiment, variant, target), by_schedule in sorted(by_group.items(), key=lambda i: str(i[0])):
                    flat = [d for ds in by_schedule.values() for d in ds]
                    difference = PairedDifference.of((mean(ds) for ds in by_schedule.values()), counted=flat)
                    rows.append(
                        {
                            "experiment": experiment,
                            "variant": variant,
                            "target_occupancy": target,
                            "metric": metric,
                            "first": first,
                            "second": second,
                            **difference.columns(),
                            "schedules": difference.estimate.n,
                            "scenarios": len(flat),
                        }
                    )
        return rows

    def ranking(self, summary: Sequence[Row]) -> list[Row]:
        """Each method's ranking of the variants of a brewery, at each occupancy and over all, lowest first."""
        entries = [dict(row) for row in summary]
        pooled: dict[tuple, list[Row]] = defaultdict(list)
        for (experiment, variant, _, _, method), entry in self.by_schedule.items():
            pooled[experiment, variant, method].append(entry)
        for (experiment, variant, method), group in pooled.items():
            pooled_row: Row = {"experiment": experiment, "variant": variant, "target_occupancy": "all"}
            pooled_row["method"] = method
            for metric in (*SHARES, *METRICS):
                pooled_row.update(Estimate.of(e[metric] for e in group).columns(metric))
            entries.append(pooled_row)
        by_group: dict[tuple, list[Row]] = defaultdict(list)
        for entry in entries:
            by_group[entry["experiment"], str(entry["target_occupancy"]), entry["method"]].append(entry)
        rows = []
        for (experiment, target, method), group in by_group.items():
            for metric in RANKED_METRICS:
                ranked = sorted(group, key=lambda e: (e[metric] is None, e[metric] or 0, e["variant"]))
                for rank, entry in enumerate(ranked, start=1):
                    rows.append(
                        {
                            "experiment": experiment,
                            "target_occupancy": target,
                            "method": method,
                            "metric": metric,
                            "rank": rank if entry[metric] is not None else None,
                            "variant": entry["variant"],
                            "mean": entry[metric],
                            "ci95": entry[f"{metric}_ci95"],
                            "schedules": entry[f"{metric}_n"],
                        }
                    )
        return rows

    def write(self, results_dir: Path) -> None:
        summary = self.summary()
        ranking = self.ranking(summary)
        write_csv(results_dir / "runs.csv", self.runs)
        write_csv(
            results_dir / "scenarios.csv",
            [
                {"scenario_id": scenario, "method": method, **entry}
                for (scenario, method), entry in self.by_scenario.items()
            ],
        )
        write_csv(results_dir / "summary.csv", summary)
        write_csv(results_dir / "paired.csv", self.paired())
        write_csv(results_dir / "ranking.csv", ranking)
        self._print(summary, ranking)

    def _print(self, summary: Sequence[Row], ranking: Sequence[Row]) -> None:
        statuses: dict[tuple[str, str], int] = defaultdict(int)
        for run in self.runs:
            statuses[run["method"], run["status"]] += 1
        print("\nruns by status: " + ", ".join(f"{m} {s} {n}" for (m, s), n in sorted(statuses.items())))
        print("\nmean over schedules ± 95% CI: bottling shift | brew shift | repair cost | seconds | solved | fallback")
        for row in summary:
            cells = [_estimate(row, metric).format() for metric in METRICS]
            cells += [_estimate(row, share).format("{:.0%}") for share in SHARES]
            print(
                f"  {row['experiment']:7} {row['variant']} {row['target_occupancy']:.0%} {row['method']:6} "
                + " | ".join(cells)
            )
        print("\nrankings of the variants, lowest first (all occupancies):")
        for metric in RANKED_METRICS:
            for experiment in EXPERIMENT_ORDER:
                orders = []
                for method in METHOD_NAMES:
                    ranked = sorted(
                        (
                            r
                            for r in ranking
                            if (r["experiment"], r["target_occupancy"], r["method"], r["metric"])
                            == (experiment, "all", method, metric)
                            and r["rank"] is not None
                        ),
                        key=lambda r: r["rank"],
                    )
                    orders.append(f"{method} " + (" < ".join(r["variant"] for r in ranked) or "-"))
                print(f"  {metric:24} {experiment:7} " + "   ".join(orders))


def _mean_present(values: Iterable[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return mean(present) if present else None


def _order(experiment: str, variant: str, target: float, method: str) -> tuple:
    return EXPERIMENT_ORDER.index(experiment), variant, target, METHOD_NAMES.index(method)


def _estimate(row: Row, metric: str) -> Estimate:
    return Estimate(row[metric], row[f"{metric}_ci95"], row[f"{metric}_n"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pilot", action="store_true", help="only schedule 0 at 100%% of every variant")
    parser.add_argument("--method", nargs="+", choices=METHOD_NAMES, default=list(METHOD_NAMES), help="methods to run")
    parser.add_argument("--aggregate-only", action="store_true", help="aggregate the runs there are, run nothing")
    parser.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) // 2), help="processes")
    args = parser.parse_args()
    manifest_path = RESULTS_DIR / "exp3" / "manifest.csv"
    if manifest_path.exists():
        manifest = Manifest.read(manifest_path)
    else:
        manifest = Manifest.draw(args.workers, RESULTS_DIR / "exp2")
        manifest.write(manifest_path)
    results_dir = RESULTS_DIR / ("exp3_pilot" if args.pilot else "exp3")
    if args.pilot:
        manifest = manifest.pilot()
        manifest.write(results_dir / "manifest.csv")
    log = RunLog(results_dir / "runs")
    methods = [method for method in METHODS if method.name in args.method]
    if not args.aggregate_only:
        ScenarioRunner(methods, log).run(manifest, args.workers, Metadata(results_dir))
    ids = {scenario.scenario_id for scenario in manifest.scenarios}
    expected = {(scenario_id, m.name, m.seed) for scenario_id in ids for m in METHODS}
    runs = [run for run in log.read() if run_key(run) in expected]
    if len(runs) < len(expected):
        print(f"warning: {len(expected) - len(runs)} of {len(expected)} runs are missing; aggregating what there is")
    Comparison(runs).write(results_dir)


if __name__ == "__main__":
    main()
