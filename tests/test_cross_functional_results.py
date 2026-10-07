import pytest

from brewery_agentic_replanning.metrics import compute_metrics
from experiments import exp3_cross_functional as exp3
from experiments.config import load_config

REPLAN_DAYS = {
    "CF1": {"UNC": [4, 6], "LF": [6], "COORD": [9]},
    "CF2": {"UNC": [4, 6], "LF": [4, 6], "COORD": [9]},
    "CF3": {"UNC": [4, 5, 6], "LF": [4, 5], "COORD": [9]},
    "CF4": {"UNC": [8, 9], "LF": [8, 9], "COORD": [9]},
}

FINAL_SCHEDULES = {
    ("conf1", "CF1"): ((3, 23, 6, 0), (3, 23, 6, 0)),
    ("conf1", "CF2"): ((12, 125, 41, 7), (11, 106, 58, 3)),
    ("conf1", "CF3"): ((12, 156, 49, 14), (12, 142, 45, 12)),
    ("conf1", "CF4"): ((13, 117, 38, 7), (13, 117, 38, 7)),
    ("conf2", "CF1"): ((1, 11, 5, 0), (1, 11, 5, 0)),
    ("conf2", "CF2"): ((2, 11, 26, 0), (2, 11, 26, 0)),
    ("conf2", "CF3"): ((3, 20, 30, 5), (3, 20, 30, 5)),
    ("conf2", "CF4"): ((3, 9, 23, 0), (3, 9, 23, 0)),
    ("conf3", "CF1"): ((2, 23, 6, 0), (2, 23, 6, 0)),
    ("conf3", "CF2"): ((12, 131, 62, 7), (12, 84, 38, 3)),
    ("conf3", "CF3"): ((12, 135, 56, 9), (12, 148, 66, 12)),
    ("conf3", "CF4"): ((12, 89, 38, 0), (12, 89, 38, 0)),
}


@pytest.mark.parametrize("policy_name", list(exp3.POLICY_FACTORIES))
@pytest.mark.parametrize("config_name, scenario_name", list(FINAL_SCHEDULES))
def test_cross_functional_run_matches_the_recorded_results(
    config_name: str, scenario_name: str, policy_name: str
) -> None:
    cfg = load_config(config_name)
    twin, _ = exp3.build_twin(cfg, scenario_name, policy_name, cfg.build_recipes())
    twin.advance_to(exp3.END_DAY)

    metrics = compute_metrics(twin.brewery, twin.original_schedule, twin.schedule, twin.current_day)
    outcome = (
        metrics.changed_batches,
        metrics.total_brew_shift_days + metrics.total_bottling_shift_days,
        metrics.schedule_shift_days,
        metrics.idle_fermenter_days,
    )
    event_driven, coordinated = FINAL_SCHEDULES[(config_name, scenario_name)]
    assert [event.day for event in twin.replan_log] == REPLAN_DAYS[scenario_name][policy_name]
    assert outcome == (coordinated if policy_name == "COORD" else event_driven)
