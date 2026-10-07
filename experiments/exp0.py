"""Walkthrough: the configuration's exp0_events are observed, the brewery
is replanned once on the day of the last of them, and the result is
compared with the baseline and exported for the calendar viewer.
"""

from __future__ import annotations

from brewery_agentic_replanning.export import CalendarExporter
from brewery_agentic_replanning.metrics import compute_metrics
from brewery_agentic_replanning.simulation import BreweryDigitalTwin
from experiments.common import manual_twin
from experiments.config import from_cli


def print_summary(twin: BreweryDigitalTwin) -> None:
    metrics = compute_metrics(twin.brewery, twin.original_schedule, twin.schedule, twin.current_day)
    print(f"{len(twin.schedule.batches)} batches total, {metrics.changed_batches} changed since the baseline:\n")
    print(f"{'id':<5}{'recipe':<24}{'ferm.days':<11}{'orig start':<12}{'orig end':<10}{'new start':<11}{'new end':<8}")
    for batch_id in sorted(twin.schedule.batches):
        batch = twin.schedule.batches[batch_id]
        original = twin.original_schedule.assignments_of(batch_id)
        new = twin.schedule.assignments_of(batch_id)
        original_start = min((a.start_day for a in original), default=None)
        original_end = max((a.end_day for a in original), default=None)
        new_start = min((a.start_day for a in new), default=None)
        new_end = max((a.end_day for a in new), default=None)
        print(
            f"{batch.id:<5}{batch.recipe.name:<24}{batch.recipe.fermentation_days:<11}"
            f"{original_start!s:<12}{original_end!s:<10}{new_start!s:<11}{new_end!s:<8}"
        )
    print(f"\nschedule shift: {metrics.schedule_shift_days} days, idle fermenter days: {metrics.idle_fermenter_days}")
    print(
        f"shifted brews: {metrics.shifted_brews} (total {metrics.total_brew_shift_days} days), "
        f"shifted bottlings: {metrics.shifted_bottlings} (total {metrics.total_bottling_shift_days} days)"
    )


def main() -> None:
    cfg = from_cli(__doc__.splitlines()[0])
    recipes = cfg.build_recipes()
    events = cfg.hook("exp0_events")(recipes)
    for event in events:
        print(f"Day {event.day}: {event.description}")

    twin = manual_twin(cfg, recipes, events, max(event.day for event in events))
    twin.replan_now()
    print(f"\n=== Replanning on day {twin.current_day} ===")
    print_summary(twin)

    CalendarExporter().export(
        cfg.calendar_path,
        twin.brewery,
        twin.original_schedule,
        twin.schedule,
        twin.current_day,
        signals=twin.signal_log,
        replan_count=len(twin.replan_log),
        original_days_off=twin.original_days_off,
    )
    print(f"\nWrote {cfg.calendar_path}")


if __name__ == "__main__":
    main()
