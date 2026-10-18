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
| `domain`     | `Recipe`, `Vessel` and `VesselRole`, `Brewery`, `Batch`, `FermentationAssignment`, `Schedule`                                      |
| `signals`    | `FermentationDelaySignal`, `IngredientShortageSignal`, `NewBatchRequirementSignal`, `DayOffSignal`, `FermenterMaintenanceSignal`   |
| `agents`     | one agent per signal type (Production, Procurement, Sales and Operations functions), each with `apply()` and `assess()`            |
| `policies`   | `UncoordinatedPolicy` (UNC), `LocallyFilteredPolicy` (LF), `CoordinatedPolicy` (COORD), and `ManualPolicy` for scripted replanning |
| `scheduling` | the `Scheduler` interface and `HeuristicScheduler`                                                                                 |
| `simulation` | `BreweryDigitalTwin`, which advances the simulation day by day                                                                     |
| `metrics`    | schedule change measures relative to the baseline                                                                                  |
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

## Experiments

Every experiment set has its own readme, brewery configurations, scripts and reference results. Experiments are run from the repository root as modules, for example `python -m experiments.ccfaa2026.exp2`.

| Code                                 | Publication                                                                                         | Status      |
|--------------------------------------|-----------------------------------------------------------------------------------------------------|-------------|
| [`ccfaa2026`](experiments/ccfaa2026) | Coordinating Cross-Functional Agent Assessments for Production Replanning in a Brewery Digital Twin | complete    |
| [`cbps2027`](experiments/cbps2027)   | Constraint-Based Production Scheduling in Heterogeneous Breweries with Batch Splitting and Merging  | in progress |

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
