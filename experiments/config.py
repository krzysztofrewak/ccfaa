"""Named brewery configurations.

A configuration is a module ``experiments/configs/<name>.py``. Every
experiment takes ``--config <name>`` (default: conf1) and writes its
outputs to ``experiments/results/<name>/``.

A configuration module defines:

    INITIAL_ASSIGNMENTS      the baseline calendar, rows of (batch_id,
                             recipe_key, fermenter_id, brew_day, volume_hl
                             [, fermentation_days])
    build_brewery()          -> Brewery
    build_recipes()          -> dict[str, Recipe]

and, for the experiments that inject hand-written signals:

    exp0_events(recipes)     -> list[Signal]
    exp1_scenarios(recipes)  -> list[Signal]
    CF_SCENARIOS             {"CF1": fn(recipes) -> list[Signal], ...}
    CF_TARGETS               {"CF1": (batch_id, target_bottling_day) | None, ...}
    CF_ADAPTATIONS           optional, {"CF3-S1": "why this signal differs
                             from conf1", ...}
"""

from __future__ import annotations

import argparse
import importlib
import pkgutil
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from brewery_agentic_replanning.domain import Batch, Brewery, FermentationAssignment, Recipe, Schedule

EXPERIMENTS_DIR = Path(__file__).resolve().parent
PROJECT_DIR = EXPERIMENTS_DIR.parent
RESULTS_DIR = EXPERIMENTS_DIR / "results"
DEFAULT_CONFIG = "conf1"
_REQUIRED = ("INITIAL_ASSIGNMENTS", "build_brewery", "build_recipes")


class ConfigError(Exception):
    pass


def available_configs() -> list[str]:
    return sorted(module.name for module in pkgutil.iter_modules([str(EXPERIMENTS_DIR / "configs")]))


def declare_initial_schedule(
    brewery: Brewery, recipes: dict[str, Recipe], initial_assignments: list[tuple]
) -> Schedule:
    schedule = Schedule()
    for row in initial_assignments:
        batch_id, recipe_key, fermenter_id, brew_day, volume_hl, *fermentation_days_override = row
        recipe = recipes[recipe_key]
        if batch_id not in schedule.batches:
            schedule.add_batch(Batch(batch_id, f"{recipe.name} #{batch_id}", recipe, planned_brew_day=brew_day))
        schedule.add_assignment(
            FermentationAssignment(
                batch_id=batch_id,
                fermenter=brewery.fermenter_by_id(fermenter_id),
                volume_hl=volume_hl,
                start_day=brew_day,
                base_fermentation_days=(
                    fermentation_days_override[0] if fermentation_days_override else recipe.fermentation_days
                ),
            )
        )

    problems = schedule.validate(brewery)
    if problems:
        raise ValueError("invalid INITIAL_ASSIGNMENTS:\n- " + "\n- ".join(problems))
    return schedule


@dataclass(frozen=True)
class ExperimentConfig:
    name: str
    module: ModuleType

    def build_brewery(self) -> Brewery:
        return self.module.build_brewery()

    def build_recipes(self) -> dict[str, Recipe]:
        return self.module.build_recipes()

    def declare_initial_schedule(self, brewery: Brewery, recipes: dict[str, Recipe]) -> Schedule:
        return declare_initial_schedule(brewery, recipes, self.module.INITIAL_ASSIGNMENTS)

    def baseline(self) -> tuple[Brewery, Schedule]:
        brewery = self.build_brewery()
        return brewery, self.declare_initial_schedule(brewery, self.build_recipes())

    def hook(self, name: str) -> Any:
        if not hasattr(self.module, name):
            raise SystemExit(f"error: configuration {self.name!r} does not define {name!r}")
        return getattr(self.module, name)

    @property
    def results_dir(self) -> Path:
        return RESULTS_DIR / self.name

    def results_path(self, *parts: str) -> Path:
        path = self.results_dir.joinpath(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def calendar_path(self) -> Path:
        return PROJECT_DIR / "public" / "data" / self.name / "calendar.json"


def load_config(name: str) -> ExperimentConfig:
    known = ", ".join(available_configs()) or "none"
    if not name.isidentifier():
        raise ConfigError(f"invalid configuration name {name!r} (available: {known})")
    module_name = f"experiments.configs.{name}"
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as error:
        if error.name != module_name:
            raise
        raise ConfigError(f"unknown configuration {name!r} (available: {known})") from None

    missing = [attribute for attribute in _REQUIRED if not hasattr(module, attribute)]
    if missing:
        raise ConfigError(f"configuration {name!r} is missing: {', '.join(missing)}")
    return ExperimentConfig(name=name, module=module)


def argument_parser(description: str | None) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "--config",
        default=DEFAULT_CONFIG,
        help=f"brewery configuration from experiments/configs/ (default: {DEFAULT_CONFIG})",
    )
    return parser


def from_cli(description: str | None = None) -> ExperimentConfig:
    parser = argument_parser(description)
    args = parser.parse_args()
    try:
        return load_config(args.config)
    except ConfigError as error:
        parser.error(str(error))
