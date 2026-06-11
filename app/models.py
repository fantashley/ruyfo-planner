"""Database models + conversion to the solver's input types."""

from __future__ import annotations

from typing import Optional

from sqlmodel import Field, SQLModel

from .solver import CarCombo, Person, Pref, ReturnOption


class Event(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    route_key: str
    has_sag: bool = False


class Participant(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    event_id: int = Field(foreign_key="event.id", index=True)

    name: str
    email: str = ""
    home_zip: str
    household: str = ""  # the participant id whose household this person shares ("" = own)

    is_rider: bool = True
    num_bikes: int = 1
    loaner_for: str = ""  # comma-separated participant ids this person lends a bike to
    bag_count: int = 0  # overnight bags to get to the hotel

    has_car: bool = False
    car_combos: str = ""  # e.g. "5x2, 2x4" => 5 people/2 bikes OR 2 people/4 bikes

    willing_drop_car: bool = False
    willing_drop_bikes_at_start: bool = False
    willing_drive_dropper_home: bool = False
    can_drive_morning: bool = False
    is_sag_driver: bool = False
    share_household_car: bool = False

    pref_tonight: str = Pref.PREFERRED.value
    pref_bikeback: str = Pref.ACCEPTABLE.value
    pref_ridehome: str = Pref.ACCEPTABLE.value


def parse_combos(text: str) -> list[CarCombo]:
    """Parse "5x2, 2x4" into CarCombo(people, bikes) entries."""
    combos: list[CarCombo] = []
    for chunk in text.replace(";", ",").split(","):
        chunk = chunk.strip().lower()
        if not chunk:
            continue
        for sep in ("x", "p/", "/", "-"):
            if sep in chunk:
                left, right = chunk.split(sep, 1)
                break
        else:
            continue
        try:
            people = int("".join(c for c in left if c.isdigit()))
            bikes = int("".join(c for c in right if c.isdigit()))
        except ValueError:
            continue
        combos.append(CarCombo(people=people, bikes=bikes))
    return combos


def to_person(p: Participant) -> Person:
    return Person(
        id=str(p.id),
        name=p.name,
        home_zip=p.home_zip,
        household=p.household or str(p.id),
        is_rider=p.is_rider,
        num_bikes=p.num_bikes,
        loaner_for=[x.strip() for x in p.loaner_for.split(",") if x.strip()],
        bag_count=p.bag_count,
        has_car=p.has_car,
        car_combos=parse_combos(p.car_combos),  # Person infers has_car from these
        willing_drop_car=p.willing_drop_car,
        willing_drop_bikes_at_start=p.willing_drop_bikes_at_start,
        willing_drive_dropper_home=p.willing_drive_dropper_home,
        can_drive_morning=p.can_drive_morning,
        is_sag_driver=p.is_sag_driver,
        share_household_car=p.share_household_car,
        return_prefs={
            ReturnOption.DRIVE_HOME_TONIGHT: Pref(p.pref_tonight),
            ReturnOption.HOTEL_BIKE_BACK: Pref(p.pref_bikeback),
            ReturnOption.HOTEL_RIDE_HOME: Pref(p.pref_ridehome),
        },
    )
