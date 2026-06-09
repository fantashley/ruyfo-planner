from app import geo
from app.events import ROUTES


def test_normalize_zip_preserves_leading_zeros():
    assert geo.normalize_zip(1001) == "01001"
    assert geo.normalize_zip("55021") == "55021"
    assert geo.normalize_zip("55021-1234") == "55021"


def test_known_zip_lookup():
    lat, lon = geo.latlon("55021")  # Faribault, MN
    assert 44.0 < lat < 44.6
    assert -93.6 < lon < -93.0
    assert geo.place_name("56001") == "Mankato, MN"


def test_unknown_zip_raises():
    import pytest

    with pytest.raises(geo.UnknownZip):
        geo.latlon("00000")


def test_faribault_mankato_distance_is_realistic():
    # Real road distance is ~50 mi; straight-line is a bit less.
    d = geo.zip_distance_miles("55021", "56001")
    assert 35 < d < 50


def test_route_helpers():
    r = ROUTES["faribault_mankato"]
    assert r.start_name == "Faribault, MN"
    assert r.finish_name == "Mankato, MN"
    assert r.distance_miles == geo.zip_distance_miles("55021", "56001")


def test_symmetric_distance():
    assert geo.zip_distance_miles("55021", "56001") == geo.zip_distance_miles(
        "56001", "55021"
    )
