# PitBox Timing API

Canonical request / response shapes for the PitBox native timing engine.
This file is the single source of truth — code reviews should reject any
endpoint or message that drifts from the schemas below.

All examples use JSON. All times are UTC unix seconds (float) unless the
field name ends in `_ms` (milliseconds, integer). All endpoints are served
by the controller process on `ui_port` (default 5000 dev / 9630 Windows).

---

## Auth (Phase 10)

Every route — HTTP and WebSocket — follows the same rule:

| `employee_password` | Result |
|---|---|
| unset | Open to all LAN clients |
| set | Requires cookie `pitbox_employee=1` |

WS handshake rejection: HTTP 403 returned during handshake (server calls
`ws.close(code=1008)` before `accept()`).

---

## HTTP routes (mounted under `/api`)

### `GET /api/timing/health`

Engine status. Cheap; safe to poll for monitoring.

```jsonc
{
  "running": true,
  "host": "0.0.0.0",
  "port": 9996,
  "packets_received": 12834,
  "unknown_packets": 0,
  "last_packet_unix": 1730000000.123,
  "last_packet_age_seconds": 0.42,
  "fresh": true,
  "event_seq": 91,
  "engine": "native-acudpclient"
}
```

`fresh` is `true` when `last_packet_age_seconds < 10`. For richer health
state (live / stale / offline) read `health.timing` from `/api/timing/snapshot`.

### `GET /api/timing/snapshot`

Full leaderboard snapshot. The frontend re-renders the entire UI from this
one document. Backend is authoritative — the frontend NEVER computes gaps,
intervals, or freshness from raw fields.

```jsonc
{
  "snapshot_seq": 4821,            // Phase 6: monotonic; client drops seq <= last
  "generated_unix": 1730000000.5,  // server wall-clock when snapshot was built
  "health": {
    "timing": {
      "state": "live",                // "live" | "stale" | "offline"
      "last_packet_unix": 1730000000.1,
      "last_packet_age_s": 0.4,
      "stale_after_s": 5.0,           // age > this  -> "stale"
      "offline_after_s": 30.0         // age > this  -> "offline"
    },
    "transport": { "ws_supported": true }
  },
  "session": {
    "server_name": "PitBox #1",
    "track_name": "spa",
    "track_config": "",
    "session_name": "Race",
    "session_type": 3,                 // 0..7 (see _SESSION_TYPE_NAMES)
    "session_type_name": "Race",
    "session_index": 0,
    "current_session_index": 0,
    "session_count": 1,
    "proto_version": 4,
    "time_minutes": 30,
    "laps": 0,
    "wait_time": 0,
    "ambient_temp": 22,
    "track_temp": 31,
    "weather_graph": "3_clear",
    "elapsed_ms": 615123,
    "started_at_unix": 1729999385.0
  },
  "drivers": [
    {
      "car_id": 0,
      "connected": true,
      "driver_name": "Alice",
      "driver_guid": "76561198...",
      "driver_team": "",
      "car_model": "ks_ferrari_488_gt3",
      "car_skin": "00_red",
      "last_lap_ms": 124567,
      "best_lap_ms": 123890,
      "total_laps": 7,
      "position": 1,
      "gap_ms": 0,                       // INTERNAL: raw AC delta-to-leader. UI must IGNORE.
      "gap_to_leader_ms": 0,             // Phase 5: AUTHORITATIVE. null = "—".
      "interval_to_ahead_ms": null,      // null for the leader
      "cuts_last_lap": 0,
      "loaded": true,
      "live_telemetry": {                // present only when a sim agent matches by nick
        "speed_kmh": 214.3, "gear": 5, "rpm": 7800,
        "throttle": 0.92, "brake": 0.0, "fuel": 38.2,
        "in_pit": false, "current_sector": 1,
        "tyre_compound": "soft", "norm_pos": 0.314,
        "stale": false, "age_sec": 0.2, "player_nick": "alice"
      },
      "freshness": {                     // Phase 7: per-driver derived state
        "timing_state": "live",          // follows health.timing.state when connected
        "telemetry_state": "live"        // "live" | "stale" | "missing"
      }
    }
  ],
  "telemetry_agents": {                  // raw per-agent blocks; UI shows agent pills
    "sim-3": { "player_nick": "alice", "stale": false, "age_sec": 0.2, ... }
  },
  "stats": {
    "running": true,
    "host": "0.0.0.0",
    "port": 9996,
    "packets_received": 12834,
    "unknown_packets": 0,
    "last_packet_unix": 1730000000.1,
    "event_seq": 91,
    "resync": {                          // Phase 4 supervisor counters
      "attempts": 0, "successes": 0,
      "next_attempt_unix": 0.0,
      "backoff_s": 5.0, "last_reason": ""
    }
  }
}
```

Frontend invariants the backend MUST keep honouring:

- `snapshot_seq` is strictly increasing across the lifetime of one engine
  process. On controller restart `generated_unix` jumps forward and `seq`
  may regress; the frontend handles that via the (gen-forward + seq-back)
  rebase rule (see `applySnapshot` in `live_timing.js`).
- `gap_to_leader_ms` / `interval_to_ahead_ms` are `null` when not yet
  authoritative (driver has no completed laps; or leader's interval).
  Frontend renders both `null` and `0` as `'—'` (a leader has no
  meaningful "+0.000" gap to themselves; see `fmtGap` in
  `live_timing.js`). Any value `> 0` renders as `"+S.sss"` or
  `"+M:SS.sss"` depending on magnitude.
- `freshness.timing_state` is `'offline'` whenever `connected=false`.

### `GET /api/timing/session`

Returns just `snapshot.session`. Convenience.

### `GET /api/timing/events?since=<seq>&limit=<1..200>`

Recent events with `seq > since`, capped at `limit` (default 100).

```jsonc
{
  "events": [
    {
      "seq": 88,                         // monotonic per engine process
      "ts": 1730000000.12,
      "type": "lap_completed",           // canonical (no aliases like "kind")
      "car_id": 0,
      "driver": "Alice",                 // canonical (no "driver_name" on events)
      "track": "spa",
      "lap_ms": 123890,                  // first-class for lap_completed events
      "payload": {                       // event-type-specific extras
        "cuts": 0, "total_laps": 7,
        "position": 1, "grip_level": 0.985
      }
    }
  ],
  "next_seq": 91                         // pass back as ?since on the next call
}
```

Canonical event `type` values currently emitted:

`server_version`, `new_session`, `end_session`, `driver_connected`,
`driver_disconnected`, `client_loaded`, `chat`, `lap_completed`,
`client_event`, `ac_error`.

Top-level fields are always `{seq, ts, type, car_id, driver, track, lap_ms, payload}`.
Anything event-specific lives under `payload`.

---

## WebSocket route

### `WS /ws/timing` — push at ~2 Hz

Initial frame on connect:

```jsonc
{ "type": "snapshot", "data": <snapshot object as above> }
```

Every subsequent tick:

```jsonc
{
  "type": "tick",
  "snapshot": <snapshot object>,
  "events":   [ <event>, <event>, ... ],   // delta since the previous tick's next_seq
  "next_seq": 91                           // shared cursor; matches HTTP /events
}
```

Frontend (Phase 9) consumes `events` + `next_seq` from WS ticks via the same
shared `consumeEvents()` path used by the HTTP fallback poll. Cursor never
moves backward across transport switches; same-`seq` events are dropped.

---

## Versioning

This doc tracks the implementation. There is intentionally no separate
schema-version field on the wire: clients dedupe via `snapshot_seq` and
`event.seq`, so additive backend changes are safe. Field removals or
semantic changes require a Phase note in this file and a code review.
