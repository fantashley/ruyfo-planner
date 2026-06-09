from app.events import ROUTES
from app.solver import (
    CarCombo,
    Person,
    Pref,
    Problem,
    ReturnOption,
    solve,
)

ROUTE = ROUTES["faribault_mankato"]  # start 55021 (Faribault), finish 56001 (Mankato)

TONIGHT = ReturnOption.DRIVE_HOME_TONIGHT
BIKEBACK = ReturnOption.HOTEL_BIKE_BACK
RIDEHOME = ReturnOption.HOTEL_RIDE_HOME


def only(option):
    """Return-prefs dict allowing exactly one option."""
    return {
        opt: (Pref.PREFERRED if opt == option else Pref.UNWILLING)
        for opt in ReturnOption
    }


def _no_unwilling_outcomes(problem, sol):
    for p in problem.people:
        if not p.is_rider:
            continue
        outcome = sol.return_outcome[p.id]
        assert p.pref(outcome) != Pref.UNWILLING, f"{p.name} got unwilling {outcome}"


def test_single_rider_must_bike_back():
    # One rider with a car who will only do the hotel + bike-back option:
    # morning drive to start, ride, bike back next morning, drive home.
    a = Person(
        id="a", name="Ann", home_zip="55021", has_car=True,
        return_prefs=only(BIKEBACK),
    )
    sol = solve(Problem(route=ROUTE, people=[a]))
    assert sol.status in ("optimal", "feasible")
    assert sol.return_outcome["a"] == BIKEBACK
    _no_unwilling_outcomes(Problem(route=ROUTE, people=[a]), sol)


def test_lonely_drive_home_tonight_is_infeasible():
    # A single rider who insists on driving home the night of has no way to get
    # a car to the finish (dropping it the night before strands them there).
    a = Person(
        id="a", name="Ann", home_zip="55021", has_car=True,
        return_prefs=only(TONIGHT),
    )
    sol = solve(Problem(route=ROUTE, people=[a]))
    assert sol.status == "infeasible"
    assert sol.message


def test_drive_home_tonight_with_dropper_and_supporter():
    # Ann insists on driving home tonight -> she must drop her car at the finish
    # the night before, which only works because Carl (a non-riding supporter)
    # drives her home that night and ferries her to the start in the morning.
    ann = Person(
        id="ann", name="Ann", home_zip="55021", has_car=True,
        willing_drop_car=True, return_prefs=only(TONIGHT),
    )
    carl = Person(
        id="carl", name="Carl", home_zip="56001", has_car=True, is_rider=False,
        can_drive_morning=True, willing_drive_dropper_home=True,
    )
    problem = Problem(route=ROUTE, people=[ann, carl])
    sol = solve(problem)
    assert sol.status in ("optimal", "feasible")
    assert sol.return_outcome["ann"] == TONIGHT
    _no_unwilling_outcomes(problem, sol)


def test_unwilling_option_is_never_assigned():
    # Two riders sharing a supporter; one forbids driving home tonight, so the
    # optimizer must not assign it to her.
    bea = Person(
        id="bea", name="Bea", home_zip="55021", has_car=True,
        return_prefs={
            TONIGHT: Pref.UNWILLING,
            BIKEBACK: Pref.PREFERRED,
            RIDEHOME: Pref.ACCEPTABLE,
        },
    )
    problem = Problem(route=ROUTE, people=[bea])
    sol = solve(problem)
    assert sol.status in ("optimal", "feasible")
    assert sol.return_outcome["bea"] != TONIGHT
    _no_unwilling_outcomes(problem, sol)


def test_sag_wagon_carries_a_non_finisher():
    # Dana has no car and can't finish; with a SAG wagon she still gets to the
    # finish and home. Without any way to move her she'd be stuck.
    dana = Person(
        id="dana", name="Dana", home_zip="55021", has_car=False,
        return_prefs={TONIGHT: Pref.PREFERRED, RIDEHOME: Pref.ACCEPTABLE,
                      BIKEBACK: Pref.UNWILLING},
    )
    sage = Person(
        id="sage", name="Sage", home_zip="55021", is_rider=False, has_car=True,
        car_combos=[CarCombo(people=8, bikes=8)],
        is_sag_driver=True, can_drive_morning=True,
    )
    problem = Problem(route=ROUTE, people=[dana, sage], has_sag=True)
    sol = solve(problem)
    assert sol.status in ("optimal", "feasible")
    _no_unwilling_outcomes(problem, sol)


def test_household_rides_together():
    # A two-person household with one car, both happy to bike back. The plan
    # should be feasible and keep them on a willing option.
    h1 = Person(
        id="h1", name="Pat", home_zip="55021", household="ekberg", has_car=True,
        car_combos=[CarCombo(people=4, bikes=4)], return_prefs=only(BIKEBACK),
    )
    h2 = Person(
        id="h2", name="Sam", home_zip="55021", household="ekberg", has_car=False,
        return_prefs=only(BIKEBACK),
    )
    problem = Problem(route=ROUTE, people=[h1, h2])
    sol = solve(problem)
    assert sol.status in ("optimal", "feasible")
    _no_unwilling_outcomes(problem, sol)
