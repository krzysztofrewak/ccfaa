import pytest

from brewery_agentic_replanning.agents import (
    FermentationAgent,
    MaintenanceAgent,
    OperationsAgent,
    ProcurementAgent,
    SalesAgent,
)
from brewery_agentic_replanning.domain import Batch, Brewery, FermentationAssignment, Recipe, Schedule
from brewery_agentic_replanning.scheduling import HeuristicScheduler
from brewery_agentic_replanning.signals import (
    DayOffSignal,
    FermentationDelaySignal,
    FermenterMaintenanceSignal,
    IngredientShortageSignal,
    NewBatchRequirementSignal,
)


def test_fermentation_delay_signal_rejects_zero_extra_days() -> None:
    with pytest.raises(ValueError):
        FermentationDelaySignal(day=1, batch_id="001", fermenter_id="f1", extra_days=0)


def test_fermentation_delay_signal_accepts_a_negative_extra_days_as_a_shortening() -> None:
    signal = FermentationDelaySignal(day=1, batch_id="001", fermenter_id="f1", extra_days=-3)
    assert "shortened by 3 day(s)" in signal.description


def test_ingredient_shortage_signal_rejects_a_resolution_before_the_signal_day() -> None:
    with pytest.raises(ValueError):
        IngredientShortageSignal(day=5, batch_id="003", available_from_day=4)


def test_signal_descriptions_are_human_readable(recipe: Recipe) -> None:
    delay = FermentationDelaySignal(day=4, batch_id="001", fermenter_id="f1", extra_days=3)
    assert delay.kind == "FermentationDelaySignal"
    assert delay.related_batch_id == "001"
    assert "001" in delay.description and "f1" in delay.description and "3" in delay.description

    shortage = IngredientShortageSignal(day=5, batch_id="003", available_from_day=9)
    assert shortage.related_batch_id == "003"
    assert "003" in shortage.description and "9" in shortage.description

    new_batch = Batch("005", "B5", recipe, planned_brew_day=10, target_bottling_day=25)
    requirement = NewBatchRequirementSignal(day=8, batch=new_batch)
    assert requirement.related_batch_id == "005"
    assert "25" in requirement.description

    day_off = DayOffSignal(day=1, off_day=20)
    assert day_off.related_batch_id is None
    assert "20" in day_off.description


def test_fermenter_maintenance_signal_rejects_non_positive_duration() -> None:
    with pytest.raises(ValueError):
        FermenterMaintenanceSignal(day=1, fermenter_id="f1", start_day=5, duration_days=0)


def test_fermenter_maintenance_signal_description() -> None:
    signal = FermenterMaintenanceSignal(day=1, fermenter_id="f1", start_day=5, duration_days=3)
    assert signal.related_batch_id is None
    assert "f1" in signal.description and "5" in signal.description and "3" in signal.description


def test_new_batch_requirement_signal_requires_a_deadline(recipe: Recipe) -> None:
    batch = Batch("005", "B5", recipe, planned_brew_day=1)
    with pytest.raises(ValueError):
        NewBatchRequirementSignal(day=1, batch=batch)


def test_fermentation_agent_extends_the_targeted_assignment(brewery: Brewery, recipe: Recipe) -> None:
    schedule = Schedule()
    schedule.add_batch(Batch("001", "B1", recipe, planned_brew_day=0))
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

    signal = FermentationDelaySignal(day=4, batch_id="001", fermenter_id="f1", extra_days=3)
    had_effect = FermentationAgent().apply(brewery, schedule, signal)

    assert schedule.assignment_for("001", "f1").extra_delay_days == 3
    assert had_effect is True


def test_fermentation_agent_shortens_the_targeted_assignment(brewery: Brewery, recipe: Recipe) -> None:
    schedule = Schedule()
    schedule.add_batch(Batch("001", "B1", recipe, planned_brew_day=0))
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

    signal = FermentationDelaySignal(day=4, batch_id="001", fermenter_id="f1", extra_days=-3)
    had_effect = FermentationAgent().apply(brewery, schedule, signal)

    assert schedule.assignment_for("001", "f1").extra_delay_days == -3
    assert had_effect is True


def test_procurement_agent_pushes_back_the_planned_brew_day(brewery: Brewery, recipe: Recipe) -> None:
    schedule = Schedule()
    schedule.add_batch(Batch("003", "B3", recipe, planned_brew_day=2))

    signal = IngredientShortageSignal(day=1, batch_id="003", available_from_day=9)
    had_effect = ProcurementAgent().apply(brewery, schedule, signal)

    assert schedule.batches["003"].planned_brew_day == 9
    assert had_effect is True


def test_procurement_agent_never_pulls_the_brew_day_earlier(brewery: Brewery, recipe: Recipe) -> None:
    schedule = Schedule()
    schedule.add_batch(Batch("003", "B3", recipe, planned_brew_day=12))

    signal = IngredientShortageSignal(day=1, batch_id="003", available_from_day=9)
    had_effect = ProcurementAgent().apply(brewery, schedule, signal)

    assert schedule.batches["003"].planned_brew_day == 12
    assert had_effect is False


def test_sales_agent_adds_the_new_batch(brewery: Brewery, recipe: Recipe) -> None:
    schedule = Schedule()
    new_batch = Batch("005", "B5", recipe, planned_brew_day=10, target_bottling_day=25)

    signal = NewBatchRequirementSignal(day=8, batch=new_batch)
    had_effect = SalesAgent().apply(brewery, schedule, signal)

    assert schedule.batches["005"] is new_batch
    assert had_effect is True


def test_operations_agent_marks_a_day_off_even_when_nothing_was_scheduled(brewery: Brewery) -> None:
    schedule = Schedule()
    signal = DayOffSignal(day=1, off_day=20)

    had_effect = OperationsAgent().apply(brewery, schedule, signal)

    assert not brewery.is_working_day(20)
    assert had_effect is False


def test_operations_agent_reports_no_effect_when_the_day_was_already_off(brewery: Brewery, recipe: Recipe) -> None:
    schedule = Schedule()
    schedule.add_batch(Batch("001", "B1", recipe, planned_brew_day=20))
    schedule.add_assignment(
        FermentationAssignment(
            batch_id="001",
            fermenter=brewery.fermenters[0],
            volume_hl=10.0,
            start_day=20,
            base_fermentation_days=10,
        )
    )
    brewery.mark_day_off(20)

    had_effect = OperationsAgent().apply(brewery, schedule, DayOffSignal(day=1, off_day=20))

    assert had_effect is False


def test_operations_agent_reports_an_effect_when_work_was_scheduled_on_the_day(
    brewery: Brewery, recipe: Recipe
) -> None:
    schedule = Schedule()
    schedule.add_batch(Batch("001", "B1", recipe, planned_brew_day=20))
    schedule.add_assignment(
        FermentationAssignment(
            batch_id="001",
            fermenter=brewery.fermenters[0],
            volume_hl=10.0,
            start_day=20,
            base_fermentation_days=10,
        )
    )

    had_effect = OperationsAgent().apply(brewery, schedule, DayOffSignal(day=1, off_day=20))

    assert had_effect is True
    assert not brewery.is_working_day(20)


def test_maintenance_agent_marks_the_fermenter_out_of_service(brewery: Brewery) -> None:
    schedule = Schedule()
    signal = FermenterMaintenanceSignal(day=1, fermenter_id="f1", start_day=5, duration_days=3)

    had_effect = MaintenanceAgent().apply(brewery, schedule, signal)

    assert brewery.fermenter_maintenance_windows("f1") == [(5, 8)]
    assert had_effect is True


def test_operations_agent_assess_keeps_an_already_off_day(brewery: Brewery) -> None:
    brewery.mark_day_off(20)
    schedule = Schedule()

    assessment = OperationsAgent().assess(brewery, schedule, DayOffSignal(day=1, off_day=20), current_day=1)

    assert assessment.decision == "KEEP"


def test_operations_agent_assess_keeps_a_day_with_nothing_scheduled(brewery: Brewery) -> None:
    schedule = Schedule()

    assessment = OperationsAgent().assess(brewery, schedule, DayOffSignal(day=1, off_day=20), current_day=1)

    assert assessment.decision == "KEEP"


def test_operations_agent_assess_defers_a_future_collision_until_its_safe_point(
    brewery: Brewery, recipe: Recipe
) -> None:
    schedule = Schedule()
    schedule.add_batch(Batch("001", "B1", recipe, planned_brew_day=20))
    schedule.add_assignment(
        FermentationAssignment(
            batch_id="001",
            fermenter=brewery.fermenters[0],
            volume_hl=10.0,
            start_day=20,
            base_fermentation_days=10,
        )
    )
    signal = DayOffSignal(day=1, off_day=20)

    early = OperationsAgent().assess(brewery, schedule, signal, current_day=1)
    assert early.decision == "PENDING"
    assert early.latest_safe_decision_point == 19

    at_the_wire = OperationsAgent().assess(brewery, schedule, signal, current_day=19)
    assert at_the_wire.decision == "REPLAN"


def test_procurement_agent_assess_keeps_a_shortage_resolved_before_the_brew_day(
    brewery: Brewery, recipe: Recipe
) -> None:
    schedule = Schedule()
    schedule.add_batch(Batch("003", "B3", recipe, planned_brew_day=12))
    schedule.add_assignment(
        FermentationAssignment(
            batch_id="003",
            fermenter=brewery.fermenters[0],
            volume_hl=10.0,
            start_day=12,
            base_fermentation_days=recipe.fermentation_days,
        )
    )
    signal = IngredientShortageSignal(day=1, batch_id="003", available_from_day=9)

    assessment = ProcurementAgent().assess(brewery, schedule, signal, current_day=1)

    assert assessment.decision == "KEEP"


def test_procurement_agent_assess_defers_a_real_shortage_until_the_brew_day(brewery: Brewery, recipe: Recipe) -> None:
    schedule = Schedule()
    schedule.add_batch(Batch("003", "B3", recipe, planned_brew_day=9))
    schedule.add_assignment(
        FermentationAssignment(
            batch_id="003",
            fermenter=brewery.fermenters[0],
            volume_hl=10.0,
            start_day=9,
            base_fermentation_days=recipe.fermentation_days,
        )
    )
    signal = IngredientShortageSignal(day=1, batch_id="003", available_from_day=15)

    last_day_before_the_brew_is_locked = 8

    early = ProcurementAgent().assess(brewery, schedule, signal, current_day=1)
    assert early.decision == "PENDING"
    assert early.latest_safe_decision_point == last_day_before_the_brew_is_locked

    at_the_wire = ProcurementAgent().assess(brewery, schedule, signal, current_day=last_day_before_the_brew_is_locked)
    assert at_the_wire.decision == "REPLAN"


def test_fermentation_agent_assess_keeps_an_early_completion(brewery: Brewery, recipe: Recipe) -> None:
    schedule = Schedule()
    schedule.add_batch(Batch("001", "B1", recipe, planned_brew_day=0))
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
    signal = FermentationDelaySignal(day=4, batch_id="001", fermenter_id="f1", extra_days=-2)

    assessment = FermentationAgent().assess(brewery, schedule, signal, current_day=4)

    assert assessment.decision == "KEEP"


def _early_finish(brewery: Brewery, recipe: Recipe, extra_days: int, **assignment_kwargs):
    schedule = Schedule()
    schedule.add_batch(Batch("001", "B1", recipe, planned_brew_day=0))
    schedule.add_assignment(
        FermentationAssignment(
            batch_id="001",
            fermenter=brewery.fermenters[0],
            volume_hl=10.0,
            start_day=0,
            base_fermentation_days=10,
            locked=True,
            **assignment_kwargs,
        )
    )
    signal = FermentationDelaySignal(day=4, batch_id="001", fermenter_id="f1", extra_days=extra_days)
    return schedule, signal


def test_fermentation_agent_assess_defers_an_early_finish_that_bottles_on_a_day_off(
    brewery: Brewery, recipe: Recipe
) -> None:
    earlier_bottling_day = 7
    brewery.mark_day_off(earlier_bottling_day)
    schedule, signal = _early_finish(brewery, recipe, extra_days=-2)
    FermentationAgent().apply(brewery, schedule, signal)

    early = FermentationAgent().assess(brewery, schedule, signal, current_day=4)
    assert early.decision == "PENDING"
    assert early.latest_safe_decision_point == 6
    assert early.affected_activity == "001"
    assert "non-working" in early.reason

    at_the_wire = FermentationAgent().assess(brewery, schedule, signal, current_day=6)
    assert at_the_wire.decision == "REPLAN"


def test_fermentation_agent_assess_defers_an_early_finish_that_bottles_on_a_full_bottling_day(
    brewery: Brewery, recipe: Recipe
) -> None:
    schedule, signal = _early_finish(brewery, recipe, extra_days=-2)
    schedule.add_batch(Batch("002", "B2", recipe, planned_brew_day=-2))
    schedule.add_assignment(
        FermentationAssignment(
            batch_id="002",
            fermenter=brewery.fermenters[1],
            volume_hl=10.0,
            start_day=-2,
            base_fermentation_days=10,
            locked=True,
        )
    )
    FermentationAgent().apply(brewery, schedule, signal)

    assessment = FermentationAgent().assess(brewery, schedule, signal, current_day=4)

    assert assessment.decision == "PENDING"
    assert assessment.latest_safe_decision_point == 6
    assert "bottling limit" in assessment.reason


def test_fermentation_agent_assess_keeps_an_early_finish_onto_a_free_working_day(
    brewery: Brewery, recipe: Recipe
) -> None:
    original_bottling_day = 9
    brewery.mark_day_off(original_bottling_day)
    schedule, signal = _early_finish(brewery, recipe, extra_days=-2)
    FermentationAgent().apply(brewery, schedule, signal)

    assert FermentationAgent().assess(brewery, schedule, signal, current_day=4).decision == "KEEP"


def test_fermentation_agent_assess_keeps_an_early_finish_with_a_fixed_bottling_day(
    brewery: Brewery, recipe: Recipe
) -> None:
    brewery.mark_day_off(7)
    schedule, signal = _early_finish(brewery, recipe, extra_days=-2, bottling_day=9)
    FermentationAgent().apply(brewery, schedule, signal)

    assert FermentationAgent().assess(brewery, schedule, signal, current_day=4).decision == "KEEP"


def test_fermentation_agent_reassesses_an_early_finish_as_keep_once_the_scheduler_moved_its_bottling(
    brewery: Brewery, recipe: Recipe
) -> None:
    brewery.mark_day_off(7)
    schedule, signal = _early_finish(brewery, recipe, extra_days=-2)
    FermentationAgent().apply(brewery, schedule, signal)

    replanned = HeuristicScheduler().replan(brewery, schedule, 6)

    assert replanned.assignment_for("001", "f1").effective_bottling_day == 8
    assert FermentationAgent().assess(brewery, replanned, signal, current_day=6).decision == "KEEP"


def test_fermentation_agent_assess_keeps_a_later_completion_with_no_collision(brewery: Brewery, recipe: Recipe) -> None:
    schedule = Schedule()
    schedule.add_batch(Batch("001", "B1", recipe, planned_brew_day=0))
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
    signal = FermentationDelaySignal(day=4, batch_id="001", fermenter_id="f1", extra_days=2)

    assessment = FermentationAgent().assess(brewery, schedule, signal, current_day=4)

    assert assessment.decision == "KEEP"


def test_fermentation_agent_assess_ignores_a_collision_with_a_locked_assignment(
    brewery: Brewery, recipe: Recipe
) -> None:
    schedule = Schedule()
    schedule.add_batch(Batch("001", "B1", recipe, planned_brew_day=0))
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
    schedule.add_batch(Batch("002", "B2", recipe, planned_brew_day=9))
    schedule.add_assignment(
        FermentationAssignment(
            batch_id="002",
            fermenter=brewery.fermenters[0],
            volume_hl=10.0,
            start_day=9,
            base_fermentation_days=10,
            locked=True,
        )
    )
    signal = FermentationDelaySignal(day=4, batch_id="001", fermenter_id="f1", extra_days=3)

    assessment = FermentationAgent().assess(brewery, schedule, signal, current_day=4)

    assert assessment.decision == "KEEP"


def test_fermentation_agent_assess_defers_a_collision_with_a_modifiable_allocation(
    brewery: Brewery, recipe: Recipe
) -> None:
    schedule = Schedule()
    schedule.add_batch(Batch("001", "B1", recipe, planned_brew_day=0))
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
    schedule.add_batch(Batch("002", "B2", recipe, planned_brew_day=11))
    schedule.add_assignment(
        FermentationAssignment(
            batch_id="002",
            fermenter=brewery.fermenters[0],
            volume_hl=10.0,
            start_day=11,
            base_fermentation_days=10,
        )
    )
    signal = FermentationDelaySignal(day=4, batch_id="001", fermenter_id="f1", extra_days=3)
    FermentationAgent().apply(brewery, schedule, signal)

    early = FermentationAgent().assess(brewery, schedule, signal, current_day=4)
    assert early.decision == "PENDING"
    assert early.latest_safe_decision_point == 10
    assert early.affected_activity == "002"

    at_the_wire = FermentationAgent().assess(brewery, schedule, signal, current_day=10)
    assert at_the_wire.decision == "REPLAN"


def test_maintenance_agent_assess_keeps_a_window_with_no_future_collision(brewery: Brewery) -> None:
    schedule = Schedule()
    signal = FermenterMaintenanceSignal(day=1, fermenter_id="f1", start_day=5, duration_days=3)

    assessment = MaintenanceAgent().assess(brewery, schedule, signal, current_day=1)

    assert assessment.decision == "KEEP"


def test_maintenance_agent_assess_keeps_a_collision_with_only_a_locked_assignment(
    brewery: Brewery, recipe: Recipe
) -> None:
    schedule = Schedule()
    schedule.add_batch(Batch("001", "B1", recipe, planned_brew_day=0))
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
    signal = FermenterMaintenanceSignal(day=1, fermenter_id="f1", start_day=5, duration_days=3)

    assessment = MaintenanceAgent().assess(brewery, schedule, signal, current_day=1)

    assert assessment.decision == "KEEP"


def test_maintenance_agent_assess_defers_a_collision_with_a_modifiable_allocation(
    brewery: Brewery, recipe: Recipe
) -> None:
    schedule = Schedule()
    schedule.add_batch(Batch("001", "B1", recipe, planned_brew_day=12))
    schedule.add_assignment(
        FermentationAssignment(
            batch_id="001",
            fermenter=brewery.fermenters[0],
            volume_hl=10.0,
            start_day=12,
            base_fermentation_days=10,
        )
    )
    signal = FermenterMaintenanceSignal(day=1, fermenter_id="f1", start_day=10, duration_days=5)

    early = MaintenanceAgent().assess(brewery, schedule, signal, current_day=1)
    assert early.decision == "PENDING"
    assert early.latest_safe_decision_point == 11
    assert early.affected_activity == "001"

    at_the_wire = MaintenanceAgent().assess(brewery, schedule, signal, current_day=11)
    assert at_the_wire.decision == "REPLAN"


def test_sales_agent_assess_defers_a_new_order_until_the_day_before_its_requested_brew_day(
    brewery: Brewery, recipe: Recipe
) -> None:
    batch = Batch("005", "B5", recipe, planned_brew_day=10, target_bottling_day=25)
    signal = NewBatchRequirementSignal(day=1, batch=batch)

    early = SalesAgent().assess(brewery, Schedule(), signal, current_day=1)
    assert early.decision == "PENDING"
    assert early.latest_safe_decision_point == 9

    at_the_wire = SalesAgent().assess(brewery, Schedule(), signal, current_day=9)
    assert at_the_wire.decision == "REPLAN"


def test_sales_agent_assess_replans_an_order_arriving_after_its_decision_day(brewery: Brewery, recipe: Recipe) -> None:
    batch = Batch("005", "B5", recipe, planned_brew_day=4, target_bottling_day=20)
    signal = NewBatchRequirementSignal(day=6, batch=batch)

    assessment = SalesAgent().assess(brewery, Schedule(), signal, current_day=6)

    assert (assessment.decision, assessment.latest_safe_decision_point) == ("REPLAN", 3)
