"""The brewery and recipes that `generate_schedule` fills; the experiments define their own."""

from __future__ import annotations

from brewery_schedule_digital_twin.domain import BottlingLine, Brewery, Brewhouse, Recipe, Vessel


def build_brewery() -> Brewery:
    return Brewery(
        name="itch2027",
        vessels=[Vessel.unitank("f", index, 1_000.0) for index in range(1, 5)],
        brewhouses=[Brewhouse("brewhouse", kettle_yield=1_000, max_brews_per_day=1)],
        bottling_lines=[BottlingLine("line", run_volume=1_000, max_runs_per_day=1)],
    )


def build_recipes() -> list[Recipe]:
    return [
        Recipe(name="Hefeweizen", fermentation_days=8),
        Recipe(name="Witbier", fermentation_days=10),
        Recipe(name="New England IPA", fermentation_days=12),
        Recipe(name="Session Pale Ale", fermentation_days=15),
        Recipe(name="Dry Stout", fermentation_days=18),
    ]
