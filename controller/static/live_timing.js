/* PitBox native Live Timing client.
 *
 * Renders the Live Timing dashboard: agent connection bar, leaderboard with
 * live (sim-side) speed/gear/RPM/pit columns, track map (positions from sim
 * telemetry normalized_car_position), driver detail panel, and event stream.
 *
 * Activates only when the Live Timing page is visible (detected via the
 * `.hidden` class on `#page-live-timing`). Tries WebSocket first; if the WS
 * fails or closes, transparently falls back to HTTP polling against
 * /api/timing/snapshot for the leaderboard and /api/timing/events for the
 * event feed (Phase 9: WS is primary for both, HTTP runs only as fallback).
 *
 * Two independent indicators (Phase 7):
 *   - `lt-conn`   : transport status (WS live / Polling / Idle).
 *   - `lt-health` : AC timing-feed status driven entirely by backend
 *                   `health.timing.state` (live / stale / offline).
 *                   Thresholds: <=5s live, <=30s stale, >30s offline.
 *                   The frontend NEVER recomputes these thresholds.
 */
(function () {
    'use strict';

    var POLL_INTERVAL_MS = 1000;
    var WS_RETRY_MS = 5000;
    var EVENTS_POLL_MS = 2000;
    var EVENTS_MAX = 30;

    // Default fallback track map (the original generic oval). Used when no
    // per-track JSON is available under /static/track_maps/<key>.json.
    var FALLBACK_MAP = {
        viewBox: '0 0 200 120',
        svg_path: 'M 30 60 C 30 20, 170 20, 170 60 C 170 100, 30 100, 30 60 Z',
        start_offset: 0.0,
        direction: 1,
        scale: 1.0,
        _fallback: true,
    };

    var state = {
        active: false,
        ws: null,
        wsRetryTimer: null,
        pollTimer: null,
        eventsTimer: null,
        staleTimer: null,
        mode: 'idle',
        lastSnapshot: null,
        selectedCarId: null,
        eventSeq: 0,
        // Phase 6: monotonic snapshot ordering. Drop any frame whose
        // snapshot_seq is <= the last one we rendered, no matter whether it
        // arrived via WS tick, WS initial, or HTTP poll. Backends without
        // this field (older controllers) are detected by snapshot_seq == null
        // and fall back to "always accept" behaviour.
        lastSnapshotSeq: 0,
        lastSnapshotGenUnix: 0,
        events: [],
        // Per-track map state
        trackKey: null,         // key currently loaded ('' = fallback applied)
        trackMap: null,         // resolved map object (FALLBACK_MAP or fetched)
        trackMapLoading: false, // a fetch is in flight
        trackMapCache: {},      // key -> map object | null (null = known-missing)
    };

    function $(id) { return document.getElementById(id); }

    function setMode(mode, label) {
        state.mode = mode;
        var pill = $('lt-conn');
        var lbl = $('lt-conn-label');
        if (!pill || !lbl) return;
        pill.classList.remove('lt-conn--idle', 'lt-conn--ws', 'lt-conn--poll', 'lt-conn--stale');
        pill.classList.add('lt-conn--' + mode);
        lbl.textContent = label;
    }

    function fmtLap(ms) {
        if (!ms || ms <= 0) return '—';
        var totalSec = ms / 1000;
        var m = Math.floor(totalSec / 60);
        var s = totalSec - (m * 60);
        var sStr = s.toFixed(3);
        if (s < 10) sStr = '0' + sStr;
        return m + ':' + sStr;
    }
    function fmtGap(ms) {
        if (ms === undefined || ms === null) return '—';
        if (ms === 0) return '—';
        var s = ms / 1000;
        if (s < 60) return '+' + s.toFixed(3);
        var m = Math.floor(s / 60);
        var rem = s - (m * 60);
        return '+' + m + ':' + (rem < 10 ? '0' : '') + rem.toFixed(3);
    }
    function fmtRemaining(session) {
        if (!session) return '—';
        if (session.session_type === 3 && session.laps > 0) return session.laps + ' laps';
        var totalS = (session.time_minutes || 0) * 60;
        if (!totalS) return '—';
        var elapsedS = (session.elapsed_ms || 0) / 1000;
        var remain = Math.max(0, totalS - elapsedS);
        var m = Math.floor(remain / 60);
        var s = Math.floor(remain - m * 60);
        return m + ':' + (s < 10 ? '0' : '') + s;
    }
    function fmtTemps(session) {
        if (!session) return '—';
        if (!session.ambient_temp && !session.track_temp) return '—';
        return session.ambient_temp + '°C / ' + session.track_temp + '°C';
    }
    function escapeHtml(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }
    function gearStr(g) {
        if (g === undefined || g === null) return '—';
        if (g === 0) return 'R';
        if (g === 1) return 'N';
        return String(g - 1);
    }
    function pct(v) {
        if (v === undefined || v === null) return '0';
        return Math.round(Math.max(0, Math.min(1, v)) * 100) + '';
    }

    function statusPill(driver, hasLive) {
        if (!driver.connected) return '<span class="lt-status lt-status--offline">Offline</span>';
        if (!driver.loaded) return '<span class="lt-status lt-status--loading">Loading</span>';
        if (driver.live_telemetry && driver.live_telemetry.in_pit) return '<span class="lt-status lt-status--pit">Pit</span>';
        if (hasLive) return '<span class="lt-status lt-status--on">Live</span>';
        return '<span class="lt-status lt-status--on">Track</span>';
    }

    function renderHeader(session) {
        if (!session) return;
        var trackTxt = session.track_name
            ? session.track_name + (session.track_config ? ' / ' + session.track_config : '')
            : '—';
        $('lt-track').textContent = trackTxt;
        $('lt-session-type').textContent = session.session_name
            ? (session.session_type_name || session.session_name) + ' — ' + session.session_name
            : (session.session_type_name || '—');
        $('lt-time-remaining').textContent = fmtRemaining(session);
        $('lt-temps').textContent = fmtTemps(session);
        $('lt-server-name').textContent = session.server_name || '—';
        $('lt-weather').textContent = session.weather_graph || '—';
        var mapName = $('lt-map-track-name');
        if (mapName) mapName.textContent = trackTxt;
        // Kick off (or refresh) the per-track map load when the track changes.
        ensureTrackMap(session);
    }

    // ---- Per-track map registry ----
    // Files live under /static/track_maps/<key>.json.
    //
    // SINGLE SOURCE OF TRUTH: `session.map_key`, computed canonically by the
    // backend (`_compute_map_key` in controller/timing/engine.py — strips
    // CSP-style virtual paths like 'csp/3749/.../jr_road_atlanta_2022'
    // before slugifying with the same rule the build-time generator uses).
    //
    // We DO NOT slugify raw `track_name` / `track_config` on the frontend.
    // That path used to produce broken keys when AC reports CSP virtual
    // paths and was the cause of the persistent oval-fallback bug.
    //
    // Fallback to the generic oval happens only when:
    //   * session.map_key is missing/empty, OR
    //   * the bare `<track>` JSON is missing (we try `<track>__<layout>`
    //     first if the key contains `__`, then bare `<track>`), OR
    //   * the fetch returns 404.
    function trackKeyCandidates(session) {
        if (!session) return [];
        var primary = (typeof session.map_key === 'string') ? session.map_key.trim() : '';
        if (!primary) return [];
        // Try `<track>__<layout>` then bare `<track>` for the same fallback
        // chain the build-time generator emits.
        var idx = primary.indexOf('__');
        return idx > 0 ? [primary, primary.slice(0, idx)] : [primary];
    }
    function applyTrackMap(map) {
        state.trackMap = map || FALLBACK_MAP;
        var svg = $('lt-map-svg');
        var path = $('lt-map-path');
        if (!svg || !path) return;
        if (state.trackMap.viewBox) svg.setAttribute('viewBox', state.trackMap.viewBox);
        if (state.trackMap.svg_path) path.setAttribute('d', state.trackMap.svg_path);
        // Force re-render of car dots against the new geometry on next snapshot.
        if (state.lastSnapshot) renderMap(state.lastSnapshot);
    }
    function ensureTrackMap(session) {
        var candidates = trackKeyCandidates(session);
        var key = candidates[0] || '';
        if (key === state.trackKey) return;       // no change
        if (state.trackMapLoading) return;        // in-flight; will resolve
        state.trackKey = key;
        // Operator-facing diagnostic: confirm what key we're trying.
        try {
            console.log('[live-timing] map_key:', session && session.map_key,
                '-> candidates:', candidates);
        } catch (_e) { /* noop */ }
        if (!key) { applyTrackMap(FALLBACK_MAP); return; }
        // Try each candidate in order; cache hits short-circuit.
        var tryNext = function (idx) {
            if (idx >= candidates.length) {
                state.trackMapCache[key] = null;
                applyTrackMap(FALLBACK_MAP);
                state.trackMapLoading = false;
                return;
            }
            var k = candidates[idx];
            if (Object.prototype.hasOwnProperty.call(state.trackMapCache, k)) {
                var cached = state.trackMapCache[k];
                if (cached) { applyTrackMap(cached); state.trackMapLoading = false; return; }
                tryNext(idx + 1); return;
            }
            fetch('/track_maps/' + encodeURIComponent(k) + '.json',
                { credentials: 'same-origin', cache: 'no-cache' })
                .then(function (r) { return r.ok ? r.json() : null; })
                .then(function (data) {
                    if (data && data.svg_path) {
                        state.trackMapCache[k] = data;
                        applyTrackMap(data);
                        state.trackMapLoading = false;
                    } else {
                        state.trackMapCache[k] = null;
                        tryNext(idx + 1);
                    }
                })
                .catch(function () {
                    state.trackMapCache[k] = null;
                    tryNext(idx + 1);
                });
        };
        state.trackMapLoading = true;
        tryNext(0);
    }

    function renderAgents(snapshot) {
        var list = $('lt-agents-list');
        if (!list) return;
        var agents = (snapshot && snapshot.telemetry_agents) || {};
        var ids = Object.keys(agents);
        if (ids.length === 0) {
            list.innerHTML = '<span class="lt-agent-pill lt-agent-pill--idle">No agents connected</span>';
            return;
        }
        var html = '';
        ids.sort();
        for (var i = 0; i < ids.length; i++) {
            var aid = ids[i];
            var a = agents[aid] || {};
            var cls = a.stale ? 'lt-agent-pill--stale' : 'lt-agent-pill--live';
            var nick = a.player_nick ? escapeHtml(a.player_nick) : '';
            var label = escapeHtml(aid) + (nick ? ' · ' + nick : '');
            html += '<span class="lt-agent-pill ' + cls + '" title="age ' + (a.age_sec || 0) + 's">'
                + label + '</span>';
        }
        list.innerHTML = html;
    }

    function renderBoard(snapshot) {
        var tbody = $('lt-rows');
        if (!tbody) return;
        var drivers = (snapshot && snapshot.drivers) || [];
        var connected = drivers.filter(function (d) { return d.connected; });
        if (connected.length === 0) {
            tbody.innerHTML = '<tr class="lt-empty-row"><td colspan="12">Waiting for AC server telemetry…</td></tr>';
            return;
        }
        var sorted = connected.slice().sort(function (a, b) {
            var pa = a.position > 0 ? a.position : 999;
            var pb = b.position > 0 ? b.position : 999;
            if (pa !== pb) return pa - pb;
            return a.car_id - b.car_id;
        });
        var rowsHtml = '';
        for (var i = 0; i < sorted.length; i++) {
            var d = sorted[i];
            var pos = d.position > 0 ? d.position : (i + 1);
            var posCls = pos === 1 ? 'lt-pos-1' : (pos === 2 ? 'lt-pos-2' : (pos === 3 ? 'lt-pos-3' : ''));
            // Phase 5: backend is authoritative for gap/interval. Display
            // backend-provided values only; never compute from gap_ms here.
            // null/undefined => '—' (not yet authoritative).
            // 0              => '—' (leader / no meaningful gap; see fmtGap).
            var gLeader = d.gap_to_leader_ms;
            var iAhead = d.interval_to_ahead_ms;
            var gapTxt = (gLeader == null) ? '—' : fmtGap(gLeader);
            var intervalTxt = (iAhead == null) ? '—' : fmtGap(iAhead);
            var lt = d.live_telemetry || null;
            var spd = lt && lt.speed_kmh != null ? Math.round(lt.speed_kmh) : '—';
            var gear = lt ? gearStr(lt.gear) : '—';
            var rpm = lt && lt.rpm != null ? lt.rpm : '—';
            var selectedCls = state.selectedCarId === d.car_id ? ' lt-row-selected' : '';
            var liveCls = lt ? ' lt-row-live' : '';
            rowsHtml += '<tr class="lt-row' + liveCls + selectedCls + '" data-car-id="' + d.car_id + '">'
                + '<td class="lt-col-pos ' + posCls + '">' + pos + '</td>'
                + '<td class="lt-col-driver">' + escapeHtml(d.driver_name || ('Car ' + d.car_id)) + '</td>'
                + '<td class="lt-col-car">' + escapeHtml(d.car_model || '—') + '</td>'
                + '<td class="lt-col-laps">' + (d.total_laps || 0) + '</td>'
                + '<td class="lt-col-best lt-best-cell">' + fmtLap(d.best_lap_ms) + '</td>'
                + '<td class="lt-col-last">' + fmtLap(d.last_lap_ms) + '</td>'
                + '<td class="lt-col-gap">' + gapTxt + '</td>'
                + '<td class="lt-col-int">' + intervalTxt + '</td>'
                + '<td class="lt-col-spd">' + spd + '</td>'
                + '<td class="lt-col-gear">' + gear + '</td>'
                + '<td class="lt-col-rpm">' + rpm + '</td>'
                + '<td class="lt-col-status">' + statusPill(d, !!lt) + '</td>'
                + '</tr>';
        }
        tbody.innerHTML = rowsHtml;
    }

    // Track map: place a colored dot per car at its normalized lap position
    // along the SVG path. Driver color is derived deterministically from car_id.
    function carColor(carId) {
        var palette = ['#22d3ee','#a78bfa','#f472b6','#facc15','#4ade80','#f97316','#60a5fa','#f87171'];
        return palette[(carId | 0) % palette.length];
    }
    function renderMap(snapshot) {
        var path = $('lt-map-path');
        var group = $('lt-map-cars');
        if (!path || !group || !path.getTotalLength) return;
        var len = path.getTotalLength();
        var map = state.trackMap || FALLBACK_MAP;
        var offset = map.start_offset || 0;
        var dir = map.direction === -1 ? -1 : 1;
        var drivers = (snapshot && snapshot.drivers) || [];
        var html = '';
        for (var i = 0; i < drivers.length; i++) {
            var d = drivers[i];
            if (!d.connected) continue;
            var lt = d.live_telemetry;
            if (!lt) continue;
            // Defense in depth: backend already drops invalid norm_pos to
            // null, but if any garbage slips through (NaN / inf / out of
            // range) we MUST NOT render it — wrapping a huge number into
            // [0,1) places dots at random points along the track path.
            var np = lt.norm_pos;
            if (np == null || typeof np !== 'number' || !isFinite(np) || np < 0 || np > 1) continue;
            // norm_pos is 0..1 from AC. Apply per-track start_offset and
            // direction (rendering-layer transform only). Wrap into [0,1).
            var t = (dir * np) + offset;
            t = t - Math.floor(t);
            if (t < 0) t += 1;
            var pt = path.getPointAtLength(t * len);
            var color = carColor(d.car_id);
            var sel = state.selectedCarId === d.car_id;
            var r = sel ? 5 : 3.5;
            var stroke = sel ? '#fff' : 'rgba(0,0,0,0.4)';
            var label = escapeHtml(d.driver_name || ('#' + d.car_id));
            html += '<circle cx="' + pt.x.toFixed(2) + '" cy="' + pt.y.toFixed(2) + '" r="' + r + '"'
                  + ' fill="' + color + '" stroke="' + stroke + '" stroke-width="' + (sel ? 1.5 : 0.8) + '">'
                  + '<title>' + label + (lt.speed_kmh != null ? ' — ' + Math.round(lt.speed_kmh) + ' km/h' : '') + '</title>'
                  + '</circle>';
        }
        group.innerHTML = html;
    }

    // Phase 8: detail panel must NEVER show ghost data for a driver that is
    // no longer in the snapshot. Selection identity is the stable `car_id`.
    // Resolution is fresh-on-every-render — we never cache a driver object.
    function findSelectedDriver(snapshot) {
        if (state.selectedCarId == null) return null;
        var drivers = (snapshot && snapshot.drivers) || [];
        for (var i = 0; i < drivers.length; i++) {
            if (drivers[i].car_id === state.selectedCarId) return drivers[i];
        }
        return null;
    }

    function renderDetailEmpty(message) {
        var nameEl = $('lt-detail-name');
        var posEl = $('lt-detail-pos');
        var emptyEl = $('lt-detail-empty');
        var bodyEl = $('lt-detail-body');
        if (nameEl) nameEl.textContent = 'Select a driver';
        if (posEl) posEl.textContent = '';
        if (emptyEl) {
            emptyEl.textContent = message;
            emptyEl.classList.remove('hidden');
        }
        if (bodyEl) bodyEl.classList.add('hidden');
    }

    function renderDetail(snapshot) {
        var drivers = (snapshot && snapshot.drivers) || [];
        // Empty state #1: no live timing data at all.
        if (drivers.length === 0) {
            renderDetailEmpty('No live timing data available.');
            return;
        }
        // Empty state #2: nothing selected yet.
        if (state.selectedCarId == null) {
            renderDetailEmpty('Click a row in the leaderboard to view live telemetry for that driver.');
            return;
        }
        var d = findSelectedDriver(snapshot);
        // Empty state #3: previously-selected driver is gone (disconnected,
        // session reset, etc.). Show explicit message; selection is already
        // cleared by applySnapshot so this state is transient.
        if (!d) {
            renderDetailEmpty('Selected driver is no longer available.');
            return;
        }
        // Valid selected driver — render fresh values.
        $('lt-detail-name').textContent = d.driver_name || ('Car ' + d.car_id);
        $('lt-detail-pos').textContent = d.position > 0 ? ('P' + d.position) : '';
        $('lt-detail-empty').classList.add('hidden');
        $('lt-detail-body').classList.remove('hidden');
        var lt = d.live_telemetry || {};
        $('lt-d-spd').textContent = lt.speed_kmh != null ? Math.round(lt.speed_kmh) : '—';
        $('lt-d-gear').textContent = gearStr(lt.gear);
        $('lt-d-rpm').textContent = lt.rpm != null ? lt.rpm : '—';
        $('lt-d-fuel').textContent = lt.fuel != null ? lt.fuel.toFixed(1) : '—';
        var thr = pct(lt.throttle), brk = pct(lt.brake);
        $('lt-d-throttle').style.width = thr + '%';
        $('lt-d-throttle-pct').textContent = thr + '%';
        $('lt-d-brake').style.width = brk + '%';
        $('lt-d-brake-pct').textContent = brk + '%';
        $('lt-d-last').textContent = fmtLap(d.last_lap_ms);
        $('lt-d-best').textContent = fmtLap(d.best_lap_ms);
        $('lt-d-sector').textContent = lt.current_sector != null ? ('S' + (lt.current_sector + 1)) : '—';
        $('lt-d-pit').textContent = lt.in_pit ? 'In Pit' : 'On Track';
        $('lt-d-tyre').textContent = lt.tyre_compound || '—';
        // Same guard as the map renderer: never let an invalid norm_pos
        // bubble up as `3.99e+28%` in the detail panel.
        var dnp = lt.norm_pos;
        var dnpOk = (typeof dnp === 'number' && isFinite(dnp) && dnp >= 0 && dnp <= 1);
        $('lt-d-norm').textContent = dnpOk ? (dnp * 100).toFixed(1) + '%' : '—';
    }

    function renderEvents() {
        var list = $('lt-events-list');
        var count = $('lt-events-count');
        if (!list) return;
        var items = state.events.slice(-EVENTS_MAX).reverse();
        if (count) count.textContent = String(items.length);
        if (items.length === 0) {
            list.innerHTML = '<li class="lt-events-empty">No events yet.</li>';
            return;
        }
        var html = '';
        for (var i = 0; i < items.length; i++) {
            var e = items[i];
            var label = e.type || 'event';
            var detail = '';
            if (e.type === 'lap_completed') detail = (e.driver || '') + ' — ' + fmtLap(e.lap_ms);
            else if (e.type === 'new_session') detail = ((e.payload && e.payload.session_type) || e.type) + (e.track ? ' @ ' + e.track : '');
            else if (e.type === 'chat') detail = (e.driver || ('Car ' + e.car_id)) + ': ' + ((e.payload && e.payload.message) || '');
            else if (e.type === 'client_event') detail = (e.driver || ('Car ' + e.car_id)) + ' — ' + ((e.payload && e.payload.subtype) || 'incident');
            else if (e.type === 'ac_error') detail = (e.payload && e.payload.message) || '';
            else if (e.type === 'driver_connected') detail = e.driver || ('Car ' + e.car_id);
            else if (e.type === 'driver_disconnected') detail = e.driver || ('Car ' + e.car_id);
            else { try { detail = JSON.stringify(e); } catch (_e) {} }
            html += '<li><span class="lt-evt-type">' + escapeHtml(label) + '</span> '
                  + '<span class="lt-evt-detail">' + escapeHtml(detail) + '</span></li>';
        }
        list.innerHTML = html;
    }

    function applySnapshot(snap) {
        if (!snap) return;
        // Phase 6: ignore stale/out-of-order frames. A WS tick that races a
        // poll response (or vice-versa) must not rewind position/lap data.
        // Restart-safe: if the backend's generated_unix moves forward while
        // its snapshot_seq regresses (controller process restarted), rebase
        // the watermark instead of dead-locking the UI.
        var seq = snap.snapshot_seq;
        var genUnix = snap.generated_unix;
        if (Number.isFinite(seq)) {
            var lastGen = state.lastSnapshotGenUnix || 0;
            var restarted = Number.isFinite(genUnix)
                && lastGen > 0
                && genUnix > lastGen
                && seq < state.lastSnapshotSeq;
            if (!restarted && seq <= state.lastSnapshotSeq) return;
            state.lastSnapshotSeq = seq;
        }
        if (Number.isFinite(genUnix)) state.lastSnapshotGenUnix = genUnix;
        state.lastSnapshot = snap;
        // Phase 8: validate selection against the latest snapshot exactly
        // once per apply, before any render runs. If the selected car_id is
        // gone (disconnected, session reset, empty snapshot), clear it so
        // every renderer below sees a consistent "nothing selected" state
        // and no stale DOM survives.
        if (state.selectedCarId != null) {
            var drivers = snap.drivers || [];
            var stillThere = false;
            for (var i = 0; i < drivers.length; i++) {
                if (drivers[i].car_id === state.selectedCarId) { stillThere = true; break; }
            }
            if (!stillThere) state.selectedCarId = null;
        }
        renderHeader(snap.session);
        renderAgents(snap);
        renderBoard(snap);
        renderMap(snap);
        renderDetail(snap);
        updateHealth(snap);
    }

    // Phase 7: backend is the only source of truth for timing-feed health.
    // The transport pill (lt-conn) reflects browser<->PitBox WS/poll status
    // and is set by setMode() when transport changes — we do NOT mutate it
    // from health any more. The new health badge (lt-health) reflects the
    // AC timing feed independently.
    function updateHealth(snap) {
        var badge = $('lt-health');
        var label = $('lt-health-label');
        var banner = $('lt-stale-banner');
        var msgEl = $('lt-stale-msg');
        if (!badge || !label) return;
        var h = (snap && snap.health && snap.health.timing) || null;
        if (!h) {
            // Older controller without health block — fall back to "unknown".
            badge.classList.remove('lt-health--live', 'lt-health--stale', 'lt-health--offline');
            badge.classList.add('lt-health--idle');
            label.textContent = 'Timing: —';
            if (banner) banner.classList.add('hidden');
            return;
        }
        var st = h.state || 'offline';
        badge.classList.remove('lt-health--live', 'lt-health--stale', 'lt-health--offline', 'lt-health--idle');
        badge.classList.add('lt-health--' + st);
        // Display-only age tick: advance the cached last_packet_age_s by the
        // wall-clock delta since the snapshot was generated, so the counter
        // moves between snapshots. Badge STATE remains strictly backend-driven
        // (we never recompute live/stale/offline thresholds here).
        var displayAge = null;
        if (h.last_packet_age_s != null) {
            displayAge = h.last_packet_age_s;
            var gen = snap.generated_unix;
            if (Number.isFinite(gen) && gen > 0) {
                var delta = (Date.now() / 1000) - gen;
                if (delta > 0 && delta < 3600) displayAge += delta;
            }
        }
        var ageTxt = (displayAge != null) ? (' (' + displayAge.toFixed(0) + 's)') : '';
        if (st === 'live')        label.textContent = 'Timing: Live';
        else if (st === 'stale')  label.textContent = 'Timing: Stale' + ageTxt;
        else                       label.textContent = 'Timing: Offline' + ageTxt;

        if (!banner) return;
        if (st === 'live') {
            banner.classList.add('hidden');
        } else {
            banner.classList.remove('hidden');
            if (h.last_packet_unix && h.last_packet_unix > 0) {
                if (msgEl) msgEl.textContent = (st === 'stale' ? 'Timing data is stale' : 'Timing feed is offline')
                    + ' — last AC packet ' + (displayAge != null ? displayAge.toFixed(1) + 's ago' : 'a long time ago')
                    + '. The dedicated server may be stopped or the UDP plugin not configured.';
            } else {
                if (msgEl) msgEl.textContent = 'No AC packets received yet. The dedicated server may be stopped or the UDP plugin not configured.';
            }
        }
    }

    function startStaleTicker() {
        // Re-render health locally each second so the age counter updates
        // between snapshots (snap stays cached; we just re-display it).
        stopStaleTicker();
        state.staleTimer = setInterval(function () {
            if (state.lastSnapshot) updateHealth(state.lastSnapshot);
        }, 1000);
    }
    function stopStaleTicker() { if (state.staleTimer) { clearInterval(state.staleTimer); state.staleTimer = null; } }

    function startPolling() {
        stopPolling();
        setMode('poll', 'Polling');
        var tick = function () {
            fetch('/api/timing/snapshot', { credentials: 'same-origin' })
                .then(function (r) { return r.ok ? r.json() : null; })
                .then(function (snap) { if (snap) applySnapshot(snap); })
                .catch(function () {});
        };
        tick();
        state.pollTimer = setInterval(tick, POLL_INTERVAL_MS);
    }
    function stopPolling() { if (state.pollTimer) { clearInterval(state.pollTimer); state.pollTimer = null; } }

    // Phase 9: ONE shared event consumer for both transports (WS tick and
    // HTTP poll). Dedupes on the canonical Phase-2 `seq` field, advances the
    // shared cursor monotonically, trims the rolling buffer, and renders.
    // Transports decide where events come from; this function decides what
    // to do with them. Never reset the cursor backwards.
    function consumeEvents(events, nextSeq) {
        var added = false;
        if (Array.isArray(events) && events.length) {
            for (var i = 0; i < events.length; i++) {
                var e = events[i];
                if (!e || typeof e !== 'object') continue;
                var seq = e.seq;
                if (Number.isFinite(seq)) {
                    if (seq <= state.eventSeq) continue;  // already seen
                    state.eventSeq = seq;
                }
                state.events.push(e);
                added = true;
            }
            if (state.events.length > 500) {
                state.events = state.events.slice(-500);
            }
        }
        if (Number.isFinite(nextSeq) && nextSeq > state.eventSeq) {
            state.eventSeq = nextSeq;
        }
        if (added) renderEvents();
    }

    function fetchEventsOnce() {
        // One-shot HTTP fetch — used to backfill on activate (the WS initial
        // 'snapshot' frame does NOT carry events) and as the fallback poll body.
        return fetch('/api/timing/events?since=' + state.eventSeq, { credentials: 'same-origin' })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (data) {
                if (data) consumeEvents(data.events, data.next_seq);
            })
            .catch(function () {});
    }

    function startEventsPoll() {
        // HTTP fallback ONLY. Started when WS is unavailable / closes; stopped
        // the moment WS becomes healthy. Continues from the shared cursor so
        // no duplicates after a transport switch.
        stopEventsPoll();
        fetchEventsOnce();
        state.eventsTimer = setInterval(fetchEventsOnce, EVENTS_POLL_MS);
    }
    function stopEventsPoll() { if (state.eventsTimer) { clearInterval(state.eventsTimer); state.eventsTimer = null; } }

    function startWebSocket() {
        // Phase 9: when WS is unavailable from the start, BOTH fallback loops
        // must run — otherwise events freeze after the one-shot backfill.
        if (typeof WebSocket === 'undefined') { startPolling(); startEventsPoll(); return; }
        try {
            var proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
            var url = proto + '//' + location.host + '/ws/timing';
            var ws = new WebSocket(url);
            state.ws = ws;
            setMode('ws', 'WS connecting…');
            ws.addEventListener('open', function () {
                // Phase 9: WS is the primary path for snapshot AND events.
                // Stop both HTTP loops; one-shot backfill (in activate) has
                // already primed state.eventSeq, so WS ticks pick up cleanly.
                stopPolling();
                stopEventsPoll();
                setMode('ws', 'WS live');
            });
            ws.addEventListener('message', function (ev) {
                var msg;
                try { msg = JSON.parse(ev.data); } catch (e) { return; }
                if (msg.type === 'snapshot') {
                    applySnapshot(msg.data);
                    // Initial frame currently carries no events array, but
                    // honour it if the backend ever adds one.
                    consumeEvents(msg.events, msg.next_seq);
                } else if (msg.type === 'tick') {
                    if (msg.snapshot) applySnapshot(msg.snapshot);
                    consumeEvents(msg.events, msg.next_seq);
                }
            });
            var fall = function () {
                state.ws = null;
                if (!state.active) return;
                // Phase 9: WS is unhealthy. Resume BOTH fallback loops and
                // continue from the shared event cursor — no duplicates.
                startPolling();
                startEventsPoll();
                clearTimeout(state.wsRetryTimer);
                state.wsRetryTimer = setTimeout(startWebSocket, WS_RETRY_MS);
            };
            ws.addEventListener('close', fall);
            ws.addEventListener('error', function () { try { ws.close(); } catch (e) {} });
        } catch (e) { startPolling(); startEventsPoll(); }
    }
    function stopWebSocket() {
        clearTimeout(state.wsRetryTimer); state.wsRetryTimer = null;
        if (state.ws) { try { state.ws.close(); } catch (e) {} state.ws = null; }
    }

    // Click on a row to select that driver for the detail panel.
    function bindRowClicks() {
        var tbody = $('lt-rows');
        if (!tbody || tbody.dataset.bound) return;
        tbody.dataset.bound = '1';
        tbody.addEventListener('click', function (ev) {
            var tr = ev.target && ev.target.closest && ev.target.closest('tr.lt-row');
            if (!tr) return;
            var cid = parseInt(tr.getAttribute('data-car-id'), 10);
            if (isNaN(cid)) return;
            state.selectedCarId = cid;
            if (state.lastSnapshot) {
                renderBoard(state.lastSnapshot);
                renderMap(state.lastSnapshot);
                renderDetail(state.lastSnapshot);
            }
        });
    }

    function activate() {
        if (state.active) return;
        state.active = true;
        setMode('idle', 'Connecting…');
        bindRowClicks();
        startStaleTicker();
        // Phase 9: One-shot events backfill (the WS 'snapshot' frame doesn't
        // carry events). After this primes state.eventSeq, WS ticks become
        // the sole live source until WS drops.
        fetchEventsOnce();
        startWebSocket();
        fetch('/api/timing/snapshot', { credentials: 'same-origin' })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (s) { if (s && state.active) applySnapshot(s); })
            .catch(function () {});
    }
    function deactivate() {
        if (!state.active) return;
        state.active = false;
        stopWebSocket(); stopPolling(); stopEventsPoll(); stopStaleTicker();
        setMode('idle', 'Idle');
    }

    function isLiveTimingVisible() {
        var el = document.getElementById('page-live-timing');
        return !!(el && !el.classList.contains('hidden'));
    }
    function maybeToggle() { if (isLiveTimingVisible()) activate(); else deactivate(); }

    function init() {
        var el = document.getElementById('page-live-timing');
        if (!el) return;
        var mo = new MutationObserver(maybeToggle);
        mo.observe(el, { attributes: true, attributeFilter: ['class'] });
        window.addEventListener('popstate', function () { setTimeout(maybeToggle, 0); });
        maybeToggle();
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
