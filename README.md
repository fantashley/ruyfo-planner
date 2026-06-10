# RUYFO Logistics Planner

RUYFO is a twice-a-year point-to-point bike event. Because it's point-to-point,
getting every **person + their bike + their car** to the start and then all the
way home again is a puzzle. This is a small web app that collects everyone's
constraints and computes the **lowest-total-driving plan** that gets everyone,
their bikes, and their cars home — honoring each person's stated preferences.

Two routes are built in:

- **Faribault → Mankato** (`55021` → `56001`)
- **Wayzata → Hutchinson** (`55391` → `55350`)

## How it works

The hard part is **continuity across time**: a car dropped at the finish the
night before isn't available to shuttle people to the start the next morning; a
car driven to the start in the morning is stranded there until its owner bikes
back or gets a ride back to retrieve it; bikes must be at the start at ride time
and home by the end.

The planner models this as a small time-expanded flow over five phases (night
before, morning, the ride, evening, next morning) and solves it to optimality
with [OR-Tools CP-SAT](https://developers.google.com/optimization/cp). It
handles:

- night-before **car drops** at the finish + the ride home for the dropper
  (everyone sleeps at home the night before — nobody is left at the start/finish),
- the night before is a **three-leg chain**, so one person can drop bikes at the
  start *and* drive their car on to the finish before heading home,
- night-before **bike drops** at the start,
- **bike hand-offs** — when your bike rides in someone else's car, the plan tells
  you to drop it at their place the night before (or have them pick it up),
- **loaner / spare bikes** — someone brings a spare for a rider who has none; the
  borrower rides it and it returns to the lender's home,
- **overnight bags** — get a bag to the hotel without carrying it on the bike;
  with a SAG wagon it's handed off at the start and waiting at the finish, and
  without one it rides a finish-bound car,
- any number of **morning car runs** to the start,
- a car driven to the start being **retrieved** by biking back *or* being driven
  back (that night or the next day),
- an optional **SAG wagon** that ferries to the start, sweeps the route for
  riders who can't finish, and drives people/bikes home,
- **households** that travel together and share a car,
- soft **return preferences** — each person marks every option (drive home
  tonight / hotel + bike back / hotel + ride home) as *preferred*, *acceptable*,
  or *unwilling*, and the planner only falls back to an acceptable option when it
  has to.

Distances are straight-line between ZIP-code centroids (offline, via the
`zipcodes` package — no API keys or network needed).

## Setup

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"   # or: pip install -r the deps in pyproject
```

## Run

```bash
.venv/bin/python -m scripts.seed_demo          # optional: load a demo roster
.venv/bin/python -m uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000, create an event, add participants, and click
**Generate plan**. The database is a local SQLite file (`ruyfo.db`; override with
the `RUYFO_DB` env var).

## Tests

```bash
.venv/bin/python -m pytest
```

`tests/test_geo.py` covers the distance helpers; `tests/test_solver.py` covers
the optimizer with hand-built scenarios (single-rider bike-back, drop-car +
supporter, SAG wagon, households, and an over-constrained infeasible case).

## Layout

| Path | What |
|------|------|
| `app/solver.py` | The CP-SAT model + plan extraction (the core) |
| `app/geo.py` | Offline ZIP → lat/lon + haversine distances |
| `app/events.py` | The two routes |
| `app/models.py` | SQLite tables + conversion to solver inputs |
| `app/main.py` | FastAPI routes |
| `app/templates/`, `app/static/` | The web UI |

## Tuning

`Problem` in `app/solver.py` exposes two knobs (both in "miles" units):

- `detour_factor` — how much an out-of-the-way passenger/bike pickup costs.
- `pref_penalty_miles` — how many extra driving miles you'd trade to keep one
  person on their *preferred* return option instead of a merely *acceptable* one.

## Known approximations

- Multi-stop pickups are charged as an approximate per-extra-home detour rather
  than a full routed path — fine at this scale, and it keeps the solve exact.
- Distances are straight-line, so they under-estimate real road miles fairly
  uniformly; good enough for deciding *who drives whom*. Swap `app/geo.py` for a
  routing API if you want true drive times.

## License

[MIT](LICENSE).
