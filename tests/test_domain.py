import math

import pytest

from brewery_schedule_digital_twin.domain import BottlingLine, Brewery, Brewhouse, Recipe, Vessel, VesselRole


def test_recipe_rejects_non_positive_fermentation_days() -> None:
    with pytest.raises(ValueError):
        Recipe(name="Broken", fermentation_days=0)


def test_recipe_stage_times_are_optional_and_independent() -> None:
    recipe = Recipe(name="Lager", fermentation_days=21, primary_fermentation_days=10, secondary_fermentation_days=28)
    assert recipe.fermentation_days == 21
    assert recipe.primary_fermentation_days == 10
    assert recipe.secondary_fermentation_days == 28

    unknown = Recipe(name="Ale", fermentation_days=14)
    assert unknown.primary_fermentation_days is None
    assert unknown.secondary_fermentation_days is None


@pytest.mark.parametrize("stage_days", [{"primary_fermentation_days": 0}, {"secondary_fermentation_days": -1}])
def test_recipe_rejects_non_positive_stage_days(stage_days: dict[str, int]) -> None:
    with pytest.raises(ValueError):
        Recipe(name="Broken", fermentation_days=14, **stage_days)


def test_vessel_id_combines_name_and_index() -> None:
    vessel = Vessel.unitank("f", 3, 10.0)
    assert vessel.id == "f3"


def test_vessel_rejects_non_positive_capacity() -> None:
    with pytest.raises(ValueError):
        Vessel.unitank("f", 1, 0.0)


def test_vessel_rejects_empty_roles() -> None:
    with pytest.raises(ValueError):
        Vessel("v", 1, 10.0, frozenset())


@pytest.mark.parametrize(
    ("vessel", "roles"),
    [
        (Vessel.unitank("ut", 1, 10.0), {VesselRole.FULL}),
        (Vessel.fermenter("fv", 1, 10.0), {VesselRole.PRIMARY}),
        (Vessel.lagering_tank("lt", 1, 10.0), {VesselRole.SECONDARY}),
        (Vessel.general_vessel("gv", 1, 10.0), {VesselRole.PRIMARY, VesselRole.SECONDARY}),
    ],
)
def test_named_constructors_set_roles(vessel: Vessel, roles: set[VesselRole]) -> None:
    assert vessel.roles == roles
    assert all(vessel.has_role(role) is (role in roles) for role in VesselRole)


def test_vessel_accepts_any_combination_of_roles() -> None:
    vessel = Vessel("b", 1, 0.2, frozenset(VesselRole))
    assert vessel.roles == frozenset(VesselRole)


def test_vessel_with_the_same_roles_equals_one_from_a_named_constructor() -> None:
    assert Vessel("ut", 1, 10.0, frozenset({VesselRole.FULL})) == Vessel.unitank("ut", 1, 10.0)


def test_brewery_lists_vessels_with_each_role() -> None:
    unitank = Vessel.unitank("ut", 1, 10.0)
    fermenter = Vessel.fermenter("fv", 1, 10.0)
    lagering_tank = Vessel.lagering_tank("lt", 1, 20.0)
    general = Vessel.general_vessel("gv", 1, 5.0)
    brewery = Brewery(
        name="Mixed Brewery",
        vessels=[unitank, fermenter, lagering_tank, general],
        brewhouses=[Brewhouse("brewhouse", yield_l=1_000, max_brews_per_day=1)],
        bottling_lines=[BottlingLine("line", run_volume_l=math.inf, max_runs_per_day=1)],
    )

    assert brewery.vessels_with(VesselRole.FULL) == [unitank]
    assert brewery.vessels_with(VesselRole.PRIMARY) == [fermenter, general]
    assert brewery.vessels_with(VesselRole.SECONDARY) == [lagering_tank, general]


def test_brewery_rejects_duplicate_vessel_ids() -> None:
    with pytest.raises(ValueError):
        Brewery(
            name="Broken",
            vessels=[Vessel.fermenter("t", 1, 10.0), Vessel.lagering_tank("t", 1, 10.0)],
            brewhouses=[Brewhouse("brewhouse", yield_l=1_000, max_brews_per_day=1)],
            bottling_lines=[BottlingLine("line", run_volume_l=math.inf, max_runs_per_day=1)],
        )


def test_brewery_vessel_by_id(brewery: Brewery) -> None:
    assert brewery.vessel_by_id("f1").index == 1
    with pytest.raises(KeyError):
        brewery.vessel_by_id("missing")


def test_brewery_days_off(brewery: Brewery) -> None:
    assert brewery.is_working_day(5)
    brewery.mark_day_off(5)
    assert not brewery.is_working_day(5)


def test_brewery_rejects_invalid_throughput(brewery: Brewery) -> None:
    with pytest.raises(ValueError):
        Brewery(
            name="Broken",
            vessels=brewery.vessels,
            brewhouses=[Brewhouse("brewhouse", yield_l=1_000, max_brews_per_day=0)],
            bottling_lines=[BottlingLine("line", run_volume_l=math.inf, max_runs_per_day=1)],
        )


def test_brewery_fermenter_maintenance_windows(brewery: Brewery) -> None:
    assert brewery.fermenter_maintenance_windows("f1") == []

    brewery.mark_fermenter_maintenance("f1", start_day=5, duration_days=3)

    assert brewery.fermenter_maintenance_windows("f1") == [(5, 8)]
    assert brewery.fermenter_maintenance_windows("f2") == []


def test_brewery_rejects_non_positive_maintenance_duration(brewery: Brewery) -> None:
    with pytest.raises(ValueError):
        brewery.mark_fermenter_maintenance("f1", start_day=5, duration_days=0)
