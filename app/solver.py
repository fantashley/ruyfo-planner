"""RUYFO logistics optimizer.

The problem is a small capacitated, time-expanded multi-commodity flow:

* Three resource types are tracked over time: **people**, **bikes**, **cars**.
* Each resource has a position at each time point, drawn from three abstract
  locations: ``H`` (that resource's *own home*), ``S`` (the route start) and
  ``F`` (the route finish).
* Time advances through 8 transitions (9 time points) that model the night
  before, the morning of, the ride itself, the evening, and the next morning.
  Two transitions per "phase" let a car ferry someone *and* then drive home.
* Cars are the only things that move people/bikes between locations (except the
  ride itself and an optional next-morning "bike back"). A car only moves when
  its owner is in it (the owner is always the driver).

We minimise total driving distance (+ an approximate pickup-detour term) plus a
penalty for putting anyone on a merely-*acceptable* return option instead of
their *preferred* one. With <=15 riders CP-SAT solves this to optimality fast.

See ``/Users/ashley/.claude/plans/squishy-twirling-axolotl.md`` for the design.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from ortools.sat.python import cp_model

from . import geo
from .events import Route

# --------------------------------------------------------------------------- #
# Inputs
# --------------------------------------------------------------------------- #

H, S, F = "H", "S", "F"
LOCS = (H, S, F)


class ReturnOption(str, Enum):
    DRIVE_HOME_TONIGHT = "drive_home_tonight"
    HOTEL_BIKE_BACK = "hotel_bike_back"
    HOTEL_RIDE_HOME = "hotel_ride_home_next_morning"


class Pref(str, Enum):
    PREFERRED = "preferred"
    ACCEPTABLE = "acceptable"
    UNWILLING = "unwilling"


@dataclass(frozen=True)
class CarCombo:
    """One way a car can be loaded: ``people`` seats (incl. driver) + ``bikes``."""

    people: int
    bikes: int


@dataclass
class Person:
    id: str
    name: str
    home_zip: str
    household: str = ""  # blank => own household (id used)
    is_rider: bool = True
    num_bikes: int = 1  # bikes this person needs transported (0 for a bike-less supporter)

    # car
    has_car: bool = False
    car_combos: list[CarCombo] = field(default_factory=list)

    # willingness gates
    willing_drop_car: bool = False  # leave car at finish overnight
    willing_drop_bikes_at_start: bool = False  # night-before bike shuttle to start
    willing_drive_dropper_home: bool = False  # night-before ride home for droppers
    can_drive_morning: bool = False  # ferry others to the start the morning of
    is_sag_driver: bool = False  # drives the SAG wagon (route sweep)

    # return preferences: every option marked preferred / acceptable / unwilling
    return_prefs: dict[ReturnOption, Pref] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.household:
            self.household = self.id
        if self.has_car and not self.car_combos:
            # sensible default: a 5-seat car with a 2-bike rack
            self.car_combos = [CarCombo(people=5, bikes=2)]
        if not self.is_rider:
            self.num_bikes = 0
        if self.is_rider and not self.return_prefs:
            # default: prefer driving home tonight, accept the rest
            self.return_prefs = {
                ReturnOption.DRIVE_HOME_TONIGHT: Pref.PREFERRED,
                ReturnOption.HOTEL_BIKE_BACK: Pref.ACCEPTABLE,
                ReturnOption.HOTEL_RIDE_HOME: Pref.ACCEPTABLE,
            }

    def pref(self, option: ReturnOption) -> Pref:
        return self.return_prefs.get(option, Pref.UNWILLING)


@dataclass
class Problem:
    route: Route
    people: list[Person]
    has_sag: bool = False
    # tuning knobs (all in "miles" units)
    detour_factor: float = 1.0  # multiplier on pickup-detour distance
    pref_penalty_miles: float = 30.0  # cost of a merely-acceptable return option


# --------------------------------------------------------------------------- #
# Time structure
# --------------------------------------------------------------------------- #

# 8 transitions connect 9 time points (t0..t8).
NK = 8
T_RIDE = 3  # transition index of the ride (t3 -> t4): start -> finish
T_BIKEBACK = 6  # next-morning bike-back transition (finish -> start)
T_HOME_TONIGHT = 6  # being home at time point 6 == "made it home tonight"

TRANSITION_LABELS = [
    "Night before — drive out",
    "Night before — return home",
    "Morning of the ride",
    "The ride",
    "Evening — after the ride",
    "Evening — continued",
    "Next morning",
    "Next morning — continued",
]

# Which directed arcs are even worth creating at each transition (pruning that
# never traps a resource). k0/k1 are the night-before round trip; the rest are
# left general so nobody gets stuck.
_ALL_ARCS = [(a, b) for a in LOCS for b in LOCS if a != b]
ALLOWED_ARCS: dict[int, list[tuple[str, str]]] = {
    0: [(H, F), (H, S)],
    1: [(F, H), (S, H), (F, S), (S, F)],
    2: _ALL_ARCS,
    3: _ALL_ARCS,
    4: _ALL_ARCS,
    5: _ALL_ARCS,
    6: _ALL_ARCS,
    7: _ALL_ARCS,
}


# --------------------------------------------------------------------------- #
# Distances
# --------------------------------------------------------------------------- #

SCALE = 10  # miles -> integer units
ZERO_MI = 0.05  # legs shorter than this are "you already live there" no-ops


def _arc_distance(person_home_zip: str, route: Route, frm: str, to: str) -> float:
    """Straight-line miles for a car driving ``frm -> to`` from this owner's home."""
    coord = {
        H: geo.latlon(person_home_zip),
        S: geo.latlon(route.start_zip),
        F: geo.latlon(route.finish_zip),
    }
    return geo.haversine_miles(coord[frm], coord[to])


# --------------------------------------------------------------------------- #
# Model
# --------------------------------------------------------------------------- #


@dataclass
class Solution:
    status: str  # "optimal" | "feasible" | "infeasible"
    total_drive_miles: float = 0.0
    pref_deviations: int = 0
    itineraries: dict[str, list[str]] = field(default_factory=dict)  # person id -> steps
    car_moves: list[str] = field(default_factory=list)
    return_outcome: dict[str, ReturnOption] = field(default_factory=dict)
    message: str = ""


class _Model:
    def __init__(self, problem: Problem):
        self.p = problem
        self.route = problem.route
        self.people = problem.people
        self.by_id = {p.id: p for p in self.people}
        self.m = cp_model.CpModel()

        self.owners = [p for p in self.people if p.has_car]
        # the SAG wagon is the car of the designated SAG driver
        self.sag = next(
            (p for p in self.owners if p.is_sag_driver), None
        ) if problem.has_sag else None

        self._build()

    # -- variable builders -------------------------------------------------- #
    def _build(self):
        m = self.m
        self.cat = {}  # (owner, t, loc) -> bool : car at loc
        self.cmove = {}  # (owner, k, frm, to) -> bool
        self.cstay = {}  # (owner, k, loc) -> bool
        self.combosel = {}  # (owner, k, frm, to, combo_idx) -> bool

        for o in self.owners:
            for t in range(NK + 1):
                for l in LOCS:
                    self.cat[o.id, t, l] = m.NewBoolVar(f"cat_{o.id}_{t}_{l}")
                m.Add(sum(self.cat[o.id, t, l] for l in LOCS) == 1)
            # starts and ends at home
            m.Add(self.cat[o.id, 0, H] == 1)
            m.Add(self.cat[o.id, NK, H] == 1)
            for k in range(NK):
                for l in LOCS:
                    self.cstay[o.id, k, l] = m.NewBoolVar(f"cstay_{o.id}_{k}_{l}")
                for (a, b) in ALLOWED_ARCS[k]:
                    self.cmove[o.id, k, a, b] = m.NewBoolVar(f"cmove_{o.id}_{k}_{a}{b}")
                    for ci in range(len(o.car_combos)):
                        self.combosel[o.id, k, a, b, ci] = m.NewBoolVar(
                            f"combo_{o.id}_{k}_{a}{b}_{ci}"
                        )
                    m.Add(
                        sum(
                            self.combosel[o.id, k, a, b, ci]
                            for ci in range(len(o.car_combos))
                        )
                        == self.cmove[o.id, k, a, b]
                    )
                # car flow conservation
                for l in LOCS:
                    out = [self.cstay[o.id, k, l]] + [
                        self.cmove[o.id, k, a, b]
                        for (a, b) in ALLOWED_ARCS[k]
                        if a == l
                    ]
                    m.Add(self.cat[o.id, k, l] == sum(out))
                for l in LOCS:
                    inn = [self.cstay[o.id, k, l]] + [
                        self.cmove[o.id, k, a, b]
                        for (a, b) in ALLOWED_ARCS[k]
                        if b == l
                    ]
                    m.Add(self.cat[o.id, k + 1, l] == sum(inn))

        # ---- people ------------------------------------------------------- #
        self.pat = {}  # (pid, t, loc)
        self.pstay = {}
        self.incar = {}  # (pid, owner, k, frm, to) -> bool : person rides this car
        self.bike_self = {}  # (pid, k, frm, to) -> bool : person self-powers (bicycle)

        for p in self.people:
            for t in range(NK + 1):
                for l in LOCS:
                    self.pat[p.id, t, l] = m.NewBoolVar(f"pat_{p.id}_{t}_{l}")
                m.Add(sum(self.pat[p.id, t, l] for l in LOCS) == 1)
            m.Add(self.pat[p.id, 0, H] == 1)
            m.Add(self.pat[p.id, NK, H] == 1)
            if p.is_rider:
                m.Add(self.pat[p.id, T_RIDE, S] == 1)  # at start for the ride
                m.Add(self.pat[p.id, T_RIDE + 1, F] == 1)  # at finish after the ride

            for k in range(NK):
                for l in LOCS:
                    self.pstay[p.id, k, l] = m.NewBoolVar(f"pstay_{p.id}_{k}_{l}")
                for (a, b) in ALLOWED_ARCS[k]:
                    # bicycle moves: the ride (S->F) and a next-morning bike-back (F->S)
                    if p.is_rider and (
                        (k == T_RIDE and (a, b) == (S, F))
                        or (k == T_BIKEBACK and (a, b) == (F, S))
                    ):
                        self.bike_self[p.id, k, a, b] = m.NewBoolVar(
                            f"bike_{p.id}_{k}_{a}{b}"
                        )
                    for o in self.owners:
                        if (o.id, k, a, b) not in self.cmove:
                            continue
                        # a rider cannot be *chauffeured* start->finish during the
                        # ride; they must bicycle (or ride the SAG wagon)
                        if (
                            k == T_RIDE
                            and (a, b) == (S, F)
                            and p.is_rider
                            and not (self.sag and o.id == self.sag.id)
                        ):
                            continue
                        self.incar[p.id, o.id, k, a, b] = m.NewBoolVar(
                            f"in_{p.id}_{o.id}_{k}_{a}{b}"
                        )

            # person flow conservation
            for k in range(NK):
                for l in LOCS:
                    carried_out = self._person_carried(p.id, k, frm=l)
                    m.Add(
                        self.pat[p.id, k, l]
                        == self.pstay[p.id, k, l] + sum(carried_out)
                    )
                for l in LOCS:
                    carried_in = self._person_carried(p.id, k, to=l)
                    m.Add(
                        self.pat[p.id, k + 1, l]
                        == self.pstay[p.id, k, l] + sum(carried_in)
                    )

        # ---- driver presence + capacity (people) -------------------------- #
        for o in self.owners:
            for k in range(NK):
                for (a, b) in ALLOWED_ARCS[k]:
                    move = self.cmove[o.id, k, a, b]
                    # owner drives their own car whenever it moves; if the owner
                    # can't be in it on this arc (a rider can't drive during the
                    # ride) then the car simply can't make that move
                    if (o.id, o.id, k, a, b) in self.incar:
                        m.Add(self.incar[o.id, o.id, k, a, b] == move)
                    else:
                        m.Add(move == 0)
                    riders_in = [
                        self.incar[p.id, o.id, k, a, b]
                        for p in self.people
                        if (p.id, o.id, k, a, b) in self.incar
                    ]
                    # people capacity from the chosen combo
                    m.Add(
                        sum(riders_in)
                        <= sum(
                            self.combosel[o.id, k, a, b, ci] * o.car_combos[ci].people
                            for ci in range(len(o.car_combos))
                        )
                    )

        # ---- gates on stated willingness ---------------------------------- #
        self._apply_gates()

        # ---- bikes -------------------------------------------------------- #
        self._build_bikes()

        # ---- return options + objective ----------------------------------- #
        self._build_returns_and_objective()

    def _person_carried(self, pid, k, frm=None, to=None):
        """All ways person ``pid`` traverses an arc at step ``k`` (filtered by end)."""
        out = []
        for (a, b) in ALLOWED_ARCS[k]:
            if frm is not None and a != frm:
                continue
            if to is not None and b != to:
                continue
            if (pid, k, a, b) in self.bike_self:
                out.append(self.bike_self[pid, k, a, b])
            for o in self.owners:
                if (pid, o.id, k, a, b) in self.incar:
                    out.append(self.incar[pid, o.id, k, a, b])
        return out

    def _apply_gates(self):
        m = self.m
        for o in self.owners:
            # leaving a car at the finish overnight requires willingness to drop
            if not o.willing_drop_car:
                m.Add(self.cat[o.id, 2, F] == 0)
            # night-before bike shuttle to the start
            if not o.willing_drop_bikes_at_start and (o.id, 0, H, S) in self.cmove:
                m.Add(self.cmove[o.id, 0, H, S] == 0)
            # night-before: carrying *droppers* home. The willingness flag only
            # governs giving rides to people outside your own household — family
            # sharing the car always travels together.
            if not o.willing_drive_dropper_home:
                for p in self.people:
                    if (
                        p.household != o.household
                        and (p.id, o.id, 1, F, H) in self.incar
                    ):
                        m.Add(self.incar[p.id, o.id, 1, F, H] == 0)
            # morning: carrying *others* to the start (household members exempt)
            if not o.can_drive_morning:
                for p in self.people:
                    if (
                        p.household != o.household
                        and (p.id, o.id, 2, H, S) in self.incar
                    ):
                        m.Add(self.incar[p.id, o.id, 2, H, S] == 0)

    def _build_bikes(self):
        m = self.m
        owners_with_bikes = [p for p in self.people if p.num_bikes > 0]
        self.bat = {}  # (owner_pid, t, loc) -> int
        self.bstay = {}
        self.bincar = {}  # (owner_pid, car_owner, k, frm, to) -> int

        for ob in owners_with_bikes:
            nb = ob.num_bikes
            for t in range(NK + 1):
                for l in LOCS:
                    self.bat[ob.id, t, l] = m.NewIntVar(0, nb, f"bat_{ob.id}_{t}_{l}")
                m.Add(sum(self.bat[ob.id, t, l] for l in LOCS) == nb)
            m.Add(self.bat[ob.id, 0, H] == nb)  # bikes start at home
            m.Add(self.bat[ob.id, NK, H] == nb)  # ... and end at home
            if ob.is_rider:
                m.Add(self.bat[ob.id, T_RIDE, S] == nb)  # all at start for the ride
                m.Add(self.bat[ob.id, T_RIDE + 1, F] == nb)  # all at finish after

            for k in range(NK):
                for l in LOCS:
                    self.bstay[ob.id, k, l] = m.NewIntVar(0, nb, f"bstay_{ob.id}_{k}_{l}")
                for (a, b) in ALLOWED_ARCS[k]:
                    for o in self.owners:
                        if (o.id, k, a, b) in self.cmove:
                            self.bincar[ob.id, o.id, k, a, b] = m.NewIntVar(
                                0, nb, f"binc_{ob.id}_{o.id}_{k}_{a}{b}"
                            )
                # bike flow conservation
                for l in LOCS:
                    out = [self.bstay[ob.id, k, l]] + self._bike_moved(ob.id, k, frm=l)
                    m.Add(self.bat[ob.id, k, l] == sum(out))
                for l in LOCS:
                    inn = [self.bstay[ob.id, k, l]] + self._bike_moved(ob.id, k, to=l)
                    m.Add(self.bat[ob.id, k + 1, l] == sum(inn))

        # bike capacity per car move + a bicyclist carries exactly one own bike
        for o in self.owners:
            for k in range(NK):
                for (a, b) in ALLOWED_ARCS[k]:
                    bikes_in = [
                        self.bincar[ob.id, o.id, k, a, b]
                        for ob in owners_with_bikes
                        if (ob.id, o.id, k, a, b) in self.bincar
                    ]
                    m.Add(
                        sum(bikes_in)
                        <= sum(
                            self.combosel[o.id, k, a, b, ci] * o.car_combos[ci].bikes
                            for ci in range(len(o.car_combos))
                        )
                    )
        # link a self-powered bicycle leg to carrying one of the rider's own bikes
        for (pid, k, a, b), var in self.bike_self.items():
            # represented in _bike_moved via this same var (coefficient 1)
            pass

    def _bike_moved(self, ob_id, k, frm=None, to=None):
        terms = []
        for (a, b) in ALLOWED_ARCS[k]:
            if frm is not None and a != frm:
                continue
            if to is not None and b != to:
                continue
            for o in self.owners:
                if (ob_id, o.id, k, a, b) in self.bincar:
                    terms.append(self.bincar[ob_id, o.id, k, a, b])
            # the owner self-powering a bicycle carries one of their own bikes
            if (ob_id, k, a, b) in self.bike_self:
                terms.append(self.bike_self[ob_id, k, a, b])
        return terms

    def _build_returns_and_objective(self):
        m = self.m
        self.home_tonight = {}
        self.opt_bikeback = {}
        self.opt_ridehome = {}
        riders = [p for p in self.people if p.is_rider]

        pref_terms = []
        for p in riders:
            ht = self.pat[p.id, T_HOME_TONIGHT, H]
            self.home_tonight[p.id] = ht
            bb = self.bike_self.get((p.id, T_BIKEBACK, F, S))
            if bb is None:
                bb = m.NewBoolVar(f"nobikeback_{p.id}")
                m.Add(bb == 0)
            self.opt_bikeback[p.id] = bb
            m.Add(bb + ht <= 1)  # can't bike back if already home tonight
            rh = m.NewBoolVar(f"ridehome_{p.id}")
            m.Add(rh == 1 - ht - bb)
            self.opt_ridehome[p.id] = rh

            options = {
                ReturnOption.DRIVE_HOME_TONIGHT: ht,
                ReturnOption.HOTEL_BIKE_BACK: bb,
                ReturnOption.HOTEL_RIDE_HOME: rh,
            }
            for opt, var in options.items():
                pr = p.pref(opt)
                if pr == Pref.UNWILLING:
                    m.Add(var == 0)
                elif pr == Pref.ACCEPTABLE:
                    pref_terms.append(var)

        # objective: driving distance (+ pickup detours) + preference penalty
        dist_terms = []
        for o in self.owners:
            for k in range(NK):
                for (a, b) in ALLOWED_ARCS[k]:
                    base = round(_arc_distance(o.home_zip, self.route, a, b) * SCALE)
                    dist_terms.append(base * self.cmove[o.id, k, a, b])
                    # approximate pickup detour for passengers from other homes,
                    # only on arcs that touch a home
                    if a == H or b == H:
                        for p in self.people:
                            if p.id == o.id or p.household == o.household:
                                continue  # same household: free consolidation
                            det = round(
                                geo.zip_distance_miles(p.home_zip, o.home_zip)
                                * self.p.detour_factor
                                * SCALE
                            )
                            if det and (p.id, o.id, k, a, b) in self.incar:
                                dist_terms.append(det * self.incar[p.id, o.id, k, a, b])
                            # the same pickup detour applies to ferrying someone
                            # else's bikes (otherwise bikes haul for free)
                            if det and (p.id, o.id, k, a, b) in self.bincar:
                                dist_terms.append(det * self.bincar[p.id, o.id, k, a, b])

        self.dist_total = m.NewIntVar(0, 10_000_000, "dist_total")
        m.Add(self.dist_total == sum(dist_terms))
        pen = round(self.p.pref_penalty_miles * SCALE)
        self.m.Minimize(self.dist_total + pen * sum(pref_terms))


# --------------------------------------------------------------------------- #
# Solve + extract
# --------------------------------------------------------------------------- #


def solve(problem: Problem, time_limit_s: float = 20.0) -> Solution:
    mb = _Model(problem)
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_s
    solver.parameters.num_search_workers = 8
    status = solver.Solve(mb.m)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return Solution(
            status="infeasible",
            message=_diagnose_infeasible(problem),
        )

    return _extract(problem, mb, solver, status)


def _diagnose_infeasible(problem: Problem) -> str:
    """Best-effort hint about what is missing for a feasible plan."""
    riders = [p for p in problem.people if p.is_rider]
    hints = []
    if not any(p.has_car for p in problem.people):
        hints.append("nobody has a car")
    wants_tonight = [
        p
        for p in riders
        if p.pref(ReturnOption.DRIVE_HOME_TONIGHT) != Pref.UNWILLING
    ]
    can_finish_car = any(p.willing_drop_car for p in problem.people) or problem.has_sag
    if wants_tonight and not can_finish_car:
        hints.append(
            "people want to drive home the night of the ride, but no car is "
            "dropped at the finish the night before (and there's no SAG wagon) — "
            "so no car is waiting at the finish"
        )
    only_tonight = [
        p
        for p in riders
        if p.pref(ReturnOption.HOTEL_BIKE_BACK) == Pref.UNWILLING
        and p.pref(ReturnOption.HOTEL_RIDE_HOME) == Pref.UNWILLING
        and p.pref(ReturnOption.DRIVE_HOME_TONIGHT) == Pref.UNWILLING
    ]
    if only_tonight:
        hints.append(
            f"{only_tonight[0].name} has marked every return option as unwilling"
        )
    base = "No workable plan was found."
    if hints:
        return base + " Likely cause: " + "; ".join(hints) + "."
    return base + " Try relaxing some return preferences or adding a driver/SAG wagon."


def _loc_name(problem: Problem, person: Person, loc: str) -> str:
    if loc == H:
        return f"home ({geo.place_name(person.home_zip)})"
    if loc == S:
        return problem.route.start_name
    return problem.route.finish_name


def _extract(problem, mb, solver, status) -> Solution:
    route = problem.route
    sol = Solution(status="optimal" if status == cp_model.OPTIMAL else "feasible")
    # total driving = base car legs + approximate pickup detours (not the
    # preference penalty, which isn't real miles)
    sol.total_drive_miles = round(solver.Value(mb.dist_total) / SCALE, 1)

    # return outcomes
    deviations = 0
    for p in problem.people:
        if not p.is_rider:
            continue
        if solver.Value(mb.home_tonight[p.id]):
            opt = ReturnOption.DRIVE_HOME_TONIGHT
        elif solver.Value(mb.opt_bikeback[p.id]):
            opt = ReturnOption.HOTEL_BIKE_BACK
        else:
            opt = ReturnOption.HOTEL_RIDE_HOME
        sol.return_outcome[p.id] = opt
        if p.pref(opt) == Pref.ACCEPTABLE:
            deviations += 1
    sol.pref_deviations = deviations

    # per-person itinerary
    for p in problem.people:
        steps = []
        for k in range(NK):
            for (a, b) in ALLOWED_ARCS[k]:
                # self-powered bicycle?
                bself = mb.bike_self.get((p.id, k, a, b))
                if bself is not None and solver.Value(bself):
                    verb = "Ride the route" if (a, b) == (S, F) else "Bike back"
                    steps.append(
                        f"{TRANSITION_LABELS[k]}: {verb} "
                        f"({_loc_name(problem, p, a)} → {_loc_name(problem, p, b)})"
                    )
                for o in mb.owners:
                    key = (p.id, o.id, k, a, b)
                    if key in mb.incar and solver.Value(mb.incar[key]):
                        # filter on *this person's* own travel, not the driver's
                        if _arc_distance(p.home_zip, route, a, b) < ZERO_MI:
                            continue  # 0-mile no-op (they live at this endpoint)
                        if o.id == p.id:
                            role = "Drive your car"
                        else:
                            role = f"Ride with {mb.by_id[o.id].name}"
                        steps.append(
                            f"{TRANSITION_LABELS[k]}: {role} "
                            f"({_loc_name(problem, p, a)} → {_loc_name(problem, p, b)})"
                        )
        if not steps:
            steps.append("Stay home — no travel needed.")
        sol.itineraries[p.id] = steps

    # car movement summary
    for o in mb.owners:
        for k in range(NK):
            for (a, b) in ALLOWED_ARCS[k]:
                if solver.Value(mb.cmove[o.id, k, a, b]):
                    passengers = [
                        mb.by_id[p.id].name
                        for p in problem.people
                        if p.id != o.id
                        and (p.id, o.id, k, a, b) in mb.incar
                        and solver.Value(mb.incar[p.id, o.id, k, a, b])
                    ]
                    bikes = 0
                    for ob in problem.people:
                        key = (ob.id, o.id, k, a, b)
                        if key in mb.bincar:
                            bikes += solver.Value(mb.bincar[key])
                    # hide only truly empty no-op moves (owner lives at the
                    # endpoint and is carrying nobody/nothing)
                    if (
                        _arc_distance(o.home_zip, route, a, b) < ZERO_MI
                        and not passengers
                        and bikes == 0
                    ):
                        continue
                    who = f" + {', '.join(passengers)}" if passengers else ""
                    sol.car_moves.append(
                        f"{TRANSITION_LABELS[k]}: {o.name}'s car "
                        f"{_loc_name(problem, o, a)} → {_loc_name(problem, o, b)} "
                        f"(driver{who}; {bikes} bike{'s' if bikes != 1 else ''})"
                    )

    # Bike hand-offs (presentation only): when a bike rides in someone else's
    # car and its owner isn't in that car, surface it as an explicit drop/collect
    # — e.g. "drop your bike at Bob's the night before, he brings it to the start".
    seen_out, seen_back = set(), set()
    prepend = {p.id: [] for p in problem.people}
    append = {p.id: [] for p in problem.people}
    for o in mb.owners:
        for k in range(NK):
            for (a, b) in ALLOWED_ARCS[k]:
                for ob in problem.people:
                    key = (ob.id, o.id, k, a, b)
                    if ob.id == o.id or key not in mb.bincar:
                        continue
                    if solver.Value(mb.bincar[key]) <= 0:
                        continue
                    owner_along = (ob.id, o.id, k, a, b) in mb.incar and solver.Value(
                        mb.incar[ob.id, o.id, k, a, b]
                    )
                    if owner_along:
                        continue  # bike travels with its owner — nothing to hand off
                    if b == S and (ob.id, o.id) not in seen_out:
                        seen_out.add((ob.id, o.id))
                        prepend[ob.id].append(
                            f"Bike hand-off: get your bike to {o.name} before ride day "
                            f"— drop it at their place the night before (or they pick it "
                            f"up); they bring it to {route.start_name}."
                        )
                        prepend[o.id].append(
                            f"Bike hand-off: you're bringing {ob.name}'s bike to "
                            f"{route.start_name} (they drop it off the night before, or "
                            f"you pick it up)."
                        )
                    elif b == H and (ob.id, o.id) not in seen_back:
                        seen_back.add((ob.id, o.id))
                        append[ob.id].append(
                            f"Bike hand-off: {o.name} brings your bike back near your "
                            f"home afterward — arrange to collect it."
                        )
                        append[o.id].append(
                            f"Bike hand-off: dropping {ob.name}'s bike back near their home."
                        )
    for p in problem.people:
        if prepend[p.id] or append[p.id]:
            base = sol.itineraries[p.id]
            if base == ["Stay home — no travel needed."]:
                base = []
            sol.itineraries[p.id] = prepend[p.id] + base + append[p.id]
    return sol
