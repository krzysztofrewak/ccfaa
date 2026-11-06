from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from brewery_schedule_digital_twin.domain.equipment import VesselRole
from brewery_schedule_digital_twin.scheduling.base import Scheduler, SchedulingError
from brewery_schedule_digital_twin.scheduling.heuristic import HeuristicScheduler
from brewery_schedule_digital_twin.scheduling.objective import RepairWeights, repair_cost

if TYPE_CHECKING:
    from brewery_schedule_digital_twin.domain import Batch, Brewery, FermentationAssignment, Schedule, Vessel

Genome = list[float]
GENES_PER_BATCH = 3


@dataclass(frozen=True)
class EvolutionReport:
    """The outcome of a replan by ``DifferentialEvolutionScheduler``.

    ``cost`` is the repair cost of the schedule returned and
    ``heuristic_cost`` that of ``HeuristicScheduler``'s, None if it found
    none; ``evaluations`` counts the schedules decoded and ``generations``
    the generations completed before the search stopped.
    """

    cost: int
    heuristic_cost: int | None
    evaluations: int
    generations: int
    stopped_by_limit: bool

    @property
    def improved(self) -> bool:
        return self.heuristic_cost is None or self.cost < self.heuristic_cost


class DifferentialEvolutionScheduler(Scheduler):
    """Repairs the schedule by differential evolution over the decisions of ``HeuristicScheduler``.

    A genome holds three keys in [0, 1] for each batch that is not locked,
    decoded by the greedy list scheduler of ``HeuristicScheduler``: the
    order key sets the order the batches are placed in; a keep key of 0.5
    or more lets a batch keep its current placement while it is still
    feasible, a smaller one places it anew; the vessel key picks, of the
    vessels that hold the batch on their own, the one at that fraction of
    the list sorted by capacity, 0 being the smallest. Locked assignments
    are reconciled as in ``HeuristicScheduler``.

    The cost of a genome is ``repair_cost`` of its schedule with
    ``weights``, the objective ``CpSatScheduler`` minimises; a genome that
    cannot be decoded into a schedule is discarded. The population of
    ``population_size`` starts with the genome that decodes into
    ``HeuristicScheduler``'s own schedule and random others, and evolves by
    DE/rand/1/bin with ``mutation`` (F) and ``crossover`` (CR): a trial
    replaces its target if it costs no more. The search stops after
    ``generations`` generations or ``time_limit_seconds``, whichever comes
    first, with the cheapest schedule found, so never worse than the
    heuristic's. The time limit is checked before every genome is decoded
    but the heuristic's, which always is; genomes of the first population
    left undecoded count as infeasible. Genomes that decode alike are
    evaluated once. With a fixed ``seed`` the search is deterministic
    unless the time limit stops it. ``last_report`` describes the last
    replan.
    """

    def __init__(
        self,
        population_size: int = 20,
        generations: int = 50,
        mutation: float = 0.5,
        crossover: float = 0.9,
        time_limit_seconds: float = 10.0,
        weights: RepairWeights | None = None,
        seed: int = 0,
    ) -> None:
        if population_size < 4:
            raise ValueError(f"population_size must be at least 4, got {population_size}")
        self._population_size = population_size
        self._generations = generations
        self._mutation = mutation
        self._crossover = crossover
        self._time_limit_seconds = time_limit_seconds
        self._weights = weights or RepairWeights()
        self._seed = seed
        self.last_report: EvolutionReport | None = None

    def replan(self, brewery: Brewery, schedule: Schedule, current_day: int) -> Schedule:
        schedule.lock_started_assignments(current_day)
        locked_batch_ids = {a.batch_id for a in schedule.assignments if a.locked}
        pending = [b for b in schedule.batches.values() if b.id not in locked_batch_ids]
        deadline = time.perf_counter() + self._time_limit_seconds
        rng = random.Random(self._seed)
        vessels = len(brewery.vessels_with(VesselRole.FULL))
        cache: dict[tuple[object, ...], tuple[float, Schedule | None]] = {}
        self.last_report = None

        def evaluate(genome: Genome) -> tuple[float, Schedule | None]:
            key = _decoded_key(genome, vessels)
            if key not in cache:
                decoder = _GenomeDecoder(pending, genome)
                try:
                    repaired = decoder.replan(brewery, schedule.clone(), current_day)
                except SchedulingError:
                    cache[key] = (math.inf, None)
                else:
                    cache[key] = (repair_cost(schedule, repaired, current_day, self._weights), repaired)
            return cache[key]

        def expired() -> bool:
            return time.perf_counter() > deadline

        dimensions = GENES_PER_BATCH * len(pending)
        population = [_heuristic_genome(pending)]
        population += [[rng.random() for _ in range(dimensions)] for _ in range(self._population_size - 1)]
        costs = [evaluate(population[0])[0]]
        costs += [math.inf if expired() else evaluate(genome)[0] for genome in population[1:]]
        heuristic_cost = None if math.isinf(costs[0]) else int(costs[0])
        generations = 0

        for _ in range(self._generations if dimensions else 0):
            if expired():
                break
            for i, target in enumerate(population):
                if expired():
                    break
                a, b, c = rng.sample([j for j in range(len(population)) if j != i], 3)
                forced = rng.randrange(dimensions)
                trial = [
                    min(1.0, max(0.0, population[a][d] + self._mutation * (population[b][d] - population[c][d])))
                    if d == forced or rng.random() < self._crossover
                    else target[d]
                    for d in range(dimensions)
                ]
                cost, _ = evaluate(trial)
                if cost <= costs[i]:
                    population[i], costs[i] = trial, cost
            else:
                generations += 1

        best = min(range(len(population)), key=lambda i: costs[i])
        cost, repaired = evaluate(population[best])
        if repaired is None:
            raise SchedulingError("differential evolution found no feasible schedule")
        stopped = dimensions > 0 and generations < self._generations
        self.last_report = EvolutionReport(int(cost), heuristic_cost, len(cache), generations, stopped)
        return repaired


def _heuristic_genome(pending: list[Batch]) -> Genome:
    """The genome that decodes into ``HeuristicScheduler``'s schedule: its order, keeping, smallest vessels."""
    ranks = {batch.id: rank for rank, batch in enumerate(sorted(pending, key=HeuristicScheduler().priority))}
    genome: Genome = []
    for batch in pending:
        genome += [(ranks[batch.id] + 0.5) / len(pending), 1.0, 0.0]
    return genome


def _decoded_key(genome: Genome, vessels: int) -> tuple[object, ...]:
    """What the decoder reads from a genome, so genomes that decode alike share it."""
    orders = genome[0::GENES_PER_BATCH]
    return (
        tuple(sorted(range(len(orders)), key=lambda i: orders[i])),
        tuple(key >= 0.5 for key in genome[1::GENES_PER_BATCH]),
        tuple(tuple(int(key * n) for n in range(1, vessels + 1)) for key in genome[2::GENES_PER_BATCH]),
    )


class _GenomeDecoder(HeuristicScheduler):
    """``HeuristicScheduler`` with its batch order, keeping and vessel choice read from a genome."""

    def __init__(self, pending: list[Batch], genome: Genome) -> None:
        super().__init__()
        self._order = {batch.id: genome[GENES_PER_BATCH * i] for i, batch in enumerate(pending)}
        self._keep = {batch.id: genome[GENES_PER_BATCH * i + 1] >= 0.5 for i, batch in enumerate(pending)}
        self._vessel_key = {batch.id: genome[GENES_PER_BATCH * i + 2] for i, batch in enumerate(pending)}
        self._batch_id: str | None = None

    def priority(self, batch: Batch) -> tuple[float, ...]:
        return (self._order[batch.id], batch.planned_brew_day)

    def _keep_existing_placement(
        self,
        brewery: Brewery,
        schedule: Schedule,
        batch: Batch,
        existing: list[FermentationAssignment],
        fermentation_days: int,
        current_day: int,
    ) -> bool:
        self._batch_id = batch.id
        if not self._keep[batch.id]:
            return False
        return super()._keep_existing_placement(brewery, schedule, batch, existing, fermentation_days, current_day)

    def _choose_vessel(self, fits: list[Vessel]) -> Vessel:
        if self._batch_id is None:
            return super()._choose_vessel(fits)
        ordered = sorted(fits, key=lambda f: f.capacity)
        return ordered[min(len(ordered) - 1, int(self._vessel_key[self._batch_id] * len(ordered)))]
