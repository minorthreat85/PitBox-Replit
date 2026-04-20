/* PitBox native Live Timing client.
 *
 * Renders the Live Timing dashboard: agent connection bar, leaderboard with
 * live (sim-side) speed/gear/RPM/pit columns, track map (positions from sim
 * telemetry normalized_car_position), driver detail panel, and event stream.
 *
 * Activates only when the Live Timing page is visible (detected via the
 * `.hidden` class on `#page-live-timing`). Tries WebSocket first; if the WS
 * fails or closes, transparently falls back to HTTP polling against
 * /api/timing/snapshot. Stale state (no AC packets for >10s) is surfaced
 * both in the connection pill and in a banner.
 */
(function () {
    'use strict';

    var POLL_INTERVAL_MS = 1000;
    var STALE_THRESHOLD_S = 10;
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
    // Files live under /static/track_maps/<key>.json. The key is derived from
    // the AC `track_name` (lower-cased, non-alphanum -> underscore). If a
    // `track_config` (layout) is present we try `<track>__<config>` first and
    // fall back to bare `<track>`. Missing files quietly fall back to the
    // generic oval. Telemetry / norm_pos logic is unchanged — only rendering.
    function slugify(s) {
        return String(s || '').toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '');
    }
    function trackKeyCandidates(session) {
        if (!session || !session.track_name) return [];
        var t = slugify(session.track_name);
        if (!t) return [];
        var c = slugify(session.track_config);
        return c ? [t + '__' + c, t] : [t];
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
        var prevGap = 0;
        for (var i = 0; i < sorted.length; i++) {
            var d = sorted[i];
            var pos = d.position > 0 ? d.position : (i + 1);
            var posCls = pos === 1 ? 'lt-pos-1' : (pos === 2 ? 'lt-pos-2' : (pos === 3 ? 'lt-pos-3' : ''));
            var gapTxt = i === 0 ? '—' : fmtGap(d.gap_ms);
            var intervalMs = i === 0 ? 0 : (d.gap_ms - prevGap);
            var intervalTxt = i === 0 ? '—' : fmtGap(intervalMs);
            prevGap = d.gap_ms || prevGap;
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
            if (!lt || lt.norm_pos == null) continue;
            // norm_pos is 0..1 from AC. Apply per-track start_offset and
            // direction (rendering-layer transform only). Wrap into [0,1).
            var t = (dir * lt.norm_pos) + offset;
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

    function renderDetail(snapshot) {
        if (state.selectedCarId == null) return;
        var drivers = (snapshot && snapshot.drivers) || [];
        var d = null;
        for (var i = 0; i < drivers.length; i++) if (drivers[i].car_id === state.selectedCarId) { d = drivers[i]; break; }
        if (!d) return;
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
        $('lt-d-norm').textContent = lt.norm_pos != null ? (lt.norm_pos * 100).toFixed(1) + '%' : '—';
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
        state.lastSnapshot = snap;
        renderHeader(snap.session);
        renderAgents(snap);
        renderBoard(snap);
        renderMap(snap);
        renderDetail(snap);
        updateStaleBanner(snap.stats);
    }

    function updateStaleBanner(stats) {
        var banner = $('lt-stale-banner');
        var ageEl = $('lt-stale-age');
        if (!banner || !stats) return;
        var last = stats.last_packet_unix || 0;
        if (!last) {
            banner.classList.remove('hidden');
            if (ageEl) ageEl.textContent = 'any time yet';
            if (state.mode === 'ws') setMode('ws', 'WS connected (no data)');
            else if (state.mode === 'poll') setMode('poll', 'Polling (no data)');
            return;
        }
        var age = Math.max(0, (Date.now() / 1000) - last);
        if (age > STALE_THRESHOLD_S) {
            banner.classList.remove('hidden');
            if (ageEl) ageEl.textContent = age.toFixed(1) + 's';
            var pill = $('lt-conn');
            if (pill) {
                pill.classList.remove('lt-conn--ws', 'lt-conn--poll', 'lt-conn--idle');
                pill.classList.add('lt-conn--stale');
            }
            $('lt-conn-label').textContent = 'Stale (' + age.toFixed(0) + 's)';
        } else {
            banner.classList.add('hidden');
            if (state.mode === 'ws') setMode('ws', 'WS live');
            else if (state.mode === 'poll') setMode('poll', 'Polling');
        }
    }

    function startStaleTicker() {
        stopStaleTicker();
        state.staleTimer = setInterval(function () {
            if (state.lastSnapshot) updateStaleBanner(state.lastSnapshot.stats);
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

    function startEventsPoll() {
        stopEventsPoll();
        var tick = function () {
            fetch('/api/timing/events?since=' + state.eventSeq, { credentials: 'same-origin' })
                .then(function (r) { return r.ok ? r.json() : null; })
                .then(function (data) {
                    if (!data) return;
                    var added = (data.events || []);
                    if (added.length) {
                        state.events = state.events.concat(added);
                        if (state.events.length > 500) state.events = state.events.slice(-500);
                        state.eventSeq = data.next_seq || state.eventSeq;
                        renderEvents();
                    }
                })
                .catch(function () {});
        };
        tick();
        state.eventsTimer = setInterval(tick, EVENTS_POLL_MS);
    }
    function stopEventsPoll() { if (state.eventsTimer) { clearInterval(state.eventsTimer); state.eventsTimer = null; } }

    function startWebSocket() {
        if (typeof WebSocket === 'undefined') { startPolling(); return; }
        try {
            var proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
            var url = proto + '//' + location.host + '/ws/timing';
            var ws = new WebSocket(url);
            state.ws = ws;
            setMode('ws', 'WS connecting…');
            ws.addEventListener('open', function () { stopPolling(); setMode('ws', 'WS live'); });
            ws.addEventListener('message', function (ev) {
                var msg;
                try { msg = JSON.parse(ev.data); } catch (e) { return; }
                if (msg.type === 'snapshot') applySnapshot(msg.data);
                else if (msg.type === 'tick' && msg.snapshot) applySnapshot(msg.snapshot);
            });
            var fall = function () {
                state.ws = null;
                if (!state.active) return;
                startPolling();
                clearTimeout(state.wsRetryTimer);
                state.wsRetryTimer = setTimeout(startWebSocket, WS_RETRY_MS);
            };
            ws.addEventListener('close', fall);
            ws.addEventListener('error', function () { try { ws.close(); } catch (e) {} });
        } catch (e) { startPolling(); }
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
        startEventsPoll();
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
