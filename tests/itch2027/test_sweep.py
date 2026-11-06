from dataclasses import replace

import pytest

from experiments.itch2027 import breweries, exp1, exp2
from experiments.itch2027.replanning import Repair
from experiments.itch2027.sweep import DelaySweep

WOLOW = DelaySweep("wolow", breweries.wolow())
BURY = DelaySweep("bury", breweries.bury())


@pytest.mark.parametrize(
    ("sweep", "variant"),
    [(sweep, variant) for sweep in (WOLOW, BURY) for variant in sweep.variants],
    ids=lambda value: getattr(value, "name", value),
)
@pytest.mark.parametrize("target", [1.0, 0.9])
def test_a_delay_of_the_sweep_is_replanned_into_a_valid_schedule(
    sweep: DelaySweep, variant: str, target: float
) -> None:
    brewery, baseline = sweep.generate(variant, target, seed=0)
    delay = sweep.delays(baseline)[0]
    assert baseline.validate(brewery) == []

    repair = Repair.run(brewery, baseline, delay.day, [delay])
    change = repair.change(sweep.horizon_days)

    assert repair.feasible and repair.problems() == []
    assert change.delayed_end_day is not None
    assert change.delayed_end_day > baseline.assignment_for(delay.batch_id, delay.fermenter_id).end_day


def test_the_sweep_delays_every_tank_ending_fermentation_one_to_three_days_after_the_signal() -> None:
    brewery, baseline = WOLOW.generate("A", 1.0, seed=0)
    delays = WOLOW.delays(baseline)

    assert {delay.fermenter_id for delay in delays} == {vessel.id for vessel in brewery.vessels}
    for delay in delays:
        assignment = baseline.assignment_for(delay.batch_id, delay.fermenter_id)
        assert delay.day in WOLOW.signal_days
        assert assignment.natural_end_day - delay.day in WOLOW.lead_days
        assert delay.extra_days in WOLOW.extra_days
    assert len(delays) == len(set(delays))


def test_a_repair_leaves_the_baseline_alone_so_it_repeats_on_the_next_delay() -> None:
    brewery, baseline = WOLOW.generate("C", 0.95, seed=1)
    delay = WOLOW.delays(baseline)[0]

    first = Repair.run(brewery, baseline, delay.day, [delay])
    again = Repair.run(brewery, baseline, delay.day, [delay])

    assert first.change(60) == again.change(60) and first.cost() == again.cost()


def test_without_a_sample_a_schedule_gets_every_delay_of_the_sweep() -> None:
    _, baseline = BURY.generate("A", 1.0, seed=0)

    assert BURY.delays_of(baseline, seed=0) == BURY.delays(baseline)


def test_a_sample_draws_that_many_delays_of_the_sweep_in_its_order_the_same_for_the_same_seed() -> None:
    sampled = replace(BURY, signals_per_schedule=20)
    _, baseline = sampled.generate("A", 1.0, seed=0)
    sweep = sampled.delays(baseline)

    delays = sampled.delays_of(baseline, seed=0)

    assert len(delays) == 20 and len(set(delays)) == 20
    assert delays == [delay for delay in sweep if delay in delays]
    assert delays == sampled.delays_of(baseline, seed=0)
    assert delays != sampled.delays_of(baseline, seed=1)
    assert len({delay.fermenter_id for delay in delays}) < len(sampled.variants["A"].vessels)


def test_a_schedule_is_replanned_undisturbed_on_every_day_it_gets_a_delay_on() -> None:
    sampled = replace(WOLOW, occupancies=(1.0,), signals_per_schedule=5)

    rows, controls = sampled.run_schedule("A", 1.0, 0, 0)

    assert len(rows) == 5
    assert [control["signal_day"] for control in controls] == sorted({row["signal_day"] for row in rows})
    assert all(row["feasible"] and row["signal"] for row in rows)
    assert not any(control["signal"] for control in controls)
    assert {f"control_{key}" for key in ("feasible", "abs_bottling_shift_days")} <= set(rows[0])


def test_exp1_sweeps_every_delay_of_wolow_and_exp2_a_sample_of_every_brewery() -> None:
    wolow = exp1.SWEEPS["wolow"]()
    sampled = {name: build() for name, build in exp2.SWEEPS.items()}

    assert list(wolow.variants) == ["A", "B", "C"] and wolow.signals_per_schedule is None
    assert list(sampled) == ["wolow", "bury", "elblag"]
    assert {sweep.signals_per_schedule for sweep in sampled.values()} == {exp2.SIGNALS_PER_SCHEDULE}


def test_a_sampled_delay_replans_a_valid_elblag_schedule() -> None:
    sweep = exp2.SWEEPS["elblag"]()
    brewery, baseline = sweep.generate("A", 1.0, seed=0)
    delays = sweep.delays_of(baseline, seed=0)

    repair = Repair.run(brewery, baseline, delays[0].day, delays[:1])

    assert len(delays) == exp2.SIGNALS_PER_SCHEDULE
    assert repair.feasible and repair.problems() == []
