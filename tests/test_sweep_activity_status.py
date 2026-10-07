from collections import Counter

import pytest

from brewery_agentic_replanning.agents import FermentationAgent, MaintenanceAgent, OperationsAgent
from brewery_agentic_replanning.signals import DayOffSignal, FermentationDelaySignal, FermenterMaintenanceSignal
from experiments import exp2, exp2c, exp2d
from experiments import exp2_assessment_check as check
from experiments.config import load_config

CFG = load_config("conf1")
RECIPES = CFG.build_recipes()
BREWERY, BASELINE = CFG.baseline()


def _maintenance_signals() -> list[FermenterMaintenanceSignal]:
    return [
        FermenterMaintenanceSignal(day=day, fermenter_id=f, start_day=start, duration_days=duration)
        for day, f, start, duration in exp2d.combinations(BREWERY)
    ]


def _day_off_signals_replanned_on_arrival() -> list[DayOffSignal]:
    return [DayOffSignal(day=s, off_day=off) for s, off, replan in exp2.combinations() if replan == s]


def _only_started_maintenance_collisions(signal: FermenterMaintenanceSignal) -> bool:
    overlapping = [
        a
        for a in BASELINE.assignments_on_fermenter(signal.fermenter_id)
        if a.start_day < signal.start_day + signal.duration_days and signal.start_day < a.end_day
    ]
    return bool(overlapping) and all(a.start_day <= signal.day for a in overlapping)


def _only_started_day_off_collisions(signal: DayOffSignal) -> bool:
    touching = [
        a for a in BASELINE.assignments if a.start_day == signal.off_day or a.effective_bottling_day == signal.off_day
    ]
    return bool(touching) and all(a.start_day == signal.off_day and a.start_day <= signal.day for a in touching)


def _verdict(agent_type, signal) -> str:
    return check.verdict(CFG, agent_type, signal).decision


def _replan_effect(signal) -> str:
    return check.replan_effect(CFG, RECIPES, signal)


MAINTENANCE_CASES = [s for s in _maintenance_signals() if _only_started_maintenance_collisions(s)]
DAY_OFF_CASES = [s for s in _day_off_signals_replanned_on_arrival() if _only_started_day_off_collisions(s)]


def test_the_sweep_holds_exactly_the_cases_the_diagnostic_found() -> None:
    assert len(MAINTENANCE_CASES) == 5524
    assert [(s.day, s.off_day) for s in DAY_OFF_CASES] == [(0, 0), (4, 4), (11, 11)]


def test_all_5524_maintenance_cases_are_keep_and_replanning_changes_nothing() -> None:
    wrong = [s for s in MAINTENANCE_CASES if _verdict(MaintenanceAgent, s) != "KEEP"]
    moved = [s for s in MAINTENANCE_CASES if _replan_effect(s) != "unchanged"]
    assert not wrong, wrong[:5]
    assert not moved, moved[:5]


@pytest.mark.parametrize("signal", DAY_OFF_CASES, ids=lambda s: f"signal{s.day}-off{s.off_day}")
def test_each_day_off_case_is_keep_and_replanning_changes_nothing(signal: DayOffSignal) -> None:
    assert _verdict(OperationsAgent, signal) == "KEEP"
    assert _replan_effect(signal) == "unchanged"


@pytest.mark.parametrize(
    "day, fermenter_id, start_day, duration_days, ongoing",
    [
        pytest.param(0, "f1", 0, 1, "004", id="one day, on the day 004 was brewed"),
        pytest.param(0, "f4", 0, 1, "001", id="001 brewed on day -8, still fermenting"),
        pytest.param(0, "f2", 0, 11, "005", id="005 brewed on day -2, window ends as it does"),
        pytest.param(2, "f1", 2, 14, "004", id="two weeks over 004, which runs to day 16"),
        pytest.param(4, "f4", 4, 12, "007", id="announced on the day 007 is brewed"),
        pytest.param(9, "f5", 9, 14, "008", id="split batch 008, tank f5"),
        pytest.param(9, "f6", 9, 14, "008", id="split batch 008, tank f6"),
        pytest.param(10, "f3", 10, 14, "010", id="010 brewed on the day the window opens"),
        pytest.param(13, "f7", 13, 14, "003", id="003 (brewed on day -5) is a long barleywine"),
    ],
)
def test_representative_maintenance_cases(
    day: int, fermenter_id: str, start_day: int, duration_days: int, ongoing: str
) -> None:
    signal = FermenterMaintenanceSignal(
        day=day, fermenter_id=fermenter_id, start_day=start_day, duration_days=duration_days
    )
    assert signal in MAINTENANCE_CASES
    assert any(a.batch_id == ongoing for a in BASELINE.assignments_on_fermenter(fermenter_id))
    assert _verdict(MaintenanceAgent, signal) == "KEEP"
    assert _replan_effect(signal) == "unchanged"


def test_no_other_maintenance_or_day_off_signal_became_keep() -> None:
    keep = {s for s in _maintenance_signals() if _verdict(MaintenanceAgent, s) == "KEEP"}
    assert keep == set(MAINTENANCE_CASES)


def test_no_day_off_keep_is_a_false_keep() -> None:
    keeps = [s for s in _day_off_signals_replanned_on_arrival() if _verdict(OperationsAgent, s) == "KEEP"]
    assert len(keeps) == 56
    assert all(_replan_effect(s) == "unchanged" for s in keeps)


def _early_finishes() -> list[FermentationDelaySignal]:
    return [
        FermentationDelaySignal(day=day, batch_id=batch_id, fermenter_id=fermenter_id, extra_days=extra_days)
        for day, batch_id, fermenter_id, extra_days in exp2c.combinations(BASELINE)
        if extra_days < 0
    ]


def _bottles_on_a_day_off(signal: FermentationDelaySignal) -> bool:
    assignment = BASELINE.assignment_for(signal.batch_id, signal.fermenter_id)
    return assignment.bottling_day is None and not BREWERY.is_working_day(
        assignment.last_fermentation_day + signal.extra_days
    )


EARLY_BOTTLING_CASES = [s for s in _early_finishes() if _bottles_on_a_day_off(s)]


def test_the_70_early_finishes_onto_a_day_off_act_before_the_bottling_day() -> None:
    assert len(EARLY_BOTTLING_CASES) == 70
    decisions: Counter[str] = Counter()
    for signal in EARLY_BOTTLING_CASES:
        assessment = check.verdict(CFG, FermentationAgent, signal)
        assignment = BASELINE.assignment_for(signal.batch_id, signal.fermenter_id)
        assert assessment.latest_safe_decision_point == assignment.last_fermentation_day + signal.extra_days - 1
        assert assessment.affected_activity == signal.batch_id
        assert _replan_effect(signal) == "changed"
        decisions[assessment.decision] += 1
    assert decisions == {"PENDING": 60, "REPLAN": 10}


@pytest.mark.parametrize(
    "day, batch_id, fermenter_id, extra_days, decision, safe_day",
    [
        pytest.param(0, "002", "f5", -1, "PENDING", 6, id="bottling 8 -> 7, a day off; split batch 002, tank f5"),
        pytest.param(0, "002", "f6", -1, "PENDING", 6, id="bottling 8 -> 7, a day off; split batch 002, tank f6"),
        pytest.param(6, "002", "f5", -1, "REPLAN", 6, id="same finish learned on the safe day itself"),
    ],
)
def test_representative_early_finishes_onto_a_day_off(
    day: int, batch_id: str, fermenter_id: str, extra_days: int, decision: str, safe_day: int
) -> None:
    signal = FermentationDelaySignal(day=day, batch_id=batch_id, fermenter_id=fermenter_id, extra_days=extra_days)
    assert signal in EARLY_BOTTLING_CASES
    assessment = check.verdict(CFG, FermentationAgent, signal)
    assert (assessment.decision, assessment.latest_safe_decision_point) == (decision, safe_day)
    assert _replan_effect(signal) == "changed"


def test_every_other_early_finish_is_still_keep_and_changes_nothing() -> None:
    others = [s for s in _early_finishes() if s not in EARLY_BOTTLING_CASES]
    assert others
    assert all(_verdict(FermentationAgent, s) == "KEEP" for s in others)
    assert all(_replan_effect(s) == "unchanged" for s in others)


def test_no_fermentation_keep_is_a_false_keep() -> None:
    signals = [
        FermentationDelaySignal(day=day, batch_id=batch_id, fermenter_id=fermenter_id, extra_days=extra_days)
        for day, batch_id, fermenter_id, extra_days in exp2c.combinations(BASELINE)
    ]
    keeps = [s for s in signals if _verdict(FermentationAgent, s) == "KEEP"]
    assert len(keeps) == 192
    assert all(_replan_effect(s) == "unchanged" for s in keeps)
