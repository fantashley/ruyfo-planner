"""Load a fixed roster, or solve it and print the plan to the terminal.

    python -m scripts.fixture plan last_ruyfo      # solve + print (no DB)
    python -m scripts.fixture load last_ruyfo      # seed into the app DB
    python -m scripts.fixture list                 # list available fixtures

Fixtures live in ``fixtures/*.json`` (see fixtures/README.md).
"""

from __future__ import annotations

import argparse
import re
import sys
import textwrap

from app import fixtures
from app.db import get_session, init_db
from app.solver import solve

WRAP_WIDTH = 100


def _print_wrapped(text: str, prefix: str, continuation: str) -> None:
    lines = textwrap.wrap(
        text,
        width=WRAP_WIDTH,
        initial_indent=prefix,
        subsequent_indent=continuation,
        break_long_words=False,
        break_on_hyphens=False,
    )
    if lines:
        print("\n".join(lines))
    else:
        print(prefix.rstrip())


def _split_vehicle_step(step: str) -> tuple[str, list[str]] | None:
    """Split a personal itinerary car step into a summary + contents rows."""
    marker = "; people:"
    if marker not in step:
        return None
    summary, details = step.split(marker, 1)
    details = "people:" + details.rstrip(")")
    return summary + ")", details.split("; ")


def _split_car_move(move: str) -> tuple[str, list[str]] | None:
    """Split an aggregate car movement into a summary + contents rows."""
    match = re.match(r"^(?P<summary>.*) \((?P<details>driver.*)\)$", move)
    if not match:
        return None
    return match.group("summary"), match.group("details").split("; ")


def _print_itinerary_step(step: str) -> None:
    split = _split_vehicle_step(step)
    if split is None:
        _print_wrapped(step, "   - ", "     ")
        return
    summary, details = split
    _print_wrapped(summary, "   - ", "     ")
    for detail in details:
        _print_wrapped(detail, "     • ", "       ")


def _print_car_move(move: str) -> None:
    split = _split_car_move(move)
    if split is None:
        _print_wrapped(move, "   * ", "     ")
        return
    summary, details = split
    _print_wrapped(summary, "   * ", "     ")
    for detail in details:
        _print_wrapped(detail, "     • ", "       ")


def _print_plan(fixture: dict) -> int:
    problem = fixtures.build_problem(fixture)
    sol = solve(problem)
    ev, route = fixture["event"], problem.route
    sag = "yes" if problem.has_sag else "no"
    print(f"\n{ev['name']} — {route.name}  (SAG: {sag})")
    if sol.status == "infeasible":
        print(f"  ✗ No workable plan. {sol.message}\n")
        return 1
    print(
        f"  {sol.status} · {sol.total_drive_miles:.0f} driving miles · "
        f"{sol.pref_deviations} on a backup return option\n"
    )
    for p in problem.people:
        role = "rider" if p.is_rider else "supporter"
        ret = f" [{sol.return_outcome[p.id].value}]" if p.id in sol.return_outcome else ""
        print(f"== {p.name} ({role}){ret} ==")
        for step in sol.itineraries[p.id]:
            _print_itinerary_step(step)
        print()
    print("Car movements:")
    for move in sol.car_moves:
        _print_car_move(move)
    print(
        f"\nBurden (equivalent miles; drive + {problem.chore_leg_miles:.0f}/chore leg + "
        f"{problem.pref_penalty_miles:.0f} if on a backup return):"
    )
    for p in problem.people:
        b = sol.burdens[p.id]
        flags = []
        if b["chore_legs"]:
            flags.append(f"{b['chore_legs']} chore leg{'s' if b['chore_legs'] != 1 else ''}")
        if b["deviation"]:
            flags.append("backup return")
        extra = f" ({', '.join(flags)})" if flags else ""
        marker = "  ← max" if b["total"] == sol.max_burden and sol.max_burden > 0 else ""
        print(f"   {p.name:12} {b['total']:6.1f}  drives {b['drive_miles']:.0f} mi{extra}{marker}")
    print()
    return 0


def cmd_plan(args) -> int:
    return _print_plan(fixtures.load(args.name))


def cmd_load(args) -> int:
    init_db()
    fx = fixtures.load(args.name)
    with get_session() as s:
        ev = fixtures.seed_event(s, fx, replace=not args.keep_existing)
        ev_id = ev.id  # read before the session closes
    print(
        f"Loaded '{fx['event']['name']}' as event {ev_id} "
        f"({len(fx['participants'])} participants).\n"
        f"Run the app and open /events/{ev_id}."
    )
    return 0


def cmd_list(args) -> int:
    files = sorted(fixtures.FIXTURES_DIR.glob("*.json"))
    if not files:
        print(f"No fixtures in {fixtures.FIXTURES_DIR}")
        return 0
    for f in files:
        try:
            fx = fixtures.load(f)
            n = len(fx.get("participants", []))
            print(f"  {f.stem:24} {fx['event']['name']} ({n} participants)")
        except Exception as exc:  # noqa: BLE001 - just a listing convenience
            print(f"  {f.stem:24} <invalid: {exc}>")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="fixture", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_plan = sub.add_parser("plan", help="solve the fixture and print the plan")
    p_plan.add_argument("name", help="fixture name (sans .json) or path")
    p_plan.set_defaults(func=cmd_plan)

    p_load = sub.add_parser("load", help="seed the fixture into the app DB")
    p_load.add_argument("name", help="fixture name (sans .json) or path")
    p_load.add_argument(
        "--keep-existing", action="store_true",
        help="don't replace an existing event of the same name",
    )
    p_load.set_defaults(func=cmd_load)

    p_list = sub.add_parser("list", help="list available fixtures")
    p_list.set_defaults(func=cmd_list)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
