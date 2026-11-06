from __future__ import annotations

import dataclasses
import itertools
import math
import time
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ortools.sat.python import cp_model

from brewery_schedule_digital_twin.domain.batches import Brew, FermentationAssignment, Fill
from brewery_schedule_digital_twin.domain.equipment import VesselRole
from brewery_schedule_digital_twin.domain.schedule import Schedule
from brewery_schedule_digital_twin.scheduling.base import Scheduler, SchedulingError
from brewery_schedule_digital_twin.scheduling.heuristic import HeuristicScheduler
from brewery_schedule_digital_twin.scheduling.objective import RepairWeights

if TYPE_CHECKING:
    from brewery_schedule_digital_twin.domain import Batch, BottlingLine, Brewery, Recipe, Vessel

PourPlan = tuple[tuple[int, float], ...]


@dataclass(frozen=True)
class _PlannedBrew:
    """A brew of a batch to place; ``day`` is its day in the schedule being repaired, None for a new batch."""

    number: int
    recipe: Recipe | None
    volume: float
    day: int | None = None
    brewhouse: str | None = None


@dataclass(frozen=True)
class _Placement:
    """The vessels of a batch in pouring order, each with its volume and the (brew number, volume) of its fills."""

    vessels: tuple[tuple[Vessel, float, PourPlan], ...]

    @property
    def key(self) -> tuple[tuple[str, float], ...]:
        return tuple((vessel.id, round(volume, 6)) for vessel, volume, _ in self.vessels)


@dataclass
class _Bottling:
    """The bottling of one vessel on one line, in daily chunks of runs on increasing days.

    Absent chunks repeat the day of the previous one, so ``days[-1]`` is
    always the last bottling day.
    """

    line: BottlingLine
    chosen: cp_model.IntVar
    days: list[cp_model.IntVar]
    runs: list[cp_model.IntVar]
    present: list[cp_model.IntVar]


@dataclass(frozen=True)
class CpSatReport:
    """The outcome of a replan by ``CpSatScheduler``.

    ``status`` is the main solve's, e.g. OPTIMAL or FEASIBLE; ``objective``
    is the cost of the schedule returned and ``best_bound`` the solver's
    lower bound on it, both None without a schedule. ``hint_completed`` is
    None without a starting solution from ``HeuristicScheduler``, and
    ``returned_start`` tells whether the search found nothing better than
    it. ``model_seconds`` is the time before the limit starts: finding the
    heuristic's schedule and building the model.
    """

    status: str
    objective: float | None
    best_bound: float | None
    hint_completed: bool | None
    returned_start: bool | None
    model_seconds: float

    @property
    def gap(self) -> float | None:
        return None if self.objective is None or self.best_bound is None else self.objective - self.best_bound

    @property
    def optimal(self) -> bool:
        return self.status == "OPTIMAL"


class CpSatScheduler(Scheduler):
    """Repairs the schedule with a CP-SAT model of all decisions not yet taken.

    Locked assignments keep their fermenter and brew days; the bottling of
    a locked vessel that has not started is a decision of the model, on
    any line, from its last fermentation day or the current day. Every
    other batch gets the day and brewhouse of each brew, the vessels it
    goes into and the line and days of each vessel's bottling runs.

    The brews of a batch are those it already has, or else the fewest equal
    brews per recipe, as in ``HeuristicScheduler``; they are at most
    ``Brewery.max_brew_spread_days`` apart and none is before the batch's
    planned brew day or the current day. The vessels of a batch are one of
    a list of placements: its current one, the one ``HeuristicScheduler``
    finds, every single FULL vessel that holds it and the minimal
    largest-first combinations of FULL vessels, ``max_placements`` in all,
    the least wasteful first. A blend's brews are spread over its vessels
    in proportion to their volumes, other brews are poured in order. A
    vessel is bottled from its last fermentation day in up to
    ``extra_chunks`` more daily chunks of runs than the line needs, on
    working days, and is occupied from its first fill to its last run. A
    target bottling day is the day the batch's last run falls on.

    The objective keeps the plan close to the one being repaired: shifts
    of brews and of the last bottling runs, changed placements and
    brewhouses are penalised by ``weights``; batches without a placement
    are brewed and bottled as early as possible. ``repair_cost`` computes
    the same objective for any schedule.

    ``HeuristicScheduler``'s schedule, when it finds one, bounds the
    horizon and, completed by a first solve of up to half the time limit,
    is the solver's starting solution, so the result costs no more than
    the heuristic's: should the search find nothing better, the completed
    starting solution itself is returned. The solver stops after
    ``time_limit_seconds`` with the best schedule found and raises
    ``SchedulingError`` if it found none. With one worker (the default) the
    search is deterministic up to the time limit. ``solver_parameters``
    sets further CP-SAT parameters by name, e.g. ``{"symmetry_level": 0}``,
    in both solves. ``last_report`` describes the last replan.
    """

    def __init__(
        self,
        time_limit_seconds: float = 10.0,
        workers: int = 1,
        max_placements: int = 40,
        extra_chunks: int = 2,
        warm_start: bool = True,
        weights: RepairWeights | None = None,
        seed: int = 0,
        solver_parameters: Mapping[str, object] | None = None,
    ) -> None:
        self._time_limit_seconds = time_limit_seconds
        self._workers = workers
        self._max_placements = max_placements
        self._extra_chunks = extra_chunks
        self._warm_start = warm_start
        self._weights = weights or RepairWeights()
        self._seed = seed
        self._solver_parameters = dict(solver_parameters or {})
        self.last_report: CpSatReport | None = None

    def replan(self, brewery: Brewery, schedule: Schedule, current_day: int) -> Schedule:
        start = time.perf_counter()
        self.last_report = None
        schedule.lock_started_assignments(current_day)
        hint = self._heuristic_schedule(brewery, schedule, current_day) if self._warm_start else None

        model = _RepairModel(
            brewery, schedule, current_day, hint, self._max_placements, self._extra_chunks, self._weights
        )
        deadline = time.perf_counter() + self._time_limit_seconds
        model_seconds = deadline - self._time_limit_seconds - start
        start_solution = None if hint is None else self._complete_hint(model.model, self._time_limit_seconds / 2)
        solver = self._solver(max(0.1, deadline - time.perf_counter()))
        status = solver.solve(model.model)
        found = status in (cp_model.OPTIMAL, cp_model.FEASIBLE)
        best: cp_model.CpSolver | None = solver if found else None
        if start_solution is not None and (best is None or start_solution.objective_value < best.objective_value):
            best = start_solution
        self.last_report = CpSatReport(
            status=solver.status_name(status),
            objective=None if best is None else best.objective_value,
            best_bound=solver.best_objective_bound if found else None,
            hint_completed=None if hint is None else start_solution is not None,
            returned_start=None if best is None else best is start_solution,
            model_seconds=model_seconds,
        )
        if best is None:
            raise SchedulingError(f"CP-SAT found no feasible schedule ({self.last_report.status})")
        return model.extract(best)

    def _solver(self, time_limit_seconds: float) -> cp_model.CpSolver:
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = time_limit_seconds
        solver.parameters.num_workers = self._workers
        solver.parameters.random_seed = self._seed
        for name, value in self._solver_parameters.items():
            setattr(solver.parameters, name, value)
        return solver

    def _complete_hint(self, model: cp_model.CpModel, time_limit_seconds: float) -> cp_model.CpSolver | None:
        """Replaces the partial hint by a complete solution that agrees with it, if there is one; its solver.

        A search that starts from a partial hint often fails to complete it,
        so the starting solution would be lost; solving with the hinted
        variables fixed completes it in a moment.
        """
        solver = self._solver(time_limit_seconds)
        solver.parameters.fix_variables_to_their_hinted_value = True
        if solver.solve(model) not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return None
        model.clear_hints()
        for index in range(len(model.proto.variables)):
            variable = model.get_int_var_from_proto_index(index)
            model.add_hint(variable, solver.value(variable))
        return solver

    @staticmethod
    def _heuristic_schedule(brewery: Brewery, schedule: Schedule, current_day: int) -> Schedule | None:
        try:
            return HeuristicScheduler().replan(brewery, schedule.clone(), current_day)
        except SchedulingError:
            return None


class _RepairModel:
    def __init__(
        self,
        brewery: Brewery,
        schedule: Schedule,
        current_day: int,
        hint: Schedule | None,
        max_placements: int,
        extra_chunks: int,
        weights: RepairWeights,
    ) -> None:
        self.model = cp_model.CpModel()
        self._brewery = brewery
        self._schedule = schedule
        self._current_day = current_day
        self._hint = hint
        self._max_placements = max_placements
        self._extra_chunks = extra_chunks
        self._weights = weights

        self._locked = [a for a in schedule.assignments if a.locked]
        locked_batch_ids = {a.batch_id for a in self._locked}
        self._pending = [b for b in schedule.batches.values() if b.id not in locked_batch_ids]
        self._horizon = self._horizon_day()

        self._brew_usage: dict[str, list[tuple[cp_model.IntervalVar, int]]] = {b.name: [] for b in brewery.brewhouses}
        self._line_usage: dict[str, list[tuple[cp_model.IntervalVar, cp_model.LinearExprT]]] = {
            line.name: [] for line in brewery.bottling_lines
        }
        self._occupancy: dict[str, list[cp_model.IntervalVar]] = {}
        self._fixed_occupancy: dict[str, list[cp_model.IntervalVar]] = {}
        self._objective: list[cp_model.LinearExprT] = []

        self._locked_bottling: dict[int, list[_Bottling]] = {}
        self._brew_days: dict[str, dict[int, cp_model.IntVar]] = {}
        self._brewhouse_choice: dict[str, dict[int, dict[str, cp_model.IntVar]]] = {}
        self._placements: dict[str, list[tuple[_Placement, cp_model.IntVar]]] = {}
        self._vessel_bottling: dict[tuple[str, str], list[_Bottling]] = {}
        self._planned_brews: dict[str, list[_PlannedBrew]] = {}
        self._fermentation_days: dict[str, int] = {}

        self._add_locked_brews()
        for index, assignment in enumerate(self._locked):
            self._add_locked(index, assignment)
        for batch in self._pending:
            self._add_batch(batch)
        self._add_resources()
        self.model.minimize(sum(self._objective))

    def _horizon_day(self) -> int:
        """A day by which every schedule the model looks for has ended."""
        brewery = self._brewery
        ends = [self._current_day + 1] + [a.end_day for a in self._schedule.assignments]
        ends += [b.planned_brew_day + 1 for b in self._pending]
        ends += [b.target_bottling_day + 1 for b in self._pending if b.target_bottling_day is not None]
        longest = max((self._batch_fermentation_days(b) for b in self._pending), default=0)
        slowest_line = min(brewery.bottling_lines, key=lambda line: line.run_volume * line.max_runs_per_day)
        largest_vessel = max((v.capacity for v in brewery.vessels), default=0.0)
        vessel_bottling_days = math.ceil(slowest_line.runs_for(largest_vessel) / slowest_line.max_runs_per_day)
        if self._hint is not None:
            ends += [a.end_day for a in self._hint.assignments]
            slack = longest + brewery.max_brew_spread_days + vessel_bottling_days + 14
        else:
            per_batch = brewery.max_brew_spread_days + longest + vessel_bottling_days * len(brewery.vessels) + 1
            slack = per_batch * (len(self._pending) + 1) + 14
        horizon = max(ends) + slack
        return horizon + sum(1 for day in brewery.days_off if self._current_day <= day <= horizon)

    def _working_days_from(self, first_day: int) -> cp_model.Domain:
        days = [
            d for d in range(max(first_day, self._current_day), self._horizon + 1) if self._brewery.is_working_day(d)
        ]
        if not days:
            raise SchedulingError(f"no working day between day {first_day} and the horizon day {self._horizon}")
        return cp_model.Domain.from_values(days)

    def _abs_shift(self, expression: cp_model.LinearExprT, planned: int, name: str) -> cp_model.IntVar:
        shift = self.model.new_int_var(0, self._horizon + abs(planned), name)
        self.model.add_abs_equality(shift, expression - planned)
        return shift

    def _bottling(self, line: BottlingLine, max_runs: int, first_day: int, name: str) -> _Bottling:
        model = self.model
        per_day = line.max_runs_per_day
        chunks = min(max_runs, math.ceil(max_runs / per_day) + self._extra_chunks)
        domain = self._working_days_from(first_day)
        chosen = model.new_bool_var(f"{name}_on_{line.name}")
        days = [model.new_int_var_from_domain(domain, f"{name}_{line.name}_day{k}") for k in range(chunks)]
        runs = [model.new_int_var(0, per_day, f"{name}_{line.name}_runs{k}") for k in range(chunks)]
        present = [model.new_bool_var(f"{name}_{line.name}_chunk{k}") for k in range(chunks)]
        model.add(present[0] == chosen)
        for k in range(chunks):
            model.add(runs[k] >= 1).only_enforce_if(present[k])
            model.add(runs[k] == 0).only_enforce_if(~present[k])
            interval = model.new_optional_fixed_size_interval_var(days[k], 1, present[k], f"{name}_{line.name}_i{k}")
            self._line_usage[line.name].append((interval, runs[k]))
            if k > 0:
                model.add_implication(present[k], present[k - 1])
                model.add(days[k] >= days[k - 1] + 1).only_enforce_if(present[k])
                model.add(days[k] == days[k - 1]).only_enforce_if(~present[k])
        return _Bottling(line, chosen, days, runs, present)

    def _hint_bottling(self, bottlings: list[_Bottling], line_name: str | None, runs: tuple[int, ...]) -> None:
        line = self._brewery.bottling_line(line_name)
        per_day = sorted(Counter(runs).items())
        for bottling in bottlings:
            self.model.add_hint(bottling.chosen, bottling.line.name == line.name)
            if bottling.line.name != line.name or len(per_day) > len(bottling.days):
                continue
            for k in range(len(bottling.days)):
                day, count = per_day[min(k, len(per_day) - 1)]
                self.model.add_hint(bottling.days[k], day)
                self.model.add_hint(bottling.runs[k], count if k < len(per_day) else 0)
                self.model.add_hint(bottling.present[k], k < len(per_day))

    def _add_locked_brews(self) -> None:
        """Brews of locked batches from the current day on, which take brewhouse slots."""
        brews = {fill.brew for a in self._locked for fill in a.effective_fills if fill.brew.day >= self._current_day}
        slots = Counter((self._brewery.brewhouse(brew.brewhouse).name, brew.day) for brew in brews)
        for (brewhouse, day), count in slots.items():
            interval = self.model.new_fixed_size_interval_var(day, 1, f"locked_brews_{brewhouse}_{day}")
            self._brew_usage[brewhouse].append((interval, count))

    def _add_locked(self, index: int, assignment: FermentationAssignment) -> None:
        model = self.model
        name = f"locked{index}"
        vessel = assignment.fermenter.id
        if assignment.bottling_day is not None and assignment.bottling_day < self._current_day:
            line = self._brewery.bottling_line(assignment.bottling_line).name
            for day, count in Counter(assignment.effective_bottling_runs).items():
                if day >= self._current_day:
                    interval = model.new_fixed_size_interval_var(day, 1, f"{name}_run{day}")
                    self._line_usage[line].append((interval, count))
            occupied = model.new_fixed_size_interval_var(
                assignment.start_day, assignment.end_day - assignment.start_day, f"{name}_tank"
            )
            self._fixed_occupancy.setdefault(vessel, []).append(occupied)
            return

        first_day = max(assignment.last_fermentation_day, self._current_day)
        bottlings = [
            self._bottling(line, line.runs_for(assignment.volume), first_day, name)
            for line in self._brewery.bottling_lines
        ]
        model.add_exactly_one(b.chosen for b in bottlings)
        end = model.new_int_var(first_day + 1, self._horizon + 1, f"{name}_end")
        last = model.new_int_var(first_day, self._horizon, f"{name}_last")
        for bottling in bottlings:
            model.add(sum(bottling.runs) == bottling.line.runs_for(assignment.volume)).only_enforce_if(bottling.chosen)
            model.add(last == bottling.days[-1]).only_enforce_if(bottling.chosen)
        model.add(end == last + 1)
        occupied = model.new_interval_var(assignment.start_day, end - assignment.start_day, end, f"{name}_tank")
        self._fixed_occupancy.setdefault(vessel, []).append(occupied)
        self._objective.append(
            self._weights.bottling_shift * self._abs_shift(last, assignment.last_bottling_day, f"{name}_shift")
        )
        self._locked_bottling[index] = bottlings

        if self._hint is not None:
            hinted = next(
                (
                    a
                    for a in self._hint.assignments
                    if a.batch_id == assignment.batch_id and a.fermenter.id == assignment.fermenter.id
                ),
                None,
            )
            if hinted is not None and hinted.bottling_day is not None:
                self._hint_bottling(bottlings, hinted.bottling_line, hinted.effective_bottling_runs)

    def _batch_fermentation_days(self, batch: Batch) -> int:
        existing = self._schedule.assignments_of(batch.id)
        return existing[0].base_fermentation_days if existing else batch.recipe.fermentation_days

    def _brews_of(self, batch: Batch, existing: list[FermentationAssignment], volume: float) -> list[_PlannedBrew]:
        if existing:
            volumes: dict[Brew, float] = {}
            for assignment in existing:
                for fill in assignment.effective_fills:
                    volumes[fill.brew] = volumes.get(fill.brew, 0.0) + fill.volume
            return [
                _PlannedBrew(brew.number, brew.recipe, volume, brew.day, brew.brewhouse)
                for brew, volume in sorted(volumes.items(), key=lambda item: item[0].number)
            ]
        plan = HeuristicScheduler.brew_plan(batch, volume, self._brewery.kettle_yield)
        return [_PlannedBrew(number, recipe, volume) for number, (recipe, volume) in enumerate(plan, start=1)]

    def _add_batch(self, batch: Batch) -> None:
        model, brewery, weights = self.model, self._brewery, self._weights
        existing = self._schedule.assignments_of(batch.id)
        fermentation_days = self._batch_fermentation_days(batch)
        volume = HeuristicScheduler.batch_volume(batch, existing, brewery.kettle_yield)
        brews = self._brews_of(batch, existing, volume)
        earliest = max(self._current_day, batch.planned_brew_day)
        hinted = self._hint.assignments_of(batch.id) if self._hint is not None else []
        self._planned_brews[batch.id] = brews
        self._fermentation_days[batch.id] = fermentation_days

        days = {
            brew.number: model.new_int_var_from_domain(
                self._working_days_from(earliest), f"{batch.id}_brew{brew.number}"
            )
            for brew in brews
        }
        self._brew_days[batch.id] = days
        self._add_brewhouses(batch, brews, days)
        if len(brews) > 1:
            first = model.new_int_var(earliest, self._horizon, f"{batch.id}_first_brew")
            last = model.new_int_var(earliest, self._horizon, f"{batch.id}_last_brew")
            model.add_min_equality(first, list(days.values()))
            model.add_max_equality(last, list(days.values()))
            model.add(last - first <= brewery.max_brew_spread_days)
            if not existing:
                for earlier, later in itertools.pairwise(brews):
                    if earlier.recipe == later.recipe and math.isclose(earlier.volume, later.volume):
                        model.add(days[earlier.number] <= days[later.number])
        else:
            first = days[brews[0].number]

        placements = self._placement_options(batch, volume, existing, hinted, brews)
        if not placements:
            raise SchedulingError(f"no FULL vessels hold the {volume:g} l of batch {batch.id!r}")
        chosen = [model.new_bool_var(f"{batch.id}_placement{i}") for i in range(len(placements))]
        model.add_exactly_one(chosen)
        self._placements[batch.id] = list(zip(placements, chosen, strict=True))

        bottling_end = model.new_int_var(self._current_day, self._horizon, f"{batch.id}_bottling_end")
        ends_here: list[cp_model.IntVar] = []
        vessels = {vessel.id: vessel for placement in placements for vessel, _, _ in placement.vessels}
        for vessel_id in vessels:
            ends_here += self._add_vessel(batch, vessel_id, placements, chosen, days, fermentation_days, bottling_end)
        model.add_exactly_one(ends_here)
        if batch.target_bottling_day is not None:
            model.add(bottling_end == batch.target_bottling_day)

        if existing:
            for brew in brews:
                assert brew.day is not None
                shift = self._abs_shift(days[brew.number], brew.day, f"{batch.id}_brew{brew.number}_shift")
                self._objective.append(weights.brew_shift * shift)
            last_bottling = max(a.last_bottling_day for a in existing)
            shift = self._abs_shift(bottling_end, last_bottling, f"{batch.id}_bottling_shift")
            self._objective.append(weights.bottling_shift * shift)
            current = self._existing_placement(existing)
            for placement, literal in self._placements[batch.id]:
                if current is None or placement.key != current.key:
                    self._objective.append(weights.placement_change * literal)
        else:
            self._objective.append(weights.lateness * (first - earliest))
            self._objective.append(weights.lateness * (bottling_end - (earliest + fermentation_days - 1)))

        if hinted:
            self._hint_batch(batch, hinted, days, chosen, placements, bottling_end)

    def _add_brewhouses(self, batch: Batch, brews: list[_PlannedBrew], days: dict[int, cp_model.IntVar]) -> None:
        model, brewery = self.model, self._brewery
        choices: dict[int, dict[str, cp_model.IntVar]] = {}
        for brew in brews:
            name = f"{batch.id}_brew{brew.number}"
            eligible = [b for b in brewery.brewhouses if b.kettle_yield >= brew.volume - 1e-9]
            if not eligible:
                raise SchedulingError(f"no brewhouse makes the {brew.volume:g} l brew {name!r}")
            if len(brewery.brewhouses) == 1:
                interval = model.new_fixed_size_interval_var(days[brew.number], 1, f"{name}_slot")
                self._brew_usage[eligible[0].name].append((interval, 1))
                continue
            literals = {b.name: model.new_bool_var(f"{name}_in_{b.name}") for b in eligible}
            model.add_exactly_one(literals.values())
            for brewhouse, literal in literals.items():
                interval = model.new_optional_fixed_size_interval_var(
                    days[brew.number], 1, literal, f"{name}_slot_{brewhouse}"
                )
                self._brew_usage[brewhouse].append((interval, 1))
            if brew.brewhouse in literals:
                self._objective.append(self._weights.brewhouse_change * (1 - literals[brew.brewhouse]))
            choices[brew.number] = literals
        self._brewhouse_choice[batch.id] = choices

    def _add_vessel(
        self,
        batch: Batch,
        vessel_id: str,
        placements: list[_Placement],
        chosen: list[cp_model.IntVar],
        days: dict[int, cp_model.IntVar],
        fermentation_days: int,
        bottling_end: cp_model.IntVar,
    ) -> list[cp_model.IntVar]:
        """The vessel's occupancy and bottling in the batch; the literals of it bottling the batch last."""
        model = self.model
        name = f"{batch.id}_{vessel_id}"
        earliest = max(self._current_day, batch.planned_brew_day)
        uses = [
            (literal, volume, fills)
            for placement, literal in zip(placements, chosen, strict=True)
            for vessel, volume, fills in placement.vessels
            if vessel.id == vessel_id
        ]
        used = model.new_bool_var(f"{name}_used")
        model.add(used == sum(literal for literal, _, _ in uses))

        start = model.new_int_var(earliest, self._horizon, f"{name}_start")
        end = model.new_int_var(earliest, self._horizon + 1, f"{name}_end")
        size = model.new_int_var(0, self._horizon + 1, f"{name}_size")
        occupied = model.new_optional_interval_var(start, size, end, used, f"{name}_tank")
        self._occupancy.setdefault(vessel_id, []).append(occupied)

        bottlings: list[_Bottling] = []
        ends_here: list[cp_model.IntVar] = []
        for line in self._brewery.bottling_lines:
            max_runs = max(line.runs_for(volume) for _, volume, _ in uses)
            bottling = self._bottling(line, max_runs, earliest + fermentation_days - 1, name)
            needed = sum(literal * line.runs_for(volume) for literal, volume, _ in uses)
            model.add(sum(bottling.runs) == needed).only_enforce_if(bottling.chosen)
            model.add(end == bottling.days[-1] + 1).only_enforce_if(bottling.chosen)
            model.add(bottling.days[-1] <= bottling_end).only_enforce_if(bottling.chosen)
            last_here = model.new_bool_var(f"{name}_{line.name}_ends_bottling")
            model.add_implication(last_here, bottling.chosen)
            model.add(bottling_end == bottling.days[-1]).only_enforce_if(last_here)
            for literal, _, fills in uses:
                for number, _ in fills:
                    model.add(bottling.days[0] >= days[number] + fermentation_days - 1).only_enforce_if(
                        [literal, bottling.chosen]
                    )
            bottlings.append(bottling)
            ends_here.append(last_here)
        model.add(sum(b.chosen for b in bottlings) == used)
        for literal, _, fills in uses:
            for number, _ in fills:
                model.add(start <= days[number]).only_enforce_if(literal)
        self._vessel_bottling[(batch.id, vessel_id)] = bottlings
        return ends_here

    def _hint_batch(
        self,
        batch: Batch,
        hinted: list[FermentationAssignment],
        days: dict[int, cp_model.IntVar],
        chosen: list[cp_model.IntVar],
        placements: list[_Placement],
        bottling_end: cp_model.IntVar,
    ) -> None:
        key = tuple((a.fermenter.id, round(a.volume, 6)) for a in hinted)
        for placement, literal in zip(placements, chosen, strict=True):
            self.model.add_hint(literal, placement.key == key)
        brew_days = {fill.brew.number: fill.brew.day for a in hinted for fill in a.effective_fills}
        if set(brew_days) == set(days):
            for number, day in brew_days.items():
                self.model.add_hint(days[number], day)
        for assignment in hinted:
            if assignment.bottling_day is not None:
                self._hint_bottling(
                    self._vessel_bottling[(batch.id, assignment.fermenter.id)],
                    assignment.bottling_line,
                    assignment.effective_bottling_runs,
                )
        self.model.add_hint(bottling_end, max(a.last_bottling_day for a in hinted))

    def _existing_placement(self, existing: list[FermentationAssignment]) -> _Placement | None:
        if not existing or not all(a.fermenter.has_role(VesselRole.FULL) for a in existing):
            return None
        return _Placement(
            tuple(
                (a.fermenter, a.volume, tuple((fill.brew.number, fill.volume) for fill in a.effective_fills))
                for a in existing
            )
        )

    def _placement_options(
        self,
        batch: Batch,
        volume: float,
        existing: list[FermentationAssignment],
        hinted: list[FermentationAssignment],
        brews: list[_PlannedBrew],
    ) -> list[_Placement]:
        """The current and the heuristic's placement, then the least wasteful others, up to ``max_placements``."""
        options: dict[tuple[tuple[str, float], ...], _Placement] = {}
        current = self._existing_placement(existing)
        if current is not None:
            options[current.key] = current
        if hinted and all(a.fermenter.has_role(VesselRole.FULL) for a in hinted):
            placement = self._poured(batch, [(a.fermenter, a.volume) for a in hinted], brews)
            options.setdefault(placement.key, placement)

        full = sorted(self._brewery.vessels_with(VesselRole.FULL), key=lambda v: (-v.capacity, v.index))
        combinations: list[tuple[float, int, tuple[Vessel, ...]]] = []
        for size in range(1, len(full) + 1):
            for vessels in itertools.combinations(full, size):
                total = sum(v.capacity for v in vessels)
                if total >= volume - 1e-9 and total - vessels[-1].capacity < volume - 1e-9:
                    combinations.append((total - volume, size, vessels))
                if len(combinations) >= 20 * self._max_placements:
                    break
            if combinations and (size > 1 or len(combinations) >= self._max_placements):
                break
        combinations.sort(key=lambda item: (item[0], item[1], [v.index for v in item[2]]))
        for _, _, vessels in combinations:
            if len(options) >= self._max_placements:
                break
            allocations = HeuristicScheduler.allocations(list(vessels), volume)
            placement = self._poured(batch, list(zip(vessels, allocations, strict=True)), brews)
            options.setdefault(placement.key, placement)
        return list(options.values())

    @staticmethod
    def _poured(batch: Batch, vessels: list[tuple[Vessel, float]], brews: list[_PlannedBrew]) -> _Placement:
        """A blend's brews spread over the vessels in proportion to their volumes, other brews poured in order."""
        total = sum(volume for _, volume in vessels)
        if batch.blend is not None:
            return _Placement(
                tuple(
                    (vessel, volume, tuple((brew.number, brew.volume * volume / total) for brew in brews))
                    for vessel, volume in vessels
                )
            )
        unpoured = [brew.volume for brew in brews]
        current = 0
        poured: list[tuple[Vessel, float, PourPlan]] = []
        for vessel, room_volume in vessels:
            fills: list[tuple[int, float]] = []
            room = room_volume
            while current < len(brews) and room > 1e-9:
                amount = min(unpoured[current], room)
                fills.append((brews[current].number, amount))
                room -= amount
                unpoured[current] -= amount
                if unpoured[current] <= 1e-9:
                    current += 1
            poured.append((vessel, room_volume, tuple(fills)))
        return _Placement(tuple(poured))

    def _add_resources(self) -> None:
        """Brewhouse and line capacities, and no overlap in a vessel; locked assignments only with pending ones."""
        model, brewery = self.model, self._brewery
        for brewhouse in brewery.brewhouses:
            usage = self._brew_usage[brewhouse.name]
            if usage:
                model.add_cumulative([i for i, _ in usage], [d for _, d in usage], brewhouse.max_brews_per_day)
        for line in brewery.bottling_lines:
            line_usage = self._line_usage[line.name]
            if line_usage:
                model.add_cumulative([i for i, _ in line_usage], [d for _, d in line_usage], line.max_runs_per_day)
        for vessel in brewery.vessels:
            pending = self._occupancy.get(vessel.id, [])
            if not pending:
                continue
            maintenance = [
                model.new_fixed_size_interval_var(start, end - start, f"maintenance_{vessel.id}_{start}")
                for start, end in brewery.fermenter_maintenance_windows(vessel.id)
            ]
            model.add_no_overlap(pending + maintenance)
            for occupied in self._fixed_occupancy.get(vessel.id, []):
                model.add_no_overlap([*pending, occupied])

    def extract(self, solver: cp_model.CpSolver) -> Schedule:
        brewery = self._brewery
        schedule = Schedule(batches=dict(self._schedule.batches))
        for index, assignment in enumerate(self._locked):
            bottlings = self._locked_bottling.get(index)
            if bottlings is None:
                schedule.add_assignment(assignment)
                continue
            line, runs = self._solved_bottling(solver, bottlings)
            schedule.add_assignment(
                dataclasses.replace(
                    assignment, bottling_day=runs[0], bottling_line=line, bottling_runs=self._stored(runs)
                )
            )

        for batch in self._pending:
            days = {number: solver.value(day) for number, day in self._brew_days[batch.id].items()}
            choices = self._brewhouse_choice[batch.id]
            brews = {
                brew.number: Brew(
                    batch.id,
                    brew.number,
                    days[brew.number],
                    brew.recipe,
                    next(name for name, literal in choices[brew.number].items() if solver.boolean_value(literal))
                    if brew.number in choices
                    else None,
                )
                for brew in self._planned_brews[batch.id]
            }
            implicit = len(brews) == 1 and all(b.recipe is None and b.brewhouse is None for b in brews.values())
            placement = next(p for p, literal in self._placements[batch.id] if solver.boolean_value(literal))
            for vessel, volume, pour in placement.vessels:
                line, runs = self._solved_bottling(solver, self._vessel_bottling[(batch.id, vessel.id)])
                fills = () if implicit else tuple(Fill(brews[number], amount) for number, amount in pour)
                schedule.add_assignment(
                    FermentationAssignment(
                        batch_id=batch.id,
                        fermenter=vessel,
                        volume=volume,
                        start_day=min(brews[number].day for number, _ in pour),
                        base_fermentation_days=self._fermentation_days[batch.id],
                        bottling_day=runs[0],
                        fills=fills,
                        bottling_line=line if len(brewery.bottling_lines) > 1 else None,
                        bottling_runs=self._stored(runs),
                    )
                )
        return schedule

    def _solved_bottling(
        self, solver: cp_model.CpSolver, bottlings: list[_Bottling]
    ) -> tuple[str | None, tuple[int, ...]]:
        bottling = next(b for b in bottlings if solver.boolean_value(b.chosen))
        runs = [
            solver.value(day)
            for day, count, present in zip(bottling.days, bottling.runs, bottling.present, strict=True)
            if solver.boolean_value(present)
            for _ in range(solver.value(count))
        ]
        line = bottling.line.name if len(self._brewery.bottling_lines) > 1 else None
        return line, tuple(runs)

    @staticmethod
    def _stored(runs: tuple[int, ...]) -> tuple[int, ...]:
        return HeuristicScheduler.stored_runs(runs)
