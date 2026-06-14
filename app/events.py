"""The RUYFO routes.

RUYFO runs twice a year and uses one of two point-to-point routes. Each route
is defined by its start ZIP (where the ride begins) and finish ZIP (where it
ends and where cars get dropped the night before).
"""

from __future__ import annotations

from dataclasses import dataclass

from . import geo


@dataclass(frozen=True)
class Route:
    key: str
    name: str
    start_zip: str
    finish_zip: str

    @property
    def start_name(self) -> str:
        return geo.place_name(self.start_zip)

    @property
    def finish_name(self) -> str:
        return geo.place_name(self.finish_zip)

    @property
    def distance_miles(self) -> float:
        return geo.zip_distance_miles(self.start_zip, self.finish_zip)


ROUTES: dict[str, Route] = {
    "faribault_mankato": Route(
        key="faribault_mankato",
        name="Sakatah Trail",
        start_zip="55021",
        finish_zip="56001",
    ),
    "wayzata_hutchinson": Route(
        key="wayzata_hutchinson",
        name="Hutchinson Route",
        start_zip="55391",
        finish_zip="55350",
    ),
}


def get_route(key: str) -> Route:
    try:
        return ROUTES[key]
    except KeyError as exc:
        raise KeyError(f"Unknown route {key!r}; options: {sorted(ROUTES)}") from exc
