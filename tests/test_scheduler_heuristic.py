import pytest

from brewery_agentic_replanning.domain import (
    Batch,
    Brewery,
    FermentationAssignment,
    Fermenter,
    Recipe,
    Schedule,
)
from brewery_agentic_replanning.scheduling import HeuristicScheduler, SchedulingError


def test_schedules_a_single_batch_on_the_planned_day(brewery: Brewery, recipe: Recipe) -> None:
    schedule = Schedule()
    schedule.add_batch(Batch("001", "B1", recipe, planned_brew_day=0))

    result = HeuristicScheduler().replan(brewery, schedule, current_day=0)

    assignments = result.assignments_of("001")
    assert len(assignments) == 1
    assignment = assignments[0]
    assert assignment.start_day == 0
    assert assignment.volume_hl == 10.0
    assert assignment.bottling_day == 9
    assert assignment.end_day == 10


def test_respects_max_brews_per_day(brewery: Brewery, recipe: Recipe) -> None:
    schedule = Schedule()
    schedule.add_batch(Batch("001", "B1", recipe, planned_brew_day=0))
    schedule.add_batch(Batch("002", "B2", recipe, planned_brew_day=0))

    result = HeuristicScheduler().replan(brewery, schedule, current_day=0)

    brew_days = sorted(result.brew_day_of(batch_id) for batch_id in ("001", "002"))
    assert brew_days == [0, 1]


def test_respects_days_off(brewery: Brewery, recipe: Recipe) -> None:
    brewery.mark_day_off(0)
    schedule = Schedule()
    schedule.add_batch(Batch("001", "B1", recipe, planned_brew_day=0))

    result = HeuristicScheduler().replan(brewery, schedule, current_day=0)

    assert result.brew_day_of("001") == 1


def test_avoids_a_fermenter_under_maintenance(brewery: Brewery, recipe: Recipe) -> None:
    brewery.mark_fermenter_maintenance("f1", start_day=0, duration_days=20)
    schedule = Schedule()
    schedule.add_batch(Batch("001", "B1", recipe, planned_brew_day=0))

    result = HeuristicScheduler().replan(brewery, schedule, current_day=0)

    assignment = result.assignments_of("001")[0]
    assert assignment.fermenter.id == "f2"


def test_delays_a_batch_when_every_fermenter_is_under_maintenance(brewery: Brewery, recipe: Recipe) -> None:
    brewery.mark_fermenter_maintenance("f1", start_day=0, duration_days=5)
    brewery.mark_fermenter_maintenance("f2", start_day=0, duration_days=2)
    schedule = Schedule()
    schedule.add_batch(Batch("001", "B1", recipe, planned_brew_day=0))

    result = HeuristicScheduler().replan(brewery, schedule, current_day=0)

    assignment = result.assignments_of("001")[0]
    assert assignment.fermenter.id == "f2"
    assert assignment.start_day == 2


def test_pending_batch_keeps_its_already_declared_fermentation_days(brewery: Brewery, recipe: Recipe) -> None:
    schedule = Schedule()
    schedule.add_batch(Batch("001", "B1", recipe, planned_brew_day=5))
    schedule.add_assignment(
        FermentationAssignment(
            batch_id="001",
            fermenter=brewery.fermenters[0],
            volume_hl=10.0,
            start_day=5,
            base_fermentation_days=recipe.fermentation_days + 2,
        )
    )

    result = HeuristicScheduler().replan(brewery, schedule, current_day=0)

    assignment = result.assignments_of("001")[0]
    assert assignment.base_fermentation_days == recipe.fermentation_days + 2


def test_keeps_a_feasible_pending_batch_exactly_where_it_was(brewery: Brewery, recipe: Recipe) -> None:
    schedule = Schedule()
    schedule.add_batch(Batch("001", "B1", recipe, planned_brew_day=5))
    schedule.add_assignment(
        FermentationAssignment(
            batch_id="001",
            fermenter=brewery.fermenters[1],
            volume_hl=10.0,
            start_day=5,
            base_fermentation_days=recipe.fermentation_days,
        )
    )

    result = HeuristicScheduler().replan(brewery, schedule, current_day=0)

    assignment = result.assignments_of("001")[0]
    assert assignment.fermenter.id == "f2"
    assert assignment.start_day == 5
    assert assignment.bottling_day == 5 + recipe.fermentation_days - 1


def test_moves_a_pending_batch_off_a_newly_declared_day_off(brewery: Brewery, recipe: Recipe) -> None:
    schedule = Schedule()
    schedule.add_batch(Batch("001", "B1", recipe, planned_brew_day=5))
    schedule.add_assignment(
        FermentationAssignment(
            batch_id="001",
            fermenter=brewery.fermenters[0],
            volume_hl=10.0,
            start_day=5,
            base_fermentation_days=recipe.fermentation_days,
        )
    )
    brewery.mark_day_off(5)

    result = HeuristicScheduler().replan(brewery, schedule, current_day=0)

    assignment = result.assignments_of("001")[0]
    assert assignment.fermenter.id == "f1"
    assert assignment.start_day == 6


def test_falls_back_to_a_fresh_search_when_the_anchor_fermenter_is_no_longer_free(
    brewery: Brewery, recipe: Recipe
) -> None:
    schedule = Schedule()
    schedule.add_batch(Batch("001", "B1", recipe, planned_brew_day=5))
    schedule.add_assignment(
        FermentationAssignment(
            batch_id="001",
            fermenter=brewery.fermenters[0],
            volume_hl=10.0,
            start_day=5,
            base_fermentation_days=recipe.fermentation_days,
        )
    )
    brewery.mark_fermenter_maintenance("f1", start_day=0, duration_days=20)

    result = HeuristicScheduler().replan(brewery, schedule, current_day=0)

    assignment = result.assignments_of("001")[0]
    assert assignment.fermenter.id == "f2"
    assert assignment.start_day == 5


def test_falls_back_when_the_batch_can_no_longer_brew_before_its_anchor_day(brewery: Brewery, recipe: Recipe) -> None:
    schedule = Schedule()
    schedule.add_batch(Batch("001", "B1", recipe, planned_brew_day=9))
    schedule.add_assignment(
        FermentationAssignment(
            batch_id="001",
            fermenter=brewery.fermenters[0],
            volume_hl=10.0,
            start_day=5,
            base_fermentation_days=recipe.fermentation_days,
        )
    )

    result = HeuristicScheduler().replan(brewery, schedule, current_day=0)

    assignment = result.assignments_of("001")[0]
    assert assignment.start_day == 9


def test_splits_batch_across_fermenters_when_none_fits_alone(recipe: Recipe) -> None:
    brewery = Brewery(
        name="Small Tanks Brewery",
        fermenters=[Fermenter("f", 1, 5.0), Fermenter("f", 2, 5.0)],
        kettle_yield_hl=10.0,
        max_brews_per_day=1,
        max_bottling_runs_per_day=2,
    )
    schedule = Schedule()
    schedule.add_batch(Batch("001", "B1", recipe, planned_brew_day=0))

    result = HeuristicScheduler().replan(brewery, schedule, current_day=0)

    assignments = result.assignments_of("001")
    assert len(assignments) == 2
    assert {a.fermenter.id for a in assignments} == {"f1", "f2"}
    assert sum(a.volume_hl for a in assignments) == 10.0


def test_does_not_move_locked_assignments(brewery: Brewery, recipe: Recipe) -> None:
    schedule = Schedule()
    schedule.add_batch(Batch("001", "B1", recipe, planned_brew_day=0))
    schedule.add_batch(Batch("002", "B2", recipe, planned_brew_day=0))
    schedule.add_assignment(
        FermentationAssignment(
            batch_id="001",
            fermenter=brewery.fermenters[0],
            volume_hl=10.0,
            start_day=0,
            base_fermentation_days=10,
            locked=True,
        )
    )

    result = HeuristicScheduler().replan(brewery, schedule, current_day=3)

    locked_assignment = result.assignment_for("001", "f1")
    assert locked_assignment.start_day == 0
    assert result.brew_day_of("002") == 3
    assert {a.fermenter.id for a in result.assignments_of("002")} == {"f2"}


def test_respects_a_batch_not_brewable_before_a_given_day(brewery: Brewery, recipe: Recipe) -> None:
    schedule = Schedule()
    schedule.add_batch(Batch("001", "B1", recipe, planned_brew_day=7))

    result = HeuristicScheduler().replan(brewery, schedule, current_day=0)

    assert result.brew_day_of("001") == 7


def test_hits_fixed_bottling_deadline(brewery: Brewery, recipe: Recipe) -> None:
    schedule = Schedule()
    schedule.add_batch(Batch("001", "B1", recipe, planned_brew_day=0, target_bottling_day=10))

    result = HeuristicScheduler().replan(brewery, schedule, current_day=0)

    assignment = result.assignments_of("001")[0]
    assert assignment.bottling_day == 10


def test_raises_when_deadline_is_infeasible(brewery: Brewery, recipe: Recipe) -> None:
    schedule = Schedule()
    schedule.add_batch(Batch("001", "B1", recipe, planned_brew_day=0, target_bottling_day=5))

    with pytest.raises(SchedulingError):
        HeuristicScheduler(max_horizon_days=30).replan(brewery, schedule, current_day=0)


def test_bottling_capacity_counts_one_slot_per_fermenter_not_per_batch(recipe: Recipe) -> None:
    brewery = Brewery(
        name="Mixed Tanks Brewery",
        fermenters=[Fermenter("f", 1, 5.0), Fermenter("f", 2, 5.0), Fermenter("f", 3, 10.0)],
        kettle_yield_hl=10.0,
        max_brews_per_day=2,
        max_bottling_runs_per_day=2,
    )
    schedule = Schedule()
    schedule.add_batch(Batch("001", "B1", recipe, planned_brew_day=0))
    schedule.add_batch(Batch("002", "B2", recipe, planned_brew_day=0))

    result = HeuristicScheduler().replan(brewery, schedule, current_day=0)

    bottling_days = [a.bottling_day for a in result.assignments]
    for day in set(bottling_days):
        assert bottling_days.count(day) <= brewery.max_bottling_runs_per_day

    single_tank_batch = result.assignments_of("001")
    split_batch = result.assignments_of("002")
    assert len(single_tank_batch) == 1
    assert len(split_batch) == 2
    last_fermentation_day = single_tank_batch[0].last_fermentation_day
    assert single_tank_batch[0].bottling_day == last_fermentation_day
    assert all(a.bottling_day == last_fermentation_day + 1 for a in split_batch)


def _mixed_brewery(*capacities: float) -> Brewery:
    return Brewery(
        name="Mixed Tanks Brewery",
        fermenters=[Fermenter("f", i, c) for i, c in enumerate(capacities, start=1)],
        kettle_yield_hl=10.0,
        max_brews_per_day=5,
        max_bottling_runs_per_day=5,
    )


def _declared(
    brewery: Brewery, recipe: Recipe, batch_id: str, rows: list[tuple[str, float]], brew_day: int = 5
) -> Schedule:
    schedule = Schedule()
    schedule.add_batch(Batch(batch_id, batch_id, recipe, planned_brew_day=brew_day))
    for fermenter_id, volume_hl in rows:
        schedule.add_assignment(
            FermentationAssignment(
                batch_id=batch_id,
                fermenter=brewery.fermenter_by_id(fermenter_id),
                volume_hl=volume_hl,
                start_day=brew_day,
                base_fermentation_days=recipe.fermentation_days,
            )
        )
    assert schedule.validate(brewery) == []
    return schedule


def test_moved_partial_brew_keeps_its_volume_in_a_same_size_tank(recipe: Recipe) -> None:
    brewery = _mixed_brewery(10.0, 5.0, 5.0)
    schedule = _declared(brewery, recipe, "001", [("f2", 5.0)])
    brewery.mark_fermenter_maintenance("f2", start_day=0, duration_days=30)

    result = HeuristicScheduler().replan(brewery, schedule, current_day=0)

    [assignment] = result.assignments_of("001")
    assert (assignment.fermenter.id, assignment.volume_hl) == ("f3", 5.0)


def test_moved_partial_brew_keeps_its_volume_in_a_larger_tank(recipe: Recipe) -> None:
    brewery = _mixed_brewery(10.0, 5.0)
    schedule = _declared(brewery, recipe, "001", [("f2", 5.0)])
    brewery.mark_fermenter_maintenance("f2", start_day=0, duration_days=30)

    result = HeuristicScheduler().replan(brewery, schedule, current_day=0)

    [assignment] = result.assignments_of("001")
    assert (assignment.fermenter.id, assignment.volume_hl) == ("f1", 5.0)
    assert result.validate(brewery) == []


def test_moved_split_brew_keeps_its_total_volume_in_one_tank(recipe: Recipe) -> None:
    brewery = _mixed_brewery(5.0, 5.0, 10.0)
    schedule = _declared(brewery, recipe, "001", [("f1", 5.0), ("f2", 5.0)])
    brewery.mark_fermenter_maintenance("f1", start_day=0, duration_days=30)

    result = HeuristicScheduler().replan(brewery, schedule, current_day=0)

    [assignment] = result.assignments_of("001")
    assert (assignment.fermenter.id, assignment.volume_hl) == ("f3", 10.0)


def test_moved_single_tank_brew_is_split_without_changing_its_volume(recipe: Recipe) -> None:
    brewery = _mixed_brewery(10.0, 5.0, 5.0)
    schedule = _declared(brewery, recipe, "001", [("f1", 10.0)])
    brewery.mark_fermenter_maintenance("f1", start_day=0, duration_days=30)

    result = HeuristicScheduler().replan(brewery, schedule, current_day=0)

    assignments = result.assignments_of("001")
    assert sorted((a.fermenter.id, a.volume_hl) for a in assignments) == [("f2", 5.0), ("f3", 5.0)]


def test_moved_partial_brew_keeps_its_volume_when_its_brew_day_moves(recipe: Recipe) -> None:
    brewery = _mixed_brewery(10.0, 5.0)
    schedule = _declared(brewery, recipe, "001", [("f2", 5.0)])
    brewery.mark_day_off(5)

    result = HeuristicScheduler().replan(brewery, schedule, current_day=0)

    [assignment] = result.assignments_of("001")
    assert assignment.start_day == 6
    assert assignment.volume_hl == 5.0


def test_new_batch_without_assignments_defaults_to_kettle_yield(recipe: Recipe) -> None:
    brewery = _mixed_brewery(10.0, 5.0)
    schedule = Schedule()
    schedule.add_batch(Batch("NEW", "new order", recipe, planned_brew_day=0))

    result = HeuristicScheduler().replan(brewery, schedule, current_day=0)

    assert sum(a.volume_hl for a in result.assignments_of("NEW")) == brewery.kettle_yield_hl


def test_new_batch_with_a_requested_volume_gets_exactly_that(recipe: Recipe) -> None:
    brewery = _mixed_brewery(10.0, 5.0)
    schedule = Schedule()
    schedule.add_batch(Batch("NEW", "new order", recipe, planned_brew_day=0, volume_hl=5.0))

    result = HeuristicScheduler().replan(brewery, schedule, current_day=0)

    [assignment] = result.assignments_of("NEW")
    assert (assignment.fermenter.id, assignment.volume_hl) == ("f2", 5.0)
    assert result.validate(brewery) == []


def test_requested_volume_above_kettle_yield_is_rejected(recipe: Recipe) -> None:
    brewery = _mixed_brewery(10.0, 10.0)
    schedule = Schedule()
    schedule.add_batch(Batch("NEW", "new order", recipe, planned_brew_day=0, volume_hl=15.0))

    with pytest.raises(SchedulingError):
        HeuristicScheduler().replan(brewery, schedule, current_day=0)
