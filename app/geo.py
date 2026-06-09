"""Offline geocoding + distance helpers.

Uses the `zipcodes` package, which ships US ZIP-code centroids inside the wheel,
so everything here works with no network access and no API keys.
"""

from __future__ import annotations

import math
from functools import lru_cache

import zipcodes

EARTH_RADIUS_MI = 3958.7613  # mean Earth radius in miles


class UnknownZip(ValueError):
    """Raised when a ZIP code can't be resolved to a location."""


def normalize_zip(value: str | int) -> str:
    """Coerce a ZIP to a 5-digit string, preserving leading zeros."""
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    if len(digits) < 5:
        digits = digits.zfill(5)
    return digits[:5]


@lru_cache(maxsize=None)
def latlon(zip_code: str | int) -> tuple[float, float]:
    """Return (latitude, longitude) for a ZIP code."""
    z = normalize_zip(zip_code)
    matches = zipcodes.matching(z)
    if not matches:
        raise UnknownZip(f"ZIP code {z!r} not found")
    rec = matches[0]
    return (float(rec["lat"]), float(rec["long"]))


@lru_cache(maxsize=None)
def place_name(zip_code: str | int) -> str:
    """Human-readable 'City, ST' for a ZIP, falling back to the ZIP itself."""
    z = normalize_zip(zip_code)
    matches = zipcodes.matching(z)
    if not matches:
        return z
    rec = matches[0]
    return f"{rec['city']}, {rec['state']}"


def haversine_miles(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle distance in miles between two (lat, lon) points."""
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_MI * math.asin(math.sqrt(h))


def zip_distance_miles(zip_a: str | int, zip_b: str | int) -> float:
    """Straight-line distance in miles between two ZIP codes."""
    return haversine_miles(latlon(zip_a), latlon(zip_b))
