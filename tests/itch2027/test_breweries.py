import pytest

from experiments.itch2027 import breweries
from experiments.itch2027.sweep import DelaySweep

WOLOW = breweries.wolow()
BURY = breweries.bury()
ELBLAG = breweries.elblag()
HETEROGENEITY = breweries.heterogeneity()


@pytest.mark.parametrize("variant", WOLOW)
def test_every_wolow_variant_holds_100_hl_and_bottles_its_largest_tank_in_a_day(variant: str) -> None:
    brewery = WOLOW[variant]
    line = brewery.bottling_lines[0]

    assert sum(vessel.capacity for vessel in brewery.vessels) == 10_000.0
    assert line.runs_for(max(vessel.capacity for vessel in brewery.vessels)) <= line.max_runs_per_day


@pytest.mark.parametrize("variant", BURY)
def test_every_bury_variant_holds_640_hl_and_bottles_four_of_its_largest_tanks_in_a_day(variant: str) -> None:
    brewery = BURY[variant]
    line = brewery.bottling_lines[0]

    assert sum(vessel.capacity for vessel in brewery.vessels) == 64_000.0
    assert 4 * line.runs_for(max(vessel.capacity for vessel in brewery.vessels)) <= line.max_runs_per_day


@pytest.mark.parametrize("variant", BURY)
def test_no_tank_of_a_full_bury_schedule_waits_for_bottling(variant: str) -> None:
    brewery, baseline = DelaySweep("bury", BURY).generate(variant, 1.0, seed=0)

    assert baseline.validate(brewery) == []
    assert all(a.end_day == a.natural_end_day for a in baseline.assignments)


@pytest.mark.parametrize("variant", ELBLAG)
def test_every_elblag_variant_holds_within_half_a_percent_of_its_own_and_bottles_three_largest_tanks_a_line(
    variant: str,
) -> None:
    brewery = ELBLAG[variant]
    own = sum(vessel.capacity for vessel in ELBLAG["A"].vessels)
    largest = max(vessel.capacity for vessel in brewery.vessels)

    assert sum(vessel.capacity for vessel in brewery.vessels) == pytest.approx(own, rel=0.005)
    assert len(brewery.brewhouses) == 2 and len(brewery.bottling_lines) == 3
    for line in brewery.bottling_lines:
        assert all(vessel.capacity % line.run_volume == 0 for vessel in brewery.vessels)
        assert 3 * line.runs_for(largest) <= line.max_runs_per_day


def test_no_tank_of_a_full_elblag_schedule_waits_for_bottling() -> None:
    brewery, baseline = DelaySweep("elblag", ELBLAG).generate("A", 1.0, seed=0)

    assert baseline.validate(brewery) == []
    assert all(a.end_day == a.natural_end_day for a in baseline.assignments)


def test_every_heterogeneity_fleet_has_ten_tanks_of_200_hl_and_the_same_brewhouse_and_line() -> None:
    assert sorted(HETEROGENEITY) == ["A", "B", "C"]
    assert {len(b.vessels) for b in HETEROGENEITY.values()} == {10}
    assert {sum(v.capacity for v in b.vessels) for b in HETEROGENEITY.values()} == {20_000.0}
    assert HETEROGENEITY["A"].brewhouses == HETEROGENEITY["B"].brewhouses == HETEROGENEITY["C"].brewhouses
    assert HETEROGENEITY["A"].bottling_lines == HETEROGENEITY["B"].bottling_lines == HETEROGENEITY["C"].bottling_lines
    assert sorted({v.capacity for b in HETEROGENEITY.values() for v in b.vessels}) == [500, 1_000, 2_000, 3_000, 3_500]


def test_the_preview_brewery_has_four_10_hl_tanks() -> None:
    brewery = breweries.preview()

    assert [vessel.capacity for vessel in brewery.vessels] == [1_000.0] * 4
