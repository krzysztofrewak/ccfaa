from __future__ import annotations

from brewery_agentic_replanning.domain import Brewery, Fermenter
from experiments.configs import conf1
from experiments.configs.conf1 import CF_SCENARIOS, CF_TARGETS, build_recipes

REPLACED_FERMENTER = "f1"
REPLACEMENT_FERMENTERS = ("f8", "f9")


def _relocate(row: tuple) -> list[tuple]:
    batch_id, recipe_key, fermenter_id, brew_day, volume_hl, *rest = row
    if fermenter_id != REPLACED_FERMENTER:
        return [row]
    share = volume_hl // len(REPLACEMENT_FERMENTERS)
    return [(batch_id, recipe_key, tank, brew_day, share, *rest) for tank in REPLACEMENT_FERMENTERS]


INITIAL_ASSIGNMENTS = [relocated for row in conf1.INITIAL_ASSIGNMENTS for relocated in _relocate(row)]

CF_ADAPTATIONS: dict[str, str] = {}


def build_brewery() -> Brewery:
    fermenters = [Fermenter("f", i, 10.0) for i in range(2, 4)] + [Fermenter("f", i, 5.0) for i in range(4, 10)]
    return Brewery(
        name="Digital Twin Brewery",
        fermenters=fermenters,
        kettle_yield_hl=10.0,
        max_brews_per_day=5,
        max_bottling_runs_per_day=5,
        days_off=set(conf1.DAYS_OFF),
    )


__all__ = [
    "CF_ADAPTATIONS",
    "CF_SCENARIOS",
    "CF_TARGETS",
    "INITIAL_ASSIGNMENTS",
    "build_brewery",
    "build_recipes",
]
