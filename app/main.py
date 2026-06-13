"""RUYFO logistics planner — FastAPI web app."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlmodel import select

from . import geo
from .db import get_session, init_db
from .events import ROUTES, Route, get_route
from .models import Event, Participant, to_person
from .solver import Problem, ReturnOption, TRANSITION_LABELS, solve

BASE = Path(__file__).parent
app = FastAPI(title="RUYFO Logistics Planner")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")

RETURN_LABELS = {
    ReturnOption.DRIVE_HOME_TONIGHT: "Drive/ride home the night of the ride",
    ReturnOption.HOTEL_BIKE_BACK: "Hotel overnight, then bike back",
    ReturnOption.HOTEL_RIDE_HOME: "Hotel overnight, then ride home next morning",
}
templates.env.globals["RETURN_LABELS"] = RETURN_LABELS
templates.env.globals["ROUTES"] = ROUTES

RETURN_SHORT_LABELS = {
    ReturnOption.DRIVE_HOME_TONIGHT: "Home tonight",
    ReturnOption.HOTEL_BIKE_BACK: "Bike back",
    ReturnOption.HOTEL_RIDE_HOME: "Ride home next morning",
}
PHASE_ORDER = {label: index for index, label in enumerate(TRANSITION_LABELS)}


def _split_phase(text: str) -> tuple[str, str]:
    if ": " not in text:
        return "", text
    phase, body = text.split(": ", 1)
    return phase, body


def _split_outer_detail(text: str) -> tuple[str, str]:
    start = None
    depth = 0
    for i, char in enumerate(text):
        if char == "(":
            if depth == 0 and i > 0 and text[i - 1] == " ":
                start = i
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0 and start is not None:
                if i == len(text) - 1:
                    return text[:start - 1], text[start + 1:i]
                start = None
    return text, ""


def _return_short(value: ReturnOption) -> str:
    return RETURN_SHORT_LABELS.get(value, str(value))


def _burden_summary(burden: dict[str, Any] | None) -> str:
    # The "eq-mi" total is an internal weighting detail; surface only the
    # concrete, human-meaningful pieces of a person's load.
    if not burden:
        return ""
    parts = []
    if burden.get("drive_miles", 0):
        parts.append(f"{burden['drive_miles']:.0f} mi driving")
    if burden.get("chore_legs", 0):
        parts.append(
            f"{burden['chore_legs']} chore leg"
            f"{'s' if burden['chore_legs'] != 1 else ''}"
        )
    if burden.get("deviation"):
        parts.append("backup return")
    return " · ".join(parts) if parts else "No driving or chores"


def _cargo_summary(cargo: str) -> str:
    if not cargo:
        return ""
    parts = []
    for part in cargo.split("; "):
        part = part.strip()
        if not part:
            continue
        parts.append(part.split(": ", 1)[0])
    return "; ".join(parts)


def _possessive(name: str) -> str:
    return f"{name}'" if name.endswith("s") else f"{name}'s"


def _human_count(count: int, singular: str, plural: str | None = None) -> str:
    word = singular if count == 1 else plural or f"{singular}s"
    return f"{count} {word}"


def _list_phrase(parts: list[str]) -> str:
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return f"{parts[0]} and {parts[1]}"
    return f"{', '.join(parts[:-1])}, and {parts[-1]}"


def _count_from_cargo(cargo: str, noun: str) -> int:
    match = re.search(rf"\b(\d+) {noun}s?\b", cargo)
    return int(match.group(1)) if match else 0


def _split_top(text: str, sep: str = ", ") -> list[str]:
    """Split on ``sep`` only at paren depth 0 (so '(1 own, loaner …)' stays whole)."""
    parts: list[str] = []
    depth = 0
    start = 0
    i = 0
    while i < len(text):
        char = text[i]
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif depth == 0 and text[i:i + len(sep)] == sep:
            parts.append(text[start:i])
            start = i + len(sep)
            i += len(sep)
            continue
        i += 1
    parts.append(text[start:])
    return [p.strip() for p in parts if p.strip()]


def _cargo_breakdown(cargo: str) -> dict[str, list[str]]:
    """Per-owner cargo labels from a car-leg cargo string.

    '5 bikes: Avery: 2 bikes (1 own, loaner for Cory), Blake: 1 bike; 1 bag: Avery: 1 bag'
    -> {'bikes': ['Avery: 2 bikes (1 own, loaner for Cory)', 'Blake: 1 bike'],
        'bags':  ['Avery: 1 bag']}
    """
    out: dict[str, list[str]] = {"bikes": [], "bags": []}
    for segment in cargo.split("; "):
        segment = segment.strip()
        match = re.match(r"\d+\s+(bike|bag)s?:\s*(.+)$", segment)
        if not match:
            continue
        kind = "bikes" if match.group(1) == "bike" else "bags"
        out[kind] = _split_top(match.group(2))
    return out


def _location_for_plan(location: str, owner: str) -> str:
    home = re.fullmatch(r"home \((.+)\)", location)
    if home:
        return f"{_possessive(owner)} home in {home.group(1)}"
    return location


def _parse_people_detail(people: str) -> tuple[str, list[str]]:
    driver = ""
    passengers: list[str] = []
    match = re.match(r"driven by (.+?)(?:, with (.+))?$", people)
    if match:
        driver = match.group(1)
        if match.group(2):
            passengers = [name.strip() for name in match.group(2).split(",") if name.strip()]
    return driver, passengers


def _raw_car_move(move: str) -> dict[str, Any] | None:
    phase, body = _split_phase(move)
    headline, detail = _split_outer_detail(body)
    people, cargo = detail, ""
    if "; " in detail:
        people, cargo = detail.split("; ", 1)
    car = re.match(r"(.+?)'s car (.+?) → (.+)$", headline)
    if not car:
        return None
    driver, passengers = _parse_people_detail(people)
    owner = car.group(1)
    return {
        "move": move,
        "phase": phase,
        "phase_order": PHASE_ORDER.get(phase, 999),
        "owner": owner,
        "origin_raw": car.group(2),
        "destination_raw": car.group(3),
        "driver": driver or owner,
        "passengers": passengers,
        "people": people,
        "cargo": cargo,
        "headline": headline,
    }


def _recovery_passengers(
    current: dict[str, Any],
    moves: list[dict[str, Any]],
    route: Route | None,
) -> list[str]:
    if not route or current["destination_raw"] != route.finish_name:
        return []
    if _count_from_cargo(current["cargo"], "bike") or _count_from_cargo(current["cargo"], "bag"):
        return []
    if not current["phase"].startswith("Night before"):
        return []

    for other in moves:
        if other is current:
            continue
        if other["owner"] != current["owner"]:
            continue
        if not other["phase"].startswith("Night before"):
            continue
        if other["phase_order"] < current["phase_order"]:
            continue
        if other["origin_raw"] == route.finish_name and other["passengers"]:
            return other["passengers"]
    return []


def _plan_purpose(
    phase: str,
    owner: str,
    origin: str,
    destination: str,
    passengers: list[str],
    bikes: int,
    bags: int,
    route: Route | None,
    recovery_passengers: list[str] | None = None,
) -> str:
    cargo_parts = []
    if bikes:
        cargo_parts.append(_human_count(bikes, "bike"))
    if bags:
        cargo_parts.append(_human_count(bags, "overnight bag"))

    if phase.startswith("Night before"):
        if bikes and route and destination == route.start_name:
            return f"Drops off {_list_phrase(cargo_parts)} in {destination} before ride day."
        if bikes:
            return f"Stages {_list_phrase(cargo_parts)} before ride day."
        if route and origin == route.finish_name and passengers:
            return f"Brings {_list_phrase(passengers)} home after the finish drop-off."
        if route and destination == route.finish_name:
            if recovery_passengers:
                return (
                    f"Goes to the finish in {destination} to give "
                    f"{_list_phrase(recovery_passengers)} a ride home."
                )
            return (
                f"Drops off {_possessive(owner)} car at the finish in {destination} "
                "so it's available after the ride."
            )
        return f"Stages {_possessive(owner)} car for the next leg of the plan."

    if phase == "Morning of the ride":
        if passengers and cargo_parts:
            return (
                f"Gets {_list_phrase(passengers)} to the start in {destination} "
                f"and brings {_list_phrase(cargo_parts)}."
            )
        if passengers:
            return f"Gets {_list_phrase(passengers)} to the start in {destination}."
        if cargo_parts:
            return f"Brings {_list_phrase(cargo_parts)} to the start in {destination}."
        return f"Gets the car to the start in {destination}."

    if phase == "The ride":
        if passengers or cargo_parts:
            parts = []
            if passengers:
                parts.append(f"carries {_list_phrase(passengers)}")
            if cargo_parts:
                parts.append(f"carries {_list_phrase(cargo_parts)}")
            return f"Follows the ride route and {_list_phrase(parts)} to the finish."
        return "Follows the ride route to the finish."

    if phase.startswith("Evening"):
        if "Faribault" in destination and not passengers and not bikes and not bags:
            return f"Positions the car in {destination} for the next-morning return."
        parts = []
        if passengers:
            parts.append(f"gets {_list_phrase(passengers)} home")
        if cargo_parts:
            parts.append(f"brings {_list_phrase(cargo_parts)} home")
        if parts:
            return f"{_list_phrase(parts)[0].upper()}{_list_phrase(parts)[1:]} after the ride."

    if phase.startswith("Next morning"):
        parts = []
        if passengers:
            parts.append(f"brings {_list_phrase(passengers)} home")
        if cargo_parts:
            parts.append(f"returns {_list_phrase(cargo_parts)}")
        if parts:
            return f"{_list_phrase(parts)[0].upper()}{_list_phrase(parts)[1:]} after the bike-back."

    if cargo_parts:
        return f"Moves {_list_phrase(cargo_parts)} where they need to be."
    if passengers:
        return f"Moves {_list_phrase(passengers)} where they need to be."
    return "Positions the car for the overall plan."


def _note_summary(note: str) -> str:
    bag = re.match(
        r"Overnight bag: bring it to (.+?) and hand it to the SAG wagon .+ in (.+?)\.",
        note,
    )
    if bag:
        return f"Overnight bag: bring to {bag.group(1)}; SAG takes it to {bag.group(2)}."

    bag_car = re.match(r"Overnight bag: it rides (.+?)'s car to the hotel in (.+?)\.", note)
    if bag_car:
        return f"Overnight bag: rides {bag_car.group(1)}'s car to {bag_car.group(2)}."

    bike_out = re.match(
        r"Bike hand-off: get your bike to (.+?) before ride day .+ they bring it to (.+?)\.",
        note,
    )
    if bike_out:
        return f"Bike hand-off: get your bike to {bike_out.group(1)}; they bring it to {bike_out.group(2)}."

    bike_driver = re.match(
        r"Bike hand-off: you're bringing (.+?)'s bike to (.+?) .+",
        note,
    )
    if bike_driver:
        return f"Bike hand-off: bring {bike_driver.group(1)}'s bike to {bike_driver.group(2)}."

    bike_back = re.match(
        r"Bike hand-off: (.+?) brings your bike back near your home afterward .+",
        note,
    )
    if bike_back:
        return f"Bike hand-off: collect your bike from {bike_back.group(1)} afterward."

    return note


def _plan_notes(steps: list[str]) -> list[str]:
    prefixes = ("Bike hand-off:", "Loaner bike:", "Overnight bag:")
    return [step for step in steps if step.startswith(prefixes)]


def _travel_steps(steps: list[str]) -> list[str]:
    return [
        step
        for step in steps
        if step not in _plan_notes(steps) and not step.startswith("Stay home")
    ]


def _step_parts(step: str) -> dict[str, str]:
    phase, body = _split_phase(step)
    headline, detail = _split_outer_detail(body)
    route, cargo = detail, ""
    if "; " in detail:
        route, cargo = detail.split("; ", 1)
    return {
        "phase": phase,
        "action": headline,
        "route": route,
        "cargo": cargo,
    }


def _move_parts(move: str, route: Route | None = None) -> dict[str, str]:
    phase, body = _split_phase(move)
    headline, detail = _split_outer_detail(body)
    people, cargo = detail, ""
    if "; " in detail:
        people, cargo = detail.split("; ", 1)
    parsed = _raw_car_move(move)
    if parsed:
        owner = parsed["owner"]
        origin = _location_for_plan(parsed["origin_raw"], owner)
        destination = _location_for_plan(parsed["destination_raw"], owner)
        driver = parsed["driver"]
        if driver == owner:
            sentence = f"{driver} drives from {origin} to {destination}."
        else:
            sentence = (
                f"{driver} drives {_possessive(owner)} car from "
                f"{origin} to {destination}."
            )
        return {
            "phase": phase,
            "headline": sentence,
            "people": "",
            "cargo": "",
            "purpose": _plan_purpose(
                phase,
                owner,
                parsed["origin_raw"],
                parsed["destination_raw"],
                parsed["passengers"],
                _count_from_cargo(parsed["cargo"], "bike"),
                _count_from_cargo(parsed["cargo"], "bag"),
                route,
            ),
        }
    return {
        "phase": phase,
        "headline": headline,
        "people": people,
        "cargo": _cargo_summary(cargo),
        "purpose": "",
    }


def _glance_moves(moves: list[str], route: Route | None = None) -> list[dict[str, str]]:
    parsed_moves = [
        parsed for move in _chronological_moves(moves) if (parsed := _raw_car_move(move))
    ]
    rows = []
    parsed_by_move = {parsed["move"]: parsed for parsed in parsed_moves}
    for move in _chronological_moves(moves):
        parsed = parsed_by_move.get(move)
        if not parsed:
            rows.append(_move_parts(move, route))
            continue

        owner = parsed["owner"]
        origin = _location_for_plan(parsed["origin_raw"], owner)
        destination = _location_for_plan(parsed["destination_raw"], owner)
        driver = parsed["driver"]
        if driver == owner:
            sentence = f"{driver} drives from {origin} to {destination}."
        else:
            sentence = (
                f"{driver} drives {_possessive(owner)} car from "
                f"{origin} to {destination}."
            )
        rows.append(
            {
                "phase": parsed["phase"],
                "headline": sentence,
                "people": "",
                "cargo": "",
                "purpose": _plan_purpose(
                    parsed["phase"],
                    owner,
                    parsed["origin_raw"],
                    parsed["destination_raw"],
                    parsed["passengers"],
                    _count_from_cargo(parsed["cargo"], "bike"),
                    _count_from_cargo(parsed["cargo"], "bag"),
                    route,
                    _recovery_passengers(parsed, parsed_moves, route),
                ),
            }
        )
    return rows


def _chronological_moves(moves: list[str]) -> list[str]:
    return [
        move
        for _, move in sorted(
            enumerate(moves),
            key=lambda item: (PHASE_ORDER.get(_split_phase(item[1])[0], 999), item[0]),
        )
    ]


# --------------------------------------------------------------------------- #
# Structured data for the visual plan page (timeline + journey strips)
# --------------------------------------------------------------------------- #

# Phase headers collapse the nine transitions into five readable groups; the
# icon is a glyph id in the plan-page SVG sprite.
PHASE_GROUP_ICON = {
    "Night before": "moon",
    "Morning of the ride": "sun",
    "The ride": "bike",
    "Evening": "sunset",
    "Next morning": "sun",
}

# Time-anchored section headers so the timeline reads unambiguously in order.
PHASE_GROUP_LABEL = {
    "Night before": "The night before",
    "Morning of the ride": "Morning of the ride",
    "The ride": "The ride",
    "Evening": "The evening after the ride",
    "Next morning": "The next morning",
}


def _initials(name: str) -> str:
    parts = [p for p in re.split(r"\s+", name.strip()) if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def _city(text: str) -> str:
    """'home (Minneapolis, MN)' or 'Faribault, MN' -> 'Minneapolis' / 'Faribault'."""
    home = re.fullmatch(r"home \((.+)\)", text)
    inner = home.group(1) if home else text
    return inner.split(",")[0].strip()


def _loc_kind(raw: str, route: Route | None) -> str:
    if re.fullmatch(r"home \(.+\)", raw):
        return "home"
    if route and raw == route.start_name:
        return "start"
    if route and raw == route.finish_name:
        return "finish"
    return "other"


def _phase_group(phase: str) -> str:
    return phase.split(" — ", 1)[0] if phase else ""


def _glance_groups(moves: list[str], route: Route | None = None) -> list[dict[str, Any]]:
    """Chronological vehicle legs, grouped by phase, as structured rows."""
    parsed_moves = [
        parsed for move in _chronological_moves(moves) if (parsed := _raw_car_move(move))
    ]
    parsed_by_move = {parsed["move"]: parsed for parsed in parsed_moves}

    legs: list[dict[str, Any]] = []
    for move in _chronological_moves(moves):
        parsed = parsed_by_move.get(move)
        if not parsed:
            mp = _move_parts(move, route)
            legs.append(
                {
                    "phase": mp["phase"],
                    "group": _phase_group(mp["phase"]),
                    "driver": None,
                    "origin": None,
                    "dest": None,
                    "passengers": [],
                    "bikes": 0,
                    "bags": 0,
                    "purpose": mp.get("purpose", ""),
                    "detail": mp["headline"],
                }
            )
            continue

        owner = parsed["owner"]
        driver = parsed["driver"]
        bikes = _count_from_cargo(parsed["cargo"], "bike")
        bags = _count_from_cargo(parsed["cargo"], "bag")
        breakdown = _cargo_breakdown(parsed["cargo"])
        origin = _location_for_plan(parsed["origin_raw"], owner)
        destination = _location_for_plan(parsed["destination_raw"], owner)
        if driver == owner:
            detail = f"{driver} drives from {origin} to {destination}."
        else:
            detail = (
                f"{driver} drives {_possessive(owner)} car "
                f"from {origin} to {destination}."
            )
        legs.append(
            {
                "phase": parsed["phase"],
                "group": _phase_group(parsed["phase"]),
                "driver": {"name": driver, "initials": _initials(driver)},
                "origin": {
                    "kind": _loc_kind(parsed["origin_raw"], route),
                    "label": _city(parsed["origin_raw"]),
                },
                "dest": {
                    "kind": _loc_kind(parsed["destination_raw"], route),
                    "label": _city(parsed["destination_raw"]),
                },
                "passengers": [
                    {"name": name, "initials": _initials(name)}
                    for name in parsed["passengers"]
                ],
                "bikes": bikes,
                "bags": bags,
                "bike_owners": breakdown["bikes"],
                "bag_owners": breakdown["bags"],
                "purpose": _plan_purpose(
                    parsed["phase"],
                    owner,
                    parsed["origin_raw"],
                    parsed["destination_raw"],
                    parsed["passengers"],
                    bikes,
                    bags,
                    route,
                    _recovery_passengers(parsed, parsed_moves, route),
                ),
                "detail": detail,
            }
        )

    groups: list[dict[str, Any]] = []
    for leg in legs:
        group = leg["group"]
        if not groups or groups[-1]["group"] != group:
            groups.append(
                {
                    "group": group,
                    "label": PHASE_GROUP_LABEL.get(group, group),
                    "icon": PHASE_GROUP_ICON.get(group, "route"),
                    "legs": [],
                }
            )
        groups[-1]["legs"].append(leg)
    return groups


def _journey(steps: list[str], route: Route | None = None) -> dict[str, list]:
    """One person's day as a chain of location stops joined by travel legs.

    Returns {"stops": [{kind,label}, ...], "legs": [{mode, label}, ...]} with
    len(stops) == len(legs) + 1, so the template can interleave stop/leg/stop.
    """
    stops: list[dict[str, str]] = []
    legs: list[dict[str, str]] = []
    for step in _travel_steps(steps):
        parts = _step_parts(step)
        route_str = parts["route"]
        if "→" not in route_str:
            continue
        a_raw, b_raw = (s.strip() for s in route_str.split("→", 1))
        action = parts["action"]
        if action.startswith(("Ride the route", "Bike back")):
            mode, label = "bike", action
        elif action.startswith("Drive"):
            mode, label = "drive", action
        else:  # "Ride with <driver>"
            mode, label = "ride", action

        start = {"kind": _loc_kind(a_raw, route), "label": _city(a_raw)}
        end = {"kind": _loc_kind(b_raw, route), "label": _city(b_raw)}
        if not stops:
            stops.append(start)
        legs.append({"mode": mode, "label": label})
        stops.append(end)
    return {"stops": stops, "legs": legs}


def _move_detail(move: str) -> dict[str, Any]:
    """Break a raw car-move string into phase, headline, and its detail lines.

    'Morning …: Sam's car home (…) → Faribault (driven by Sam, with …; 5 bikes: …; 1 bag: …)'
    -> {phase, headline: "Sam's car …", lines: ["driven by …", "5 bikes: …", "1 bag: …"]}
    """
    phase, body = _split_phase(move)
    headline, detail = _split_outer_detail(body)
    lines = [seg.strip() for seg in detail.split("; ") if seg.strip()] if detail else []
    return {"phase": phase, "headline": headline, "lines": lines}


templates.env.filters["return_short"] = _return_short
templates.env.filters["initials"] = _initials
templates.env.filters["glance_groups"] = _glance_groups
templates.env.filters["journey"] = _journey
templates.env.filters["move_detail"] = _move_detail
templates.env.filters["burden_summary"] = _burden_summary
templates.env.filters["note_summary"] = _note_summary
templates.env.filters["plan_notes"] = _plan_notes
templates.env.filters["travel_steps"] = _travel_steps
templates.env.filters["step_parts"] = _step_parts
templates.env.filters["move_parts"] = _move_parts
templates.env.filters["chronological_moves"] = _chronological_moves
templates.env.filters["glance_moves"] = _glance_moves


@app.on_event("startup")
def _startup() -> None:
    init_db()


def _checkbox(value: str | None) -> bool:
    return value is not None


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    with get_session() as s:
        events = s.exec(select(Event)).all()
    return templates.TemplateResponse(request, "index.html", {"events": events})


@app.post("/events")
def create_event(name: str = Form(...), route_key: str = Form(...),
                 has_sag: str | None = Form(None)):
    with get_session() as s:
        ev = Event(name=name, route_key=route_key, has_sag=_checkbox(has_sag))
        s.add(ev)
        s.commit()
        s.refresh(ev)
    return RedirectResponse(f"/events/{ev.id}", status_code=303)


@app.get("/events/{event_id}", response_class=HTMLResponse)
def event_page(request: Request, event_id: int, error: str | None = None):
    with get_session() as s:
        ev = s.get(Event, event_id)
        if ev is None:
            return RedirectResponse("/", status_code=303)
        people = s.exec(
            select(Participant).where(Participant.event_id == event_id)
        ).all()
    route = get_route(ev.route_key)
    # readable household-mates per participant (others sharing the same household)
    household_mates: dict[int, list[str]] = {}
    for p in people:
        key = p.household or str(p.id)
        mates = [
            q.name for q in people
            if q.id != p.id and (q.household or str(q.id)) == key
        ]
        household_mates[p.id] = mates
    return templates.TemplateResponse(
        request,
        "event.html",
        {
            "event": ev,
            "route": route,
            "people": people,
            "household_mates": household_mates,
            "error": error,
        },
    )


@app.post("/events/{event_id}/participants")
def add_participant(
    event_id: int,
    name: str = Form(...),
    email: str = Form(""),
    home_zip: str = Form(...),
    household: str = Form(""),  # id of an existing participant to share a household with
    is_rider: str | None = Form(None),
    num_bikes: int = Form(1),
    loaner_for: list[str] = Form(default=[]),  # ids of borrowers (multi-select)
    has_overnight_bag: str | None = Form(None),
    car_combos: str = Form(""),
    willing_drop_car: str | None = Form(None),
    willing_drop_bikes_at_start: str | None = Form(None),
    willing_drive_dropper_home: str | None = Form(None),
    can_drive_morning: str | None = Form(None),
    is_sag_driver: str | None = Form(None),
    share_household_car: str | None = Form(None),
    sag_extra_miles: int = Form(20),
    pref_tonight: str = Form("preferred"),
    pref_bikeback: str = Form("acceptable"),
    pref_ridehome: str = Form("acceptable"),
):
    # validate the ZIP up front so we fail clearly rather than at solve time
    try:
        geo.latlon(home_zip)
    except geo.UnknownZip:
        return RedirectResponse(
            f"/events/{event_id}?error=Unknown+ZIP+code+{geo.normalize_zip(home_zip)}",
            status_code=303,
        )

    car_flags = [
        willing_drop_car, willing_drop_bikes_at_start,
        willing_drive_dropper_home, can_drive_morning, is_sag_driver,
    ]
    # a car is implied by entering capacity or checking any car-requiring box
    has_car = bool(car_combos.strip()) or any(f is not None for f in car_flags)

    with get_session() as s:
        # "household" is the id of an existing participant to share with; resolve
        # to their household identifier so chains stay consistent
        household_value = ""
        if household:
            try:
                target = s.get(Participant, int(household))
            except (ValueError, TypeError):
                target = None
            if target is not None and target.event_id == event_id:
                household_value = target.household or str(target.id)
        loaner_ids = ",".join(b for b in loaner_for if b)
        riding = _checkbox(is_rider)

        p = Participant(
            event_id=event_id,
            name=name,
            email=email,
            home_zip=geo.normalize_zip(home_zip),
            household=household_value,
            is_rider=riding,
            num_bikes=num_bikes if riding else 0,  # "bikes I'll ride" only applies to riders
            loaner_for=loaner_ids,
            bag_count=1 if _checkbox(has_overnight_bag) else 0,
            has_car=has_car,
            car_combos=car_combos,
            willing_drop_car=_checkbox(willing_drop_car),
            willing_drop_bikes_at_start=_checkbox(willing_drop_bikes_at_start),
            willing_drive_dropper_home=_checkbox(willing_drive_dropper_home),
            can_drive_morning=_checkbox(can_drive_morning),
            is_sag_driver=_checkbox(is_sag_driver),
            share_household_car=_checkbox(share_household_car),
            sag_extra_miles=sag_extra_miles,
            pref_tonight=pref_tonight,
            pref_bikeback=pref_bikeback,
            pref_ridehome=pref_ridehome,
        )
        s.add(p)
        s.commit()
    return RedirectResponse(f"/events/{event_id}", status_code=303)


@app.post("/events/{event_id}/participants/{pid}/delete")
def delete_participant(event_id: int, pid: int):
    with get_session() as s:
        p = s.get(Participant, pid)
        if p is not None:
            s.delete(p)
            s.commit()
    return RedirectResponse(f"/events/{event_id}", status_code=303)


def _fixture_dict(ev: Event, people: list[Participant]) -> dict[str, Any]:
    """Serialize an event + roster into the ``fixtures/*.json`` schema."""
    id_to_name = {str(p.id): p.name for p in people}
    group_of = {p.id: (p.household or str(p.id)) for p in people}
    group_size: dict[str, int] = {}
    for g in group_of.values():
        group_size[g] = group_size.get(g, 0) + 1

    participants = []
    for p in people:
        entry: dict[str, Any] = {"name": p.name, "home_zip": p.home_zip}
        if p.email:
            entry["email"] = p.email
        group = group_of[p.id]
        if group_size[group] > 1 and group in id_to_name:
            # a shared label all household members agree on (round-trips by name)
            entry["household"] = id_to_name[group]
        entry["is_rider"] = p.is_rider
        if p.is_rider:
            entry["num_bikes"] = p.num_bikes
        loaners = [id_to_name[i] for i in p.loaner_for.split(",") if i in id_to_name]
        if loaners:
            entry["loaner_for"] = loaners
        if p.bag_count:
            entry["bag_count"] = p.bag_count
        if p.car_combos:
            entry["car_combos"] = p.car_combos
        elif p.has_car:
            entry["has_car"] = True
        for flag in (
            "willing_drop_car", "willing_drop_bikes_at_start",
            "willing_drive_dropper_home", "can_drive_morning",
            "is_sag_driver", "share_household_car",
        ):
            if getattr(p, flag):
                entry[flag] = True
        if p.is_sag_driver and p.sag_extra_miles != 20:
            entry["sag_extra_miles"] = p.sag_extra_miles
        entry["return"] = {
            "tonight": p.pref_tonight,
            "bikeback": p.pref_bikeback,
            "ridehome": p.pref_ridehome,
        }
        participants.append(entry)

    return {
        "event": {"name": ev.name, "route_key": ev.route_key, "has_sag": ev.has_sag},
        "participants": participants,
    }


@app.get("/events/{event_id}/export")
def export_fixture(event_id: int):
    with get_session() as s:
        ev = s.get(Event, event_id)
        if ev is None:
            return RedirectResponse("/", status_code=303)
        people = s.exec(
            select(Participant).where(Participant.event_id == event_id)
        ).all()
    body = json.dumps(_fixture_dict(ev, people), indent=2)
    slug = re.sub(r"[^a-z0-9]+", "_", ev.name.lower()).strip("_") or "roster"
    return Response(
        content=body,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{slug}.json"'},
    )


@app.get("/events/{event_id}/plan", response_class=HTMLResponse)
def plan_page(request: Request, event_id: int):
    with get_session() as s:
        ev = s.get(Event, event_id)
        if ev is None:
            return RedirectResponse("/", status_code=303)
        people = s.exec(
            select(Participant).where(Participant.event_id == event_id)
        ).all()

    route = get_route(ev.route_key)
    if not people:
        return templates.TemplateResponse(
            request,
            "plan.html",
            {"event": ev, "route": route,
             "solution": None, "people": [], "empty": True},
        )

    problem = Problem(
        route=route,
        people=[to_person(p) for p in people],
        has_sag=ev.has_sag,
    )
    solution = solve(problem)
    by_id = {str(p.id): p for p in people}
    sag_driver = (
        next((p.name for p in people if p.is_sag_driver), None)
        if ev.has_sag
        else None
    )
    # people who didn't get a preferred return (for the "unmet preferences" stat)
    unmet_lines: list[str] = []
    for p in people:
        sid = str(p.id)
        burden = solution.burdens.get(sid) if solution else None
        if not (burden and burden.get("deviation")):
            continue
        prefs = {
            ReturnOption.DRIVE_HOME_TONIGHT: p.pref_tonight,
            ReturnOption.HOTEL_BIKE_BACK: p.pref_bikeback,
            ReturnOption.HOTEL_RIDE_HOME: p.pref_ridehome,
        }
        wanted = [
            RETURN_SHORT_LABELS.get(opt, str(opt))
            for opt, value in prefs.items() if value == "preferred"
        ]
        wanted_label = ", ".join(wanted) if wanted else "no preference"
        actual = solution.return_outcome.get(sid)
        got_label = RETURN_SHORT_LABELS.get(actual, str(actual)) if actual else "—"
        unmet_lines.append(f"{p.name} — wanted {wanted_label}, got {got_label}")

    return templates.TemplateResponse(
        request,
        "plan.html",
        {
            "event": ev,
            "route": route,
            "solution": solution,
            "people": people,
            "by_id": by_id,
            "sag_driver": sag_driver,
            "unmet_count": len(unmet_lines),
            "unmet_tip": "\n".join(unmet_lines),
            "empty": False,
        },
    )
