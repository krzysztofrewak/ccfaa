import json
import math
from pathlib import Path

import pytest

from brewery_schedule_digital_twin.scheduling import CpSatScheduler, DifferentialEvolutionScheduler, HeuristicScheduler
from experiments.itch2027 import breweries
from experiments.itch2027.estimates import Estimate, PairedDifference
from experiments.itch2027.measures import BaselineProfile, fingerprint
from experiments.itch2027.methods import RepairMethod, solver_columns
from experiments.itch2027.replanning import Repair
from experiments.itch2027.storage import Metadata, RunLog
from experiments.itch2027.sweep import DelaySweep

WOLOW = DelaySweep("wolow", breweries.wolow())


def test_an_estimate_is_a_mean_with_a_t_interval_and_none_for_a_single_value() -> None:
    estimate = Estimate.of([1, 11, 21, None])

    assert (estimate.mean, estimate.n) == (11, 3)
    assert estimate.ci95 == pytest.approx(4.303 * 10 / math.sqrt(3))
    assert Estimate.of([5]) == Estimate(5, None, 1)
    assert Estimate.of([]) == Estimate(None, None, 0)
    assert Estimate(2.0, 0.5, 4).format() == "2.0 ±0.5"


def test_a_paired_difference_counts_the_signs_of_its_values() -> None:
    difference = PairedDifference.of([-2.0, 0.0, 1.0, 3.0])

    assert (difference.lower, difference.equal, difference.higher) == (1, 1, 2)
    assert difference.columns()["mean_second_minus_first"] == 0.5


def test_a_method_builds_its_scheduler_with_its_settings_and_seed_and_describes_every_parameter() -> None:
    method = RepairMethod("cpsat", 3, {"time_limit_seconds": 5.0, "workers": 4})
    scheduler = method.scheduler()

    assert isinstance(scheduler, CpSatScheduler) and method.key == "cpsat:3" and method.threads == 4
    assert method.description()["class"] == "CpSatScheduler"
    assert {
        "time_limit_seconds": 5.0,
        "workers": 4,
        "seed": 3,
        "max_placements": 40,
    }.items() <= method.parameters().items()
    assert isinstance(RepairMethod("greedy").scheduler(), HeuristicScheduler) and RepairMethod("greedy").key == "greedy"
    with pytest.raises(ValueError):
        RepairMethod("tabu")


def test_solver_columns_report_the_last_search_of_cpsat_and_of_differential_evolution() -> None:
    brewery, baseline = WOLOW.generate("B", 0.97, seed=0)
    delay = WOLOW.delays(baseline)[0]
    cpsat = CpSatScheduler(time_limit_seconds=5.0)
    de = DifferentialEvolutionScheduler(population_size=6, generations=3)

    Repair.run(brewery, baseline, delay.day, [delay], cpsat)
    Repair.run(brewery, baseline, delay.day, [delay], de)

    columns = solver_columns(cpsat)
    assert columns["cpsat_objective"] is not None and columns["cpsat_best_bound"] <= columns["cpsat_objective"]
    assert columns["cpsat_gap"] == columns["cpsat_objective"] - columns["cpsat_best_bound"]
    evolution = solver_columns(de)
    assert evolution["de_cost"] <= evolution["de_greedy_cost"] and evolution["de_generations"] == 3
    assert solver_columns(HeuristicScheduler()) == {}


def test_a_repair_measures_a_valid_schedule_and_keeps_its_baseline() -> None:
    brewery, baseline = WOLOW.generate("A", 1.0, seed=0)
    delay = WOLOW.delays(baseline)[0]
    before = fingerprint(baseline)

    repair = Repair.run(brewery, baseline, delay.day, [delay])

    assert repair.feasible and repair.problems() == [] and repair.cost() >= 0
    assert repair.wall_seconds >= repair.scheduler_seconds > 0
    assert fingerprint(baseline) == before
    assert BaselineProfile.of(brewery, baseline, 60).as_row()["tanks"] == len(brewery.vessels)


def test_a_run_log_keeps_every_run_and_drops_a_line_cut_short(tmp_path: Path) -> None:
    log = RunLog(tmp_path)
    log.append("s", {"run": 1})
    log.append("s", {"run": 2})
    with (tmp_path / "s.jsonl").open("a") as f:
        f.write('{"run": ')

    assert log.read() == [{"run": 1}, {"run": 2}]
    assert (tmp_path / "s.jsonl").read_text() == json.dumps({"run": 1}) + "\n" + json.dumps({"run": 2}) + "\n"


def test_metadata_records_the_protocol_and_every_session(tmp_path: Path) -> None:
    metadata = Metadata(tmp_path)

    started = metadata.start_session({"protocol": {"seed": 1}}, workers=2, done=0, pending=5)
    metadata.finish_session(started, made=5)

    recorded = json.loads((tmp_path / "metadata.json").read_text())
    assert recorded["protocol"] == {"seed": 1}
    (session,) = recorded["sessions"]
    assert (session["workers"], session["runs_made"]) == (2, 5) and "hardware" in session
