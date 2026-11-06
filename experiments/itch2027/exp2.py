"""Experiment 2: a sample of fermentation delays in breweries of every size, in each of their variants.

Browar Wołów, Browar Bury and Browar Elbląg span 100 hl to 175,000 hl of
unitanks. The full sweep of Experiment 1 would give a schedule of Elbląg up
to 900 delays of about a second each, so here each schedule gets only
``SIGNALS_PER_SCHEDULE`` delays of the same sweep, drawn with its seed;
``sweep`` describes the run. Writes ``results/exp2/<brewery>/``.
"""

from __future__ import annotations

from collections.abc import Callable

from experiments.itch2027 import breweries
from experiments.itch2027.storage import RESULTS_DIR
from experiments.itch2027.sweep import DelaySweep, main

SIGNALS_PER_SCHEDULE = 20


def wolow() -> DelaySweep:
    return DelaySweep(name="wolow", variants=breweries.wolow(), signals_per_schedule=SIGNALS_PER_SCHEDULE)


def bury() -> DelaySweep:
    return DelaySweep(name="bury", variants=breweries.bury(), signals_per_schedule=SIGNALS_PER_SCHEDULE)


def elblag() -> DelaySweep:
    return DelaySweep(name="elblag", variants=breweries.elblag(), signals_per_schedule=SIGNALS_PER_SCHEDULE)


SWEEPS: dict[str, Callable[[], DelaySweep]] = {"wolow": wolow, "bury": bury, "elblag": elblag}


if __name__ == "__main__":
    main(SWEEPS, RESULTS_DIR / "exp2", __doc__.splitlines()[0])
