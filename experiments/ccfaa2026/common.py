from __future__ import annotations

import csv
from collections.abc import Sequence
from pathlib import Path

from brewery_schedule_digital_twin.agents import default_agents
from brewery_schedule_digital_twin.domain import Recipe, Schedule
from brewery_schedule_digital_twin.metrics import compute_metrics
from brewery_schedule_digital_twin.policies import ManualPolicy
from brewery_schedule_digital_twin.scheduling import HeuristicScheduler
from brewery_schedule_digital_twin.signals import Signal
from brewery_schedule_digital_twin.simulation import BreweryDigitalTwin
from experiments.ccfaa2026.config import ExperimentConfig

METRIC_COLUMNS = (
    "plan_changed",
    "changed_batches",
    "net_schedule_shift",
    "idle_fermenter_days",
    "shifted_brews",
    "shifted_bottlings",
)


def manual_twin(
    cfg: ExperimentConfig, recipes: dict[str, Recipe], signals: Sequence[Signal], day: int
) -> BreweryDigitalTwin:
    brewery = cfg.build_brewery()
    twin = BreweryDigitalTwin(
        brewery=brewery,
        schedule=cfg.declare_initial_schedule(brewery, recipes),
        scheduler=HeuristicScheduler(),
        policy=ManualPolicy(),
        agents=default_agents(),
    )
    for signal in signals:
        twin.inject_signal(signal)
    twin.advance_to(day)
    return twin


def metric_columns(twin: BreweryDigitalTwin) -> dict:
    metrics = compute_metrics(twin.brewery, twin.original_schedule, twin.schedule, twin.current_day)
    return {
        "plan_changed": metrics.changed_batches > 0,
        "changed_batches": metrics.changed_batches,
        "net_schedule_shift": metrics.schedule_shift_days,
        "idle_fermenter_days": metrics.idle_fermenter_days,
        "shifted_brews": metrics.shifted_brews,
        "shifted_bottlings": metrics.shifted_bottlings,
    }


def hl(volume: float) -> float:
    """A volume in litres as the hectolitres the outputs report; whole hectolitres given as an int stay an int."""
    return volume // 100 if isinstance(volume, int) and volume % 100 == 0 else volume / 100


def placements(schedule: Schedule) -> set[tuple]:
    return {
        (a.batch_id, a.fermenter.id, hl(a.volume), a.start_day, a.effective_bottling_day, a.end_day)
        for a in schedule.assignments
    }


def write_csv(path: Path, rows: Sequence[dict], fieldnames: Sequence[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(fieldnames or rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"{len(rows)} rows -> {path}")


def report_sweep(rows: Sequence[dict]) -> None:
    changed = sum(1 for row in rows if row["plan_changed"])
    print(f"{changed}/{len(rows)} combinations changed the plan.")
