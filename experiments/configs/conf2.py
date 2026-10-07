"""conf2, reduced production load: the conf1 brewery, recipes and
scenarios with four batches removed from the baseline.

Of the batches brewed after day 0 that no scenario signal names, taken in
(brew day, batch id) order, every third one is removed: 009, 013, 018 and
019. This lowers volumetric utilisation over days 0-41 from 95.7% to
73.3%.
"""

from __future__ import annotations

from experiments.configs import conf1
from experiments.configs.conf1 import CF_SCENARIOS, CF_TARGETS, build_brewery, build_recipes

REMOVED_BATCHES = {"009", "013", "018", "019"}

INITIAL_ASSIGNMENTS = [row for row in conf1.INITIAL_ASSIGNMENTS if row[0] not in REMOVED_BATCHES]

CF_ADAPTATIONS: dict[str, str] = {}

__all__ = [
    "CF_ADAPTATIONS",
    "CF_SCENARIOS",
    "CF_TARGETS",
    "INITIAL_ASSIGNMENTS",
    "build_brewery",
    "build_recipes",
]
