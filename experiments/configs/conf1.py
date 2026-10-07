from __future__ import annotations

from brewery_agentic_replanning.domain import Batch, Brewery, Fermenter, Recipe
from brewery_agentic_replanning.signals import (
    DayOffSignal,
    FermentationDelaySignal,
    FermenterMaintenanceSignal,
    IngredientShortageSignal,
    NewBatchRequirementSignal,
    Signal,
)

INITIAL_ASSIGNMENTS = [
    ("001", "new_england_ipa", "f4", -8, 5),
    ("002", "session_pale_ale", "f5", -6, 5),
    ("002", "session_pale_ale", "f6", -6, 5),
    ("003", "barleywine", "f7", -5, 5),
    ("004", "session_pale_ale", "f1", 0, 10, 16),
    ("005", "hazy_ipa", "f2", -2, 10),
    ("006", "witbier", "f3", 0, 10),
    ("007", "new_england_ipa", "f4", 4, 5),
    ("008", "amarillo_single_hop", "f5", 9, 5),
    ("008", "amarillo_single_hop", "f6", 9, 5),
    ("009", "hefeweizen", "f2", 11, 10),
    ("010", "amarillo_single_hop", "f3", 10, 10),
    ("011", "hazy_ipa", "f1", 16, 10, 14),
    ("012", "hefeweizen", "f4", 16, 5),
    ("013", "hazy_ipa", "f2", 19, 10),
    ("014", "amarillo_single_hop", "f5", 23, 5),
    ("014", "amarillo_single_hop", "f6", 23, 5),
    ("015", "barleywine", "f4", 24, 5),
    ("016", "saison", "f7", 27, 5, 17),
    ("017", "witbier", "f1", 30, 10),
    ("018", "dry_stout", "f3", 24, 10),
    ("019", "hefeweizen", "f2", 32, 10),
]

DAYS_OFF = {7, 14, 21, 28, 35, 42, 49, 56, 63}


def build_brewery() -> Brewery:
    fermenters = [Fermenter("f", i, 10.0) for i in range(1, 4)] + [Fermenter("f", i, 5.0) for i in range(4, 8)]
    return Brewery(
        name="Digital Twin Brewery",
        fermenters=fermenters,
        kettle_yield_hl=10.0,
        max_brews_per_day=5,
        max_bottling_runs_per_day=5,
        days_off=set(DAYS_OFF),
    )


def build_recipes() -> dict[str, Recipe]:
    return {
        "hefeweizen": Recipe(name="Hefeweizen", fermentation_days=8),
        "session_pale_ale": Recipe(name="Session Pale Ale", fermentation_days=15),
        "hazy_ipa": Recipe(name="Hazy IPA", fermentation_days=13),
        "amarillo_single_hop": Recipe(name="Amarillo Single Hop", fermentation_days=14),
        "new_england_ipa": Recipe(name="New England IPA", fermentation_days=12),
        "barleywine": Recipe(name="Barleywine", fermentation_days=32),
        "witbier": Recipe(name="witbier", fermentation_days=10),
        "saison": Recipe(name="saison", fermentation_days=16),
        "dry_stout": Recipe(name="dry stout", fermentation_days=18),
    }


def _new_order(recipes: dict[str, Recipe], day: int, planned_brew_day: int, target_bottling_day: int) -> Signal:
    batch = Batch(
        "SPEC",
        "New England IPA - rush order",
        recipes["new_england_ipa"],
        planned_brew_day=planned_brew_day,
        target_bottling_day=target_bottling_day,
    )
    return NewBatchRequirementSignal(day=day, batch=batch)


def exp0_events(recipes: dict[str, Recipe]) -> list[Signal]:
    return [FermentationDelaySignal(day=4, batch_id="005", fermenter_id="f2", extra_days=3)]


def exp1_scenarios(recipes: dict[str, Recipe]) -> list[Signal]:
    return [
        FermentationDelaySignal(day=4, batch_id="006", fermenter_id="f3", extra_days=3),
        FermentationDelaySignal(day=4, batch_id="006", fermenter_id="f3", extra_days=2),
        FermentationDelaySignal(day=4, batch_id="006", fermenter_id="f3", extra_days=1),
        IngredientShortageSignal(day=4, batch_id="010", available_from_day=11),
        IngredientShortageSignal(day=4, batch_id="010", available_from_day=14),
        IngredientShortageSignal(day=4, batch_id="010", available_from_day=20),
        DayOffSignal(day=4, off_day=5),
        DayOffSignal(day=4, off_day=8),
        DayOffSignal(day=4, off_day=15),
        FermenterMaintenanceSignal(day=4, fermenter_id="f3", start_day=10, duration_days=1),
        FermenterMaintenanceSignal(day=4, fermenter_id="f3", start_day=10, duration_days=10),
        FermenterMaintenanceSignal(day=4, fermenter_id="f3", start_day=10, duration_days=30),
        _new_order(recipes, day=4, planned_brew_day=10, target_bottling_day=22),
        _new_order(recipes, day=4, planned_brew_day=10, target_bottling_day=30),
        _new_order(recipes, day=4, planned_brew_day=10, target_bottling_day=48),
    ]


def _cf1_events(recipes: dict[str, Recipe]) -> list[Signal]:
    return [
        DayOffSignal(day=4, off_day=5),
        IngredientShortageSignal(day=6, batch_id="010", available_from_day=14),
    ]


def _cf2_events(recipes: dict[str, Recipe]) -> list[Signal]:
    return [
        _new_order(recipes, day=4, planned_brew_day=15, target_bottling_day=30),
        IngredientShortageSignal(day=6, batch_id="010", available_from_day=14),
    ]


def _cf3_events(recipes: dict[str, Recipe]) -> list[Signal]:
    return [
        FermenterMaintenanceSignal(day=4, fermenter_id="f3", start_day=10, duration_days=10),
        _new_order(recipes, day=5, planned_brew_day=15, target_bottling_day=30),
        IngredientShortageSignal(day=6, batch_id="010", available_from_day=14),
    ]


def _cf4_events(recipes: dict[str, Recipe]) -> list[Signal]:
    return [
        _new_order(recipes, day=8, planned_brew_day=15, target_bottling_day=30),
        FermentationDelaySignal(day=9, batch_id="006", fermenter_id="f3", extra_days=3),
    ]


CF_SCENARIOS = {
    "CF1": _cf1_events,
    "CF2": _cf2_events,
    "CF3": _cf3_events,
    "CF4": _cf4_events,
}

CF_TARGETS: dict[str, tuple[str, int] | None] = {
    "CF1": None,
    "CF2": ("SPEC", 30),
    "CF3": ("SPEC", 30),
    "CF4": ("SPEC", 30),
}
