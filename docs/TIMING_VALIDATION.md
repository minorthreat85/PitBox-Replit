# PitBox Live Timing — Validation Report (Phase 12)

This document closes out the 12-phase live-timing remediation. It records
what is covered by automated tests, what was validated manually, and what
remains untested.

---

## Automated test coverage

Run all timing tests:

```
python -m unittest controller.tests.test_timing_engine controller.tests.test_timing_ws_auth
```

**Result: 29 tests / 0 failures / 0 errors.**

| Phase | Concern | Test class / file | Tests |
|---|---|---|---|
| 2 | Canonical event schema (`{seq, ts, type, car_id, driver, track, lap_ms, payload}`; no `kind`/`driver_name` aliases) | `test_timing_engine.TestCanonicalEventSchema` | 2 |
| 9 | Event cursor monotonicity, dedupe, transport-overlap behaviour at the engine level | `test_timing_engine.TestEventCursorMonotonicity` | 3 |
| 6 | Snapshot `seq` strictly increases; `generated_unix` present and monotonic | `test_timing_engine.TestSnapshotMonotonicOrdering` | 2 |
| 5 | Backend-authoritative gap/interval: leader = 0, no laps = `null`, never-negative interval, mixed grid | `test_timing_engine.TestGapIntervalEdgeCases` | 4 |
| 7 | Timing-feed health (live / stale / offline thresholds); per-driver `freshness.timing_state` | `test_timing_engine.TestTimingHealthSemantics` | 6 |
| 4 | Resync diagnose: not-started, cold-start grace window, stale threshold, fresh-clears-stale | `test_timing_engine.TestResyncTriggers` | 4 |
| 4 | Resync probe boundedness: no running servers → no spam, no exception | `test_timing_engine.TestResyncProbeBoundedness` | 1 |
| 10 | HTTP↔WS auth parity in all 3 password states (+ wrong-cookie variant) | `test_timing_ws_auth.TestTimingWsAuthParity` | 7 |

---

## Manual validation checklist

Status as of 2026-04-20. The Replit dev environment cannot reach a real
Assetto Corsa server, so AC-data-driven scenarios were validated either
through the engine snapshot path (no AC required) or are flagged below as
deferred to the Windows production environment (`C:\Users\info\pitbox\`).

| # | Scenario | Status | Notes |
|---|---|---|---|
| 1 | Controller starts before AC server | ✅ Verified in dev | `health.timing.state` reports `offline` immediately; engine boots, listener attempts to bind port 9996, snapshot continues to serve. Phase 4 supervisor would nudge once AC starts. |
| 2 | Controller starts after AC session already started | ⏸ Deferred to Windows | Requires real AC dedicated server. Phase 4 cold_start logic covered by `TestResyncTriggers.test_cold_start_only_after_grace`. |
| 3 | WS disconnect during active timing → fallback polling activates | ⚠ Partial | Engine-side correctness (events_since cursor + snapshot ordering) covered. Frontend transport state machine NOT exercised by automated tests — see "Untested" below. |
| 4 | WS reconnect after fallback → polling stops, no duplicate events | ⚠ Partial | Same as #3. The shared `consumeEvents()` dedupe path is implemented and reviewed; no JS test harness in repo. |
| 5 | Timing feed becomes stale while UI transport remains reachable | ✅ Verified in dev | `TestTimingHealthSemantics` proves backend correctly transitions live → stale → offline as `last_packet_unix` ages. Frontend reads `health.timing.state` directly (no own thresholds — Phase 7). |
| 6 | Driver disconnect while selected → detail panel clears | ⚠ Logic verified, UI not | `applySnapshot` clears `state.selectedCarId` when the car is gone (Phase 8). Manually confirmed in code review; no automated DOM test. |
| 7 | Session transition (qual → race or reset) | ⏸ Deferred to Windows | Requires real AC; engine handler (`_on_ACSP_NEW_SESSION`) resets per-driver per-session counters; covered structurally but not end-to-end here. |
| 8 | Unauthorized timing WS attempt rejected, authorized works | ✅ Verified | `test_timing_ws_auth` covers all 3 auth states for both HTTP and WS. Reject-before-accept returns HTTP 403 during handshake. |

Verified live in dev (this session):

```
$ curl -s http://localhost:5000/api/timing/snapshot | jq 'keys'
[ "drivers", "generated_unix", "health", "session", "snapshot_seq", "stats", "telemetry_agents" ]
```

---

## Untested / known gaps

The following could not be hardened via automated tests in the current
environment without disproportionate effort. They are real, callable risks.

1. **Frontend transport state machine** — `startWebSocket` / `startPolling`
   / `startEventsPoll` / `consumeEvents` interactions are not covered by a
   JS test harness. The state machine is straightforward, reviewed twice,
   and the engine-level dedupe guarantees no duplicates regardless of
   transport behaviour, but it is conceivable that some browser-edge case
   (e.g. `WebSocket` fires `error` without a subsequent `close` on some
   versions) could leave both loops armed. Mitigation: every lifecycle
   transition explicitly calls both `stop` functions before starting fresh
   ones, so the worst case is one extra HTTP poll, not divergent state.

2. **Selected-driver invalidation in the DOM** — Phase 8's
   `findSelectedDriver` + `renderDetailEmpty` path is logic-verified by
   reading the code, not by automated DOM tests. No JS testing
   infrastructure exists in this repo (would require introducing
   jest/vitest just for one feature, deferred).

3. **Real AC integration** — packet ingestion from a live Assetto Corsa
   dedicated server cannot be exercised on Linux. The vendored
   `acudpclient` parsers are unit-tested upstream, and `_on_ACSP_*`
   handlers are pure (no I/O), so per-handler mutation tests are feasible
   in a follow-up if needed.

4. **Resync supervisor end-to-end** — only the diagnose function and the
   "no running servers" probe path are tested. The full
   `_resync_loop` sleep/iteration loop is not exercised; it would require
   a clock-mock approach that adds complexity without much new signal.

5. **Leaderboard ordering for no-lap drivers** — the engine sorts by AC's
   `position` field. We do not currently guarantee that drivers with zero
   completed laps appear at the bottom; that ordering is whatever AC sends.
   Not tested as an invariant.

6. **Controller EXE rebuild** — The Windows EXE has not been rebuilt with
   the Phase 2/9/10 changes. Until rebuilt, the production controller runs
   the pre-Phase-10 behaviour (open WS). Tracked in scratchpad as
   "DEFERRED: Controller EXE rebuild".

---

## Tiny corrective fixes made during validation

None. The new tests passed against the engine as it stood after Phase 11.

---

## Cross-phase regression check

```
$ python -m unittest controller.tests.test_timing_engine controller.tests.test_timing_ws_auth
Ran 29 tests in 0.085s
OK
```

All previously-added timing tests still pass alongside the new Phase 12
suite. No phase regressed.
