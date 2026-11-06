from pathlib import Path

import pytest

from brewery_schedule_digital_twin.scheduling import HeuristicScheduler, SchedulingError
from experiments.itch2027 import exp3, exp3_elblag_pilot
from experiments.itch2027.measures import fingerprint
from experiments.itch2027.storage import RunLog


def test_methods_are_one_greedy_one_cpsat_and_three_de_runs_with_fixed_seeds_and_limits() -> None:
    assert [method.key for method in exp3.METHODS] == ["greedy", "cpsat:0", "de:0", "de:1", "de:2"]
    solvers = exp3.protocol(exp3.METHODS)["solvers"]

    assert solvers["greedy"]["class"] == "HeuristicScheduler"
    cpsat = solvers["cpsat:0"]
    assert (cpsat["class"], cpsat["time_limit_seconds"], cpsat["workers"], cpsat["seed"]) == (
        "CpSatScheduler",
        10.0,
        1,
        0,
    )
    assert [(solvers[f"de:{s}"]["time_limit_seconds"], solvers[f"de:{s}"]["seed"]) for s in range(3)] == [
        (10.0, 0),
        (10.0, 1),
        (10.0, 2),
    ]


def _scenarios(task: tuple[str, str, float, int]) -> list[exp3.Scenario]:
    scenarios, _, missing = exp3._draw_schedule(task)
    assert missing == []
    return scenarios


def test_a_schedule_gives_one_scenario_per_extra_days_drawn_from_its_delays_alike_every_time() -> None:
    scenarios, delays, _ = exp3._draw_schedule(("wolow", "A", 0.97, 3))

    assert [s.extra_days for s in scenarios] == [1, 2, 3]
    for scenario in scenarios:
        assert f"{scenario.signal_day}/{scenario.delayed_tank}/{scenario.delayed_batch}/{scenario.extra_days}" in delays
    assert _scenarios(("wolow", "A", 0.97, 3)) == scenarios


def test_the_sample_is_900_scenarios_of_every_variant_occupancy_and_schedule() -> None:
    variants = sum(len(sweep.variants) for sweep in exp3.sweeps().values())

    assert variants == 10
    assert variants * len(exp3.OCCUPANCIES) * exp3.SCHEDULES * len(exp3.EXTRA_DAYS) == 900


def test_a_manifest_is_written_and_read_back_and_filtered(tmp_path: Path) -> None:
    manifest = exp3.Manifest(_scenarios(("wolow", "B", 1.0, 0)) + _scenarios(("wolow", "B", 0.9, 1)))

    manifest.write(tmp_path / "manifest.csv")
    again = exp3.Manifest.read(tmp_path / "manifest.csv")

    assert again.scenarios == manifest.scenarios
    assert [s.scenario_id for s in again.pilot().scenarios] == [f"wolow-B-100-0-x{x}" for x in (1, 2, 3)]
    assert len(again.where(index=(0, 1), extra_days=2).scenarios) == 2


def test_a_scenario_regenerates_its_baseline_and_rejects_another() -> None:
    (scenario, *_) = _scenarios(("wolow", "B", 0.97, 0))

    _, baseline = scenario.baseline()

    assert fingerprint(baseline) == scenario.baseline_fingerprint
    with pytest.raises(RuntimeError):
        exp3.Scenario(**{**scenario.as_row(), "baseline_fingerprint": "0" * 16}).baseline()


def _run(monkeypatch: pytest.MonkeyPatch | None = None, error: Exception | None = None) -> dict:
    (scenario, *_) = _scenarios(("wolow", "B", 0.97, 0))
    brewery, baseline = scenario.baseline()
    if monkeypatch is not None and error is not None:

        def fail(*args: object) -> None:
            raise error

        monkeypatch.setattr(HeuristicScheduler, "replan", fail)
    return exp3.run_method(brewery, baseline, scenario.delay, 60, {"scenario_id": "s"}, exp3.METHODS[0])


def test_a_greedy_run_is_solved_valid_and_measured() -> None:
    row = _run()

    assert (row["status"], row["valid"], row["fallback"]) == ("solved", True, "none")
    assert row["validation_problems"] == 0 and row["repair_cost"] >= 0
    assert row["wall_seconds"] >= row["scheduler_seconds"] > 0


def test_a_run_that_finds_no_schedule_is_kept_without_a_cost(monkeypatch: pytest.MonkeyPatch) -> None:
    row = _run(monkeypatch, SchedulingError("no schedule"))

    assert (row["status"], row["feasible"], row["valid"]) == ("infeasible", False, None)
    assert "repair_cost" not in row and row["scheduler_seconds"] >= 0


def test_a_run_that_crashes_is_kept_as_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    row = _run(monkeypatch, ValueError("boom"))

    assert (row["status"], row["error"]) == ("error", "ValueError: boom")


def test_a_runner_runs_only_what_its_log_lacks(tmp_path: Path) -> None:
    manifest = exp3.Manifest(_scenarios(("wolow", "A", 1.0, 0))[:2])
    runner = exp3.ScenarioRunner([exp3.METHODS[0]], RunLog(tmp_path))

    runner.run(manifest, workers=2)

    assert runner.pending(manifest) == []
    assert sorted(run["scenario_id"] for run in runner.log.read()) == ["wolow-A-100-0-x1", "wolow-A-100-0-x2"]


def _row(scenario: str, index: int, method: str, seed: int | None, status: str, value: float = 0.0) -> dict:
    row = {
        "scenario_id": scenario,
        "experiment": "wolow",
        "variant": "A",
        "target_occupancy": 1.0,
        "index": index,
        "method": method,
        "method_seed": seed,
        "status": status,
    }
    if status == "solved":
        row.update(dict.fromkeys(exp3.METRICS, value))
    return row


def test_de_runs_are_averaged_and_a_scenario_with_a_failed_run_gets_no_measure() -> None:
    comparison = exp3.Comparison(
        [
            _row("s1", 0, "de", 0, "solved", 10),
            _row("s1", 0, "de", 1, "solved", 20),
            _row("s1", 0, "de", 2, "solved", 60),
            _row("s2", 0, "de", 0, "solved", 10),
            _row("s2", 0, "de", 1, "infeasible"),
            _row("s2", 0, "de", 2, "solved", 10),
        ]
    )

    assert comparison.by_scenario["s1", "de"]["repair_cost"] == 30
    assert comparison.by_scenario["s2", "de"]["repair_cost"] is None
    assert comparison.by_scenario["s2", "de"]["solved"] == pytest.approx(2 / 3)


def test_schedules_average_their_scenarios_and_groups_their_schedules() -> None:
    comparison = exp3.Comparison(
        [_row(f"s{i}{x}", i, "greedy", None, "solved", 10 * i + x) for i in range(3) for x in (0, 2)]
    )

    (row,) = comparison.summary()

    assert [comparison.by_schedule["wolow", "A", 1.0, i, "greedy"]["repair_cost"] for i in range(3)] == [1, 11, 21]
    assert (row["repair_cost"], row["repair_cost_n"], row["solved"]) == (11, 3, 1)


def test_methods_are_compared_on_the_scenarios_both_solved_and_ranked() -> None:
    comparison = exp3.Comparison(
        [
            _row("s1", 0, "greedy", None, "solved", 50),
            _row("s1", 0, "cpsat", 0, "solved", 20),
            _row("s2", 0, "greedy", None, "solved", 50),
            _row("s2", 0, "cpsat", 0, "infeasible"),
        ]
    )

    (row,) = [
        r
        for r in comparison.paired()
        if (r["first"], r["second"], r["metric"], r["variant"]) == ("greedy", "cpsat", "repair_cost", "A")
    ]
    ranking = comparison.ranking(comparison.summary())

    assert (row["mean_second_minus_first"], row["scenarios"], row["second_lower"]) == (-30, 1, 1)
    assert {r["rank"] for r in ranking if r["method"] == "cpsat" and r["metric"] == "repair_cost"} == {1}


def test_the_elblag_pilot_takes_every_delay_once_for_every_variant_and_occupancy() -> None:
    scenarios = [
        scenario for variant in "ABCD" for target in exp3.OCCUPANCIES for scenario in _fake_scenarios(variant, target)
    ]

    chosen = exp3_elblag_pilot.representative(exp3.Manifest(scenarios)).scenarios

    assert len(chosen) == 12
    for variant in "ABCD":
        assert sorted(s.extra_days for s in chosen if s.variant == variant) == [1, 2, 3]
    for target in exp3.OCCUPANCIES:
        assert {s.extra_days for s in chosen if s.target_occupancy == target} == {1, 2, 3}


def test_the_elblag_pilot_takes_the_de_runs_of_experiment_3_on_its_own_scenarios_only() -> None:
    pilot = [
        {"scenario_id": s, "method": m, "method_seed": seed}
        for s in ("x", "y")
        for m, seed in (("greedy", None), ("cpsat", 0))
    ]
    exp3_runs = [
        {"scenario_id": s, "method": m, "method_seed": seed}
        for s in ("x", "y", "z")
        for m, seed in (("greedy", None), ("cpsat", 0), ("de", 0), ("de", 1), ("de", 2))
    ]

    runs = exp3_elblag_pilot.with_exp3_runs(pilot, exp3_runs)

    assert runs[: len(pilot)] == pilot
    assert sorted((r["scenario_id"], r["method_seed"]) for r in runs[len(pilot) :]) == [
        (s, seed) for s in ("x", "y") for seed in range(3)
    ]
    assert {r["method"] for r in runs[len(pilot) :]} == {"de"}


def _fake_scenarios(variant: str, target: float) -> list[exp3.Scenario]:
    return [
        exp3.Scenario(
            scenario_id=exp3.Scenario.make_id("elblag", variant, target, 0, extra),
            experiment="elblag",
            variant=variant,
            target_occupancy=target,
            index=0,
            seed=0,
            signal_day=5,
            delayed_tank="f1",
            delayed_batch="B001",
            extra_days=extra,
            lead_days=2,
            candidates=1,
            baseline_fingerprint="0" * 16,
        )
        for extra in exp3.EXTRA_DAYS
    ]
