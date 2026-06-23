# RUYFO Logistics Planner

RUYFO is a twice-a-year point-to-point bike event. Because it's point-to-point,
getting every **person + their bike + their car** to the start and then all the
way home again is a puzzle. This is a small web app that collects everyone's
constraints and computes the **lowest-total-driving plan** that gets everyone,
their bikes, and their cars home — honoring each person's stated preferences.

Two routes are built in:

- **Sakatah Trail** — Faribault → Mankato (`55021` → `56001`)
- **Hutchinson Route** — Wayzata → Hutchinson (`55391` → `55350`)

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

Or, with Nix flakes:

```bash
nix develop
python -m pytest
```

## Run

```bash
.venv/bin/python -m scripts.seed_demo          # optional: load a demo roster
.venv/bin/python -m uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000, create an event, add participants, and click
**Generate plan**. The database is a local SQLite file (`ruyfo.db`; override with
the `RUYFO_DB` env var).

There are no accounts. Each event is reached through a secret **organizer link**
(`/e/<token>`) handed out when you create it — that link is how you get back in
to manage the roster, so keep it safe. You can also seed an event from a JSON
roster with the **Import data** button and pull the current roster back out with
**Export data**.

### Email recovery (optional)

Creating an event sends **no email** — the organizer link is shown on the next
page (save it). To enable email recovery, open your event and add a recovery
address under **Recovery email**: the app sends that address a single, content-
free confirmation link (no event name, no organizer link), and only once you
follow it does the address go on file. The home page's **Lost your link?** form
then re-sends the links for every event under a *confirmed* address.

This double opt-in is deliberate: the only address the app ever mails is one
someone proved they control, so the open forms can't be used to spam arbitrary
people. Email is off until SMTP is configured via the environment (defaults
target Fastmail); without it the app and tests run fine and the recovery UI
hides itself. The knobs:

| Var | Default | What |
|-----|---------|------|
| `RUYFO_SMTP_HOST` | `smtp.fastmail.com` | SMTP server |
| `RUYFO_SMTP_PORT` | `465` | SMTP port (implicit TLS) |
| `RUYFO_SMTP_USER` | — | SMTP username |
| `RUYFO_SMTP_PASSWORD` | — | SMTP password (or use the `_FILE` form) |
| `RUYFO_SMTP_PASSWORD_FILE` | — | path to read the password from (for secret managers) |
| `RUYFO_EMAIL_FROM` | falls back to `RUYFO_SMTP_USER` | From address |
| `RUYFO_EMAIL_DAILY_CAP` | `100` | Max emails sent in any rolling 24h window (anti-relay backstop) |
| `RUYFO_EMAIL_RECIPIENT_DAILY_CAP` | `5` | Max emails to a single address in a rolling 24h window |

The two caps are a backstop. The double opt-in above means the app only mails
confirmed addresses (plus the one confirmation message per attach), so it can't
be turned into a spam relay — but the caps still bound total volume if a bot
ever drives the confirmation path. They live at the single `app/mailer.send`
chokepoint and are backed by a small `emailsend` audit table.

### Locking the app to a fronting CDN (optional)

If the app sits behind a CDN/WAF that handles the bot challenge and rate
limiting (see [`terraform/fastly/`](terraform/fastly/) for a Fastly setup), set
a shared secret so the app rejects (HTTP 403) any request that reached the
origin directly — the way to keep bots out when the host serves other sites and
can't be IP-firewalled. Configure the CDN to stamp it on every origin request
as the `X-Origin-Secret` header, and tell the app the same value:

| Var | Default | What |
|-----|---------|------|
| `RUYFO_ORIGIN_SECRET` | — | Shared secret; requests without a matching `X-Origin-Secret` header get 403. Empty disables the check |
| `RUYFO_ORIGIN_SECRET_FILE` | — | Path to read the secret from instead (for secret managers) |

### CAPTCHA on the public forms (optional)

The event-creation and "Lost your link?" forms are unauthenticated, so they
carry a Google reCAPTCHA v2 challenge when configured. With both keys set, the
widget renders on those forms and the server rejects a submission whose token
fails verification (it fails *closed* — a missing token or an unreachable Google
is rejected). Unset ⇒ the gate is disabled and the widget is hidden, so dev and
tests run without it.

| Var | Default | What |
|-----|---------|------|
| `RUYFO_RECAPTCHA_SITE_KEY` | — | Public reCAPTCHA v2 site key (rendered in the form) |
| `RUYFO_RECAPTCHA_SECRET` | — | Private reCAPTCHA secret (or use the `_FILE` form) |
| `RUYFO_RECAPTCHA_SECRET_FILE` | — | Path to read the secret from instead (for secret managers) |

## Fixtures (fixed rosters for testing)

Keep a real roster as JSON in `fixtures/` and re-check plans as the model changes
— see [`fixtures/README.md`](fixtures/README.md) for the schema.

```bash
.venv/bin/python -m scripts.fixture list             # list fixtures
.venv/bin/python -m scripts.fixture plan example     # solve + print the plan (no DB)
.venv/bin/python -m scripts.fixture load example     # seed it into the app DB for the web UI
```

`plan` is the quick feedback loop (no browser); `load` is idempotent (re-loading
replaces the same-named event). Real rosters are git-ignored by default since they
hold personal data — only the fictional `example.json` is tracked.

## Tests

```bash
.venv/bin/python -m pytest
```

`tests/test_geo.py` covers the distance helpers; `tests/test_solver.py` covers
the optimizer with hand-built scenarios (single-rider bike-back, drop-car +
supporter, SAG wagon, households, and an over-constrained infeasible case);
`tests/test_fixtures.py` covers loading rosters and the SQLite migrations;
`tests/test_recovery.py` covers organizer-link email recovery.

## Layout

| Path | What |
|------|------|
| `app/solver.py` | The CP-SAT model + plan extraction (the core) |
| `app/geo.py` | Offline ZIP → lat/lon + haversine distances |
| `app/events.py` | The two routes |
| `app/models.py` | SQLite tables + conversion to solver inputs |
| `app/db.py` | SQLite engine, sessions, and lightweight migrations |
| `app/fixtures.py` | Load JSON rosters into a solver `Problem` or the DB |
| `app/mailer.py` | Outbound SMTP for organizer-link recovery |
| `app/main.py` | FastAPI routes |
| `app/templates/`, `app/static/` | The web UI |

## Tuning

`Problem` in `app/solver.py` exposes four knobs (all in "miles" units):

- `detour_factor` — how much an out-of-the-way passenger/bike pickup costs.
- `pref_penalty_miles` — how many extra driving miles you'd trade to keep one
  person on their *preferred* return option instead of a merely *acceptable* one.
- `fairness_weight` (default 0.5) — weight on the **most-burdened person's load**.
  A person's burden = miles they drive (the SAG's route sweep doesn't count — that's
  the volunteered role) + `chore_leg_miles` per chore leg (night-before drops;
  going back out the next morning) + the preference penalty if they're on a backup
  return. The optimizer pays up to `fairness_weight × Δ` extra total miles to take
  `Δ` off the heaviest plate. Set 0 for pure efficiency.
- `chore_leg_miles` (default 15) — equivalent-mile cost of one chore leg above.

Plans report each person's burden (CLI table and plan page), so you can see who
carries the event and turn the dial if it looks lopsided. Fixtures can set these
in an event-level `"tuning"` block (see `fixtures/README.md`).

## Known approximations

- Multi-stop pickups are charged as an approximate per-extra-home detour rather
  than a full routed path — fine at this scale, and it keeps the solve exact.
- Distances are straight-line, so they under-estimate real road miles fairly
  uniformly; good enough for deciding *who drives whom*. Swap `app/geo.py` for a
  routing API if you want true drive times.

## License

[MIT](LICENSE).
