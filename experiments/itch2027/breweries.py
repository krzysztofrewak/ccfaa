"""The breweries the itch2027 experiments compare, each in the variants of its unitanks, and their recipes."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from brewery_schedule_digital_twin.domain import BottlingLine, Brewery, Brewhouse, Recipe, Vessel

RECIPES = (
    Recipe(name="Hefeweizen", fermentation_days=8),
    Recipe(name="Witbier", fermentation_days=10),
    Recipe(name="New England IPA", fermentation_days=12),
    Recipe(name="Session Pale Ale", fermentation_days=15),
    Recipe(name="Dry Stout", fermentation_days=18),
)


def unitank_variants(
    name: str,
    fleets: Mapping[str, Sequence[float]],
    brewhouses: Sequence[Brewhouse],
    bottling_lines: Sequence[BottlingLine],
) -> dict[str, Brewery]:
    """A brewery called ``name`` <variant> for each fleet of unitank capacities, with the same brewhouses and lines."""
    return {
        variant: Brewery(
            name=f"{name} {variant}",
            vessels=[Vessel.unitank("f", index, capacity) for index, capacity in enumerate(tanks, start=1)],
            brewhouses=list(brewhouses),
            bottling_lines=list(bottling_lines),
        )
        for variant, tanks in fleets.items()
    }


def preview() -> Brewery:
    """The brewery ``generate_schedule`` fills: four 10 hl unitanks, a 10 hl brewhouse and one run a day."""
    return Brewery(
        name="itch2027",
        vessels=[Vessel.unitank("f", index, 1_000.0) for index in range(1, 5)],
        brewhouses=[Brewhouse("brewhouse", kettle_yield=1_000, max_brews_per_day=1)],
        bottling_lines=[BottlingLine("line", run_volume=1_000, max_runs_per_day=1)],
    )


def wolow() -> dict[str, Brewery]:
    """Browar Wołów with 100 hl of unitanks in three fleets.

    One 6 hl brewhouse of 4 brews a day and manual bottling of up to 20 hl
    a day, so the largest tank is bottled in one day.
    """
    fleets = {
        "A": [1_000.0] * 4 + [2_000.0] * 3,
        "B": [1_000.0] * 10,
        "C": [2_000.0] * 5,
    }
    brewhouse = Brewhouse("brewhouse", kettle_yield=600, max_brews_per_day=4)
    line = BottlingLine.by_daily_volume("manual", volume_per_day=2_000)
    return unitank_variants("Browar Wołów", fleets, [brewhouse], [line])


def bury() -> dict[str, Brewery]:
    """Browar Bury with 640 hl of unitanks in three fleets: its own and two of one size.

    One 26 hl brewhouse of 4 brews a day and one bottling line of up to
    160 hl a day, four of the largest tanks, so that the line is no
    bottleneck: no tank of a generated schedule waits for it past its
    fermentation.
    """
    fleets = {
        "A": [4_000.0] * 15 + [2_000.0] * 2,
        "B": [4_000.0] * 16,
        "C": [2_000.0] * 32,
    }
    brewhouse = Brewhouse("brewhouse", kettle_yield=2_600, max_brews_per_day=4)
    line = BottlingLine.by_daily_volume("line", volume_per_day=16_000)
    return unitank_variants("Browar Bury", fleets, [brewhouse], [line])


def heterogeneity() -> dict[str, Brewery]:
    """A brewery of 10 unitanks and 200 hl in three fleets of rising heterogeneity.

    One 5 hl brewhouse of 4 brews a day, since every tank holds whole brews
    of it, and bottling of up to 35 hl a day, so the largest tank is bottled
    in one day.
    """
    fleets = {
        "A": [2_000.0] * 10,
        "B": [1_000.0] * 5 + [3_000.0] * 5,
        "C": [500.0] * 5 + [3_500.0] * 5,
    }
    brewhouse = Brewhouse("brewhouse", kettle_yield=500, max_brews_per_day=4)
    line = BottlingLine.by_daily_volume("line", volume_per_day=3_500)
    return unitank_variants("Browar 200 hl", fleets, [brewhouse], [line])


def elblag() -> dict[str, Brewery]:
    """Browar Elbląg with about 175,000 hl of unitanks in four fleets: its own and three of one size.

    Two 700 hl brewhouses of 14 brews a day each, two bottling lines and a
    canning line of up to 15,000 hl a day each, three of the largest tanks,
    so that the lines are no bottleneck: no tank of a generated schedule
    waits for them past its fermentation. A line counts in runs of 20 hl,
    which every tank holds a whole number of. The fleets hold 174,440,
    174,800, 175,200 and 175,260 hl.
    """
    fleets = {
        "A": [460_000.0] * 32 + [240_000.0] * 5 + [254_000.0] * 6,
        "B": [460_000.0] * 38,
        "C": [240_000.0] * 73,
        "D": [254_000.0] * 69,
    }
    brewhouses = [Brewhouse(f"brewhouse{i}", kettle_yield=70_000, max_brews_per_day=14) for i in (1, 2)]
    lines = [
        BottlingLine.by_daily_volume(name, volume_per_day=1_500_000, step=2_000)
        for name in ("bottling1", "bottling2", "canning")
    ]
    return unitank_variants("Browar Elbląg", fleets, brewhouses, lines)
