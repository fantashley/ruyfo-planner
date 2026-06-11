from ortools.sat.python import cp_model

from app.events import ROUTES
from app.solver import (
    ALLOWED_ARCS,
    F,
    H,
    NIGHT_BEFORE,
    S,
    CarCombo,
    Person,
    Pref,
    Problem,
    ReturnOption,
    T_BIKEBACK,
    T_HOME_TONIGHT,
    T_MORNING,
    T_RIDE,
    _Model,
    _extract,
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
        id="alex", name="Alex", home_zip="55060", has_car=True,
        can_drive_morning=True, num_bikes=1, loaner_for="bo",
        return_prefs=only(BIKEBACK),
    )
    bo = Person(
        id="bo", name="Bo", home_zip="55060", has_car=False, num_bikes=0,
        return_prefs=only(BIKEBACK),
    )
    problem = Problem(route=ROUTE, people=[alex, bo])
    sol = solve(problem)
    assert sol.status in ("optimal", "feasible")
    bo_steps = " ".join(sol.itineraries["bo"]).lower()
    assert "loaner" in bo_steps and "alex" in bo_steps
    alex_steps = " ".join(sol.itineraries["alex"])
    assert "people: Alex (driver)" in alex_steps
    assert "bikes: 2 bikes" in alex_steps
    assert "loaner for Bo" in alex_steps
    assert any("spare bike for Bo" in s for s in sol.itineraries["alex"])
    assert any("loaner for Bo" in move for move in sol.car_moves)


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
    sage_steps = " ".join(sol.itineraries["sage"])
    assert "overnight bags: 1 bag: Ann: 1 bag" in sage_steps


def test_bikeback_rider_can_bring_overnight_bag_home():
    # A bike-back rider can stage an overnight bag at the hotel, use it Friday
    # night, and carry it back to the start Saturday morning.
    ann = Person(
        id="ann", name="Ann", home_zip="55021", has_car=False, bag_count=1,
        return_prefs=HOTEL_ONLY,
    )
    sage = Person(
        id="sage", name="Sage", home_zip="55021", is_rider=False, has_car=True,
        car_combos=[CarCombo(people=8, bikes=8)], is_sag_driver=True,
        can_drive_morning=True,
    )
    sol = solve(Problem(route=ROUTE, people=[ann, sage], has_sag=True))
    assert sol.status in ("optimal", "feasible")
    assert sol.return_outcome["ann"] == BIKEBACK
    assert any(
        "Bike back" in step and "overnight bags: 1 bag: Ann: 1 bag" in step
        for step in sol.itineraries["ann"]
    )


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


def test_overnight_bag_stays_at_hotel_friday_night():
    # A bag for someone staying overnight cannot be sent home Friday evening;
    # they still need it at the hotel that night.
    ann = Person(
        id="ann", name="Ann", home_zip="55021", has_car=False, bag_count=1,
        return_prefs=HOTEL_ONLY,
    )
    sage = Person(
        id="sage", name="Sage", home_zip="55021", is_rider=False, has_car=True,
        car_combos=[CarCombo(people=8, bikes=8)], is_sag_driver=True,
        can_drive_morning=True,
    )
    mb = _Model(Problem(route=ROUTE, people=[ann, sage], has_sag=True))
    mb.m.Add(mb.home_tonight["ann"] == 0)
    mb.m.Add(mb.gat["ann", T_HOME_TONIGHT, H] == 1)
    solver = cp_model.CpSolver()
    assert solver.Solve(mb.m) == cp_model.INFEASIBLE


def test_night_before_bag_drop_must_stay_with_parked_car():
    # A bag can be staged in a locked car, but not dropped by a car that leaves
    # it behind before ride morning.
    ann = Person(
        id="ann", name="Ann", home_zip="55021", has_car=False, bag_count=1,
        return_prefs=HOTEL_ONLY,
    )
    bob = Person(
        id="bob", name="Bob", home_zip="55021", is_rider=False, has_car=True,
        car_combos=[CarCombo(people=4, bikes=2)],
    )
    mb = _Model(Problem(route=ROUTE, people=[ann, bob]))
    mb.m.Add(mb.gincar["ann", "bob", 0, H, F] == 1)
    mb.m.Add(mb.cat["bob", T_MORNING, F] == 0)
    solver = cp_model.CpSolver()
    assert solver.Solve(mb.m) == cp_model.INFEASIBLE


def test_overnight_bag_prefers_morning_sag_over_mankato_drop():
    # Even if a car is already being left in Mankato overnight, prefer bringing
    # the bag to the start in the morning and handing it to the SAG.
    ann = Person(
        id="ann", name="Ann", home_zip="55021", has_car=False, bag_count=1,
        return_prefs=HOTEL_ONLY,
    )
    bob = Person(
        id="bob", name="Bob", home_zip="55021", is_rider=False, has_car=True,
        car_combos=[CarCombo(people=2, bikes=0)], willing_drop_car=True,
        return_prefs=only(TONIGHT),
    )
    sage = Person(
        id="sage", name="Sage", home_zip="55021", is_rider=False, has_car=True,
        car_combos=[CarCombo(people=8, bikes=8)], is_sag_driver=True,
        can_drive_morning=True, willing_drive_dropper_home=True,
    )
    problem = Problem(route=ROUTE, people=[ann, bob, sage], has_sag=True)
    mb = _Model(problem)
    mb.m.Add(mb.cat["bob", T_MORNING, F] == 1)
    solver = cp_model.CpSolver()
    status = solver.Solve(mb.m)
    assert status in (cp_model.OPTIMAL, cp_model.FEASIBLE)
    assert solver.Value(mb.gincar["ann", "sage", T_RIDE, S, F]) == 1
    assert all(
        solver.Value(mb.gincar["ann", owner.id, k, a, b]) == 0
        for owner in mb.owners
        for k in NIGHT_BEFORE
        for (a, b) in ALLOWED_ARCS[k]
        if b == F and ("ann", owner.id, k, a, b) in mb.gincar
    )


def test_everyone_sleeps_at_home_the_night_before():
    # Ann drops her car at the finish the night before; the home-at-night rule
    # means she (and her ride) must be back home when the morning begins, rather
    # than parked at the start or finish overnight.
    ann = Person(
        id="ann", name="Ann", home_zip="55021", has_car=True,
        willing_drop_car=True, return_prefs=only(TONIGHT),
    )
    carl = Person(
        id="carl", name="Carl", home_zip="56001", has_car=True, is_rider=False,
        can_drive_morning=True, willing_drive_dropper_home=True,
    )
    mb = _Model(Problem(route=ROUTE, people=[ann, carl]))
    solver = cp_model.CpSolver()
    assert solver.Solve(mb.m) in (cp_model.OPTIMAL, cp_model.FEASIBLE)
    for p in (ann, carl):
        assert solver.Value(mb.pat[p.id, T_MORNING, H]) == 1


def test_chain_drop_bikes_then_car_is_feasible():
    # The same person can drop bikes at the start and then drive their car on to
    # the finish the night before (home -> start -> finish), and still get home.
    # Val's car has no rack, so only Ash's car can carry the bike.
    ash = Person(
        id="ash", name="Ash", home_zip="55060", has_car=True,
        car_combos=[CarCombo(people=5, bikes=2)], num_bikes=1,
        willing_drop_bikes_at_start=True, willing_drop_car=True,
        return_prefs=only(TONIGHT),
    )
    val = Person(
        id="val", name="Val", home_zip="55021", is_rider=False, has_car=True,
        car_combos=[CarCombo(people=4, bikes=0)],
        willing_drive_dropper_home=True, can_drive_morning=True,
    )
    mb = _Model(Problem(route=ROUTE, people=[ash, val]))
    # force the chain: Ash's bike sits at the start after the first night-before
    # leg, and her car is parked at the finish when morning begins
    mb.m.Add(mb.bat["ash", 1, S] == 1)
    mb.m.Add(mb.cat["ash", T_MORNING, F] == 1)
    solver = cp_model.CpSolver()
    assert solver.Solve(mb.m) in (cp_model.OPTIMAL, cp_model.FEASIBLE)
    # and Ash is still home when the morning begins
    assert solver.Value(mb.pat["ash", T_MORNING, H]) == 1


def test_no_before_ride_day_note_for_a_post_ride_bike_return():
    # A home-tonight rider whose bike strands and is ferried back through the
    # start the *next* morning must NOT be told to "drop it before ride day".
    # Force that exact leg: Robin is home tonight, but their bike rides the SAG's
    # car finish->start the next morning (then home with their household partner).
    quinn = Person(
        id="quinn", name="Quinn", home_zip="55391", household="vance", has_car=True,
        car_combos=[CarCombo(2, 2)], num_bikes=1, bag_count=1, can_drive_morning=True,
        return_prefs=only(BIKEBACK),
    )
    robin = Person(
        id="robin", name="Robin", home_zip="55391", household="vance", has_car=False,
        num_bikes=1, return_prefs=only(TONIGHT),
    )
    sag = Person(
        id="sag", name="Sag", home_zip="55410", is_rider=False, has_car=True,
        car_combos=[CarCombo(7, 2)], is_sag_driver=True, can_drive_morning=True,
        willing_drive_dropper_home=True,
    )
    problem = Problem(route=ROUTE, people=[quinn, robin, sag], has_sag=True)
    mb = _Model(problem)
    mb.m.Add(mb.pat["robin", T_HOME_TONIGHT, H] == 1)  # Robin made it home that night
    mb.m.Add(mb.bincar["robin", "sag", T_BIKEBACK, F, S] == 1)  # bike ferried back next AM
    solver = cp_model.CpSolver()
    status = solver.Solve(mb.m)
    assert status in (cp_model.OPTIMAL, cp_model.FEASIBLE)
    sol = _extract(problem, mb, solver, status)
    steps = sol.itineraries["robin"]
    assert not any("before ride day" in s for s in steps)  # the bug: no false drop note
    assert any("brings your bike back" in s for s in steps)  # the real return is shown


def test_no_return_handoff_note_for_pre_ride_bike_shuttle():
    # A bike may ride through a carrier's home while being staged for the start.
    # That pre-ride shuttle should not look like an after-the-ride bike return.
    ann = Person(
        id="ann", name="Ann", home_zip="55060", has_car=False,
        return_prefs=only(BIKEBACK),
    )
    bob = Person(
        id="bob", name="Bob", home_zip="55060", is_rider=False, has_car=True,
        car_combos=[CarCombo(people=4, bikes=2)], can_drive_morning=True,
    )
    problem = Problem(route=ROUTE, people=[ann, bob])
    mb = _Model(problem)
    mb.m.Add(mb.bincar["ann", "bob", 0, H, F] == 1)
    mb.m.Add(mb.bincar["ann", "bob", 1, F, H] == 1)
    mb.m.Add(mb.bincar["ann", "bob", T_MORNING, H, S] == 1)
    solver = cp_model.CpSolver()
    status = solver.Solve(mb.m)
    assert status in (cp_model.OPTIMAL, cp_model.FEASIBLE)
    sol = _extract(problem, mb, solver, status)
    steps = sol.itineraries["ann"]
    assert not any("brings your bike back" in s for s in steps)


def test_cargo_prefers_direct_leg_when_car_route_is_fixed():
    # If a car is already making an unrelated round trip, cargo should not tag
    # along unless it helps. The bike can wait for the direct morning leg.
    ann = Person(
        id="ann", name="Ann", home_zip="55060", has_car=False,
        return_prefs=only(BIKEBACK),
    )
    bob = Person(
        id="bob", name="Bob", home_zip="55060", is_rider=False, has_car=True,
        car_combos=[CarCombo(people=4, bikes=2)], can_drive_morning=True,
        return_prefs=only(RIDEHOME),
    )
    mb = _Model(Problem(route=ROUTE, people=[ann, bob]))
    mb.m.Add(mb.cmove["bob", 0, H, F] == 1)
    mb.m.Add(mb.cmove["bob", 1, F, H] == 1)
    mb.m.Add(mb.cmove["bob", T_MORNING, H, S] == 1)
    solver = cp_model.CpSolver()
    status = solver.Solve(mb.m)
    assert status in (cp_model.OPTIMAL, cp_model.FEASIBLE)
    assert solver.Value(mb.bincar["ann", "bob", 0, H, F]) == 0
    assert solver.Value(mb.bincar["ann", "bob", 1, F, H]) == 0
    assert solver.Value(mb.bincar["ann", "bob", T_MORNING, H, S]) == 1


def test_home_tonight_person_cannot_have_saturday_activity():
    # If a person goes home the night of the ride, they should not be assigned
    # any next-morning passenger/driver activity. Force such a leg and the model
    # should become infeasible.
    rider = Person(
        id="rider", name="Rider", home_zip="55021", has_car=True,
        return_prefs=only(TONIGHT),
    )
    sag = Person(
        id="sag", name="Sag", home_zip="55021", is_rider=False, has_car=True,
        car_combos=[CarCombo(8, 8)], is_sag_driver=True, can_drive_morning=True,
    )
    mb = _Model(Problem(route=ROUTE, people=[rider, sag], has_sag=True))
    mb.m.Add(mb.home_tonight["rider"] == 1)
    mb.m.Add(mb.incar["rider", "rider", T_BIKEBACK, H, F] == 1)
    solver = cp_model.CpSolver()
    assert solver.Solve(mb.m) == cp_model.INFEASIBLE


def test_supporter_return_preference_is_honored():
    # A SAG driver / supporter is now a full participant for return purposes: if
    # they're unwilling to head home the next morning, they must be home the night
    # of, like anyone else (rather than being kept out overnight to ferry things).
    rider = Person(
        id="r", name="R", home_zip="55408", has_car=False, num_bikes=1,
        return_prefs=only(TONIGHT),
    )
    sag = Person(
        id="sag", name="Sag", home_zip="55408", is_rider=False, has_car=True,
        car_combos=[CarCombo(8, 8)], is_sag_driver=True, can_drive_morning=True,
        return_prefs={TONIGHT: Pref.PREFERRED, BIKEBACK: Pref.UNWILLING,
                      RIDEHOME: Pref.UNWILLING},
    )
    sol = solve(Problem(route=ROUTE, people=[rider, sag], has_sag=True))
    assert sol.status in ("optimal", "feasible")
    assert sol.return_outcome["sag"] == TONIGHT


def test_burden_accounting_is_consistent():
    # max_burden matches the per-person totals, and a carless participant on
    # their preferred return carries no burden at all.
    h1 = Person(
        id="h1", name="Pat", home_zip="55021", household="ek", has_car=True,
        car_combos=[CarCombo(people=4, bikes=4)], return_prefs=only(BIKEBACK),
    )
    h2 = Person(
        id="h2", name="Sam", home_zip="55021", household="ek", has_car=False,
        return_prefs=only(BIKEBACK),
    )
    sol = solve(Problem(route=ROUTE, people=[h1, h2]))
    assert sol.status in ("optimal", "feasible")
    assert sol.max_burden == max(b["total"] for b in sol.burdens.values())
    assert sol.burdens["h2"]["total"] == 0  # no car, no chores, preferred return


def test_passenger_on_chore_leg_gets_chore_surcharge():
    # Riding home from a night-before drop is still part of the logistical load,
    # even for the passenger, but it counts as a fixed chore leg rather than
    # distance-based driving burden.
    rider = Person(
        id="rider", name="Rider", home_zip="55021", has_car=True,
        willing_drop_car=True, return_prefs=only(TONIGHT),
    )
    helper = Person(
        id="helper", name="Helper", home_zip="56001", is_rider=False, has_car=True,
        willing_drive_dropper_home=True, can_drive_morning=True,
    )
    mb = _Model(Problem(route=ROUTE, people=[rider, helper]))
    mb.m.Add(mb.incar["rider", "helper", 1, F, H] == 1)
    solver = cp_model.CpSolver()
    status = solver.Solve(mb.m)
    assert status in (cp_model.OPTIMAL, cp_model.FEASIBLE)
    sol = _extract(Problem(route=ROUTE, people=[rider, helper]), mb, solver, status)
    assert sol.burdens["rider"]["chore_legs"] >= 1
    assert sol.burdens["rider"]["total"] >= 15


def test_sag_driver_legs_are_counted_as_chore_burden():
    # Every leg driven by the SAG driver is real driving and a chore, so the
    # SAG driver's burden includes both drive miles and chore surcharges.
    dana = Person(
        id="dana", name="Dana", home_zip="55021", has_car=False,
        return_prefs={TONIGHT: Pref.PREFERRED, BIKEBACK: Pref.UNWILLING,
                      RIDEHOME: Pref.UNWILLING},
    )
    sage = Person(
        id="sage", name="Sage", home_zip="55021", is_rider=False, has_car=True,
        car_combos=[CarCombo(people=8, bikes=8)], is_sag_driver=True,
        can_drive_morning=True,
    )
    problem = Problem(route=ROUTE, people=[dana, sage], has_sag=True)
    mb = _Model(problem)
    mb.m.Add(mb.cmove["sage", T_RIDE, S, F] == 1)
    solver = cp_model.CpSolver()
    status = solver.Solve(mb.m)
    assert status in (cp_model.OPTIMAL, cp_model.FEASIBLE)
    sol = _extract(problem, mb, solver, status)
    # Sage lives in the start town: counted miles are sweep + finish->home (~75),
    # not just finish->home (~37).
    assert 65 < sol.burdens["sage"]["drive_miles"] < 85
    assert sol.burdens["sage"]["chore_legs"] == 3
    assert 110 < sol.burdens["sage"]["total"] < 130
    assert sol.burdens["dana"]["chore_legs"] == 0
    assert sol.burdens["dana"]["total"] == 0


def test_fairness_can_trade_miles_for_lower_max_burden():
    # Two carless riders need a morning lift to the start and an evening ride
    # home from the finish. One supporter can do all the driving with fewer total
    # miles, but the fairness term may split duties to lower the heaviest load.
    riders = [
        Person(id=f"r{i}", name=f"R{i}", home_zip="55060", has_car=False,
               return_prefs=only(TONIGHT))
        for i in range(2)
    ]
    sup1 = Person(
        id="sup1", name="Sup1", home_zip="55060", is_rider=False, has_car=True,
        car_combos=[CarCombo(people=8, bikes=8)], can_drive_morning=True,
    )
    sup2 = Person(
        id="sup2", name="Sup2", home_zip="55060", is_rider=False, has_car=True,
        car_combos=[CarCombo(people=8, bikes=8)], can_drive_morning=True,
    )
    problem = Problem(
        route=ROUTE, people=riders + [sup1, sup2],
        fairness_weight=2.0, chore_leg_miles=5.0,
    )
    sol = solve(problem)
    assert sol.status in ("optimal", "feasible")
    # both supporters share the driving rather than one doing everything.
    assert sol.burdens["sup1"]["drive_miles"] > 0
    assert sol.burdens["sup2"]["drive_miles"] > 0

    # With fairness off, one supporter carries the whole load because it is
    # cheaper in total miles; the max burden is worse than the fair plan's.
    sol0 = solve(Problem(route=ROUTE, people=riders + [sup1, sup2],
                         fairness_weight=0.0, chore_leg_miles=5.0))
    assert sol0.total_drive_miles < sol.total_drive_miles  # fairness costs miles
    assert sol.max_burden < sol0.max_burden  # ...but lightens the heaviest load


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
