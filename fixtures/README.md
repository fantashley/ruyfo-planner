# Roster fixtures

Fixed, real rosters as JSON — the single source of truth for a given RUYFO, never
randomized. Use them to re-check plans as the model changes:

```bash
python -m scripts.fixture list                 # list fixtures here
python -m scripts.fixture plan example         # solve + print the plan (no DB)
python -m scripts.fixture load example         # seed it into the app DB for the web UI
```

`load` replaces any existing event of the same name, so re-loading is idempotent
(pass `--keep-existing` to keep duplicates). See `example.json` for a worked file.

## Schema

```jsonc
{
  "event": {
    "name": "Spring RUYFO 2026",          // unique; used as the event title + dedup key
    "route_key": "faribault_mankato",      // or "wayzata_hutchinson"
    "has_sag": true                        // is a SAG wagon available?
  },
  "tuning": {                              // optional solver knobs (defaults shown)
    "fairness_weight": 0.5,                // weight on the most-burdened person's load
    "chore_leg_miles": 15,                 // equivalent miles charged per chore car leg
    "pref_penalty_miles": 30,              // cost of a merely-acceptable return option
    "detour_factor": 1.0                   // multiplier on pickup-detour distance
  },
  "participants": [ { …person… }, … ]
}
```

Each participant (only `name` and `home_zip` are required; everything else has a
sensible default):

| field | type | default | meaning |
|-------|------|---------|---------|
| `name` | string | — | **required**, must be unique in the roster (used as the key) |
| `email` | string | `""` | optional |
| `home_zip` | string | — | **required** 5-digit ZIP |
| `household` | string | `""` (own) | share a label to travel together for free |
| `is_rider` | bool | `true` | `false` = a non-riding supporter/driver |
| `num_bikes` | int | `1` | bikes this person **owns and brings**; `0` if they only ride a loaner |
| `loaner_for` | string | `""` | the **name** of the rider you bring a spare bike for |
| `bag_count` | int | `0` | overnight bags to get to the hotel |
| `has_car` | bool | `false` | do they have a car to use? |
| `car_combos` | string | `"5x2"` if `has_car` | capacity options, `people x bikes`, comma-separated (e.g. `"5x2, 2x4"`) |
| `willing_drop_car` | bool | `false` | leave their car at the finish the night before |
| `willing_drop_bikes_at_start` | bool | `false` | shuttle bikes to the start the night before |
| `willing_drive_dropper_home` | bool | `false` | drive a car-dropper home the night before |
| `can_drive_morning` | bool | `false` | drive others to the start the morning of |
| `is_sag_driver` | bool | `false` | drives the SAG wagon (only meaningful if `has_sag`) |
| `return` | object | `tonight=preferred, others=acceptable` | per-option preference, each `"preferred"` / `"acceptable"` / `"unwilling"`: `{"tonight": …, "bikeback": …, "ridehome": …}` |

Notes:
- A **loaner**: the lender sets `loaner_for` to the borrower's name; the borrower
  sets `num_bikes: 0`. The bike returns to the lender's home.
- Keys omitted from `return` fall back to the defaults above.
