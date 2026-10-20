import math

import pytest

from brewery_schedule_digital_twin.agents import OperationsAgent
from brewery_schedule_digital_twin.domain import (
    Batch,
    BottlingLine,
    Brew,
    Brewery,
    Brewhouse,
    FermentationAssignment,
    Fill,
    Recipe,
    Schedule,
    Vessel,
)
from brewery_schedule_digital_twin.signals import DayOffSignal


def _brewery(*capacities: float, kettle_yield_hl: float = 50.0, max_brews_per_day: int = 2) -> Brewery:
    return Brewery(
        name="Merging Brewery",
        vessels=[Vessel.unitank("f", i, c) for i, c in enumerate(capacities, start=1)],
        brewhouses=[Brewhouse("brewhouse", yield_l=kettle_yield_hl * 100, max_brews_per_day=max_brews_per_day)],
        bottling_lines=[BottlingLine("line", run_volume_l=math.inf, max_runs_per_day=2)],
    )


def _merged(batch_id: str, vessel: Vessel, *brews: tuple[int, int, float]) -> FermentationAssignment:
    """An assignment filled from brews given as (number, day, volume_hl)."""
    fills = tuple(Fill(Brew(batch_id, number, day), volume) for number, day, volume in brews)
    return FermentationAssignment(
        batch_id=batch_id,
        fermenter=vessel,
        volume_hl=sum(fill.volume_hl for fill in fills),
        start_day=min(day for _, day, _ in brews),
        base_fermentation_days=10,
        fills=fills,
    )


def test_brew_id_combines_batch_and_number() -> None:
    assert Brew("001", 2, day=5).id == "001/2"


def test_brew_numbers_start_at_one() -> None:
    with pytest.raises(ValueError):
        Brew("001", 0, day=5)


def test_fill_rejects_non_positive_volume() -> None:
    with pytest.raises(ValueError):
        Fill(Brew("001", 1, day=5), 0.0)


def test_assignment_without_fills_is_one_brew_on_its_start_day() -> None:
    vessel = Vessel.unitank("f", 1, 10.0)
    assignment = FermentationAssignment("001", vessel, 10.0, start_day=3, base_fermentation_days=10)

    assert assignment.effective_fills == (Fill(Brew("001", 1, day=3), 10.0),)
    assert assignment.fill_days == {3}
    assert assignment.last_fill_day == 3
    assert assignment.natural_end_day == 13


def test_merged_fermentation_ends_after_the_last_fill() -> None:
    assignment = _merged("001", Vessel.unitank("f", 1, 100.0), (1, 3, 50.0), (2, 5, 50.0))

    assert assignment.start_day == 3
    assert assignment.fill_days == {3, 5}
    assert assignment.natural_end_day == 15
    assert assignment.covers(3)


def test_fills_must_start_on_the_start_day() -> None:
    with pytest.raises(ValueError):
        FermentationAssignment(
            "001",
            Vessel.unitank("f", 1, 100.0),
            100.0,
            start_day=2,
            base_fermentation_days=10,
            fills=(Fill(Brew("001", 1, day=3), 50.0), Fill(Brew("001", 2, day=3), 50.0)),
        )


def test_fills_must_add_up_to_the_volume() -> None:
    with pytest.raises(ValueError):
        FermentationAssignment(
            "001",
            Vessel.unitank("f", 1, 100.0),
            100.0,
            start_day=3,
            base_fermentation_days=10,
            fills=(Fill(Brew("001", 1, day=3), 50.0),),
        )


def test_fills_must_come_from_the_batch_own_brews() -> None:
    with pytest.raises(ValueError):
        FermentationAssignment(
            "001",
            Vessel.unitank("f", 1, 100.0),
            50.0,
            start_day=3,
            base_fermentation_days=10,
            fills=(Fill(Brew("002", 1, day=3), 50.0),),
        )


def test_brewery_rejects_negative_brew_spread() -> None:
    with pytest.raises(ValueError):
        Brewery(
            name="Broken",
            vessels=[],
            brewhouses=[Brewhouse("brewhouse", yield_l=1_000, max_brews_per_day=1)],
            bottling_lines=[BottlingLine("line", run_volume_l=math.inf, max_runs_per_day=1)],
            max_brew_spread_days=-1,
        )


def test_brew_volumes_add_up_a_brew_split_between_vessels() -> None:
    brewery = _brewery(100.0, 100.0)
    schedule = Schedule()
    schedule.add_assignment(_merged("001", brewery.vessels[0], (1, 0, 40.0), (2, 0, 40.0), (3, 1, 20.0)))
    schedule.add_assignment(_merged("001", brewery.vessels[1], (3, 1, 20.0)))

    assert schedule.brew_volumes() == {
        Brew("001", 1, day=0): 40.0,
        Brew("001", 2, day=0): 40.0,
        Brew("001", 3, day=1): 40.0,
    }
    assert schedule.brews_on(0) == 2
    assert schedule.brews_on(1) == 1


def test_brews_on_counts_a_single_brew_once_across_split_vessels(recipe: Recipe) -> None:
    brewery = _brewery(5.0, 5.0, kettle_yield_hl=10.0)
    schedule = Schedule()
    for vessel in brewery.vessels:
        schedule.add_assignment(FermentationAssignment("001", vessel, 5.0, start_day=0, base_fermentation_days=10))

    assert schedule.brews_on(0) == 1


def test_has_scheduled_work_on_a_later_fill_day() -> None:
    schedule = Schedule()
    schedule.add_assignment(_merged("001", Vessel.unitank("f", 1, 100.0), (1, 3, 50.0), (2, 5, 50.0)))

    assert schedule.has_scheduled_work_on(5)
    assert not schedule.has_scheduled_work_on(4)


def test_lock_started_assignments_locks_every_vessel_of_a_started_batch() -> None:
    brewery = _brewery(100.0, 100.0)
    schedule = Schedule()
    schedule.add_assignment(_merged("001", brewery.vessels[0], (1, 0, 50.0), (2, 1, 50.0)))
    schedule.add_assignment(_merged("001", brewery.vessels[1], (3, 2, 50.0)))

    schedule.lock_started_assignments(current_day=0)

    assert all(a.locked for a in schedule.assignments)


def _validated(brewery: Brewery, recipe: Recipe, *assignments: FermentationAssignment) -> list[str]:
    schedule = Schedule()
    schedule.add_batch(Batch("001", "B1", recipe, planned_brew_day=0))
    for assignment in assignments:
        schedule.add_assignment(assignment)
    return schedule.validate(brewery)


def test_validate_accepts_a_batch_above_the_kettle_yield_in_several_brews(recipe: Recipe) -> None:
    brewery = _brewery(100.0)
    assert _validated(brewery, recipe, _merged("001", brewery.vessels[0], (1, 0, 50.0), (2, 1, 50.0))) == []


def test_validate_rejects_a_brew_above_the_kettle_yield(recipe: Recipe) -> None:
    brewery = _brewery(100.0)
    problems = _validated(brewery, recipe, _merged("001", brewery.vessels[0], (1, 0, 60.0), (2, 1, 40.0)))
    assert any("'001/1' totals 60 hl" in problem for problem in problems)


def test_validate_counts_brews_not_batches_per_day(recipe: Recipe) -> None:
    brewery = _brewery(150.0, max_brews_per_day=2)
    problems = _validated(brewery, recipe, _merged("001", brewery.vessels[0], (1, 0, 50.0), (2, 0, 50.0), (3, 0, 50.0)))
    assert any("brewhouse 'brewhouse' has 3 brews on day 0" in problem for problem in problems)


def test_validate_rejects_brews_further_apart_than_the_spread(recipe: Recipe) -> None:
    brewery = _brewery(100.0)
    problems = _validated(brewery, recipe, _merged("001", brewery.vessels[0], (1, 0, 50.0), (2, 3, 50.0)))
    assert any("more than 2 day(s) apart" in problem for problem in problems)


def test_validate_rejects_a_later_brew_on_a_day_off(recipe: Recipe) -> None:
    brewery = _brewery(100.0)
    brewery.mark_day_off(1)
    problems = _validated(brewery, recipe, _merged("001", brewery.vessels[0], (1, 0, 50.0), (2, 1, 50.0)))
    assert any("brewed on day 1, a day off" in problem for problem in problems)


def test_validate_rejects_a_first_brew_off_the_planned_day(recipe: Recipe) -> None:
    brewery = _brewery(100.0)
    problems = _validated(brewery, recipe, _merged("001", brewery.vessels[0], (1, 1, 50.0), (2, 2, 50.0)))
    assert any("don't start on its declared brew day 0" in problem for problem in problems)


def test_validate_rejects_one_brew_poured_on_different_days(recipe: Recipe) -> None:
    brewery = _brewery(100.0, 100.0)
    problems = _validated(
        brewery,
        recipe,
        _merged("001", brewery.vessels[0], (1, 0, 50.0)),
        _merged("001", brewery.vessels[1], (1, 1, 30.0)),
    )
    assert any("brew '001/1' is poured on different days [0, 1]" in problem for problem in problems)


STOUT = Recipe(name="Stout", fermentation_days=10)


def _blended(batch: Batch, *brews: tuple[int, Recipe | None, float]) -> list[str]:
    """Validates a batch brewed on day 0 into one vessel from brews given as (number, recipe, volume_hl)."""
    fills = tuple(Fill(Brew(batch.id, number, 0, recipe), volume) for number, recipe, volume in brews)
    volume = sum(fill.volume_hl for fill in fills)
    schedule = Schedule()
    schedule.add_batch(batch)
    schedule.add_assignment(
        FermentationAssignment(
            batch.id, Vessel.unitank("f", 1, 100.0), volume, start_day=0, base_fermentation_days=10, fills=fills
        )
    )
    return schedule.validate(_brewery(100.0, max_brews_per_day=3))


def test_blend_needs_at_least_two_recipes(recipe: Recipe) -> None:
    with pytest.raises(ValueError):
        Batch("001", "B1", recipe, planned_brew_day=0, blend={recipe: 50.0})


def test_blend_rejects_non_positive_volumes(recipe: Recipe) -> None:
    with pytest.raises(ValueError):
        Batch("001", "B1", recipe, planned_brew_day=0, blend={recipe: 50.0, STOUT: 0.0})


def test_blend_sets_the_batch_volume(recipe: Recipe) -> None:
    assert Batch("001", "B1", recipe, planned_brew_day=0, blend={recipe: 70.0, STOUT: 30.0}).volume_hl == 100.0


def test_blend_rejects_a_different_batch_volume(recipe: Recipe) -> None:
    with pytest.raises(ValueError):
        Batch("001", "B1", recipe, planned_brew_day=0, volume_hl=90.0, blend={recipe: 70.0, STOUT: 30.0})


def test_blend_cannot_be_changed_after_creation(recipe: Recipe) -> None:
    components = {recipe: 70.0, STOUT: 30.0}
    batch = Batch("001", "B1", recipe, planned_brew_day=0, blend=components)
    components[STOUT] = 50.0

    assert batch.blend is not None and batch.blend[STOUT] == 30.0
    with pytest.raises(TypeError):
        batch.blend[STOUT] = 50.0  # type: ignore[index]


def test_validate_rejects_a_brew_of_another_recipe_without_a_blend(recipe: Recipe) -> None:
    problems = _blended(Batch("001", "B1", recipe, planned_brew_day=0), (1, None, 50.0), (2, STOUT, 50.0))
    assert problems == ["batch '001' of 'Test Pale Ale' has brews of 'Stout' but declares no blend"]


def test_validate_accepts_a_declared_blend(recipe: Recipe) -> None:
    batch = Batch("001", "B1", recipe, planned_brew_day=0, blend={recipe: 70.0, STOUT: 30.0})
    assert _blended(batch, (1, recipe, 35.0), (2, None, 35.0), (3, STOUT, 30.0)) == []


def test_validate_rejects_a_blend_off_its_proportions(recipe: Recipe) -> None:
    batch = Batch("001", "B1", recipe, planned_brew_day=0, blend={recipe: 70.0, STOUT: 30.0})
    problems = _blended(batch, (1, recipe, 50.0), (2, STOUT, 50.0))
    assert "batch '001' blends 50 hl of 'Test Pale Ale', declared 70 hl" in problems
    assert "batch '001' blends 50 hl of 'Stout', declared 30 hl" in problems


def test_validate_rejects_a_recipe_outside_the_blend(recipe: Recipe) -> None:
    porter = Recipe(name="Porter", fermentation_days=10)
    batch = Batch("001", "B1", recipe, planned_brew_day=0, blend={recipe: 50.0, STOUT: 30.0})
    problems = _blended(batch, (1, recipe, 50.0), (2, STOUT, 30.0), (3, porter, 20.0))
    assert "batch '001' has brews of 'Porter', which is not in its blend" in problems


def test_operations_agent_sees_a_later_brew_on_the_day_off(recipe: Recipe) -> None:
    brewery = _brewery(100.0)
    schedule = Schedule()
    schedule.add_batch(Batch("001", "B1", recipe, planned_brew_day=20))
    schedule.add_assignment(_merged("001", brewery.vessels[0], (1, 20, 50.0), (2, 21, 50.0)))

    assessment = OperationsAgent().assess(brewery, schedule, DayOffSignal(day=1, off_day=21), current_day=1)

    assert assessment.decision != "KEEP"
