from __future__ import annotations

import math

from brewery_schedule_digital_twin.domain import BottlingLine, Brewery, Brewhouse, Vessel
from experiments.ccfaa2026.configs import conf1
from experiments.ccfaa2026.configs.conf1 import CF_SCENARIOS, CF_TARGETS, build_recipes

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
    vessels = [Vessel.unitank("f", i, 10.0) for i in range(2, 4)] + [Vessel.unitank("f", i, 5.0) for i in range(4, 10)]
    return Brewery(
        name="Digital Twin Brewery",
        vessels=vessels,
        brewhouses=[Brewhouse("brewhouse", yield_l=1_000, max_brews_per_day=5)],
        # A line that bottles any tank in one run, so bottling is limited by runs a day only.
        bottling_lines=[BottlingLine("line", run_volume_l=math.inf, max_runs_per_day=5)],
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
