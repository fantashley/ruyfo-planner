"""Populate a database with a demo event + roster so you can click around.

    python -m scripts.seed_demo

Uses the same RUYFO_DB env var as the app (defaults to ruyfo.db).
"""

from __future__ import annotations

from sqlmodel import select

from app.db import get_session, init_db
from app.models import Event, Participant

DEMO_EVENT = "Demo — Fall RUYFO"


def run() -> None:
    init_db()
    with get_session() as s:
        existing = s.exec(select(Event).where(Event.name == DEMO_EVENT)).first()
        if existing:
            print(f"Demo event already present (id={existing.id}).")
            return
        ev = Event(name=DEMO_EVENT, route_key="faribault_mankato", has_sag=True)
        s.add(ev)
        s.commit()
        s.refresh(ev)

        roster = [
            # name, zip, household, rider, bikes, car, combos, flags..., prefs
            dict(name="Ann Reyes", home_zip="55021", has_car=True, car_combos="5x3",
                 willing_drop_car=True, willing_drive_dropper_home=True,
                 can_drive_morning=True,
                 pref_tonight="preferred", pref_bikeback="acceptable",
                 pref_ridehome="acceptable"),
            dict(name="Bob Lind", home_zip="55060", has_car=True, car_combos="5x2",
                 pref_tonight="preferred", pref_bikeback="unwilling",
                 pref_ridehome="acceptable"),
            # Cyd owns no bike — she'll ride Ann's loaner (wired up below)
            dict(name="Cyd Okafor", home_zip="55021", num_bikes=0,
                 pref_tonight="acceptable", pref_bikeback="preferred",
                 pref_ridehome="acceptable"),
            dict(name="Deb Eklund", home_zip="56007", household="Eklund",
                 has_car=True, car_combos="4x4",
                 pref_tonight="acceptable", pref_bikeback="acceptable",
                 pref_ridehome="preferred"),
            # Eli stays overnight and sends an overnight bag to the hotel
            dict(name="Eli Eklund", home_zip="56007", household="Eklund", bag_count=1,
                 pref_tonight="unwilling", pref_bikeback="acceptable",
                 pref_ridehome="preferred"),
            dict(name="Sage Moore", home_zip="55021", is_rider=False, num_bikes=0,
                 has_car=True, car_combos="8x8", is_sag_driver=True,
                 can_drive_morning=True, willing_drive_dropper_home=True),
        ]
        rows = [Participant(event_id=ev.id, **r) for r in roster]
        s.add_all(rows)
        s.commit()
        for row in rows:
            s.refresh(row)
        # wire the loaner: Ann brings a spare bike for Cyd
        ann = next(r for r in rows if r.name.startswith("Ann"))
        cyd = next(r for r in rows if r.name.startswith("Cyd"))
        ann.loaner_for = str(cyd.id)
        s.add(ann)
        s.commit()
        print(f"Seeded '{DEMO_EVENT}' (id={ev.id}) with {len(roster)} participants.")


if __name__ == "__main__":
    run()
