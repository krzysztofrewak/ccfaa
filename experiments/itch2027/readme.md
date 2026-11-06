# The Impact of Tank Capacity Heterogeneity on Brewery Production Scheduling

Experiments for this publication, in preparation. They run on the [`brewery_schedule_digital_twin`](../../readme.md) package; see the main readme for installation. Commands below are run from the repository root.

The experiments ask whether a brewery's mix of unitank sizes makes its schedule more or less stable under fermentation delays. Each brewery comes in variants with the same total capacity but different tanks; generated or order-driven schedules are delayed and repaired, and the variants are compared by how much the repair moves the plan, above all by D_pack, the total absolute shift of the batches' last bottling starts.

## Experiments

Experiments are run as modules, for example `python -m experiments.itch2027.exp1`. Every experiment writes its outputs to `experiments/itch2027/results/`, which is not tracked; the outputs of a complete run are distributed in [`reference_results/`](reference_results), in the same layout. The experiments that run CP-SAT or differential evolution log every run as it ends and resume where they stopped when run again.

| Experiment          | Description                                                                                                                         | Time               |
|---------------------|-------------------------------------------------------------------------------------------------------------------------------------|--------------------|
| `exp1`              | Every delay of the sweep over every tank of Browar Wołów, greedy repair (about 19,000 repairs).                                    | minutes            |
| `exp2`              | 20 delays a schedule in Browar Wołów, Bury and Elbląg, 100 hl to 175,000 hl, greedy repair.                                        | about 15 minutes   |
| `exp3`              | 900 delays of Experiment 2 repaired by the greedy scheduler, CP-SAT and differential evolution (three seeds), 10 s a repair.        | about 3 hours      |
| `exp3_elblag_pilot` | CP-SAT with a longer limit and several workers on Elbląg scenarios of Experiment 3, and the solver settings tried there.            | 5–35 minutes       |
| `exp4`              | Tank heterogeneity alone: three fleets of 10 tanks and 200 hl on the same orders, with `--method` greedy, `cpsat` or `de`.          | seconds to 3 hours |
| `generate_schedule` | A random schedule of a small preview brewery for the calendar viewer.                                                             | seconds            |

To run everything that the reference results hold:

```bash
python -m experiments.itch2027.exp1
python -m experiments.itch2027.exp2
python -m experiments.itch2027.exp3
python -m experiments.itch2027.exp3_elblag_pilot
python -m experiments.itch2027.exp3_elblag_pilot --representative --param cp_model_probing_level=0
python -m experiments.itch2027.exp3_elblag_pilot --representative --param symmetry_level=0
python -m experiments.itch2027.exp3_elblag_pilot --representative --param cp_model_probing_level=0 --param symmetry_level=0
for method in greedy cpsat de; do python -m experiments.itch2027.exp4 --method $method; done
for rate in 0.4 0.5 0.6; do python -m experiments.itch2027.exp4 --first-set 1000 --sets 200 --orders-per-day $rate; done
```

## Code

| Module              | Contents                                                                                                                                          |
|---------------------|---------------------------------------------------------------------------------------------------------------------------------------------------|
| `breweries`         | the breweries in their variants (`wolow`, `bury`, `elblag`, `heterogeneity`, `preview`) and the recipes                                          |
| `measures`          | `BaselineProfile` of a schedule, `ScheduleChange` and `BatchChange` of a replan, the validation of a replanned schedule and a schedule's `fingerprint` |
| `replanning`        | `Repair`: a baseline replanned by the digital twin on one day after delays, with its time, cost, change and problems                             |
| `methods`           | `RepairMethod`, a scheduler with its settings and seed, and the columns that CP-SAT and differential evolution report of a search                |
| `estimates`         | `Estimate`, a mean with its 95% t confidence interval, and `PairedDifference`                                                                     |
| `storage`           | CSV tables, the resumable `RunLog` and the `Metadata` of a run: protocol, libraries, commit and hardware                                         |
| `sweep`             | `DelaySweep`, the design of Experiments 1 and 2                                                                                                   |
| `exp1` … `exp4`     | the experiments; `exp3` has `Scenario`, `Manifest`, `ScenarioRunner` and `Comparison`, `exp4` has `OrderSet`, `Delay`, `FleetComparison` and `MethodComparison` |

Tests are in `tests/itch2027/`.

## Breweries

`breweries` builds the breweries the experiments compare, each in the variants of its unitanks, and their five recipes of 8–18 fermentation days, so Elbląg ferments ales rather than lagers with long lagering. A brewery whose variants differ only in their unitanks lists its fleets, brewhouses and bottling lines and builds the variants with `unitank_variants`.

`wolow` is Browar Wołów: one 6 hl brewhouse (4 brews a day) and manual bottling of up to 20 hl a day, so the largest tank is bottled in one day. Its unitanks come in three variants of 100 hl:

| Variant | Unitanks             |
|---------|----------------------|
| A       | 4 × 10 hl, 3 × 20 hl |
| B       | 10 × 10 hl           |
| C       | 5 × 20 hl            |

`bury` is Browar Bury: one 26 hl brewhouse (4 brews a day) and one bottling line of up to 160 hl a day, four of the largest tanks. The line is no bottleneck: no tank of a generated schedule waits for it past its fermentation, and with any larger line the generated schedules reach the same occupancy. Its unitanks come in three variants of 640 hl; A is the brewery as it is:

| Variant | Unitanks              |
|---------|-----------------------|
| A       | 15 × 40 hl, 2 × 20 hl |
| B       | 16 × 40 hl            |
| C       | 32 × 20 hl            |

`elblag` is Browar Elbląg, a large plant: two 700 hl brewhouses (14 brews a day each), two bottling lines and a canning line of up to 15,000 hl a day each, three of the largest tanks, counted in runs of 20 hl. The lines are no bottleneck in the same sense as in `bury`; with 10,000 hl a line about 1% of tanks would wait. Its unitanks come in four variants of about 175,000 hl, within 0.5% of each other; A is the brewery as it is:

| Variant | Unitanks                                  | Total      |
|---------|-------------------------------------------|------------|
| A       | 32 × 4,600 hl, 5 × 2,400 hl, 6 × 2,540 hl | 174,440 hl |
| B       | 38 × 4,600 hl                             | 174,800 hl |
| C       | 73 × 2,400 hl                             | 175,200 hl |
| D       | 69 × 2,540 hl                             | 175,260 hl |

`heterogeneity` is a brewery of ten unitanks and 200 hl for Experiment 4, with one 5 hl brewhouse (4 brews a day), since every tank holds whole brews of it, and bottling of up to 35 hl a day, so the largest tank is bottled in one day:

| Variant | Unitanks             |
|---------|----------------------|
| A       | 10 × 20 hl           |
| B       | 5 × 10 hl, 5 × 30 hl |
| C       | 5 × 5 hl, 5 × 35 hl  |

`preview` is a small brewery for `generate_schedule`: four 10 hl unitanks, a 10 hl brewhouse of one brew a day and one bottling run a day.

## Generated schedules

Experiments 1–3 compare the variants on schedules of a brewery in operation, generated by `ScheduleGenerator` of `brewery_schedule_digital_twin.generation` up to a target occupancy. The generator first gives every tank a chain of batches brewed back to back, each on the day the previous one leaves the tank, from a batch already fermenting on day 0 to one that ends on or after the horizon; each batch is of a random recipe whose brews and bottling fit the brewhouses and lines, and only if none fits does the tank stay empty for a day. Every tank is filled to its capacity: a tank larger than a brew takes several brews merged in it, at most `max_brew_spread_days` apart, and a smaller one shares one brew with the other tanks free on the same day. A tank that needs more bottling runs than its line has free on its last fermentation day is bottled over the following days. A brew is made in the first listed brewhouse that makes it and has a free slot, and each tank is bottled on the line that finishes it soonest. Then random batches are removed, each only if the occupancy, the share of tank-days in the horizon that hold a batch, stays at or above the target; the kept batches are bottled again on their lines, so no tank leaves later than before. A target of 100% removes nothing and keeps every tank busy as far as the brewhouses and lines allow, so `baseline_occupancy` records the occupancy reached.

`generate_schedule` fills the preview brewery and exports the schedule for the calendar viewer. Each run without `--seed` draws a new seed and prints it; `--seed` repeats a schedule:

```bash
python -m experiments.itch2027.generate_schedule --fill 0.7 --horizon 60
python -m http.server -d public
```

Then open `http://localhost:8000/index.html?config=itch2027`. The viewer compares an original and a replanned schedule, so both show the generated one.

## Experiments 1 and 2: sweeps of fermentation delays

A `DelaySweep` holds the variants of one brewery and what is swept on them. For every variant and target occupancy (100%, 97%, 95%, 93%, 90% and 85%), `--count` schedules (default 10) are generated over 60 days with seeds `--seed` to `--seed` + `--count` − 1. The sweep holds, on each signal day 3–20, the batch in any tank whose fermentation would end 1–3 days later, extended by 1, 2 or 3 days. A schedule gets these delays one at a time, every one of them or, with `signals_per_schedule` set, that many drawn with its seed. Every delay is replanned by the greedy `HeuristicScheduler` on a fresh copy of the baseline on its signal day. As a control, every baseline is also replanned undisturbed on every day it gets a delay on, so the changes the scheduler makes to a generated schedule on its own can be told apart from those of a delay. Every repair is also measured by `repair_cost`, the objective CP-SAT and differential evolution minimise; they are not run here, as they search up to their limit in each of tens of thousands of repairs. `--experiment` chooses breweries and `--workers` the processes (default: all CPUs).

`exp1` gives every schedule of Browar Wołów every delay of the sweep, over every tank: about 19,000 repairs of a few to a few dozen milliseconds. `exp2` compares Wołów, Bury and Elbląg. The full sweep would give a schedule of Elbląg up to 900 delays of about a second each, so each schedule gets only 20 (`SIGNALS_PER_SCHEDULE`), falling on some of its tanks; a schedule of Elbląg takes 20–35 s, the whole of Elbląg about 2 CPU hours.

```bash
python -m experiments.itch2027.exp1 --count 10
python -m experiments.itch2027.exp2 --experiment bury elblag --count 10
```

The outputs go to `results/exp1/<brewery>/` and `results/exp2/<brewery>/`:

- `runs.csv` has one row per delay: the brewery, variant, target occupancy and seed; the baseline (occupancy reached, batches, brews, split batches and merged tanks, makespan, idle tank-days); the delay (day, tank and its capacity in litres, batch, lead and extra days, the batch's original end and bottling day); and the repair: feasibility, problems of the replanned schedule, changed batches, also without the delayed one, net and total absolute schedule shift, shifted brews and bottlings with their absolute (`abs_bottling_shift_days` is D_pack) and largest shifts, batches moved to other tanks, unplaced batches, idle tank-days before and after, occupancy and makespan after the repair, the delayed batch's new bottling and end day and the batch next in its tank, the scheduler's time and the `repair_cost`. The `control_*` columns hold the measures of the undisturbed replan on the signal day.
- `controls.csv` has one row per undisturbed replan, with `signal` false.

## Experiment 3: the greedy scheduler, CP-SAT and differential evolution on the same delays

`exp3` asks whether the ranking of the variants holds when another method repairs the schedule. It takes every variant of the three breweries of Experiment 2 at target occupancies of 90%, 97% and 100%, with each of its 10 schedules, and of the 20 delays each schedule got in Experiment 2 draws one for each of 1, 2 and 3 extra days, with a fixed seed and without looking at any result: 900 scenarios. A schedule without a delay of some extra days would be reported before anything runs, with no other delay standing in for it; in the sample of Experiment 2 there is none. The `Manifest` of the scenarios goes to `results/exp3/manifest.csv` with a fingerprint of each baseline, checked against the delays recorded in `results/exp2/<brewery>/runs.csv` when they are there.

Each scenario regenerates its baseline, checks its fingerprint and gives every method a fresh copy of it with the same delay, replanned on the signal day: `HeuristicScheduler` once, `CpSatScheduler` once (10 s, seed 0, one worker) and `DifferentialEvolutionScheduler` three times (10 s each, seeds 0, 1 and 2). All of them keep the batches already started in their vessels and brews, and every result is costed by `repair_cost` against the same state and validated, moved locked batches included. The methods do not take the same decisions: the greedy scheduler places batches one by one; differential evolution searches only the order of the batches, whether each keeps its vessels and which single vessel it goes into, leaving brews, splits and bottling to the greedy decoder; CP-SAT decides brew days and brewhouses, one of up to 40 candidate placements per batch and the line and days of every bottling run, starting from the greedy schedule if completing it fits in half its limit; when its search then finds nothing better, it returns that completed starting solution, marked `fallback` = `cpsat_start`. CP-SAT's limit covers its solves only, not finding the greedy schedule and building the model; differential evolution overruns its limit by at most one decoding. `metadata.json` records these settings and decisions, what the times measure and, for every session, the libraries, the commit and the hardware.

A run that finds no schedule (`infeasible`), crashes (`error`) or returns an invalid one (`invalid`) is kept with its status and without a cost. `Comparison` writes `runs.csv` (every run), `scenarios.csv` (the three differential evolution runs averaged, never the best taken, and a scenario given a measure only if all its runs are valid), `summary.csv` (scenarios averaged by schedule, then means with 95% confidence intervals over the schedules of each brewery, variant, occupancy and method, for the absolute bottling and brew shifts, `repair_cost`, the scheduler's time and the shares of valid repairs and fallbacks), `paired.csv` (methods compared pairwise on the scenarios both repaired, by schedule first) and `ranking.csv` (each method's ranking of the variants). On 4 processes of a laptop i5-1245U the full sample takes about 3 hours, half of it Elbląg, where building the CP-SAT model alone takes 8–30 s before its limit starts.

```bash
python -m experiments.itch2027.exp3 --pilot               # schedule 0 at 100% of every variant, into results/exp3_pilot
python -m experiments.itch2027.exp3                       # all 900 scenarios; run again to resume
python -m experiments.itch2027.exp3 --method greedy cpsat # only some of the methods
python -m experiments.itch2027.exp3 --aggregate-only
```

## CP-SAT on Elbląg

With 10 s and one worker, CP-SAT finds no schedule in 309 of the 360 Elbląg scenarios of Experiment 3 and only its starting solution in the other 51. `exp3_elblag_pilot` runs the greedy scheduler and CP-SAT with `--seconds` (60), `--solver-workers` (4), `--max-placements` (40) and any `--param` of the solver on the Elbląg scenarios of schedules 0 and 1 (72), or with `--representative` on 12 of them: schedule 0 of every variant and occupancy once, every variant and every occupancy with every delay. It uses the runner of Experiment 3, so baselines, delays, validation and costs are the same; each setting goes to `results/exp3_elblag_pilot/<setting>/`, with `report.csv` by variant and occupancy.

| Setting (12 scenarios) | Search found a schedule | Only the start returned | Proven optimal | D_pack lower / higher than greedy | Repair cost, greedy → CP-SAT |
|------------------------|-------------------------|-------------------------|----------------|-----------------------------------|------------------------------|
| default                | 0                       | 12                      | 0              | 0 / 0                             | 1406 → 1342                  |
| no probing             | 9                       | 3                       | 1              | 0 / 0                             | 1406 → 1342                  |
| no symmetry detection  | 0                       | 12                      | 0              | 0 / 0                             | 1406 → 1342                  |
| neither                | 11                      | 1                       | 1              | 0 / 0                             | 1406 → 1337                  |

With 60 s and 4 workers, every one of the 72 scenarios gets a valid schedule, but always the completed starting solution: its bottling is spread better than the greedy one's, so it costs less in 45 of them (1274 → 1221 on average), yet D_pack is the greedy one's in all of them. The model of an Elbląg scenario has about 230,000 variables and 550,000 constraints, and the solver's presolve, probing above all, takes the rest of the limit, so the search never starts. Without probing it starts in most scenarios, but it finds nothing better than its start and its bound stays about 65% below its cost. So on Elbląg CP-SAT returns the greedy repair with its bottling spread better, and the methods are compared on the smaller instances.

`comparison/` of each setting holds the tables of `Comparison` of Experiment 3 on the setting's scenarios, with the pilot's greedy and CP-SAT runs and Experiment 3's differential evolution runs of the same scenarios (10 s, seeds 0, 1 and 2), read from `results/exp3/runs/`; nothing is optimised again. On the 72 scenarios of the default setting the three methods have the same D_pack. CP-SAT returns the greedy D_pack, as above, and so does differential evolution: on Elbląg its 10 s run out while it decodes its initial population (5–8 decodings, no generation in any of the 1,080 Elbląg runs of Experiment 3), so it returns the greedy schedule. Means over the 6 schedules of each variant (2 schedules × 3 occupancies, the delays of a schedule averaged first) ± 95% CI, next to the greedy scheduler on all 360 Elbląg scenarios of Experiment 3:

| Variant | Greedy, CP-SAT (60 s, 4 workers) and DE (10 s), 72 scenarios | Greedy, 360 scenarios |
|---------|--------------------------------------------------------------|-----------------------|
| A       | 49.7 ± 79.6                                                  | 51.1 ± 27.1           |
| B       | 6.9 ± 6.8                                                    | 6.3 ± 2.4             |
| C       | 6.5 ± 7.2                                                    | 4.5 ± 1.6             |
| D       | 4.3 ± 2.7                                                    | 4.9 ± 1.8             |
| All     | 16.8 ± 17.3                                                  | 16.7 ± 7.5            |

## Experiment 4: tank heterogeneity alone

`exp4` isolates the effect of heterogeneity on the stability of a schedule, with the same number of tanks, total capacity, brewhouse, line and orders. For each of 30 sets the fleets of `heterogeneity` get one `OrderSet`: recipes, volumes of 5–35 hl in whole brews and days of availability, drawn without looking at any tank. Orders come from day −18 to day 59 at 0.5 batches a day, a rate fixed from baselines alone, before any delay was run. The greedy scheduler plans each fleet from the orders by the same rules, underfilling and splitting where it needs to, so the occupancy is measured, not imposed.

Every batch that every fleet ends so that the signal falls on days 3–20 is delayed, one at a time, by 1, 2 and 3 days, in all its vessels and 3 days before each fleet's planned end of that batch: about 7 batches a set, 1,872 delays. The method of `--method` repairs each on a fresh copy of the baseline: `greedy` (the default), `cpsat` (CP-SAT warm-started from the greedy repair, 300 s and 4 workers a repair, up to 200 placements a batch) or `de` (differential evolution of 30 × 100, seed 0, 600 s at most). Each baseline is also replanned undisturbed on each signal day as a control.

D_pack is the total absolute shift of the batches' last bottling starts, so a split batch counts once. The outputs go to `results/exp4/<method>/`: `runs.csv` (one row per repair: D_pack in all, for the delayed batch and for the others, batches moved to other tanks, split batches before and after, occupancy by tank-days and by volume, the control and the solver's status, bound and time), `sets.csv` (averaged per set, fleet and delay), `summary.csv` and `paired.csv` (the fleets compared set by set, with 95% confidence intervals; a set with a failed repair in any fleet is dropped for all). After every run `results/exp4/` gets `methods.csv` (the paired fleet differences of every method that has runs) and `bounds.csv` (each method's repair cost against the CP-SAT lower bound on the same scenarios). Every repair of every method was valid and no control moved a bottling. The greedy repairs take about 20 seconds on 10 processes, CP-SAT about 2.7 hours on 3 processes of 4 workers (1811 optimal, 61 feasible at the limit, median 2 s a repair) and DE about 3 hours on 12 processes (none at the limit).

```bash
python -m experiments.itch2027.exp4                                   # greedy
python -m experiments.itch2027.exp4 --method cpsat                    # run again to resume
python -m experiments.itch2027.exp4 --method de
python -m experiments.itch2027.exp4 --pilot --method cpsat            # 2 sets, into results/exp4_pilot/
python -m experiments.itch2027.exp4 --first-set 1000 --sets 200 --orders-per-day 0.4
```

Mean over 30 sets ± 95% CI, over all delays, with the greedy repair:

| Fleet | D_pack    | Others    | Moved batches | Split batches | Tank-days   | By volume   |
|-------|-----------|-----------|---------------|---------------|-------------|-------------|
| A     | 5.2 ± 1.3 | 3.1 ± 1.3 | 5.7 ± 1.2     | 16.3 ± 1.3    | 0.82 ± 0.04 | 0.58 ± 0.03 |
| B     | 9.2 ± 3.4 | 7.1 ± 3.4 | 4.8 ± 1.5     | 8.8 ± 1.4     | 0.76 ± 0.05 | 0.59 ± 0.03 |
| C     | 8.4 ± 2.9 | 6.3 ± 2.9 | 4.8 ± 1.1     | 4.6 ± 0.8     | 0.73 ± 0.05 | 0.59 ± 0.03 |

D_pack, the second fleet minus the first, paired by set ± 95% CI, with the greedy repair:

| Delay  | B − A      | C − A      | C − B      |
|--------|------------|------------|------------|
| 1 day  | +2.3 ± 2.3 | +2.0 ± 1.8 | −0.3 ± 1.9 |
| 2 days | +3.2 ± 2.7 | +3.0 ± 2.3 | −0.2 ± 2.9 |
| 3 days | +6.4 ± 4.4 | +4.5 ± 4.1 | −1.9 ± 6.1 |
| All    | +4.0 ± 2.8 | +3.2 ± 2.4 | −0.8 ± 3.2 |

On the same orders the uniform fleet shifts bottling least, though it moves more batches to other tanks, and B and C cannot be told apart. 200 other sets of orders (`--first-set 1000 --sets 200`) give the same order at 0.4, 0.5 and 0.6 batches a day, with C − A of +1.5 ± 0.6, +4.2 ± 1.1 and +11.2 ± 2.2. The differences are skewed: at the 30 sets the median C − A is only +0.6. The tank-day occupancy is not equal across fleets, since A splits more batches. Repairs add almost no splits in any fleet.

Mean over 30 sets ± 95% CI, over all delays, by repair method:

| Method | Fleet | D_pack    | Others    | Moved batches | Repair cost |
|--------|-------|-----------|-----------|---------------|-------------|
| greedy | A     | 5.2 ± 1.3 | 3.1 ± 1.3 | 5.7 ± 1.2     | 202 ± 67    |
| greedy | B     | 9.2 ± 3.4 | 7.1 ± 3.4 | 4.8 ± 1.5     | 378 ± 164   |
| greedy | C     | 8.4 ± 2.9 | 6.3 ± 2.9 | 4.8 ± 1.1     | 342 ± 137   |
| DE     | A     | 4.6 ± 1.2 | 2.5 ± 1.2 | 3.1 ± 1.0     | 152 ± 52    |
| DE     | B     | 5.3 ± 1.5 | 3.2 ± 1.5 | 1.9 ± 0.6     | 177 ± 65    |
| DE     | C     | 5.2 ± 1.3 | 3.2 ± 1.3 | 2.3 ± 0.6     | 176 ± 57    |
| CP-SAT | A     | 2.8 ± 0.3 | 0.9 ± 0.3 | 1.8 ± 0.5     | 58 ± 11     |
| CP-SAT | B     | 4.1 ± 1.0 | 2.1 ± 1.0 | 2.0 ± 0.6     | 115 ± 50    |
| CP-SAT | C     | 4.8 ± 1.2 | 2.8 ± 1.1 | 2.5 ± 0.7     | 139 ± 49    |

D_pack over all delays, the second fleet minus the first, paired by set ± 95% CI, by repair method, and the same for the CP-SAT lower bound on the repair cost:

| Method              | B − A        | C − A        | C − B        |
|---------------------|--------------|--------------|--------------|
| greedy              | +4.0 ± 2.8   | +3.2 ± 2.4   | −0.8 ± 3.2   |
| DE                  | +0.7 ± 1.1   | +0.7 ± 0.8   | −0.0 ± 1.0   |
| CP-SAT              | +1.3 ± 0.9   | +2.0 ± 1.1   | +0.7 ± 0.9   |
| CP-SAT bound (cost) | +25.4 ± 18.2 | +59.6 ± 25.6 | +34.3 ± 19.6 |

With CP-SAT, A keeps the lowest D_pack and the fleets fall in the order of their spread, A < B < C, by 1, 2 and 3 days of delay alike, though C − B is not significant on its own. The gap is about a third (B − A) and three fifths (C − A) of the greedy one, so most of the greedy gap comes from the repair, and the rest from the fleet. The lower bounds of the repair cost rise with heterogeneity too, so the residual is not an artefact of CP-SAT stopping short. DE narrows the gap further, but not because it repairs B and C better than CP-SAT: it repairs A worse, since its decoder chooses a vessel only for a batch in one vessel, and A splits the most batches.

The CP-SAT bound holds for the repair model it solves, with up to 200 placements a batch and two extra chunks, and bounds the repair cost, not D_pack; DE, which is not held to that model, falls below it in 12 of 1872 repairs. The repair cost counts each vessel of a held split batch, which raises A's cost more than its D_pack, so it can only narrow the bound's gap between A and the others. With 4 workers CP-SAT is not deterministic, so a rerun may differ in the repairs stopped at the limit.

## Reference results

`reference_results/` holds the outputs of one complete run of every experiment, in the layout of `results/`, so a new run can be compared with it file by file. The run logs (`runs/*.jsonl`), from which `runs.csv` is written, are left out; the times and CP-SAT's repairs stopped at its limit differ between runs and machines.

| Path                                                    | Experiment          | Contents                                                                                               |
|---------------------------------------------------------|---------------------|--------------------------------------------------------------------------------------------------------|
| `exp1/wolow/`                                           | `exp1`              | `runs.csv`, `controls.csv`                                                                             |
| `exp2/<brewery>/`                                       | `exp2`              | `runs.csv`, `controls.csv` of Wołów, Bury and Elbląg                                                   |
| `exp3/`                                                 | `exp3`              | the manifest, every run, and the scenario, summary, paired and ranking tables, with metadata           |
| `exp3_elblag_pilot/<setting>/`                          | `exp3_elblag_pilot` | every run and the report of the default setting on 72 scenarios and of three on 12, with `comparison/` |
| `exp4/<method>/`, `exp4/methods.csv`, `exp4/bounds.csv` | `exp4`              | runs, sets, summary and paired tables of each method, and the methods compared                         |
| `exp4_orders<rate>_sets1000-1199/greedy/`               | `exp4`              | the robustness check on 200 other sets at 0.4, 0.5 and 0.6 orders a day                                |
