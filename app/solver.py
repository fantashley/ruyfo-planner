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
    num_bikes: int = 1  # bikes this person OWNS and rides (NOT counting loaners they
    #                     bring for others); 0 for a supporter or a loaner-only rider
    loaner_for: list[str] = field(default_factory=list)  # ids this person lends a bike to
    bag_count: int = 0  # overnight bags to get to the hotel (finish)

    # car
    has_car: bool = False
    car_combos: list[CarCombo] = field(default_factory=list)

    # willingness gates
    willing_drop_car: bool = False  # leave car at finish overnight
    willing_drop_bikes_at_start: bool = False  # night-before bike shuttle to start
    willing_drive_dropper_home: bool = False  # night-before ride home for droppers
    can_drive_morning: bool = False  # ferry others to the start the morning of
    is_sag_driver: bool = False  # drives the SAG wagon (route sweep)
    share_household_car: bool = False  # opt in to pooling cars with the household
    sag_extra_miles: int = 20  # SAG driver only: extra base miles beyond their own
    #   home->start->finish->home route they'll drive (hard cap on SAG over-use)

    # return preferences: every option marked preferred / acceptable / unwilling
    return_prefs: dict[ReturnOption, Pref] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.household:
            self.household = self.id
        # a bare string loaner is accepted and treated as a one-element list
        if isinstance(self.loaner_for, str):
            self.loaner_for = [self.loaner_for] if self.loaner_for else []
        # "has a car" is implied by anything that needs one, so callers don't have
        # to set it explicitly (the web form drops the redundant checkbox)
        if not self.has_car and (
            self.car_combos
            or self.willing_drop_car
            or self.willing_drop_bikes_at_start
            or self.willing_drive_dropper_home
            or self.can_drive_morning
            or self.is_sag_driver
        ):
            self.has_car = True
        if self.has_car and not self.car_combos:
            # sensible default: a 5-seat car with a 2-bike rack
            self.car_combos = [CarCombo(people=5, bikes=2)]
        if not self.is_rider:
            self.num_bikes = 0
        if not self.return_prefs:
            # default: prefer driving home the night of, accept the rest. Bike-back
            # only makes sense for riders, so supporters are unwilling for it.
            self.return_prefs = {
                ReturnOption.DRIVE_HOME_TONIGHT: Pref.PREFERRED,
                ReturnOption.HOTEL_BIKE_BACK: (
                    Pref.ACCEPTABLE if self.is_rider else Pref.UNWILLING
                ),
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
    fairness_weight: float = 0.5  # weight on the most-burdened person's load
    chore_leg_miles: float = 15.0  # equivalent miles charged per "chore" car leg
    bag_night_before_drop_penalty_miles: float = 100.0  # prefer morning/SAG bag delivery


# --------------------------------------------------------------------------- #
# Time structure
# --------------------------------------------------------------------------- #

# 9 transitions connect 10 time points (t0..t9). The night before is THREE legs
# so one person can chain, e.g., drop bikes at the start -> drive their car to the
# finish -> get a ride home.
NK = 9
NIGHT_BEFORE = (0, 1, 2)  # the three night-before legs
T_MORNING = 3  # morning car-run transition (t3 -> t4). A car left at the finish
#                overnight is parked there at time point T_MORNING.
T_RIDE = 4  # the ride (t4 -> t5): start -> finish
T_BIKEBACK = 7  # next-morning bike-back transition (finish -> start)
T_HOME_TONIGHT = 7  # being home at time point 7 == "made it home tonight"

TRANSITION_LABELS = [
    "Night before — drive out",
    "Night before — continue",
    "Night before — continue",
    "Morning of the ride",
    "The ride",
    "Evening — after the ride",
    "Evening — continued",
    "Next morning",
    "Next morning — continued",
]

# Which directed arcs are worth creating at each transition. At t0 everyone and
# everything is home, so the first leg can only head out; every later leg is left
# general so a resource never gets trapped.
_ALL_ARCS = [(a, b) for a in LOCS for b in LOCS if a != b]
ALLOWED_ARCS: dict[int, list[tuple[str, str]]] = {k: list(_ALL_ARCS) for k in range(NK)}
ALLOWED_ARCS[0] = [(H, F), (H, S)]


# --------------------------------------------------------------------------- #
# Distances
# --------------------------------------------------------------------------- #

SCALE = 10  # miles -> integer units
ZERO_MI = 0.05  # legs shorter than this are "you already live there" no-ops
CARGO_TIEBREAKER_WEIGHT = 10_000


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
    # person id -> burden breakdown in equivalent miles
    burdens: dict[str, dict] = field(default_factory=dict)
    max_burden: float = 0.0
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

    def _precompute_loaners(self):
        """Resolve loaner pairings and per-owner bike accounting.

        A loaner is declared on the lender (`loaner_for` = borrower id). Each
        loaner is one extra bike owned by the lender that the borrower rides and
        which returns to the lender's home.
        """
        self.loaners = []  # (lender_id, borrower_id)
        for p in self.people:
            for borrower_id in p.loaner_for:
                if borrower_id in self.by_id and borrower_id != p.id:
                    self.loaners.append((p.id, borrower_id))

        # total bikes each person brings = their own + loaners they provide
        self.total_bikes = {p.id: p.num_bikes for p in self.people}
        for lender_id, _ in self.loaners:
            self.total_bikes[lender_id] += 1

        # which owners' bikes a rider may pedal, and which riders pedal an owner's
        self.bike_owners_for_rider = {}  # rider_id -> [owner_id, ...]
        self.riders_of_owner_bike = {}  # owner_id -> [rider_id, ...]
        for p in self.people:
            if p.is_rider and p.num_bikes > 0:
                self.bike_owners_for_rider.setdefault(p.id, []).append(p.id)
                self.riders_of_owner_bike.setdefault(p.id, []).append(p.id)
        for lender_id, borrower_id in self.loaners:
            self.bike_owners_for_rider.setdefault(borrower_id, []).append(lender_id)
            self.riders_of_owner_bike.setdefault(lender_id, []).append(borrower_id)

    # -- variable builders -------------------------------------------------- #
    def _build(self):
        m = self.m
        self._precompute_loaners()
        self.cat = {}  # (owner, t, loc) -> bool : car at loc
        self.cmove = {}  # (owner, k, frm, to) -> bool
        self.cstay = {}  # (owner, k, loc) -> bool
        self.combosel = {}  # (owner, k, frm, to, combo_idx) -> bool

        for o in self.owners:
            for t in range(NK + 1):
                for l in LOCS:
                    self.cat[o.id, t, l] = m.new_bool_var(f"cat_{o.id}_{t}_{l}")
                m.add(sum(self.cat[o.id, t, l] for l in LOCS) == 1)
            # starts and ends at home
            m.add(self.cat[o.id, 0, H] == 1)
            m.add(self.cat[o.id, NK, H] == 1)
            for k in range(NK):
                for l in LOCS:
                    self.cstay[o.id, k, l] = m.new_bool_var(f"cstay_{o.id}_{k}_{l}")
                for (a, b) in ALLOWED_ARCS[k]:
                    self.cmove[o.id, k, a, b] = m.new_bool_var(f"cmove_{o.id}_{k}_{a}{b}")
                    for ci in range(len(o.car_combos)):
                        self.combosel[o.id, k, a, b, ci] = m.new_bool_var(
                            f"combo_{o.id}_{k}_{a}{b}_{ci}"
                        )
                    m.add(
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
                    m.add(self.cat[o.id, k, l] == sum(out))
                for l in LOCS:
                    inn = [self.cstay[o.id, k, l]] + [
                        self.cmove[o.id, k, a, b]
                        for (a, b) in ALLOWED_ARCS[k]
                        if b == l
                    ]
                    m.add(self.cat[o.id, k + 1, l] == sum(inn))

        # ---- people ------------------------------------------------------- #
        self.pat = {}  # (pid, t, loc)
        self.pstay = {}
        self.incar = {}  # (pid, owner, k, frm, to) -> bool : person rides this car
        self.bike_self = {}  # (pid, k, frm, to) -> bool : person self-powers (bicycle)

        for p in self.people:
            for t in range(NK + 1):
                for l in LOCS:
                    self.pat[p.id, t, l] = m.new_bool_var(f"pat_{p.id}_{t}_{l}")
                m.add(sum(self.pat[p.id, t, l] for l in LOCS) == 1)
            m.add(self.pat[p.id, 0, H] == 1)
            m.add(self.pat[p.id, NK, H] == 1)
            # everyone sleeps at home the night before — any night-before drop must
            # be a round trip home (droppers get a ride back). Cars/bikes left at
            # the start or finish still stay put; this only pins people.
            m.add(self.pat[p.id, T_MORNING, H] == 1)
            # nobody sleeps at the start town: overnight (the Saturday-morning
            # wake point) everyone is at home or the finish hotel, never at S.
            # Bike-back riders are unaffected — they wake at F and pedal F->S
            # during T_BIKEBACK, reaching S only at the next time point.
            m.add(self.pat[p.id, T_BIKEBACK, S] == 0)
            if p.is_rider:
                m.add(self.pat[p.id, T_RIDE, S] == 1)  # at start for the ride
                m.add(self.pat[p.id, T_RIDE + 1, F] == 1)  # at finish after the ride

            for k in range(NK):
                for l in LOCS:
                    self.pstay[p.id, k, l] = m.new_bool_var(f"pstay_{p.id}_{k}_{l}")
                for (a, b) in ALLOWED_ARCS[k]:
                    # bicycle moves: the ride (S->F) and a next-morning bike-back
                    # (F->S). A rider may pedal their own bike or a loaner, so the
                    # key is (rider, owner-of-the-bike). A rider with no own bike
                    # and no loaner gets no bicycle var -> can only cross via SAG.
                    if p.is_rider and (
                        (k == T_RIDE and (a, b) == (S, F))
                        or (k == T_BIKEBACK and (a, b) == (F, S))
                    ):
                        for owner_id in self.bike_owners_for_rider.get(p.id, ()):
                            self.bike_self[p.id, owner_id, k, a, b] = m.new_bool_var(
                                f"bike_{p.id}_{owner_id}_{k}_{a}{b}"
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
                        self.incar[p.id, o.id, k, a, b] = m.new_bool_var(
                            f"in_{p.id}_{o.id}_{k}_{a}{b}"
                        )

            # person flow conservation
            for k in range(NK):
                for l in LOCS:
                    carried_out = self._person_carried(p.id, k, frm=l)
                    m.add(
                        self.pat[p.id, k, l]
                        == self.pstay[p.id, k, l] + sum(carried_out)
                    )
                for l in LOCS:
                    carried_in = self._person_carried(p.id, k, to=l)
                    m.add(
                        self.pat[p.id, k + 1, l]
                        == self.pstay[p.id, k, l] + sum(carried_in)
                    )

        # ---- driver presence + capacity (people) -------------------------- #
        # self.drives[(driver, owner, k, a, b)] -> the bool of "driver drives owner's
        # car on this leg". A car moves iff exactly one of its eligible drivers is
        # driving. By default the only eligible driver is the owner; two household
        # members pool cars only if *both* opt in (`share_household_car`). Used for
        # burden attribution and the driver label. The owner-only case aliases the
        # cmove var (no extra variable).
        self.drives = {}
        for o in self.owners:
            eligible_drivers = [o.id] + [
                hm.id
                for hm in self.people
                if hm.id != o.id
                and hm.household == o.household
                and hm.share_household_car
                and o.share_household_car
            ]
            shared = len(eligible_drivers) > 1
            for k in range(NK):
                for (a, b) in ALLOWED_ARCS[k]:
                    move = self.cmove[o.id, k, a, b]
                    if not shared:
                        # owner is the only possible driver of their own car
                        if (o.id, o.id, k, a, b) in self.incar:
                            m.add(self.incar[o.id, o.id, k, a, b] == move)
                            self.drives[o.id, o.id, k, a, b] = move
                        else:
                            m.add(move == 0)
                    else:
                        # any opted-in household member who can be aboard may drive
                        dvs = []
                        for hm_id in eligible_drivers:
                            if (hm_id, o.id, k, a, b) not in self.incar:
                                continue
                            dv = m.new_bool_var(f"drv_{hm_id}_{o.id}_{k}_{a}{b}")
                            m.add(dv <= self.incar[hm_id, o.id, k, a, b])
                            self.drives[hm_id, o.id, k, a, b] = dv
                            dvs.append(dv)
                        m.add(move == sum(dvs))  # exactly one driver iff it moves
                    riders_in = [
                        self.incar[p.id, o.id, k, a, b]
                        for p in self.people
                        if (p.id, o.id, k, a, b) in self.incar
                    ]
                    # people capacity from the chosen combo
                    m.add(
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

        # ---- SAG over-use cap --------------------------------------------- #
        self._build_sag_limit()

    def _build_sag_limit(self):
        """Hard-cap how far the SAG wagon may be driven.

        The SAG's only obligation is the ride sweep (start -> finish). Its free
        baseline is the minimal drive it must do anyway — home -> start (to reach
        the sweep) + start -> finish (the sweep) + finish -> home — so carrying
        people on those same legs is free. Any *extra* base miles beyond that
        baseline (e.g. a finish -> start backtrack, or multi-town detours) are
        forbidden once they exceed the SAG driver's stated tolerance
        (`sag_extra_miles`, default 20). Set a larger tolerance to volunteer for
        more; an over-budget plan is reported infeasible.
        """
        if not self.sag:
            return
        sag = self.sag
        baseline = (
            _arc_distance(sag.home_zip, self.route, H, S)
            + _arc_distance(sag.home_zip, self.route, S, F)
            + _arc_distance(sag.home_zip, self.route, F, H)
        )
        budget = round((baseline + max(0, sag.sag_extra_miles)) * SCALE)
        miles = []
        for k in range(NK):
            for (a, b) in ALLOWED_ARCS[k]:
                mv = self.cmove.get((sag.id, k, a, b))
                if mv is None:
                    continue
                d = round(_arc_distance(sag.home_zip, self.route, a, b) * SCALE)
                if d:
                    miles.append(d * mv)
        if miles:
            self.m.add(sum(miles) <= budget)

    def _person_carried(self, pid, k, frm=None, to=None):
        """All ways person ``pid`` traverses an arc at step ``k`` (filtered by end)."""
        out = []
        for (a, b) in ALLOWED_ARCS[k]:
            if frm is not None and a != frm:
                continue
            if to is not None and b != to:
                continue
            for owner_id in self.bike_owners_for_rider.get(pid, ()):
                if (pid, owner_id, k, a, b) in self.bike_self:
                    out.append(self.bike_self[pid, owner_id, k, a, b])
            for o in self.owners:
                if (pid, o.id, k, a, b) in self.incar:
                    out.append(self.incar[pid, o.id, k, a, b])
        return out

    def _apply_gates(self):
        m = self.m
        for o in self.owners:
            household = [hm for hm in self.people if hm.household == o.household]

            def willing_drivers(k, a, b, *attrs):
                # the drives bools for household members who opted into this chore
                return [
                    self.drives[hm.id, o.id, k, a, b]
                    for hm in household
                    if any(getattr(hm, attr) for attr in attrs)
                    and (hm.id, o.id, k, a, b) in self.drives
                ]

            # leaving a car at the finish overnight (parked there when morning
            # begins) requires the owner's willingness — it's the owner's car
            if not o.willing_drop_car:
                m.add(self.cat[o.id, T_MORNING, F] == 0)
            # these are *driver* chores: with shared household cars, whoever is
            # actually driving must have opted in (not just the car's owner).
            for k in NIGHT_BEFORE:
                # night-before finish-bound staging — the actual driver must be
                # willing either to drop a car at the finish or retrieve a dropper.
                for a in (H, S):
                    if (o.id, k, a, F) in self.cmove:
                        m.add(
                            self.cmove[o.id, k, a, F]
                            <= sum(
                                willing_drivers(
                                    k,
                                    a,
                                    F,
                                    "willing_drop_car",
                                    "willing_drive_dropper_home",
                                )
                            )
                        )
                # night-before bike shuttle to the start — any night-before H->S leg
                if (o.id, k, H, S) in self.cmove:
                    m.add(
                        self.cmove[o.id, k, H, S]
                        <= sum(willing_drivers(k, H, S, "willing_drop_bikes_at_start"))
                    )
                # carrying *droppers* home (F->H). Household members travelling
                # together are exempt; a non-household rider needs a willing driver.
                ok = willing_drivers(k, F, H, "willing_drive_dropper_home")
                for p in self.people:
                    if p.household != o.household and (p.id, o.id, k, F, H) in self.incar:
                        m.add(self.incar[p.id, o.id, k, F, H] <= sum(ok))
            # morning: carrying *non-household* others to the start
            ok = willing_drivers(T_MORNING, H, S, "can_drive_morning")
            for p in self.people:
                if (
                    p.household != o.household
                    and (p.id, o.id, T_MORNING, H, S) in self.incar
                ):
                    m.add(self.incar[p.id, o.id, T_MORNING, H, S] <= sum(ok))

    def _build_bikes(self):
        m = self.m
        # a person "has bikes" if they own some or bring loaners
        owners_with_bikes = [p for p in self.people if self.total_bikes[p.id] > 0]
        self.bat = {}  # (owner_pid, t, loc) -> int
        self.bstay = {}
        self.bincar = {}  # (owner_pid, car_owner, k, frm, to) -> int

        for ob in owners_with_bikes:
            nb = self.total_bikes[ob.id]  # own bikes + loaners they bring
            for t in range(NK + 1):
                for l in LOCS:
                    self.bat[ob.id, t, l] = m.new_int_var(0, nb, f"bat_{ob.id}_{t}_{l}")
                m.add(sum(self.bat[ob.id, t, l] for l in LOCS) == nb)
            m.add(self.bat[ob.id, 0, H] == nb)  # bikes start at home
            m.add(self.bat[ob.id, NK, H] == nb)  # ... and end at home
            # every bike in play is ridden (by the owner or a declared borrower),
            # so all of them are at the start for the ride and the finish after
            m.add(self.bat[ob.id, T_RIDE, S] == nb)
            m.add(self.bat[ob.id, T_RIDE + 1, F] == nb)

            for k in range(NK):
                for l in LOCS:
                    self.bstay[ob.id, k, l] = m.new_int_var(0, nb, f"bstay_{ob.id}_{k}_{l}")
                for (a, b) in ALLOWED_ARCS[k]:
                    for o in self.owners:
                        if (o.id, k, a, b) in self.cmove:
                            self.bincar[ob.id, o.id, k, a, b] = m.new_int_var(
                                0, nb, f"binc_{ob.id}_{o.id}_{k}_{a}{b}"
                            )
                # bike flow conservation
                for l in LOCS:
                    out = [self.bstay[ob.id, k, l]] + self._bike_moved(ob.id, k, frm=l)
                    m.add(self.bat[ob.id, k, l] == sum(out))
                for l in LOCS:
                    inn = [self.bstay[ob.id, k, l]] + self._bike_moved(ob.id, k, to=l)
                    m.add(self.bat[ob.id, k + 1, l] == sum(inn))

        # bike capacity per car move + a bicyclist carries exactly one own bike
        for o in self.owners:
            for k in range(NK):
                for (a, b) in ALLOWED_ARCS[k]:
                    bikes_in = [
                        self.bincar[ob.id, o.id, k, a, b]
                        for ob in owners_with_bikes
                        if (ob.id, o.id, k, a, b) in self.bincar
                    ]
                    m.add(
                        sum(bikes_in)
                        <= sum(
                            self.combosel[o.id, k, a, b, ci] * o.car_combos[ci].bikes
                            for ci in range(len(o.car_combos))
                        )
                    )
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
            # any rider pedalling one of this owner's bikes moves it (own or loaner)
            for rider_id in self.riders_of_owner_bike.get(ob_id, ()):
                if (rider_id, ob_id, k, a, b) in self.bike_self:
                    terms.append(self.bike_self[rider_id, ob_id, k, a, b])
        return terms

    def _build_bags(self):
        """Overnight bags: non-rideable items that ride only in cars/SAG.

        A bag must reach the finish (hotel) by the evening and stay there for
        the night *if* its owner stays overnight, then return home. With a SAG
        the bag goes home->start in a morning car then start->finish on the SAG;
        without one it needs a finish-bound car (a night-before drop or a
        supporter driving to F). Before ride morning, a bag staged away from
        home must be with a parked car; unlike bikes, bags are not assumed safe
        to leave loose. A rider who bikes back can carry their own bag from the
        hotel back to the start the next morning.
        """
        m = self.m
        bag_owners = [p for p in self.people if p.bag_count > 0]
        self.gat = {}  # (owner_pid, t, loc) -> int : bags at loc
        self.gstay = {}
        self.gincar = {}  # (owner_pid, car_owner, k, frm, to) -> int

        for ob in bag_owners:
            nb = ob.bag_count
            for t in range(NK + 1):
                for l in LOCS:
                    self.gat[ob.id, t, l] = m.new_int_var(0, nb, f"gat_{ob.id}_{t}_{l}")
                m.add(sum(self.gat[ob.id, t, l] for l in LOCS) == nb)
            m.add(self.gat[ob.id, 0, H] == nb)  # bags start at home
            m.add(self.gat[ob.id, NK, H] == nb)  # ... and end at home
            # at the finish (hotel) by the evening, and still there Friday night,
            # iff staying overnight
            if ob.id in self.home_tonight:
                stay = 1 - self.home_tonight[ob.id]  # 1 == hotel overnight
                m.add(self.gat[ob.id, T_RIDE + 1, F] >= nb * stay)
                m.add(self.gat[ob.id, T_HOME_TONIGHT, F] >= nb * stay)
            for t in range(1, T_MORNING + 1):
                for l in (S, F):
                    parked_cars = [self.cat[o.id, t, l] for o in self.owners]
                    m.add(self.gat[ob.id, t, l] <= nb * sum(parked_cars))

            for k in range(NK):
                for l in LOCS:
                    self.gstay[ob.id, k, l] = m.new_int_var(
                        0, nb, f"gstay_{ob.id}_{k}_{l}"
                    )
                for (a, b) in ALLOWED_ARCS[k]:
                    for o in self.owners:
                        if (o.id, k, a, b) in self.cmove:
                            g = m.new_int_var(0, nb, f"ginc_{ob.id}_{o.id}_{k}_{a}{b}")
                            self.gincar[ob.id, o.id, k, a, b] = g
                            # bags ride only a car that actually makes the move;
                            # they don't consume people/bike capacity (small)
                            m.add(g <= nb * self.cmove[o.id, k, a, b])
                            if k in NIGHT_BEFORE and b in (S, F):
                                # A night-before bag staging run must leave the
                                # bag inside a car parked at that location; do
                                # not assume unattended bags can transfer between
                                # cars after the carrier leaves.
                                m.add(g <= nb * self.cat[o.id, T_MORNING, b])
                # bag flow conservation (cars only — bags are never pedalled)
                for l in LOCS:
                    out = [self.gstay[ob.id, k, l]] + self._bag_moved(ob.id, k, frm=l)
                    m.add(self.gat[ob.id, k, l] == sum(out))
                for l in LOCS:
                    inn = [self.gstay[ob.id, k, l]] + self._bag_moved(ob.id, k, to=l)
                    m.add(self.gat[ob.id, k + 1, l] == sum(inn))

    def _bag_moved(self, ob_id, k, frm=None, to=None):
        terms = []
        for (a, b) in ALLOWED_ARCS[k]:
            if frm is not None and a != frm:
                continue
            if to is not None and b != to:
                continue
            for o in self.owners:
                if (ob_id, o.id, k, a, b) in self.gincar:
                    terms.append(self.gincar[ob_id, o.id, k, a, b])
            if k == T_BIKEBACK and (a, b) == (F, S) and ob_id in self.opt_bikeback:
                terms.append(self.by_id[ob_id].bag_count * self.opt_bikeback[ob_id])
        return terms

    def _build_returns_and_objective(self):
        m = self.m
        self.home_tonight = {}
        self.opt_bikeback = {}
        self.opt_ridehome = {}
        self.deviation = {}

        pref_terms = []
        # everyone (riders *and* supporters/SAG driver) gets a return preference:
        # home the night of, or stay over and head home the next morning. Only
        # riders can bike back, so for non-riders bike-back is just always 0.
        for p in self.people:
            ht = self.pat[p.id, T_HOME_TONIGHT, H]
            self.home_tonight[p.id] = ht
            # bike-back = pedalling *some* bike (own or loaner) back next morning
            bb_terms = [
                self.bike_self[p.id, owner_id, T_BIKEBACK, F, S]
                for owner_id in self.bike_owners_for_rider.get(p.id, ())
                if (p.id, owner_id, T_BIKEBACK, F, S) in self.bike_self
            ]
            bb = m.new_bool_var(f"bikeback_{p.id}")
            m.add(bb == sum(bb_terms))  # person flow guarantees the sum is <= 1
            self.opt_bikeback[p.id] = bb
            m.add(bb + ht <= 1)  # can't bike back if already home tonight
            rh = m.new_bool_var(f"ridehome_{p.id}")
            m.add(rh == 1 - ht - bb)
            self.opt_ridehome[p.id] = rh
            # If someone went home the night of the ride, they're done: no
            # next-morning errands or passenger legs. Their bikes/bags may still
            # be ferried by someone who stayed over.
            for t in range(T_HOME_TONIGHT, NK + 1):
                m.add(self.pat[p.id, t, H] >= ht)

            options = {
                ReturnOption.DRIVE_HOME_TONIGHT: ht,
                ReturnOption.HOTEL_BIKE_BACK: bb,
                ReturnOption.HOTEL_RIDE_HOME: rh,
            }
            acceptable_vars = []
            for opt, var in options.items():
                pr = p.pref(opt)
                if pr == Pref.UNWILLING:
                    m.add(var == 0)
                elif pr == Pref.ACCEPTABLE:
                    acceptable_vars.append(var)
            # exactly one option is true, so this sums to 0 or 1: did this person
            # land on a merely-acceptable option instead of their preferred one?
            dev = m.new_bool_var(f"dev_{p.id}")
            m.add(dev == sum(acceptable_vars))
            self.deviation[p.id] = dev
            pref_terms.append(dev)

        # overnight bags depend on home_tonight, so build them now
        self._build_bags()

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
                            if not det:
                                continue
                            # same pickup detour for ferrying someone else's
                            # passengers, bikes, or bags (otherwise they haul free)
                            if (p.id, o.id, k, a, b) in self.incar:
                                dist_terms.append(det * self.incar[p.id, o.id, k, a, b])
                            if (p.id, o.id, k, a, b) in self.bincar:
                                dist_terms.append(det * self.bincar[p.id, o.id, k, a, b])
                            if (p.id, o.id, k, a, b) in self.gincar:
                                dist_terms.append(det * self.gincar[p.id, o.id, k, a, b])

        self.dist_total = m.new_int_var(0, 10_000_000, "dist_total")
        m.add(self.dist_total == sum(dist_terms))
        cargo_terms = list(self.bincar.values()) + list(self.gincar.values())
        self.cargo_motion_total = m.new_int_var(0, 10_000_000, "cargo_motion_total")
        m.add(self.cargo_motion_total == sum(cargo_terms))
        night_before_bag_drop_terms = [
            var
            for (ob_id, owner_id, k, a, b), var in self.gincar.items()
            if k in NIGHT_BEFORE and b == F
        ]
        self.night_before_bag_drop_total = m.new_int_var(
            0, 10_000_000, "night_before_bag_drop_total"
        )
        m.add(self.night_before_bag_drop_total == sum(night_before_bag_drop_terms))
        pen = round(self.p.pref_penalty_miles * SCALE)
        bag_drop_pen = round(self.p.bag_night_before_drop_penalty_miles * SCALE)

        # ---- fairness: per-person burden + soft minimax ---------------------- #
        self._build_burdens(pen)

        # The fairness weight is fractional, so scale the whole objective by 10
        # to keep CP-SAT coefficients integral (0.1 granularity on the weight).
        fw = round(self.p.fairness_weight * 10)
        primary_objective = (
            10 * self.dist_total
            + 10 * pen * sum(pref_terms)
            + 10 * bag_drop_pen * self.night_before_bag_drop_total
            + fw * self.max_burden
        )
        self.m.minimize(
            CARGO_TIEBREAKER_WEIGHT * primary_objective + self.cargo_motion_total
        )

    def _is_sag_sweep(self, owner_id: str, k: int, a: str, b: str) -> bool:
        """The SAG wagon's route sweep (start -> finish during the ride)."""
        return bool(
            self.sag
            and owner_id == self.sag.id
            and k == T_RIDE
            and (a, b) == (S, F)
        )

    def _is_sag_drive(self, owner_id: str) -> bool:
        """Any leg driven by the designated SAG driver."""
        return bool(self.sag and owner_id == self.sag.id)

    def _build_burdens(self, pen: int):
        """Per-person burden in scaled equivalent miles.

        burden = own-car driving miles
               + chore_leg_miles per chore leg (night-before legs; SAG driver legs;
                 next-morning legs by someone who was already home that night)
               + pref_penalty_miles if they landed on a merely-acceptable return.
        """
        m = self.m
        chore = round(self.p.chore_leg_miles * SCALE)
        self.burden = {}
        for p in self.people:
            terms = [pen * self.deviation[p.id]]
            ht = self.home_tonight[p.id]
            # legs this person actually drives — their own car or a household car
            for (driver_id, owner_id, k, a, b), dv in self.drives.items():
                if driver_id != p.id:
                    continue
                owner = self.by_id[owner_id]
                base = round(_arc_distance(owner.home_zip, self.route, a, b) * SCALE)
                if base:
                    terms.append(base * dv)
                if (k in NIGHT_BEFORE or self._is_sag_drive(owner_id)) and chore:
                    terms.append(chore * dv)
                elif k >= T_BIKEBACK and chore:
                    # a next-morning leg is a chore only if the driver was home
                    # tonight (had to go back out); an overnighter driving home is
                    # just their own return
                    both = m.new_bool_var(f"chore_{p.id}_{owner_id}_{k}_{a}{b}")
                    m.add(both <= dv)
                    m.add(both <= ht)
                    m.add(both >= dv + ht - 1)
                    terms.append(chore * both)
            for o in self.owners:
                if o.id == p.id:
                    continue
                owner_ht = self.home_tonight[o.id]
                for k in range(NK):
                    for (a, b) in ALLOWED_ARCS[k]:
                        key = (p.id, o.id, k, a, b)
                        if key not in self.incar:
                            continue
                        ride = self.incar[key]
                        if k in NIGHT_BEFORE:
                            terms.append(chore * ride)
                        elif k >= T_BIKEBACK:
                            both = m.new_bool_var(f"passenger_chore_{p.id}_{o.id}_{k}_{a}{b}")
                            m.add(both <= ride)
                            m.add(both <= owner_ht)
                            m.add(both >= ride + owner_ht - 1)
                            terms.append(chore * both)
            bvar = m.new_int_var(0, 10_000_000, f"burden_{p.id}")
            m.add(bvar == sum(terms))
            self.burden[p.id] = bvar
        self.max_burden = m.new_int_var(0, 10_000_000, "max_burden")
        m.add_max_equality(self.max_burden, list(self.burden.values()))


# --------------------------------------------------------------------------- #
# Solve + extract
# --------------------------------------------------------------------------- #


def solve(problem: Problem, time_limit_s: float = 20.0) -> Solution:
    mb = _Model(problem)
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_s
    solver.parameters.num_search_workers = 8
    status = solver.solve(mb.m)

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
    # a rider with no bike of their own and no loaner can only cross on the SAG
    borrowers = {b for p in problem.people for b in p.loaner_for}
    bikeless = [
        p for p in riders if p.num_bikes <= 0 and p.id not in borrowers
    ]
    if bikeless and not problem.has_sag:
        hints.append(
            f"{bikeless[0].name} is riding but has no bike (own or loaner) and "
            "there's no SAG wagon to carry them"
        )
    # a bag can only reach the hotel via the SAG or a finish-bound car
    finish_reachable = (
        problem.has_sag
        or any(p.willing_drop_car for p in problem.people)
        or any(p.has_car and not p.is_rider for p in problem.people)
    )
    baggers = [p for p in problem.people if p.bag_count > 0]
    if baggers and not finish_reachable:
        hints.append(
            f"{baggers[0].name}'s overnight bag can't reach the hotel — there's no "
            "SAG wagon, no car dropped at the finish, and no non-riding driver to "
            "take it to the finish"
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
    sol.total_drive_miles = round(solver.value(mb.dist_total) / SCALE, 1)

    # return outcomes (riders and supporters alike)
    deviations = 0
    for p in problem.people:
        if solver.value(mb.home_tonight[p.id]):
            opt = ReturnOption.DRIVE_HOME_TONIGHT
        elif solver.value(mb.opt_bikeback[p.id]):
            opt = ReturnOption.HOTEL_BIKE_BACK
        else:
            opt = ReturnOption.HOTEL_RIDE_HOME
        sol.return_outcome[p.id] = opt
        if p.pref(opt) == Pref.ACCEPTABLE:
            deviations += 1
    sol.pref_deviations = deviations

    # per-person burden breakdown (mirrors _build_burdens)
    for p in problem.people:
        drive_units = 0
        chore_legs = 0
        home_tonight = bool(solver.value(mb.home_tonight[p.id]))
        # legs this person actually drives (own car or a household car)
        for (driver_id, owner_id, k, a, b), dv in mb.drives.items():
            if driver_id != p.id or not solver.value(dv):
                continue
            owner = mb.by_id[owner_id]
            drive_units += round(_arc_distance(owner.home_zip, route, a, b) * SCALE)
            if (
                k in NIGHT_BEFORE
                or mb._is_sag_drive(owner_id)
                or (k >= T_BIKEBACK and home_tonight)
            ):
                chore_legs += 1
        for o in mb.owners:
            if o.id == p.id:
                continue
            owner_home_tonight = bool(solver.value(mb.home_tonight[o.id]))
            for k in range(NK):
                for (a, b) in ALLOWED_ARCS[k]:
                    key = (p.id, o.id, k, a, b)
                    if key not in mb.incar or not solver.value(mb.incar[key]):
                        continue
                    if k in NIGHT_BEFORE or (k >= T_BIKEBACK and owner_home_tonight):
                        chore_legs += 1
        sol.burdens[p.id] = {
            "total": round(solver.value(mb.burden[p.id]) / SCALE, 1),
            "drive_miles": round(drive_units / SCALE, 1),
            "chore_legs": chore_legs,
            "deviation": bool(solver.value(mb.deviation[p.id])),
        }
    sol.max_burden = round(solver.value(mb.max_burden) / SCALE, 1)

    # per-person itinerary
    for p in problem.people:
        steps = []
        for k in range(NK):
            for (a, b) in ALLOWED_ARCS[k]:
                # self-powered bicycle? (own bike or a loaner)
                for owner_id in mb.bike_owners_for_rider.get(p.id, ()):
                    bself = mb.bike_self.get((p.id, owner_id, k, a, b))
                    if bself is None or not solver.value(bself):
                        continue
                    verb = "Ride the route" if (a, b) == (S, F) else "Bike back"
                    bike = (
                        f" on {mb.by_id[owner_id].name}'s loaner bike"
                        if owner_id != p.id
                        else ""
                    )
                    bag = ""
                    if (
                        k == T_BIKEBACK
                        and p.bag_count > 0
                        and solver.value(mb.opt_bikeback[p.id])
                    ):
                        bag_word = "bag" if p.bag_count == 1 else "bags"
                        bag = (
                            f"; overnight bags: {p.bag_count} {bag_word}: "
                            f"{p.name}: {p.bag_count} {bag_word}"
                        )
                    steps.append(
                        f"{TRANSITION_LABELS[k]}: {verb}{bike} "
                        f"({_loc_name(problem, p, a)} → {_loc_name(problem, p, b)}"
                        f"{bag})"
                    )
                for o in mb.owners:
                    key = (p.id, o.id, k, a, b)
                    if key in mb.incar and solver.value(mb.incar[key]):
                        # filter on *this person's* own travel, not the driver's
                        if _arc_distance(p.home_zip, route, a, b) < ZERO_MI:
                            continue  # 0-mile no-op (they live at this endpoint)
                        driver_id = _leg_driver_id(mb, solver, o.id, k, a, b)
                        if driver_id == p.id:
                            role = (
                                "Drive your car" if o.id == p.id
                                else f"Drive {mb.by_id[o.id].name}'s car"
                            )
                        else:
                            role = f"Ride with {mb.by_id[driver_id].name}"
                        contents = _vehicle_contents_text(mb, solver, o.id, k, a, b)
                        steps.append(
                            f"{TRANSITION_LABELS[k]}: {role} "
                            f"({_loc_name(problem, p, a)} → {_loc_name(problem, p, b)}; "
                            f"{contents})"
                        )
        if not steps:
            steps.append("Stay home — no travel needed.")
        sol.itineraries[p.id] = steps

    # car movement summary
    for o in mb.owners:
        for k in range(NK):
            for (a, b) in ALLOWED_ARCS[k]:
                if solver.value(mb.cmove[o.id, k, a, b]):
                    driver_id = _leg_driver_id(mb, solver, o.id, k, a, b)
                    passengers = [
                        mb.by_id[p.id].name
                        for p in problem.people
                        if p.id != driver_id
                        and (p.id, o.id, k, a, b) in mb.incar
                        and solver.value(mb.incar[p.id, o.id, k, a, b])
                    ]
                    bikes = 0
                    bike_parts = []
                    bags = 0
                    bag_parts = []
                    for ob in problem.people:
                        key = (ob.id, o.id, k, a, b)
                        if key in mb.bincar:
                            count = solver.value(mb.bincar[key])
                            bikes += count
                            if count:
                                bike_parts.append(_bike_cargo_label(mb, ob, count))
                        if key in mb.gincar:
                            gcount = solver.value(mb.gincar[key])
                            bags += gcount
                            if gcount:
                                bag_word = "bag" if gcount == 1 else "bags"
                                bag_parts.append(f"{ob.name}: {gcount} {bag_word}")
                    # hide only truly empty no-op moves (owner lives at the
                    # endpoint and is carrying nobody/nothing)
                    if (
                        _arc_distance(o.home_zip, route, a, b) < ZERO_MI
                        and not passengers
                        and bikes == 0
                        and bags == 0
                    ):
                        continue
                    who = f", with {', '.join(passengers)}" if passengers else ""
                    if bike_parts:
                        biketxt = f"{bikes} bike{'s' if bikes != 1 else ''}: "
                        biketxt += ", ".join(bike_parts)
                    else:
                        biketxt = "0 bikes"
                    if bag_parts:
                        bagtxt = (
                            f"; {bags} bag{'s' if bags != 1 else ''}: "
                            + ", ".join(bag_parts)
                        )
                    else:
                        bagtxt = ""
                    sol.car_moves.append(
                        f"{TRANSITION_LABELS[k]}: {o.name}'s car "
                        f"{_loc_name(problem, o, a)} → {_loc_name(problem, o, b)} "
                        f"(driven by {mb.by_id[driver_id].name}{who}; {biketxt}{bagtxt})"
                    )

    # Bike hand-offs (presentation only): when a bike rides in someone else's
    # car and its owner isn't in that car, surface it as an explicit drop/collect
    # — e.g. "drop your bike at Bob's the night before, he brings it to the start".
    seen_out, seen_back = set(), set()
    prepend = {p.id: [] for p in problem.people}
    append = {p.id: [] for p in problem.people}

    # loaner-bike notes (static, from the declared pairings)
    for lender_id, borrower_id in mb.loaners:
        prepend[borrower_id].append(
            f"Loaner bike: you'll ride {mb.by_id[lender_id].name}'s spare bike "
            f"(it returns to them afterward)."
        )
        prepend[lender_id].append(
            f"Loaner bike: bring a spare bike for {mb.by_id[borrower_id].name}."
        )

    # overnight-bag notes: how each staying-over owner's bag reaches the hotel
    for ob in problem.people:
        if ob.bag_count <= 0:
            continue
        if ob.id in mb.home_tonight and solver.value(mb.home_tonight[ob.id]):
            continue  # went home that night — the bag was never needed at the finish
        carrier = None
        for o in mb.owners:
            for k in range(NK):
                for (a, b) in ALLOWED_ARCS[k]:
                    key = (ob.id, o.id, k, a, b)
                    if b == F and key in mb.gincar and solver.value(mb.gincar[key]) > 0:
                        carrier = o
        if carrier is None:
            continue
        if mb.sag and carrier.id == mb.sag.id:
            prepend[ob.id].append(
                f"Overnight bag: bring it to {route.start_name} and hand it to the "
                f"SAG wagon — it'll be waiting at the hotel in {route.finish_name}."
            )
        else:
            prepend[ob.id].append(
                f"Overnight bag: it rides {carrier.name}'s car to the hotel in "
                f"{route.finish_name}."
            )

    for o in mb.owners:
        for k in range(NK):
            for (a, b) in ALLOWED_ARCS[k]:
                for ob in problem.people:
                    key = (ob.id, o.id, k, a, b)
                    if ob.id == o.id or key not in mb.bincar:
                        continue
                    if solver.value(mb.bincar[key]) <= 0:
                        continue
                    owner_along = (ob.id, o.id, k, a, b) in mb.incar and solver.value(
                        mb.incar[ob.id, o.id, k, a, b]
                    )
                    if owner_along:
                        continue  # bike travels with its owner — nothing to hand off
                    # a "drop it before the ride" note only makes sense for genuine
                    # pre-ride legs; after the ride a bike can pass through the start
                    # just on its way home, which is not a hand-off to arrange
                    if b == S and k <= T_MORNING and (ob.id, o.id) not in seen_out:
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
                    elif b == H and k > T_RIDE and (ob.id, o.id) not in seen_back:
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


def _bike_cargo_label(mb, owner: Person, count: int) -> str:
    """Readable description for bikes in a car, including declared loaners."""
    borrowers = [
        mb.by_id[borrower_id].name
        for lender_id, borrower_id in mb.loaners
        if lender_id == owner.id
    ]
    if not borrowers:
        bike_word = "bike" if count == 1 else "bikes"
        return f"{owner.name}: {count} {bike_word}"

    own = owner.num_bikes
    parts = []
    if own:
        parts.append(f"{own} own")
    for borrower in borrowers:
        parts.append(f"loaner for {borrower}")

    if count == mb.total_bikes[owner.id]:
        bike_word = "bike" if count == 1 else "bikes"
        return f"{owner.name}: {count} {bike_word} ({', '.join(parts)})"

    # Usually the model carries all bikes for an owner together, but if it splits
    # them, avoid pretending we know exactly which individual bike is in this car.
    bike_word = "bike" if count == 1 else "bikes"
    return f"{owner.name}: {count} of {mb.total_bikes[owner.id]} {bike_word}"


def _vehicle_bike_cargo_text(mb, solver, car_owner_id: str, k: int, a: str, b: str) -> str:
    """Readable bike cargo for one car leg."""
    bikes = 0
    bike_parts = []
    for owner in mb.people:
        key = (owner.id, car_owner_id, k, a, b)
        if key not in mb.bincar:
            continue
        count = solver.value(mb.bincar[key])
        bikes += count
        if count:
            bike_parts.append(_bike_cargo_label(mb, owner, count))
    if not bike_parts:
        return "bikes: none"
    return f"bikes: {bikes} bike{'s' if bikes != 1 else ''}: " + ", ".join(bike_parts)


def _leg_driver_id(mb, solver, owner_id: str, k: int, a: str, b: str) -> str:
    """Who actually drives ``owner_id``'s car on this leg (a household member may)."""
    for hm in mb.people:
        dv = mb.drives.get((hm.id, owner_id, k, a, b))
        if dv is not None and solver.value(dv):
            return hm.id
    return owner_id


def _vehicle_people_text(mb, solver, car_owner_id: str, k: int, a: str, b: str) -> str:
    """Readable people list for one car leg."""
    driver_id = _leg_driver_id(mb, solver, car_owner_id, k, a, b)
    driver = mb.by_id[driver_id].name
    riders = [
        p.name
        for p in mb.people
        if p.id != driver_id
        and (p.id, car_owner_id, k, a, b) in mb.incar
        and solver.value(mb.incar[p.id, car_owner_id, k, a, b])
    ]
    if riders:
        return f"people: {driver} (driver), " + ", ".join(riders)
    return f"people: {driver} (driver)"


def _vehicle_bag_cargo_text(mb, solver, car_owner_id: str, k: int, a: str, b: str) -> str:
    """Readable overnight-bag cargo for one car leg."""
    bag_parts = []
    bags = 0
    for owner in mb.people:
        key = (owner.id, car_owner_id, k, a, b)
        if key not in mb.gincar:
            continue
        count = solver.value(mb.gincar[key])
        bags += count
        if count:
            bag_word = "bag" if count == 1 else "bags"
            bag_parts.append(f"{owner.name}: {count} {bag_word}")
    if not bag_parts:
        return "overnight bags: none"
    return f"overnight bags: {bags} bag{'s' if bags != 1 else ''}: " + ", ".join(bag_parts)


def _vehicle_contents_text(mb, solver, car_owner_id: str, k: int, a: str, b: str) -> str:
    """Readable full contents of one car leg for personal itineraries."""
    return "; ".join(
        [
            _vehicle_people_text(mb, solver, car_owner_id, k, a, b),
            _vehicle_bike_cargo_text(mb, solver, car_owner_id, k, a, b),
            _vehicle_bag_cargo_text(mb, solver, car_owner_id, k, a, b),
        ]
    )
