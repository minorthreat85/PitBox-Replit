# PitBox port scheme

Authoritative constants live in [`pitbox_common/ports.py`](pitbox_common/ports.py).

| Role | TCP port | Notes |
|------|-----------|--------|
| PitBox Controller (web UI + REST) | **9630** | Admin PC |
| Sim1 … Sim8 Agent HTTP | **9631 … 9638** | Sim *n* → `9630 + n` |
| Enrollment (UDP) | 9640 | LAN enrollment broadcast |
| Discovery / beacons (UDP) | 9641 | Agent ↔ controller discovery |

Other products may use **9600** (e.g. Emperor/server-manager, SimHub). PitBox agents do **not** use 9600 for HTTP; legacy configs with `"port": 9600` are treated as “derive port from `agent_id`” at runtime.
