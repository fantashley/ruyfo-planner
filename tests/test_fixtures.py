from sqlmodel import SQLModel, Session, create_engine, select
from sqlalchemy import text

from app.db import _add_missing_sqlite_columns, _backfill_event_tokens
from app import fixtures
from app.models import Event, Participant
from app.solver import solve


def test_example_fixture_solves_with_loaner_resolved():
    fx = fixtures.load("example")
    problem = fixtures.build_problem(fx)
    sol = solve(problem)
    assert sol.status in ("optimal", "feasible")
    # Cory borrows Avery's loaner — the by-name reference should resolve so Cory
    # actually rides it (rather than a phantom bike).
    cory = " ".join(sol.itineraries["Cory Diaz"]).lower()
    assert "loaner" in cory and "avery" in cory


def test_build_problem_defaults_and_return_prefs():
    fx = {
        "event": {"name": "T", "route_key": "faribault_mankato", "has_sag": False},
        "participants": [
            {"name": "Pat", "home_zip": "55021"},  # minimal: everything defaults
            {"name": "Sam", "home_zip": "55021", "return": {"tonight": "unwilling"}},
        ],
    }
    problem = fixtures.build_problem(fx)
    pat, sam = problem.people
    assert pat.is_rider and pat.num_bikes == 1 and not pat.has_car
    # omitted return keys fall back to defaults; specified ones win
    from app.solver import Pref, ReturnOption
    assert sam.return_prefs[ReturnOption.DRIVE_HOME_TONIGHT] == Pref.UNWILLING
    assert sam.return_prefs[ReturnOption.HOTEL_BIKE_BACK] == Pref.ACCEPTABLE


def test_tuning_block_is_forwarded_to_problem():
    fx = {
        "event": {"name": "T", "route_key": "faribault_mankato"},
        "tuning": {"fairness_weight": 1.5, "chore_leg_miles": 5,
                   "pref_penalty_miles": 40},
        "participants": [{"name": "Pat", "home_zip": "55021"}],
    }
    problem = fixtures.build_problem(fx)
    assert problem.fairness_weight == 1.5
    assert problem.chore_leg_miles == 5.0
    assert problem.pref_penalty_miles == 40.0
    assert problem.detour_factor == 1.0  # untouched default


def test_seed_event_persists_and_resolves_loaner():
    engine = create_engine("sqlite://")  # in-memory
    SQLModel.metadata.create_all(engine)
    fx = fixtures.load("example")
    with Session(engine) as s:
        ev = fixtures.seed_event(s, fx)
        parts = s.exec(select(Participant).where(Participant.event_id == ev.id)).all()
        assert len(parts) == len(fx["participants"])
        avery = next(p for p in parts if p.name == "Avery Brooks")
        cory = next(p for p in parts if p.name == "Cory Diaz")
        assert avery.loaner_for == str(cory.id)  # name resolved to participant id


def test_seed_event_replaces_same_named_event():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    fx = fixtures.load("example")
    with Session(engine) as s:
        fixtures.seed_event(s, fx)
        fixtures.seed_event(s, fx)  # second load replaces the first
        from app.models import Event
        assert len(s.exec(select(Event)).all()) == 1


def test_sqlite_schema_migration_adds_new_participant_columns():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE participant DROP COLUMN share_household_car"))

    _add_missing_sqlite_columns(engine)

    with Session(engine) as s:
        p = Participant(event_id=1, name="Pat", home_zip="55021")
        s.add(p)
        s.commit()
        s.refresh(p)
        assert p.share_household_car is False


def test_sqlite_schema_migration_adds_event_access_tokens():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE event ("
                "id INTEGER PRIMARY KEY, "
                "name VARCHAR NOT NULL, "
                "route_key VARCHAR NOT NULL, "
                "has_sag BOOLEAN NOT NULL DEFAULT 0)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO event (name, route_key, has_sag) "
                "VALUES ('Test RUYFO', 'faribault_mankato', 0)"
            )
        )

    _add_missing_sqlite_columns(engine)
    _backfill_event_tokens(engine)

    with Session(engine) as s:
        ev = s.exec(select(Event)).one()
        tokens = {ev.organizer_token, ev.participant_token, ev.readonly_token}
        assert len(tokens) == 3
        assert all(len(token) >= 24 for token in tokens)
