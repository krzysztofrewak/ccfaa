from brewery_agentic_replanning.policies import (
    CoordinatedPolicy,
    LocallyFilteredPolicy,
    ManualPolicy,
    UncoordinatedPolicy,
)


def test_uncoordinated_policy_always_replans_immediately() -> None:
    policy = UncoordinatedPolicy()
    assert policy.always_consider_signals
    assert policy.should_replan_immediately(day=1)
    assert not policy.should_replan_end_of_day(day=1)


def test_locally_filtered_policy_never_replans_without_an_assessed_signal() -> None:
    policy = LocallyFilteredPolicy()
    policy.register_signal(day=1)
    assert not policy.should_replan_immediately(day=1)
    assert not policy.should_replan_end_of_day(day=1)


def test_coordinating_policy_never_replans_without_an_active_signal() -> None:
    policy = CoordinatedPolicy()
    assert not policy.should_replan_immediately(day=1)
    assert not policy.should_replan_end_of_day(day=1)


def test_manual_policy_never_replans_on_its_own() -> None:
    policy = ManualPolicy()
    policy.register_signal(day=1)
    assert not policy.should_replan_immediately(day=1)
    assert not policy.should_replan_end_of_day(day=1)
