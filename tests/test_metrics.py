from brewery_agentic_replanning.domain import Batch, Brewery, FermentationAssignment, Fermenter, Recipe, Schedule
from brewery_agentic_replanning.metrics import compute_metrics


def _assignment(batch_id: str, fermenter: Fermenter, start_day: int, fermentation_days: int) -> FermentationAssignment:
    return FermentationAssignment(
        batch_id=batch_id,
        fermenter=fermenter,
        volume_hl=10.0,
        start_day=start_day,
        base_fermentation_days=fermentation_days,
    )


def test_compute_metrics_counts_changed_batches_and_shift(recipe: Recipe) -> None:
    brewery = Brewery(
        name="Metrics Brewery",
        fermenters=[Fermenter("f", 1, 10.0), Fermenter("f", 2, 10.0)],
        kettle_yield_hl=10.0,
        max_brews_per_day=1,
        max_bottling_runs_per_day=1,
    )
    f1, f2 = brewery.fermenters

    original = Schedule()
    original.add_batch(Batch("001", "B1", recipe, planned_brew_day=0))
    original.add_batch(Batch("002", "B2", recipe, planned_brew_day=0))
    original.add_assignment(_assignment("001", f1, start_day=0, fermentation_days=10))
    original.add_assignment(_assignment("002", f2, start_day=0, fermentation_days=8))

    replanned = Schedule()
    replanned.add_batch(Batch("001", "B1", recipe, planned_brew_day=0))
    replanned.add_batch(Batch("002", "B2", recipe, planned_brew_day=0))
    replanned.add_assignment(_assignment("001", f1, start_day=0, fermentation_days=10))
    replanned.add_assignment(_assignment("002", f2, start_day=0, fermentation_days=12))

    metrics = compute_metrics(brewery, original, replanned, current_day=5)

    assert metrics.changed_batches == 1
    assert metrics.schedule_shift_days == 4
    assert metrics.idle_fermenter_days == 0


def test_compute_metrics_counts_idle_gaps(recipe: Recipe) -> None:
    brewery = Brewery(
        name="Metrics Brewery",
        fermenters=[Fermenter("f", 1, 10.0)],
        kettle_yield_hl=10.0,
        max_brews_per_day=1,
        max_bottling_runs_per_day=1,
    )
    (f1,) = brewery.fermenters

    original = Schedule()
    replanned = Schedule()
    replanned.add_batch(Batch("001", "B1", recipe, planned_brew_day=0))
    replanned.add_batch(Batch("002", "B2", recipe, planned_brew_day=0))
    replanned.add_assignment(_assignment("001", f1, start_day=0, fermentation_days=5))
    replanned.add_assignment(_assignment("002", f1, start_day=8, fermentation_days=4))

    metrics = compute_metrics(brewery, original, replanned, current_day=0)

    assert metrics.idle_fermenter_days == 3
