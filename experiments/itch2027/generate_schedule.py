"""Fills the preview brewery of ``breweries`` with a random schedule and exports it for the calendar viewer.

Without ``--seed`` every run draws a new seed and prints it, so a schedule
worth keeping can be generated again. The schedule is written to
``public/data/itch2027/calendar.json``; serve ``public/`` and open
``index.html?config=itch2027`` to see it.
"""

from __future__ import annotations

import argparse
import random
from pathlib import Path

from brewery_schedule_digital_twin.export import CalendarExporter
from brewery_schedule_digital_twin.generation import ScheduleGenerator, occupancy
from experiments.itch2027.breweries import RECIPES, preview

PROJECT_DIR = Path(__file__).resolve().parents[2]
CALENDAR_PATH = PROJECT_DIR / "public" / "data" / "itch2027" / "calendar.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fill", type=float, default=0.7, help="target occupancy of the tanks, 0-1 (default 0.7)")
    parser.add_argument("--horizon", type=int, default=60, help="days to fill (default 60)")
    parser.add_argument("--seed", type=int, default=None, help="random seed (default: a new one)")
    args = parser.parse_args()
    seed = args.seed if args.seed is not None else random.randrange(1_000_000)

    brewery = preview()
    schedule = ScheduleGenerator().fill(brewery, RECIPES, args.fill, args.horizon, random.Random(seed))

    problems = schedule.validate(brewery)
    print(
        f"seed {seed}: {len(schedule.batches)} batches, occupancy {occupancy(brewery, schedule, args.horizon):.1%} "
        f"(target {args.fill:.0%}) over {args.horizon} days"
    )
    for a in sorted(schedule.assignments, key=lambda a: (a.start_day, a.fermenter.id)):
        recipe = schedule.batches[a.batch_id].recipe
        print(
            f"  {a.batch_id}  {recipe.name:<18} {a.fermenter.id}  brew {a.start_day:>3}  "
            f"bottling {a.effective_bottling_day:>3}  free from {a.end_day:>3}"
        )
    for problem in problems:
        print(f"PROBLEM: {problem}")

    CalendarExporter().export(CALENDAR_PATH, brewery, schedule, schedule, current_day=0)
    print(f"Wrote {CALENDAR_PATH}; view it with: python -m http.server -d public, then open index.html?config=itch2027")


if __name__ == "__main__":
    main()
