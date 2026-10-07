import pytest

from brewery_agentic_replanning.domain import (
    Batch,
    Brewery,
    FermentationAssignment,
    Recipe,
    Schedule,
)


def test_fermentation_assignment_end_day_defaults_to_natural_end(brewery: Brewery) -> None:
    assignment = FermentationAssignment(
        batch_id="001",
        fermenter=brewery.fermenters[0],
        volume_hl=10.0,
        start_day=0,
        base_fermentation_days=10,
    )
    assert assignment.natural_end_day == 10
    assert assignment.last_fermentation_day == 9
    assert assignment.end_day == 10
    assert assignment.covers(9)
    assert not assignment.covers(10)


def test_fermentation_assignment_bottling_day_is_the_last_occupied_day(brewery: Brewery) -> None:
    assignment = FermentationAssignment(
        batch_id="001",
        fermenter=brewery.fermenters[0],
        volume_hl=10.0,
        start_day=0,
        base_fermentation_days=10,
        extra_delay_days=2,
        bottling_day=15,
    )
    assert assignment.natural_end_day == 12
    assert assignment.last_fermentation_day == 11
    assert assignment.end_day == 16
    assert assignment.covers(15)
    assert not assignment.covers(16)


def test_fermentation_assignment_end_day_never_precedes_natural_end(brewery: Brewery) -> None:
    assignment = FermentationAssignment(
        batch_id="001",
        fermenter=brewery.fermenters[0],
        volume_hl=10.0,
        start_day=0,
        base_fermentation_days=10,
        bottling_day=10,
    )
    assignment.extra_delay_days = 5

    assert assignment.natural_end_day == 15
    assert assignment.end_day == 15


def test_fermentation_assignment_rejects_volume_over_capacity(brewery: Brewery) -> None:
    with pytest.raises(ValueError):
        FermentationAssignment(
            batch_id="001",
            fermenter=brewery.fermenters[0],
            volume_hl=999.0,
            start_day=0,
            base_fermentation_days=10,
        )


def test_schedule_lock_started_assignments(brewery: Brewery, recipe: Recipe) -> None:
    schedule = Schedule()
    schedule.add_batch(Batch("001", "B1", recipe, planned_brew_day=0))
    schedule.add_assignment(
        FermentationAssignment(
            batch_id="001",
            fermenter=brewery.fermenters[0],
            volume_hl=10.0,
            start_day=5,
            base_fermentation_days=10,
        )
    )

    schedule.lock_started_assignments(current_day=3)
    assert not schedule.assignments[0].locked

    schedule.lock_started_assignments(current_day=5)
    assert schedule.assignments[0].locked


def test_schedule_clone_is_independent(brewery: Brewery, recipe: Recipe) -> None:
    schedule = Schedule()
    schedule.add_batch(Batch("001", "B1", recipe, planned_brew_day=0))
    schedule.add_assignment(
        FermentationAssignment(
            batch_id="001",
            fermenter=brewery.fermenters[0],
            volume_hl=10.0,
            start_day=0,
            base_fermentation_days=10,
        )
    )

    clone = schedule.clone()
    clone.assignments[0].extra_delay_days = 5
    clone.assignments[0].locked = True

    assert schedule.assignments[0].extra_delay_days == 0
    assert not schedule.assignments[0].locked


def test_schedule_unscheduled_batches(recipe: Recipe) -> None:
    schedule = Schedule()
    schedule.add_batch(Batch("001", "B1", recipe, planned_brew_day=0))
    schedule.add_batch(Batch("002", "B2", recipe, planned_brew_day=0))
    assert {b.id for b in schedule.unscheduled_batches()} == {"001", "002"}


def test_schedule_horizon_days_uses_latest_end_day(brewery: Brewery, recipe: Recipe) -> None:
    schedule = Schedule()
    schedule.add_batch(Batch("001", "B1", recipe, planned_brew_day=0))
    schedule.add_assignment(
        FermentationAssignment(
            batch_id="001",
            fermenter=brewery.fermenters[0],
            volume_hl=10.0,
            start_day=0,
            base_fermentation_days=10,
        )
    )
    assert schedule.horizon_days() == 10


def _one_batch(brewery: Brewery, recipe: Recipe, rows: list[tuple[int, float]], **batch_kwargs) -> Schedule:
    schedule = Schedule()
    schedule.add_batch(Batch("001", "B1", recipe, planned_brew_day=0, **batch_kwargs))
    for fermenter_index, volume_hl in rows:
        schedule.add_assignment(
            FermentationAssignment(
                batch_id="001",
                fermenter=brewery.fermenters[fermenter_index],
                volume_hl=volume_hl,
                start_day=0,
                base_fermentation_days=recipe.fermentation_days,
            )
        )
    return schedule


def test_validate_accepts_a_partial_brew(brewery: Brewery, recipe: Recipe) -> None:
    assert _one_batch(brewery, recipe, [(0, 5.0)]).validate(brewery) == []


def test_validate_rejects_a_batch_above_the_kettle_yield(brewery: Brewery, recipe: Recipe) -> None:
    problems = _one_batch(brewery, recipe, [(0, 10.0), (1, 5.0)]).validate(brewery)
    assert any("totals 15 hl" in p for p in problems)


def test_validate_rejects_an_assignment_over_tank_capacity(brewery: Brewery, recipe: Recipe) -> None:
    schedule = _one_batch(brewery, recipe, [(0, 5.0)])
    schedule.assignments[0].volume_hl = 12.0

    problems = schedule.validate(brewery)

    assert any("capacity 10 hl" in p for p in problems)


def test_validate_rejects_a_requested_volume_its_assignments_dont_match(brewery: Brewery, recipe: Recipe) -> None:
    problems = _one_batch(brewery, recipe, [(0, 5.0)], volume_hl=10.0).validate(brewery)
    assert any("requested 10 hl" in p for p in problems)


def test_batch_requested_volume_must_be_positive(recipe: Recipe) -> None:
    with pytest.raises(ValueError):
        Batch("001", "B1", recipe, planned_brew_day=0, volume_hl=0.0)
