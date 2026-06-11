"""Load fixed rosters from JSON files in ``fixtures/``.

A fixture is the single source of truth for a roster — never randomized. It can
be turned straight into a solver :class:`~app.solver.Problem` (fast, no DB) or
seeded into the SQLite DB for the web UI. See ``fixtures/README.md`` for the
schema and ``fixtures/example.json`` for a worked example.
"""

from __future__ import annotations

import json
from pathlib import Path

from sqlmodel import delete, select

from .events import get_route
from .models import Event, Participant, parse_combos
from .solver import CarCombo, Person, Pref, Problem, ReturnOption

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"

_RETURN_KEYS = {
    "tonight": ReturnOption.DRIVE_HOME_TONIGHT,
    "bikeback": ReturnOption.HOTEL_BIKE_BACK,
    "ridehome": ReturnOption.HOTEL_RIDE_HOME,
}
# defaults mirror the web form: prefer driving home, accept the rest
_RETURN_DEFAULTS = {"tonight": "preferred", "bikeback": "acceptable", "ridehome": "acceptable"}


def _resolve_path(name_or_path: str | Path) -> Path:
    p = Path(name_or_path)
    candidates = [p, FIXTURES_DIR / p, FIXTURES_DIR / f"{p}.json"]
    for c in candidates:
        if c.is_file():
            return c
    raise FileNotFoundError(
        f"Fixture {name_or_path!r} not found (looked in {FIXTURES_DIR} and as a path)"
    )


def load(name_or_path: str | Path) -> dict:
    """Read and parse a fixture JSON file by name (sans .json) or path."""
    return json.loads(_resolve_path(name_or_path).read_text())


def _return_strings(p: dict) -> dict[str, str]:
    merged = dict(_RETURN_DEFAULTS)
    merged.update(p.get("return", {}))
    return merged


def build_problem(fixture: dict) -> Problem:
    """Turn a fixture into a solver Problem (people keyed by name; no DB)."""
    ev = fixture["event"]
    people = []
    for p in fixture["participants"]:
        ret = _return_strings(p)
        people.append(
            Person(
                id=p["name"],  # names are the keys within a roster
                name=p["name"],
                home_zip=str(p["home_zip"]),
                household=p.get("household", ""),
                is_rider=p.get("is_rider", True),
                num_bikes=p.get("num_bikes", 1),
                loaner_for=p.get("loaner_for", ""),  # borrower's name == their id
                bag_count=p.get("bag_count", 0),
                has_car=p.get("has_car", False),
                car_combos=parse_combos(p.get("car_combos", "")) if p.get("has_car") else [],
                willing_drop_car=p.get("willing_drop_car", False),
                willing_drop_bikes_at_start=p.get("willing_drop_bikes_at_start", False),
                willing_drive_dropper_home=p.get("willing_drive_dropper_home", False),
                can_drive_morning=p.get("can_drive_morning", False),
                is_sag_driver=p.get("is_sag_driver", False),
                return_prefs={opt: Pref(ret[k]) for k, opt in _RETURN_KEYS.items()},
            )
        )
    tuning = fixture.get("tuning", {})
    return Problem(
        route=get_route(ev["route_key"]),
        people=people,
        has_sag=ev.get("has_sag", False),
        **{
            k: float(tuning[k])
            for k in (
                "detour_factor",
                "pref_penalty_miles",
                "fairness_weight",
                "chore_leg_miles",
            )
            if k in tuning
        },
    )


def seed_event(session, fixture: dict, *, replace: bool = True) -> Event:
    """Insert the fixture's event + participants into the DB.

    With ``replace`` (default), any existing event of the same name is removed
    first so re-loading is idempotent. Loaner references (by borrower name) are
    resolved to participant ids after insert.
    """
    ev_data = fixture["event"]
    if replace:
        for old in session.exec(select(Event).where(Event.name == ev_data["name"])).all():
            session.exec(delete(Participant).where(Participant.event_id == old.id))
            session.delete(old)
        session.commit()

    ev = Event(
        name=ev_data["name"],
        route_key=ev_data["route_key"],
        has_sag=ev_data.get("has_sag", False),
    )
    session.add(ev)
    session.commit()
    session.refresh(ev)

    rows = []
    for p in fixture["participants"]:
        ret = _return_strings(p)
        rows.append(
            Participant(
                event_id=ev.id,
                name=p["name"],
                email=p.get("email", ""),
                home_zip=str(p["home_zip"]),
                household=p.get("household", ""),
                is_rider=p.get("is_rider", True),
                num_bikes=p.get("num_bikes", 1),
                bag_count=p.get("bag_count", 0),
                has_car=p.get("has_car", False),
                car_combos=p.get("car_combos", ""),
                willing_drop_car=p.get("willing_drop_car", False),
                willing_drop_bikes_at_start=p.get("willing_drop_bikes_at_start", False),
                willing_drive_dropper_home=p.get("willing_drive_dropper_home", False),
                can_drive_morning=p.get("can_drive_morning", False),
                is_sag_driver=p.get("is_sag_driver", False),
                pref_tonight=ret["tonight"],
                pref_bikeback=ret["bikeback"],
                pref_ridehome=ret["ridehome"],
            )
        )
    session.add_all(rows)
    session.commit()
    for r in rows:
        session.refresh(r)

    # resolve loaner_for (given as the borrower's name) to the borrower's id
    by_name = {r.name: r for r in rows}
    for p, r in zip(fixture["participants"], rows):
        borrower = p.get("loaner_for", "")
        if borrower and borrower in by_name:
            r.loaner_for = str(by_name[borrower].id)
            session.add(r)
    session.commit()
    return ev
