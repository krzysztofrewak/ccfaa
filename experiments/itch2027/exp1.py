"""Experiment 1: every fermentation delay of the sweep, over every tank of Browar Wołów, in each of its variants.

The small brewery is cheap to replan, so each of its schedules gets every
delay of the sweep and the variants are compared over many alternative
scenarios; ``sweep`` describes the run. Writes ``results/exp1/wolow/``.
"""

from __future__ import annotations

from collections.abc import Callable

from experiments.itch2027 import breweries
from experiments.itch2027.storage import RESULTS_DIR
from experiments.itch2027.sweep import DelaySweep, main


def wolow() -> DelaySweep:
    return DelaySweep(name="wolow", variants=breweries.wolow())


SWEEPS: dict[str, Callable[[], DelaySweep]] = {"wolow": wolow}


if __name__ == "__main__":
    main(SWEEPS, RESULTS_DIR / "exp1", __doc__.splitlines()[0])
