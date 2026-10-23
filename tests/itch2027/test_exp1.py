import pytest

from experiments.itch2027.exp1 import generate, replan_after, sweep_signals, wolow

WOLOW = wolow()


@pytest.mark.parametrize("variant", WOLOW.variants)
def test_every_variant_holds_100_hl_and_bottles_its_largest_tank_in_a_day(variant: str) -> None:
    brewery = WOLOW.variants[variant]
    line = brewery.bottling_lines[0]

    assert sum(vessel.capacity for vessel in brewery.vessels) == 10_000.0
    assert line.runs_for(max(vessel.capacity for vessel in brewery.vessels)) <= line.max_runs_per_day


@pytest.mark.parametrize("variant", WOLOW.variants)
@pytest.mark.parametrize("target", [1.0, 0.9])
def test_a_sweep_signal_replans_a_valid_schedule_after_the_delay(variant: str, target: float) -> None:
    brewery, baseline = generate(WOLOW, variant, target, seed=0)
    signal = sweep_signals(WOLOW, baseline)[0]
    assert baseline.validate(brewery) == []

    row, _ = replan_after(brewery, baseline, signal, 60, {})

    assert row["feasible"] and row["replanned_problems"] == 0
    assert row["delayed_end_day"] > row["delayed_original_end_day"]


def test_the_sweep_delays_every_tank_ending_fermentation_one_to_three_days_after_the_signal() -> None:
    brewery, baseline = generate(WOLOW, "A", 1.0, seed=0)
    signals = sweep_signals(WOLOW, baseline)

    assert {signal.fermenter_id for signal in signals} == {vessel.id for vessel in brewery.vessels}
    for signal in signals:
        assignment = baseline.assignment_for(signal.batch_id, signal.fermenter_id)
        assert signal.day in WOLOW.signal_days
        assert assignment.natural_end_day - signal.day in WOLOW.lead_days
        assert signal.extra_days in WOLOW.extra_days
    assert len(signals) == len(set(signals))


def test_a_sweep_signal_leaves_the_baseline_for_the_next_one() -> None:
    brewery, baseline = generate(WOLOW, "C", 0.95, seed=1)
    signal = sweep_signals(WOLOW, baseline)[0]
    row: dict = {}

    first, _ = replan_after(brewery, baseline.clone(), signal, 60, row)
    again, _ = replan_after(brewery, baseline.clone(), signal, 60, row)

    assert first["feasible"] and first["replanned_problems"] == 0
    assert {k: v for k, v in first.items() if k != "scheduler_seconds"} == {
        k: v for k, v in again.items() if k != "scheduler_seconds"
    }
