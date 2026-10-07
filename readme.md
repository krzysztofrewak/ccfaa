# Coordinating Cross-Functional Agent Assessments for Production Replanning in a Brewery Digital Twin

A digital twin of a batch brewery receives signals from four production functions. Domain agents assess each signal against the current production state as KEEP, PENDING or REPLAN, and a replanning policy decides when the shared schedule is reconstructed by a greedy scheduler. Three policies are compared: uncoordinated event-driven, locally filtered event-driven and coordinated replanning.

## Installation

Python 3.10 or newer is required. The package has no runtime dependencies.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Package

`brewery_agentic_replanning`:

| Module       | Contents                                                                                                                         |
|--------------|----------------------------------------------------------------------------------------------------------------------------------|
| `domain`     | `Recipe`, `Fermenter`, `Brewery`, `Batch`, `FermentationAssignment`, `Schedule`                                                  |
| `signals`    | `FermentationDelaySignal`, `IngredientShortageSignal`, `NewBatchRequirementSignal`, `DayOffSignal`, `FermenterMaintenanceSignal` |
| `agents`     | one agent per signal type (Production, Procurement, Sales and Operations functions), each with `apply()` and `assess()`          |
| `policies`   | `UncoordinatedPolicy` (UNC), `LocallyFilteredPolicy` (LF), `CoordinatedPolicy` (COORD), and `ManualPolicy` for scripted replanning|
| `scheduling` | the `Scheduler` interface and `HeuristicScheduler`                                                                               |
| `simulation` | `BreweryDigitalTwin`, which advances the simulation day by day                                                                   |
| `metrics`    | schedule change measures relative to the baseline                                                                                |
| `export`     | JSON export for the calendar viewer in `public/index.html`                                                                       |

## Brewery configurations

Every experiment runs on a named configuration from `experiments/configs/` and accepts `--config <name>` (default `conf1`):

| Configuration | Description                                                      |
|---------------|------------------------------------------------------------------|
| `conf1`       | reference brewery: 3 × 10 hl and 4 × 5 hl fermenters, 19 batches |
| `conf2`       | conf1 with four batches removed (reduced production load)        |
| `conf3`       | conf1 with fermenter f1 replaced by two 5 hl fermenters          |

`experiments/config.py` lists what a configuration module must define.

## Experiments

Experiments are run from the repository root as modules, for example `python -m experiments.exp2 --config conf1`. Every experiment is deterministic and writes its outputs to `experiments/results/`, which is not tracked; the outputs of a complete run are distributed in [`reference_results/`](#reference-results).

| Experiment                | Description                                                                                                                                                                                                                                                   |
|---------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `exp0`                    | Walkthrough: the configuration's `exp0_events` are applied and the brewery is replanned once; writes the calendar for the viewer.                                                                                                                             |
| `exp1`                    | Single signals from `exp1_scenarios`, each applied to a fresh baseline and replanned on day 4; prints the schedule change measures.                                                                                                                           |
| `exp2`                    | Sweep of non-working-day signals: signal day, day off and replanning day in 0–13 (1,015 instances).                                                                                                                                                           |
| `exp2b`                   | Sweep of ingredient shortages: signal day, every batch not yet brewed, availability 1–13 days later (2,080 instances).                                                                                                                                        |
| `exp2c`                   | Sweep of fermentation completion deviations of −3…−1 and +1…+7 days for every active tank (948 instances).                                                                                                                                                    |
| `exp2d`                   | Sweep of maintenance windows for every fermenter, starting 0–13 days after the signal and lasting 1–14 days (19,208 instances).                                                                                                                               |
| `exp2e`                   | Sweep of new production requirements with brewing 0–13 days after the signal and bottling slack of 0, 3, 7 or 14 days (676 instances).                                                                                                                        |
| `exp2_assessment_check`   | Agreement of each agent assessment with the effect of replanning, for all `exp2*` instances. Requires the `exp2*` results.                                                                                                                                    |
| `exp3_cross_functional`   | Scenarios CF1–CF4 under the three policies on one configuration.                                                                                                                                                                                              |
| `exp4_configurations`     | Baseline validation and `exp3_cross_functional` on conf1–conf3, with combined results in `experiments/results/combined/`.                                                                                                                                     |
| `exp5_random_scenarios`   | Robustness check: 200 random cross-functional scenarios (seed 20260930) on conf1–conf3 under the three policies, with the legacy Sales rule (`experiments/legacy.py`). Run `generate`, then `run`; outputs go to `experiments/results/exp5/`.                 |
| `exp6_sales_rule_variant` | COORD with the current Sales rule on the exp5 scenarios, compared with the exp5 results under the legacy rule. Requires the exp5 outputs.                                                                                                                     |
| `exp7_feasibility_check`  | LF, COORD and COORD_FC, COORD with an experimental deferral feasibility check (trial reconstructions on isolated copies), on the exp5 scenarios, with reconstructions, trial runs and computation time reported separately.                                   |
| `exp8_holdout`            | Frozen hold-out test of the final method: 200 new random scenarios (seed 20261001, exp5 generator) on conf1–conf3 under UNC, LF and COORD with the production agents. Run `generate`, then `run`; `run` checks the recorded SHA-256 of the scenarios.         |

To run the single-signal sweeps, the assessment check and the comparison of configurations:

```bash
for e in exp2 exp2b exp2c exp2d exp2e exp2_assessment_check exp4_configurations; do
    python -m experiments.$e
done
```

### Outputs of the cross-functional experiment

`exp3_results.csv` has one row per scenario and policy:

| Column                                     | Meaning                                                                                                                                 |
|--------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------|
| `scheduler_invocations`, `replanning_days` | number and days of scheduler calls                                                                                                      |
| `changed_batches`, `pct_changed_batches`   | batches whose assignments differ from the baseline, including new batches; the percentage is relative to all baseline and final batches |
| `total_abs_schedule_shift`                 | sum of absolute brewing and bottling day changes of existing batches                                                                    |
| `net_schedule_shift`                       | signed sum of changes in each fermenter's last completion day                                                                           |
| `idle_fermenter_days`                      | unused fermenter days between day 15 and each fermenter's last completion                                                               |
| `feasible`, `target_feasible`              | whether the scheduler succeeded and the new order met its target bottling day                                                           |

`exp3_assessment_log.csv` has one row per agent assessment: `local_assessment` is the agent's verdict, `status` the policy's action on it and `latest_safe_day` the latest safe decision point. `exp3_decision_log.csv` has one row per decision cycle, listing the signals it covered and whether the scheduler was invoked. Schedule snapshots after every scheduler call are written to `exp3/<scenario>/<policy>/`.

## Reference results

`reference_results/` holds the outputs of one complete run of every experiment with the code in this repository. It has the same layout as `experiments/results/`, so a new run can be compared with it file by file:

```bash
diff -r experiments/results reference_results
```

| Path                                                                    | Experiment                | Contents                                                                                                                                                            |
|-------------------------------------------------------------------------|---------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `conf1/exp0_calendar.json`                                              | `exp0`                    | calendar data for the viewer, copied from `public/data/conf1/calendar.json`                                                                                         |
| `conf1/exp1_output.txt`                                                 | `exp1`                    | console output; the experiment writes no files                                                                                                                      |
| `conf1/exp2*_results.csv`                                               | `exp2`–`exp2e`            | one row per single-signal instance                                                                                                                                  |
| `conf1/exp2_assessment_verdicts.csv`, `conf1/exp2_assessment_check.csv` | `exp2_assessment_check`   | the agent assessment of every instance, and its agreement with the effect of replanning                                                                             |
| `conf<n>/exp3_*.csv`, `conf<n>/exp3/`                                   | `exp3_cross_functional`   | results, assessment and decision logs and schedule snapshots per configuration                                                                                      |
| `combined/`                                                             | `exp4_configurations`     | configuration summary, baseline validation, scenario definitions, input identity, the exp3 outputs of all configurations, policy comparison and volume preservation |
| `exp5/`                                                                 | `exp5_random_scenarios`   | scenario definitions, run results, policy differences and summaries                                                                                                 |
| `exp6/`                                                                 | `exp6_sales_rule_variant` | variant runs, feasibility, summaries and failure diagnosis                                                                                                          |
| `exp7/`                                                                 | `exp7_feasibility_check`  | runs, feasibility, feasibility check events, cost and summaries                                                                                                     |
| `exp8/`                                                                 | `exp8_holdout`            | the frozen scenario definitions with their SHA-256, run results, pairwise differences and summaries                                                                 |

The `*_seconds` columns of `exp7/runs.csv` and `exp7/cost_summary.csv` are wall-clock times and differ between runs and machines; all other values are reproduced exactly. `exp8_holdout run` only accepts scenario definitions matching `exp8/scenario_definitions.sha256`, so the holdout is rerun by copying both files to `experiments/results/exp8/` instead of generating new ones.

## Calendar viewer

`public/index.html` shows the original and replanned schedules written by `exp0`. Serve the directory and open `index.html?config=<name>`:

```bash
python -m experiments.exp0
python -m http.server -d public
```

## Tests

```bash
pytest
ruff check .
mypy
```

`tests/test_cross_functional_results.py` checks the scheduler invocation days and final schedule measures of all 36 runs (four scenarios, three configurations, three policies).

## License

MIT, see [licence.md](licence.md).
