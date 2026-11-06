from pathlib import Path

import pytest

from experiments.itch2027 import breweries, exp4
from experiments.itch2027.measures import replanning_problems
from experiments.itch2027.methods import RepairMethod
from experiments.itch2027.storage import RunLog


def test_the_orders_depend_only_on_the_seed_and_on_no_tank() -> None:
    orders = exp4.OrderSet(7).orders

    assert exp4.OrderSet(7).orders == orders and exp4.OrderSet(8).orders != orders
    assert {b.volume for b in orders} <= set(exp4.BATCH_VOLUMES)
    assert all(exp4.FIRST_ORDER_DAY <= b.planned_brew_day < exp4.HORIZON_DAYS for b in orders)
    assert len(exp4.OrderSet(7, orders_per_day=0.4).orders) < len(orders)


@pytest.mark.parametrize("fleet", exp4.FLEETS)
def test_a_fleet_plans_the_whole_production_without_problems(fleet: str) -> None:
    brewery = breweries.heterogeneity()[fleet]
    orders = exp4.OrderSet(0)

    baseline = orders.baselines[fleet]

    assert sorted(baseline.batches) == [b.id for b in orders.orders]
    assert replanning_problems(brewery, baseline) == []
    for batch in orders.orders:
        assert sum(a.volume for a in baseline.assignments_of(batch.id)) == batch.volume


def test_every_fleet_gets_every_delay_of_every_shared_batch_with_the_same_lead() -> None:
    orders = exp4.OrderSet(3)
    delays = orders.delays()

    assert len(delays) == len(orders.shared_batches()) * len(exp4.FLEETS) * len(exp4.EXTRA_DAYS)
    for delay in delays:
        (signal, *others) = delay.signals()
        assert exp4._batch_end(delay.baseline, delay.batch_id) - signal.day == exp4.LEAD_DAYS
        assert signal.day in exp4.SIGNAL_DAYS
        assert len(others) + 1 == len(delay.baseline.assignments_of(delay.batch_id))
        assert all(a.effective_bottling_day >= signal.day for a in delay.baseline.assignments_of(delay.batch_id))


def _split_delay(extra_days: int = 2) -> exp4.Delay:
    return next(
        d
        for d in exp4.OrderSet(0).delays()
        if d.fleet == "A" and d.extra_days == extra_days and len(d.baseline.assignments_of(d.batch_id)) > 1
    )


def test_a_greedy_repair_is_valid_and_splits_d_pack_into_the_delayed_batch_and_the_others() -> None:
    delay = _split_delay(3)

    row = delay.run(exp4.METHODS["greedy"][0])

    assert row["status"] == "solved" and row["delayed_vessels"] > 1
    assert row["d_pack_delayed"] >= 3
    assert row["d_pack"] == row["d_pack_delayed"] + row["d_pack_others"]
    assert row["added_split_batches"] == row["repaired_split_batches"] - row["baseline_split_batches"]
    assert row["control_d_pack"] == 0


def test_cpsat_repairs_at_no_more_than_the_greedy_cost_and_bounds_its_own() -> None:
    delay = _split_delay()

    greedy = delay.run(exp4.METHODS["greedy"][0])
    cpsat = delay.run(RepairMethod("cpsat", 0, {"time_limit_seconds": 5.0, "max_placements": 200}))

    assert cpsat["status"] == "solved"
    assert cpsat["repair_cost"] == cpsat["cpsat_objective"] <= greedy["repair_cost"]
    assert cpsat["cpsat_best_bound"] <= cpsat["repair_cost"]


def test_differential_evolution_repairs_at_no_more_than_the_greedy_cost() -> None:
    delay = _split_delay()

    greedy = delay.run(exp4.METHODS["greedy"][0])
    de = delay.run(RepairMethod("de", 1, {"population_size": 6, "generations": 5}))

    assert de["status"] == "solved" and de["de_greedy_cost"] == greedy["repair_cost"]
    assert de["repair_cost"] == de["de_cost"] <= greedy["repair_cost"]


def _run(seed: int, fleet: str, extra_days: int, value: float, status: str = "solved") -> dict:
    return {
        "scenario_id": f"{seed}-{fleet}-{extra_days}",
        "method": "greedy",
        "method_seed": None,
        "seed": seed,
        "fleet": fleet,
        "extra_days": extra_days,
        "status": status,
    } | dict.fromkeys(exp4.METRICS, value)


def test_fleets_are_compared_set_by_set_and_a_set_with_a_failed_repair_is_dropped() -> None:
    values = {0: (10, 20, 8, 16, 4, 8), 1: (20, 20, 20, 20, 14, 14), 2: (100, 100, 0, 0, 0, 0)}
    runs = [
        _run(seed, fleet, extra, value, "infeasible" if (seed, fleet, extra) == (2, "C", 1) else "solved")
        for seed, row in values.items()
        for (fleet, extra), value in zip([(f, x) for f in "ABC" for x in (1, 2)], row, strict=True)
    ]

    comparison = exp4.FleetComparison(runs)
    (row,) = [
        r
        for r in comparison.paired()
        if (r["metric"], r["first"], r["second"], r["extra_days"]) == ("d_pack", "A", "C", exp4.ALL_DELAYS)
    ]

    assert {entry["seed"] for entry in comparison.sets} == {0, 1}
    assert (row["mean_second_minus_first"], row["sets"], row["second_lower"]) == (-7.5, 2, 2)


def test_a_method_resumes_without_repeating_finished_runs(tmp_path: Path) -> None:
    design = exp4.Design(range(1))
    log = RunLog(tmp_path)

    first = exp4.run_method("greedy", design, log, workers=2)
    again = exp4.run_method("greedy", design, log, workers=2)

    assert len(first) == len(again) == len(design.delays())
    assert all(run["method"] == "greedy" and run["status"] == "solved" for run in again)


def test_a_robustness_check_goes_to_a_directory_of_its_own() -> None:
    assert exp4.Design(range(30)).results_root(pilot=False).name == "exp4"
    assert exp4.Design(range(2)).results_root(pilot=True).name == "exp4_pilot"
    assert exp4.Design(range(1000, 1200), 0.4).results_root(pilot=False).name == "exp4_orders0.4_sets1000-1199"
