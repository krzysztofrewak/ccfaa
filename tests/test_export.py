import json
from pathlib import Path

from brewery_agentic_replanning.domain import Batch, Brewery, FermentationAssignment, Fermenter, Recipe, Schedule
from brewery_agentic_replanning.export import CalendarExporter
from brewery_agentic_replanning.signals import DayOffSignal, FermentationDelaySignal


def test_export_writes_expected_schema(tmp_path: Path, recipe: Recipe) -> None:
    brewery = Brewery(
        name="Export Brewery",
        fermenters=[Fermenter("f", 1, 10.0)],
        kettle_yield_hl=10.0,
        max_brews_per_day=1,
        max_bottling_runs_per_day=1,
    )
    (fermenter,) = brewery.fermenters
    brewery.mark_day_off(9)

    original = Schedule()
    original.add_batch(Batch("001", "B1", recipe, planned_brew_day=0))
    original.add_assignment(
        FermentationAssignment(
            batch_id="001", fermenter=fermenter, volume_hl=10.0, start_day=0, base_fermentation_days=10
        )
    )

    replanned = Schedule()
    replanned.add_batch(Batch("001", "B1", recipe, planned_brew_day=0))
    replanned.add_assignment(
        FermentationAssignment(
            batch_id="001", fermenter=fermenter, volume_hl=10.0, start_day=2, base_fermentation_days=10
        )
    )

    signals = [
        FermentationDelaySignal(day=1, batch_id="001", fermenter_id="f1", extra_days=2),
        DayOffSignal(day=3, off_day=9),
    ]

    destination = tmp_path / "nested" / "calendar.json"
    CalendarExporter().export(destination, brewery, original, replanned, current_day=0, signals=signals, replan_count=2)

    payload = json.loads(destination.read_text())
    assert payload["current_day"] == 0
    assert payload["start_day"] == 0
    assert payload["horizon_days"] == 12
    assert payload["days_off"] == [9]
    assert payload["original_days_off"] == [9]
    assert payload["replan_count"] == 2
    assert payload["metrics"]["changed_batches"] == 1

    fermenter_payload = payload["fermenters"][0]
    assert fermenter_payload["name"] == "f"
    assert fermenter_payload["index"] == 1
    assert fermenter_payload["capacity"] == "10hl"
    assert len(fermenter_payload["original_slots"]) == 12
    assert fermenter_payload["original_slots"][0] == "001"
    assert fermenter_payload["new_slots"][0] is None
    assert fermenter_payload["new_slots"][2] == "001"
    assert fermenter_payload["changed_slots"][0] is True
    assert fermenter_payload["changed_slots"][5] is False

    assert fermenter_payload["original_status"][0] == "ongoing"
    assert fermenter_payload["new_status"][2] == "planned"

    assert fermenter_payload["original_brew_starts"][0] is True
    assert fermenter_payload["new_brew_starts"][2] is True
    assert not any(fermenter_payload["new_brew_starts"][:2])

    batch_payload = payload["batches"][0]
    assert batch_payload["id"] == "001"
    assert batch_payload["original"] == [{"fermenter": "f1", "start": 0, "end": 10}]
    assert batch_payload["new"] == [{"fermenter": "f1", "start": 2, "end": 12}]
    assert batch_payload["changed"] is True

    assert [s["day"] for s in payload["signals"]] == [1, 3]
    assert payload["signals"][0]["kind"] == "FermentationDelaySignal"
    assert payload["signals"][0]["related_batch_id"] == "001"
    assert payload["signals"][1]["related_batch_id"] is None


def test_export_keeps_original_days_off_separate_from_current(tmp_path: Path, recipe: Recipe) -> None:
    brewery = Brewery(
        name="Export Brewery",
        fermenters=[Fermenter("f", 1, 10.0)],
        kettle_yield_hl=10.0,
        max_brews_per_day=1,
        max_bottling_runs_per_day=1,
        days_off={7},
    )
    brewery.mark_day_off(9)
    schedule = Schedule()
    destination = tmp_path / "calendar.json"

    CalendarExporter().export(destination, brewery, schedule, schedule, current_day=0, original_days_off={7})

    payload = json.loads(destination.read_text())
    assert payload["original_days_off"] == [7]
    assert payload["days_off"] == [7, 9]


def test_export_calendar_never_shows_days_before_zero(tmp_path: Path, recipe: Recipe) -> None:
    brewery = Brewery(
        name="Export Brewery",
        fermenters=[Fermenter("f", 1, 10.0)],
        kettle_yield_hl=10.0,
        max_brews_per_day=1,
        max_bottling_runs_per_day=1,
    )
    (fermenter,) = brewery.fermenters

    schedule = Schedule()
    schedule.add_batch(Batch("001", "B1", recipe, planned_brew_day=-5))
    schedule.add_assignment(
        FermentationAssignment(
            batch_id="001",
            fermenter=fermenter,
            volume_hl=10.0,
            start_day=-5,
            base_fermentation_days=10,
            locked=True,
        )
    )

    destination = tmp_path / "calendar.json"
    CalendarExporter().export(destination, brewery, schedule, schedule, current_day=0)

    payload = json.loads(destination.read_text())
    assert payload["start_day"] == 0
    assert payload["horizon_days"] == 5

    fermenter_payload = payload["fermenters"][0]
    assert fermenter_payload["original_slots"][0] == "001"
    assert fermenter_payload["original_status"][0] == "ongoing"
    assert fermenter_payload["original_slots"][-1] == "001"

    assert not any(fermenter_payload["original_brew_starts"])

    batch_payload = payload["batches"][0]
    assert batch_payload["original"] == [{"fermenter": "f1", "start": -5, "end": 5}]


def test_export_lists_every_tank_a_batch_used_on_one_row(tmp_path: Path, recipe: Recipe) -> None:
    brewery = Brewery(
        name="Export Brewery",
        fermenters=[Fermenter("f", 1, 5.0), Fermenter("f", 2, 5.0)],
        kettle_yield_hl=10.0,
        max_brews_per_day=1,
        max_bottling_runs_per_day=2,
    )
    f1, f2 = brewery.fermenters

    schedule = Schedule()
    schedule.add_batch(Batch("001", "B1", recipe, planned_brew_day=0))
    schedule.add_assignment(
        FermentationAssignment(batch_id="001", fermenter=f1, volume_hl=5.0, start_day=0, base_fermentation_days=10)
    )
    schedule.add_assignment(
        FermentationAssignment(
            batch_id="001",
            fermenter=f2,
            volume_hl=5.0,
            start_day=0,
            base_fermentation_days=10,
            extra_delay_days=3,
        )
    )

    destination = tmp_path / "calendar.json"
    CalendarExporter().export(destination, brewery, schedule, schedule, current_day=0)

    payload = json.loads(destination.read_text())
    rows = [row for row in payload["batches"] if row["id"] == "001"]
    assert len(rows) == 1

    assert rows[0]["original"] == [
        {"fermenter": "f1", "start": 0, "end": 10},
        {"fermenter": "f2", "start": 0, "end": 13},
    ]
    assert rows[0]["new"] == rows[0]["original"]
    assert rows[0]["changed"] is False


def test_export_flags_a_batch_that_moved_to_a_different_tank(tmp_path: Path, recipe: Recipe) -> None:
    brewery = Brewery(
        name="Export Brewery",
        fermenters=[Fermenter("f", 1, 10.0), Fermenter("f", 2, 10.0)],
        kettle_yield_hl=10.0,
        max_brews_per_day=1,
        max_bottling_runs_per_day=1,
    )
    f1, f2 = brewery.fermenters

    original = Schedule()
    original.add_batch(Batch("001", "B1", recipe, planned_brew_day=0))
    original.add_assignment(
        FermentationAssignment(batch_id="001", fermenter=f1, volume_hl=10.0, start_day=0, base_fermentation_days=10)
    )

    replanned = Schedule()
    replanned.add_batch(Batch("001", "B1", recipe, planned_brew_day=0))
    replanned.add_assignment(
        FermentationAssignment(batch_id="001", fermenter=f2, volume_hl=10.0, start_day=5, base_fermentation_days=10)
    )

    destination = tmp_path / "calendar.json"
    CalendarExporter().export(destination, brewery, original, replanned, current_day=0)

    payload = json.loads(destination.read_text())
    rows = [row for row in payload["batches"] if row["id"] == "001"]
    assert len(rows) == 1

    assert rows[0]["original"] == [{"fermenter": "f1", "start": 0, "end": 10}]
    assert rows[0]["new"] == [{"fermenter": "f2", "start": 5, "end": 15}]
    assert rows[0]["changed"] is True


def test_export_defaults_to_no_signals(tmp_path: Path, recipe: Recipe) -> None:
    brewery = Brewery(
        name="Export Brewery",
        fermenters=[Fermenter("f", 1, 10.0)],
        kettle_yield_hl=10.0,
        max_brews_per_day=1,
        max_bottling_runs_per_day=1,
    )
    schedule = Schedule()
    destination = tmp_path / "calendar.json"

    CalendarExporter().export(destination, brewery, schedule, schedule, current_day=0)

    payload = json.loads(destination.read_text())
    assert payload["signals"] == []
    assert payload["replan_count"] == 0
