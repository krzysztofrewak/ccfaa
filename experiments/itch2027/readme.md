# The Impact of Tank Capacity Heterogeneity on Brewery Production Scheduling

Experiments for this publication are in preparation. They run on the [`brewery_schedule_digital_twin`](../../readme.md) package; see the main readme for installation. Commands below are run from the repository root.

## Brewery

`experiments/itch2027/brewery.py` builds the brewery: one 10 hl brewhouse (1 brew a day), four 10 hl unitanks and one bottling line (one run of up to 10 hl a day), and five recipes of 8–18 fermentation days.

## Generating a schedule

`generate_schedule` fills the brewery with `ScheduleGenerator` from `brewery_schedule_digital_twin.generation` and exports the schedule for the calendar viewer:

```bash
python -m experiments.itch2027.generate_schedule --fill 0.7 --horizon 60
python -m http.server -d public
```

Then open `http://localhost:8000/index.html?config=itch2027`. Each run without `--seed` draws a new seed and prints it; `--seed` repeats a schedule. The viewer compares an original and a replanned schedule, so both show the generated one.

The schedule is a snapshot of a brewery in operation over the horizon. The generator first gives every tank a chain of batches brewed back to back, each on the day the previous one leaves the tank, from a batch already fermenting on day 0 to one that ends on or after the horizon; each batch is of a random recipe whose brews and bottling fit the brewhouse and the bottling line, and only if none fits does the tank stay empty for a day. Every tank is filled to its capacity: a tank larger than a brew takes several brews merged in it (at most `max_brew_spread_days` apart), and a smaller one shares one brew with the other tanks free on the same day, split between them while the brew fills them all. A tank that needs more bottling runs than the line has free on its last fermentation day is bottled over the following days. Then it removes random batches, each only if the occupancy, the share of tank-days in the horizon that hold a batch, stays at or above `--fill`. `--fill 1` removes nothing and leaves the tanks busy back to back.

## Experiment 1: a sweep of fermentation delays in Browar Wołów

An `Experiment` in `experiments/itch2027/exp1.py` holds everything a run varies: the variants of a brewery, the recipes, the target occupancies, the horizon and the sweep (signal, lead and extra days). Each is built by a function listed in `EXPERIMENTS` and chosen with `--experiment`; another brewery or configuration is compared by adding such a function. The only one so far, `wolow`, is Browar Wołów: one 6 hl brewhouse (4 brews a day) and manual bottling of up to 20 hl a day, so the largest tank is bottled in one day. Its unitanks come in three variants, each of 100 hl in total:

| Variant | Unitanks             |
|---------|----------------------|
| A       | 4 × 10 hl, 3 × 20 hl |
| B       | 10 × 10 hl           |
| C       | 5 × 20 hl            |

For every variant and target occupancy (for `wolow` 100%, 97%, 95%, 93% and 90%), `--count` schedules (default 10) are generated over the horizon (for `wolow` 60 days) with seeds `--seed` to `--seed` + `--count` − 1. Each of them gets, one at a time, every signal of a sweep: on each signal day (for `wolow` 3–20), the batch in any tank whose fermentation would end in one of the lead days (`natural_end_day` − signal day; for `wolow` 1–3) has it extended by each of the extra days (for `wolow` 1–3). Every signal is applied to a fresh copy of the baseline, the twin advances to the signal day and the greedy scheduler of the ccfaa2026 experiments (`HeuristicScheduler`) replans on that day. As a control, every baseline is also replanned without any signal on every signal day, so changes the greedy scheduler makes to a generated schedule on its own can be told apart from the effect of a delay. With 10 schedules this is about 13,000 replans, with 100 about 130,000; they run in parallel on `--workers` processes (default: all CPUs).

```bash
python -m experiments.itch2027.exp1 --experiment wolow --count 10
```

The outputs go to `experiments/itch2027/results/exp1/<experiment>/`:

- `runs.csv` has one row per signal: the experiment, variant, target occupancy and seed, the baseline (achieved occupancy, batches, brews, split batches and merged tanks, makespan, idle tank-days), the signal (day, tank and its capacity in litres, batch, lead and extra days, the batch's original end and bottling day), and the effect of replanning: feasibility, problems of the replanned schedule, changed batches (also excluding the delayed one), net and total absolute schedule shift, shifted brews and bottlings with their absolute and largest shifts, batches moved to other tanks, unplaced batches, idle tank-days before and after, occupancy and makespan after replanning, the delayed batch's new bottling and end day and the batch next in its tank, and the scheduler's wall-clock time. The `control_*` columns hold the measures of the undisturbed replan on the signal day.
- `controls.csv` has one row per undisturbed replan, with `signal` false.

A target of 100% keeps every tank busy only as far as the brewhouse and the bottling line allow, so `baseline_occupancy` records the occupancy actually reached.
