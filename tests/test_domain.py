import pytest

from brewery_agentic_replanning.domain import Brewery, Fermenter, Recipe


def test_recipe_rejects_non_positive_fermentation_days() -> None:
    with pytest.raises(ValueError):
        Recipe(name="Broken", fermentation_days=0)


def test_fermenter_id_combines_name_and_index() -> None:
    fermenter = Fermenter("f", 3, 10.0)
    assert fermenter.id == "f3"


def test_fermenter_rejects_non_positive_capacity() -> None:
    with pytest.raises(ValueError):
        Fermenter("f", 1, 0.0)


def test_brewery_fermenter_by_id(brewery: Brewery) -> None:
    assert brewery.fermenter_by_id("f1").index == 1
    with pytest.raises(KeyError):
        brewery.fermenter_by_id("missing")


def test_brewery_days_off(brewery: Brewery) -> None:
    assert brewery.is_working_day(5)
    brewery.mark_day_off(5)
    assert not brewery.is_working_day(5)


def test_brewery_rejects_invalid_throughput(brewery: Brewery) -> None:
    with pytest.raises(ValueError):
        Brewery(
            name="Broken",
            fermenters=brewery.fermenters,
            kettle_yield_hl=10.0,
            max_brews_per_day=0,
            max_bottling_runs_per_day=1,
        )


def test_brewery_fermenter_maintenance_windows(brewery: Brewery) -> None:
    assert brewery.fermenter_maintenance_windows("f1") == []

    brewery.mark_fermenter_maintenance("f1", start_day=5, duration_days=3)

    assert brewery.fermenter_maintenance_windows("f1") == [(5, 8)]
    assert brewery.fermenter_maintenance_windows("f2") == []


def test_brewery_rejects_non_positive_maintenance_duration(brewery: Brewery) -> None:
    with pytest.raises(ValueError):
        brewery.mark_fermenter_maintenance("f1", start_day=5, duration_days=0)
