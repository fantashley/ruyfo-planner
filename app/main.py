"""RUYFO logistics planner — FastAPI web app."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlmodel import select

from . import geo
from .db import get_session, init_db
from .events import ROUTES, get_route
from .models import Event, Participant, to_person
from .solver import Problem, ReturnOption, solve

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
    bag_count: int = Form(0),
    car_combos: str = Form(""),
    willing_drop_car: str | None = Form(None),
    willing_drop_bikes_at_start: str | None = Form(None),
    willing_drive_dropper_home: str | None = Form(None),
    can_drive_morning: str | None = Form(None),
    is_sag_driver: str | None = Form(None),
    share_household_car: str | None = Form(None),
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

        p = Participant(
            event_id=event_id,
            name=name,
            email=email,
            home_zip=geo.normalize_zip(home_zip),
            household=household_value,
            is_rider=_checkbox(is_rider),
            num_bikes=num_bikes,
            loaner_for=loaner_ids,
            bag_count=bag_count,
            has_car=has_car,
            car_combos=car_combos,
            willing_drop_car=_checkbox(willing_drop_car),
            willing_drop_bikes_at_start=_checkbox(willing_drop_bikes_at_start),
            willing_drive_dropper_home=_checkbox(willing_drive_dropper_home),
            can_drive_morning=_checkbox(can_drive_morning),
            is_sag_driver=_checkbox(is_sag_driver),
            share_household_car=_checkbox(share_household_car),
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
    return templates.TemplateResponse(
        request,
        "plan.html",
        {
            "event": ev,
            "route": route,
            "solution": solution,
            "people": people,
            "by_id": by_id,
            "empty": False,
        },
    )
