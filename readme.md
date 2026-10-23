# Brewery Schedule Digital Twin

A digital twin of a batch brewery for experiments on production scheduling and replanning. The twin keeps a fermentation schedule for a configurable set of fermenters, advances it day by day and reacts to signals from production functions such as fermentation delays, ingredient shortages, new orders, days off and maintenance windows. Each experiment set built on the twin belongs to one publication and lives in its own directory under `experiments/`.

## Installation

Python 3.10 or newer is required. The package has no runtime dependencies.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Package

`brewery_schedule_digital_twin`:

| Module       | Contents                                                                                                                           |
|--------------|------------------------------------------------------------------------------------------------------------------------------------|
| `domain`     | `Recipe`, `Vessel` and `VesselRole`, `Brewhouse`, `BottlingLine`, `Brewery`, `Batch`, `Brew`, `Fill`, `FermentationAssignment`, `Schedule`                      |
| `signals`    | `FermentationDelaySignal`, `IngredientShortageSignal`, `NewBatchRequirementSignal`, `DayOffSignal`, `FermenterMaintenanceSignal`   |
| `agents`     | one agent per signal type (Production, Procurement, Sales and Operations functions), each with `apply()` and `assess()`            |
| `policies`   | `UncoordinatedPolicy` (UNC), `LocallyFilteredPolicy` (LF), `CoordinatedPolicy` (COORD), and `ManualPolicy` for scripted replanning |
| `scheduling` | the `Scheduler` interface and `HeuristicScheduler`                                                                                 |
| `simulation` | `BreweryDigitalTwin`, which advances the simulation day by day                                                                     |
| `metrics`    | schedule change measures relative to the baseline                                                                                  |
| `generation` | `ScheduleGenerator`, which fills a running brewery with random batches up to a target occupancy, and `occupancy`                 |
| `export`     | JSON export for the calendar viewer in `public/index.html`                                                                         |

## Vessels and recipes

A brewery is configured with any list of vessels. A `Vessel` has a name, an index, a capacity and a set of roles saying what it can hold a batch for:

| Role        | Use                                                          | Recipe time                   |
|-------------|--------------------------------------------------------------|-------------------------------|
| `FULL`      | the whole fermentation, without a transfer                   | `fermentation_days`           |
| `PRIMARY`   | primary fermentation, followed by a transfer                 | `primary_fermentation_days`   |
| `SECONDARY` | secondary fermentation, after a transfer from another vessel | `secondary_fermentation_days` |

A batch either stays in one `FULL` vessel, or ferments in a `PRIMARY` vessel and is transferred to a different `SECONDARY` vessel. The three recipe times are independent: the stage times need not add up to `fermentation_days`, and they are optional. Any combination of roles is allowed; named constructors cover the common vessels:

| Constructor             | Roles                  |
|-------------------------|------------------------|
| `Vessel.unitank`        | `FULL`                 |
| `Vessel.fermenter`      | `PRIMARY`              |
| `Vessel.lagering_tank`  | `SECONDARY`            |
| `Vessel.general_vessel` | `PRIMARY`, `SECONDARY` |

`HeuristicScheduler` keeps a batch in one vessel for `fermentation_days`, so it only uses `FULL` vessels; scheduling with transfers is not implemented yet.

## Brewhouses and bottling lines

A `Brewery` takes a list of `Brewhouse`s and a list of `BottlingLine`s besides its vessels; it needs at least one of each. All volumes in the package (vessel capacities, batch, fill and run volumes, kettle yields) are in litres:

| Equipment      | Settings                                                                                                     |
|----------------|--------------------------------------------------------------------------------------------------------------|
| `Brewhouse`    | `kettle_yield`: litres per brew; `max_brews_per_day`                                                         |
| `BottlingLine` | `run_volume`: litres bottled or canned in one run (`math.inf` for any vessel in one run); `max_runs_per_day` |

A brew names its brewhouse in `Brew.brewhouse`, and an assignment names its line in `FermentationAssignment.bottling_line`; None names the only one in a brewery that has one. A bottling run takes beer from one vessel, so a vessel holding more than one run volume is bottled in several runs, listed in `FermentationAssignment.bottling_runs` by day, and stays occupied until the last of them. `BottlingLine.by_daily_volume(name, volume_per_day_l)` makes a line limited only by the volume it bottles a day: it counts runs of 1 hl, so several vessels share the daily volume. `Brewery.kettle_yield_hl`, `max_brews_per_day` and `max_bottling_runs_per_day` sum up the equipment: the largest brew and the brews and runs a day of the whole brewery.

`HeuristicScheduler` brews each brew in the smallest brewhouse that makes it and has a free slot that day, so the brews of one batch may come from several brewhouses. It bottles each vessel on the line that finishes it soonest, taking every free run of the line on each working day, so bottling takes as few days as possible; the vessels of one batch start bottling on the same day, possibly on different lines, unless the batch needs more runs than all lines take a day: then each further vessel starts on the first day its line has a free run.

## Batches, brews and fills

A `Batch` is a quantity of one beer. It is brewed in one or more `Brew`s, each at most the yield of its brewhouse, and poured into vessels as `Fill`s, so a batch can be split and merged at the same time:

- split: one brew is poured into several vessels, for example a 10 hl brew into two 5 hl vessels;
- merge: several brews are poured into one vessel, for example two 50 hl brews into a 100 hl vessel.

A `FermentationAssignment` is a batch's beer in one vessel. Without `fills` it holds a single brew poured on `start_day`; with `fills` it lists the brews poured into it. Fermentation starts with the first fill and ends `fermentation_days` after the last one. All brews of a batch are brewed within `Brewery.max_brew_spread_days` (2 by default) of the first.

A blend is declared explicitly on the batch, as the volume brewed from each recipe: `Batch(..., recipe=STOUT, blend={STOUT: 70.0, PORTER: 30.0})`. Each brew is of one recipe, set in `Brew.recipe`, and the brews are mixed in the vessels. The batch's `recipe` sets the fermentation time. A batch without `blend` takes brews of its own recipe only.

`Schedule.validate()` lists errors, among them brews above the yield of their brewhouse, more brews or bottling runs in a day than a brewhouse or line allows, too few bottling runs for a vessel's volume, brews spread over more days than allowed, brews of another recipe in a batch that declares no blend and blends whose brewed volumes differ from the declared ones.

`HeuristicScheduler` brews a batch above the largest brew in the fewest equal brews, as close together as the brewhouses allow, and fills its vessels with them in order. It brews a blend recipe by recipe, each in the fewest equal brews, and spreads every brew over the vessels in proportion to their volumes, so each vessel holds the declared blend. `tests/test_scheduler_contract.py` holds the rules every scheduler has to keep on every brewery configuration; a new scheduler is checked by adding it to `SCHEDULERS` there.

## Experiments

Every experiment set has its own readme, brewery configurations, scripts and reference results. Experiments are run from the repository root as modules, for example `python -m experiments.ccfaa2026.exp2`.

| Code                                 | Publication                                                                                         | Status      |
|--------------------------------------|-----------------------------------------------------------------------------------------------------|-------------|
| [`ccfaa2026`](experiments/ccfaa2026) | Coordinating Cross-Functional Agent Assessments for Production Replanning in a Brewery Digital Twin | complete    |
| [`itch2027`](experiments/itch2027)   | The Impact of Tank Capacity Heterogeneity on Brewery Production Scheduling                          | in progress |

A new experiment set goes to `experiments/<code>/`, where the code is an abbreviation of the publication title followed by the year, with a readme describing its experiments and outputs. Tests specific to an experiment set go to `tests/<code>/`.

## Calendar viewer

`public/index.html` shows an original and a replanned schedule exported with `brewery_schedule_digital_twin.export`. Serve the directory and open `index.html?config=<name>`, which reads `public/data/<name>/calendar.json`:

```bash
python -m experiments.ccfaa2026.exp0
python -m http.server -d public
```

## Tests

```bash
pytest
ruff check .
mypy
```

## License

MIT, see [licence.md](licence.md).
