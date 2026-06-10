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


def test_bike_handoff_labelled_when_bike_rides_in_another_car():
    # Ann has no car and rides to the start with Cyd (whose car has no bike
    # rack), so her bike must travel in Bob's car instead. That separation
    # should surface as an explicit night-before hand-off instruction.
    ann = Person(
        id="ann", name="Ann", home_zip="55060", has_car=False,
        return_prefs=only(BIKEBACK),
    )
    bob = Person(
        id="bob", name="Bob", home_zip="55060", has_car=True,
        car_combos=[CarCombo(people=1, bikes=3)], can_drive_morning=True,
        return_prefs=only(BIKEBACK),
    )
    cyd = Person(
        id="cyd", name="Cyd", home_zip="55060", is_rider=False, has_car=True,
        car_combos=[CarCombo(people=4, bikes=0)], can_drive_morning=True,
    )
    problem = Problem(route=ROUTE, people=[ann, bob, cyd])
    sol = solve(problem)
    assert sol.status in ("optimal", "feasible")
    ann_steps = " ".join(sol.itineraries["ann"])
    assert "Bike hand-off" in ann_steps and "Bob" in ann_steps
    # and the carrier is told they're bringing it
    assert any("Bike hand-off" in s and "Ann" in s for s in sol.itineraries["bob"])


HOTEL_ONLY = {
    TONIGHT: Pref.UNWILLING,
    BIKEBACK: Pref.PREFERRED,
    RIDEHOME: Pref.ACCEPTABLE,
}


def test_loaner_bike_lets_a_bikeless_rider_ride():
    # Bo owns no bike; Alex brings a spare for Bo. Bo should ride Alex's loaner,
    # and the plan should say so in both itineraries.
    alex = Person(
        id="alex", name="Alex", home_zip="55021", has_car=True,
        can_drive_morning=True, num_bikes=1, loaner_for="bo",
        return_prefs=only(BIKEBACK),
    )
    bo = Person(
        id="bo", name="Bo", home_zip="55021", has_car=False, num_bikes=0,
        return_prefs=only(BIKEBACK),
    )
    problem = Problem(route=ROUTE, people=[alex, bo])
    sol = solve(problem)
    assert sol.status in ("optimal", "feasible")
    bo_steps = " ".join(sol.itineraries["bo"]).lower()
    assert "loaner" in bo_steps and "alex" in bo_steps
    assert any("spare bike for Bo" in s for s in sol.itineraries["alex"])


def test_bikeless_rider_without_loaner_or_sag_is_infeasible():
    # A rider with no bike, no loaner, and no SAG has nothing to ride.
    solo = Person(
        id="solo", name="Solo", home_zip="55021", has_car=True, num_bikes=0,
        return_prefs=only(BIKEBACK),
    )
    sol = solve(Problem(route=ROUTE, people=[solo]))
    assert sol.status == "infeasible"
    assert "bike" in sol.message.lower()


def test_overnight_bag_reaches_hotel_via_sag():
    # Ann stays overnight with a bag; the SAG carries it to the hotel.
    ann = Person(
        id="ann", name="Ann", home_zip="55021", has_car=False, bag_count=1,
        return_prefs=HOTEL_ONLY,
    )
    sage = Person(
        id="sage", name="Sage", home_zip="55021", is_rider=False, has_car=True,
        car_combos=[CarCombo(people=8, bikes=8)], is_sag_driver=True,
        can_drive_morning=True,
    )
    problem = Problem(route=ROUTE, people=[ann, sage], has_sag=True)
    sol = solve(problem)
    assert sol.status in ("optimal", "feasible")
    assert any("bag" in s.lower() and "sag" in s.lower()
               for s in sol.itineraries["ann"])


def test_overnight_bag_without_finish_bound_vehicle_is_infeasible():
    # Ann stays overnight with a bag, but her only car goes to the start (she
    # rides), there's no SAG and no car dropped at the finish — the bag is stuck.
    ann = Person(
        id="ann", name="Ann", home_zip="55021", has_car=True, bag_count=1,
        return_prefs=HOTEL_ONLY,
    )
    sol = solve(Problem(route=ROUTE, people=[ann]))
    assert sol.status == "infeasible"
    assert "bag" in sol.message.lower()


def test_overnight_bag_with_supporter_to_the_finish_is_ok():
    # A non-riding supporter can ferry the bag to the finish without a SAG.
    ann = Person(
        id="ann", name="Ann", home_zip="55021", has_car=False, bag_count=1,
        return_prefs=HOTEL_ONLY,
    )
    carl = Person(
        id="carl", name="Carl", home_zip="55021", is_rider=False, has_car=True,
        can_drive_morning=True,
    )
    problem = Problem(route=ROUTE, people=[ann, carl])
    sol = solve(problem)
    assert sol.status in ("optimal", "feasible")


def test_one_person_can_drop_bikes_at_start_then_car_at_finish():
    # Ash's car is the only bike rack, so her bike must be dropped at the start
    # before the same car continues to the finish to be left there overnight —
    # a three-leg night-before chain (home -> start -> finish -> ...).
    ash = Person(
        id="ash", name="Ash", home_zip="55416", has_car=True,
        car_combos=[CarCombo(people=5, bikes=2)], num_bikes=1,
        willing_drop_bikes_at_start=True, willing_drop_car=True,
        return_prefs=only(TONIGHT),
    )
    val = Person(
        id="val", name="Val", home_zip="56001", is_rider=False, has_car=True,
        car_combos=[CarCombo(people=4, bikes=0)],  # no rack: can't carry the bike
        willing_drive_dropper_home=True, can_drive_morning=True,
    )
    problem = Problem(route=ROUTE, people=[ash, val])
    sol = solve(problem)
    assert sol.status in ("optimal", "feasible")
    steps = sol.itineraries["ash"]
    start, finish = ROUTE.start_name, ROUTE.finish_name
    # leg to the start (dropping the bike) and a leg start -> finish (dropping car)
    assert any(
        "Night before" in s and "Drive your car" in s and f"→ {start}" in s
        for s in steps
    )
    assert any(
        "Night before" in s and f"{start} → {finish}" in s for s in steps
    )


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
