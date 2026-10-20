import math

import pytest

from brewery_schedule_digital_twin.domain import (
    Batch,
    BottlingLine,
    Brewery,
    Brewhouse,
    FermentationAssignment,
    Recipe,
    Schedule,
    Vessel,
)
from brewery_schedule_digital_twin.scheduling import HeuristicScheduler, SchedulingError


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
            fermenter=brewery.vessels[0],
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
            fermenter=brewery.vessels[1],
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
            fermenter=brewery.vessels[0],
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
            fermenter=brewery.vessels[0],
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
            fermenter=brewery.vessels[0],
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
        vessels=[Vessel.unitank("f", 1, 5.0), Vessel.unitank("f", 2, 5.0)],
        brewhouses=[Brewhouse("brewhouse", yield_l=1_000, max_brews_per_day=1)],
        bottling_lines=[BottlingLine("line", run_volume_l=math.inf, max_runs_per_day=2)],
    )
    schedule = Schedule()
    schedule.add_batch(Batch("001", "B1", recipe, planned_brew_day=0))

    result = HeuristicScheduler().replan(brewery, schedule, current_day=0)

    assignments = result.assignments_of("001")
    assert len(assignments) == 2
    assert {a.fermenter.id for a in assignments} == {"f1", "f2"}
    assert sum(a.volume_hl for a in assignments) == 10.0


def test_uses_only_vessels_with_the_full_role(recipe: Recipe) -> None:
    brewery = Brewery(
        name="Mixed Brewery",
        vessels=[
            Vessel.fermenter("fv", 1, 10.0),
            Vessel.lagering_tank("lt", 1, 10.0),
            Vessel.general_vessel("gv", 1, 10.0),
            Vessel.unitank("ut", 1, 10.0),
        ],
        brewhouses=[Brewhouse("brewhouse", yield_l=1_000, max_brews_per_day=2)],
        bottling_lines=[BottlingLine("line", run_volume_l=math.inf, max_runs_per_day=2)],
    )
    schedule = Schedule()
    schedule.add_batch(Batch("001", "B1", recipe, planned_brew_day=0))
    schedule.add_batch(Batch("002", "B2", recipe, planned_brew_day=0))

    result = HeuristicScheduler().replan(brewery, schedule, current_day=0)

    assert [a.fermenter.id for a in result.assignments_of("001")] == ["ut1"]
    assert [a.fermenter.id for a in result.assignments_of("002")] == ["ut1"]
    assert result.brew_day_of("002") == 10


def test_does_not_move_locked_assignments(brewery: Brewery, recipe: Recipe) -> None:
    schedule = Schedule()
    schedule.add_batch(Batch("001", "B1", recipe, planned_brew_day=0))
    schedule.add_batch(Batch("002", "B2", recipe, planned_brew_day=0))
    schedule.add_assignment(
        FermentationAssignment(
            batch_id="001",
            fermenter=brewery.vessels[0],
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
        vessels=[Vessel.unitank("f", 1, 5.0), Vessel.unitank("f", 2, 5.0), Vessel.unitank("f", 3, 10.0)],
        brewhouses=[Brewhouse("brewhouse", yield_l=1_000, max_brews_per_day=2)],
        bottling_lines=[BottlingLine("line", run_volume_l=math.inf, max_runs_per_day=2)],
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
        vessels=[Vessel.unitank("f", i, c) for i, c in enumerate(capacities, start=1)],
        brewhouses=[Brewhouse("brewhouse", yield_l=1_000, max_brews_per_day=5)],
        bottling_lines=[BottlingLine("line", run_volume_l=math.inf, max_runs_per_day=5)],
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
                fermenter=brewery.vessel_by_id(fermenter_id),
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


def test_requested_volume_above_kettle_yield_is_brewed_in_several_brews(recipe: Recipe) -> None:
    brewery = _mixed_brewery(10.0, 10.0)
    schedule = Schedule()
    schedule.add_batch(Batch("NEW", "new order", recipe, planned_brew_day=0, volume_hl=15.0))

    result = HeuristicScheduler().replan(brewery, schedule, current_day=0)

    assert {brew.id: volume for brew, volume in result.brew_volumes().items()} == {"NEW/1": 7.5, "NEW/2": 7.5}
    assert sum(a.volume_hl for a in result.assignments_of("NEW")) == 15.0
    assert result.validate(brewery) == []


def _merging_brewery(*capacities: float, max_brews_per_day: int = 2, max_brew_spread_days: int = 2) -> Brewery:
    return Brewery(
        name="Merging Brewery",
        vessels=[Vessel.unitank("f", i, c) for i, c in enumerate(capacities, start=1)],
        brewhouses=[Brewhouse("brewhouse", yield_l=5_000, max_brews_per_day=max_brews_per_day)],
        bottling_lines=[BottlingLine("line", run_volume_l=math.inf, max_runs_per_day=2)],
        max_brew_spread_days=max_brew_spread_days,
    )


def _fills(schedule: Schedule, batch_id: str) -> dict[str, list[tuple[str, int, float]]]:
    """Per vessel, the (brew id, day, volume) of every fill."""
    return {
        a.fermenter.id: [(fill.brew.id, fill.brew.day, fill.volume_hl) for fill in a.effective_fills]
        for a in schedule.assignments_of(batch_id)
    }


def _plan(brewery: Brewery, recipe: Recipe, volume_hl: float) -> Schedule:
    """Plans one batch; validated unless the scheduler moved it off its planned day 0."""
    schedule = Schedule()
    schedule.add_batch(Batch("BIG", "big order", recipe, planned_brew_day=0, volume_hl=volume_hl))
    result = HeuristicScheduler().replan(brewery, schedule, current_day=0)
    if result.brew_day_of("BIG") == 0:
        assert result.validate(brewery) == []
    return result


def test_merges_two_brews_on_one_day_into_a_large_vessel(recipe: Recipe) -> None:
    result = _plan(_merging_brewery(100.0), recipe, 100.0)

    assert _fills(result, "BIG") == {"f1": [("BIG/1", 0, 50.0), ("BIG/2", 0, 50.0)]}
    [assignment] = result.assignments_of("BIG")
    assert (assignment.start_day, assignment.natural_end_day, assignment.bottling_day) == (0, 10, 9)


def test_brews_on_following_days_when_the_brewhouse_is_busy(recipe: Recipe) -> None:
    result = _plan(_merging_brewery(100.0, max_brews_per_day=1), recipe, 100.0)

    assert _fills(result, "BIG") == {"f1": [("BIG/1", 0, 50.0), ("BIG/2", 1, 50.0)]}
    [assignment] = result.assignments_of("BIG")
    assert (assignment.start_day, assignment.natural_end_day, assignment.bottling_day) == (0, 11, 10)


def test_skips_a_day_off_between_brews(recipe: Recipe) -> None:
    brewery = _merging_brewery(100.0, max_brews_per_day=1)
    brewery.mark_day_off(1)

    result = _plan(brewery, recipe, 100.0)

    assert _fills(result, "BIG") == {"f1": [("BIG/1", 0, 50.0), ("BIG/2", 2, 50.0)]}


def test_moves_the_first_brew_when_the_brews_do_not_fit_in_the_spread(recipe: Recipe) -> None:
    brewery = _merging_brewery(100.0, max_brews_per_day=1, max_brew_spread_days=1)
    brewery.mark_day_off(1)

    result = _plan(brewery, recipe, 100.0)

    assert _fills(result, "BIG") == {"f1": [("BIG/1", 2, 50.0), ("BIG/2", 3, 50.0)]}


def test_brews_a_batch_in_the_fewest_equal_brews(recipe: Recipe) -> None:
    result = _plan(_merging_brewery(100.0), recipe, 60.0)

    assert _fills(result, "BIG") == {"f1": [("BIG/1", 0, 30.0), ("BIG/2", 0, 30.0)]}


def test_splits_a_brew_between_vessels_while_merging(recipe: Recipe) -> None:
    result = _plan(_merging_brewery(100.0, 100.0, max_brews_per_day=3), recipe, 120.0)

    assert _fills(result, "BIG") == {
        "f1": [("BIG/1", 0, 40.0), ("BIG/2", 0, 40.0), ("BIG/3", 0, 20.0)],
        "f2": [("BIG/3", 0, 20.0)],
    }


def test_each_brew_of_a_merged_batch_takes_brewhouse_capacity(recipe: Recipe) -> None:
    brewery = _merging_brewery(100.0, 50.0)
    schedule = Schedule()
    schedule.add_batch(Batch("BIG", "big order", recipe, planned_brew_day=0, volume_hl=100.0))
    schedule.add_batch(Batch("SMALL", "small order", recipe, planned_brew_day=0, volume_hl=50.0))

    result = HeuristicScheduler().replan(brewery, schedule, current_day=0)

    assert result.brew_day_of("BIG") == 0
    assert result.brew_day_of("SMALL") == 1
    assert (result.brews_on(0), result.brews_on(1)) == (2, 1)


def test_keeps_a_merged_placement_that_is_still_feasible(recipe: Recipe) -> None:
    brewery = _merging_brewery(100.0, max_brews_per_day=1)
    first = _plan(brewery, recipe, 100.0)

    second = HeuristicScheduler().replan(brewery, first.clone(), current_day=0)

    assert _fills(second, "BIG") == _fills(first, "BIG")
    assert second.validate(brewery) == []


def test_keeps_every_vessel_of_a_batch_whose_first_brew_was_poured(recipe: Recipe) -> None:
    brewery = _merging_brewery(100.0, 100.0, max_brews_per_day=1)
    first = _plan(brewery, recipe, 120.0)
    assert _fills(first, "BIG") == {
        "f1": [("BIG/1", 0, 40.0), ("BIG/2", 1, 40.0), ("BIG/3", 2, 20.0)],
        "f2": [("BIG/3", 2, 20.0)],
    }

    second = HeuristicScheduler().replan(brewery, first.clone(), current_day=1)

    assert _fills(second, "BIG") == _fills(first, "BIG")
    assert all(a.locked for a in second.assignments_of("BIG"))


STOUT = Recipe(name="Stout", fermentation_days=14)


def _recipe_fills(schedule: Schedule, batch_id: str) -> dict[str, list[tuple[str, str, int, float]]]:
    """Per vessel, the (brew id, recipe name, day, volume) of every fill."""
    batch = schedule.batches[batch_id]
    return {
        a.fermenter.id: [
            (fill.brew.id, batch.recipe_of(fill.brew).name, fill.brew.day, fill.volume_hl) for fill in a.effective_fills
        ]
        for a in schedule.assignments_of(batch_id)
    }


def _plan_blend(brewery: Brewery, blend: Batch) -> Schedule:
    schedule = Schedule()
    schedule.add_batch(blend)
    result = HeuristicScheduler().replan(brewery, schedule, current_day=0)
    assert result.validate(brewery) == []
    return result


def test_brews_each_recipe_of_a_blend_in_its_own_brews(recipe: Recipe) -> None:
    blend = Batch("BLD", "blend", STOUT, planned_brew_day=0, blend={STOUT: 70.0, recipe: 30.0})

    result = _plan_blend(_merging_brewery(100.0, max_brews_per_day=3), blend)

    assert _recipe_fills(result, "BLD") == {
        "f1": [("BLD/1", "Stout", 0, 35.0), ("BLD/2", "Stout", 0, 35.0), ("BLD/3", "Test Pale Ale", 0, 30.0)]
    }
    [assignment] = result.assignments_of("BLD")
    assert assignment.base_fermentation_days == STOUT.fermentation_days


def test_splits_a_blend_between_vessels_in_its_proportions(recipe: Recipe) -> None:
    blend = Batch("BLD", "blend", STOUT, planned_brew_day=0, blend={STOUT: 80.0, recipe: 40.0})

    result = _plan_blend(_merging_brewery(100.0, 100.0, max_brews_per_day=3), blend)

    fills = _recipe_fills(result, "BLD")
    assert [(brew, name, volume) for brew, name, _, volume in fills["f1"]] == pytest.approx(
        [("BLD/1", "Stout", 100 / 3), ("BLD/2", "Stout", 100 / 3), ("BLD/3", "Test Pale Ale", 100 / 3)]
    )
    assert [(brew, name, volume) for brew, name, _, volume in fills["f2"]] == pytest.approx(
        [("BLD/1", "Stout", 20 / 3), ("BLD/2", "Stout", 20 / 3), ("BLD/3", "Test Pale Ale", 20 / 3)]
    )


def test_keeps_the_blend_when_a_moved_batch_is_planned_again(recipe: Recipe) -> None:
    brewery = _merging_brewery(100.0, max_brews_per_day=1)
    schedule = Schedule()
    schedule.add_batch(Batch("BLD", "blend", STOUT, planned_brew_day=2, blend={STOUT: 50.0, recipe: 50.0}))
    first = HeuristicScheduler().replan(brewery, schedule, current_day=0)
    assert _recipe_fills(first, "BLD") == {"f1": [("BLD/1", "Stout", 2, 50.0), ("BLD/2", "Test Pale Ale", 3, 50.0)]}
    brewery.mark_day_off(3)

    second = HeuristicScheduler().replan(brewery, first.clone(), current_day=0)

    assert _recipe_fills(second, "BLD") == {"f1": [("BLD/1", "Stout", 2, 50.0), ("BLD/2", "Test Pale Ale", 4, 50.0)]}
    assert second.validate(brewery) == []
