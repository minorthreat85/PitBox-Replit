/* PitBox native Live Timing client.
 *
 * Activates only when the Live Timing page is visible (detected via the
 * `.hidden` class on `#page-live-timing`). Tries WebSocket first; if the WS
 * fails or closes for any reason, transparently falls back to HTTP polling
 * against /api/timing/snapshot. Stale state (no AC packets for >10s) is
 * surfaced both in the connection pill and in a banner so operators can tell
 * at a glance whether the server is silent.
 */
(function () {
    'use strict';

    var POLL_INTERVAL_MS = 1000;
    var STALE_THRESHOLD_S = 10;
    var WS_RETRY_MS = 5000;

    var state = {
        active: false,
        ws: null,
        wsRetryTimer: null,
        pollTimer: null,
        staleTimer: null,
        mode: 'idle', // 'idle' | 'ws' | 'poll' | 'stale'
        lastSnapshot: null,
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
        if (session.session_type === 3 && session.laps > 0) {
            // Race-by-laps: just show lap count.
            return session.laps + ' laps';
        }
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

    function statusPill(driver) {
        if (!driver.connected) return '<span class="lt-status lt-status--offline">Offline</span>';
        if (!driver.loaded) return '<span class="lt-status lt-status--loading">Loading</span>';
        return '<span class="lt-status lt-status--on">On Track</span>';
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
    }

    function renderBoard(snapshot) {
        var tbody = $('lt-rows');
        if (!tbody) return;
        var drivers = (snapshot && snapshot.drivers) || [];
        var connected = drivers.filter(function (d) { return d.connected; });
        if (connected.length === 0) {
            tbody.innerHTML = '<tr class="lt-empty-row"><td colspan="9">Waiting for AC server telemetry…</td></tr>';
            return;
        }
        // Compute interval (gap to car directly ahead) using gap-to-leader.
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
            rowsHtml += '<tr>'
                + '<td class="lt-col-pos ' + posCls + '">' + pos + '</td>'
                + '<td class="lt-col-driver">' + escapeHtml(d.driver_name || ('Car ' + d.car_id)) + '</td>'
                + '<td class="lt-col-car">' + escapeHtml(d.car_model || '—') + '</td>'
                + '<td class="lt-col-laps">' + (d.total_laps || 0) + '</td>'
                + '<td class="lt-col-best lt-best-cell">' + fmtLap(d.best_lap_ms) + '</td>'
                + '<td class="lt-col-last">' + fmtLap(d.last_lap_ms) + '</td>'
                + '<td class="lt-col-gap">' + gapTxt + '</td>'
                + '<td class="lt-col-int">' + intervalTxt + '</td>'
                + '<td class="lt-col-status">' + statusPill(d) + '</td>'
                + '</tr>';
        }
        tbody.innerHTML = rowsHtml;
    }

    function applySnapshot(snap) {
        if (!snap) return;
        state.lastSnapshot = snap;
        renderHeader(snap.session);
        renderBoard(snap);
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
            if (state.mode !== 'stale') {
                // Don't overwrite mode permanently; just relabel pill.
                var pill = $('lt-conn');
                if (pill) {
                    pill.classList.remove('lt-conn--ws', 'lt-conn--poll', 'lt-conn--idle');
                    pill.classList.add('lt-conn--stale');
                }
                $('lt-conn-label').textContent = 'Stale (' + age.toFixed(0) + 's)';
            }
        } else {
            banner.classList.add('hidden');
            // Restore pill colour based on transport
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
    function stopStaleTicker() {
        if (state.staleTimer) { clearInterval(state.staleTimer); state.staleTimer = null; }
    }

    // ---- Polling fallback ----
    function startPolling() {
        stopPolling();
        setMode('poll', 'Polling');
        var tick = function () {
            fetch('/api/timing/snapshot', { credentials: 'same-origin' })
                .then(function (r) { return r.ok ? r.json() : null; })
                .then(function (snap) { if (snap) applySnapshot(snap); })
                .catch(function () { /* keep trying */ });
        };
        tick();
        state.pollTimer = setInterval(tick, POLL_INTERVAL_MS);
    }
    function stopPolling() {
        if (state.pollTimer) { clearInterval(state.pollTimer); state.pollTimer = null; }
    }

    // ---- WebSocket ----
    function startWebSocket() {
        if (typeof WebSocket === 'undefined') { startPolling(); return; }
        try {
            var proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
            var url = proto + '//' + location.host + '/ws/timing';
            var ws = new WebSocket(url);
            state.ws = ws;
            setMode('ws', 'WS connecting…');
            ws.addEventListener('open', function () {
                stopPolling(); // WS now drives updates
                setMode('ws', 'WS live');
            });
            ws.addEventListener('message', function (ev) {
                var msg;
                try { msg = JSON.parse(ev.data); } catch (e) { return; }
                if (msg.type === 'snapshot') applySnapshot(msg.data);
                else if (msg.type === 'tick' && msg.snapshot) applySnapshot(msg.snapshot);
            });
            var fall = function () {
                state.ws = null;
                if (!state.active) return;
                // Fall back to polling immediately so the UI keeps updating,
                // and keep trying to upgrade back to WS in the background.
                startPolling();
                clearTimeout(state.wsRetryTimer);
                state.wsRetryTimer = setTimeout(startWebSocket, WS_RETRY_MS);
            };
            ws.addEventListener('close', fall);
            ws.addEventListener('error', function () { try { ws.close(); } catch (e) {} });
        } catch (e) {
            startPolling();
        }
    }

    function stopWebSocket() {
        clearTimeout(state.wsRetryTimer);
        state.wsRetryTimer = null;
        if (state.ws) {
            try { state.ws.close(); } catch (e) {}
            state.ws = null;
        }
    }

    function activate() {
        if (state.active) return;
        state.active = true;
        setMode('idle', 'Connecting…');
        startStaleTicker();
        startWebSocket();
        // Also kick off a one-shot poll so the page isn't blank for the first
        // ~half-second while the WS handshake is in flight.
        fetch('/api/timing/snapshot', { credentials: 'same-origin' })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (s) { if (s && state.active) applySnapshot(s); })
            .catch(function () {});
    }

    function deactivate() {
        if (!state.active) return;
        state.active = false;
        stopWebSocket();
        stopPolling();
        stopStaleTicker();
        setMode('idle', 'Idle');
    }

    // Watch for the Live Timing page becoming visible. The host app toggles
    // `.hidden` on each `.content-page` div from app.js's showPage().
    function isLiveTimingVisible() {
        var el = document.getElementById('page-live-timing');
        return !!(el && !el.classList.contains('hidden'));
    }

    function maybeToggle() {
        if (isLiveTimingVisible()) activate();
        else deactivate();
    }

    function init() {
        var el = document.getElementById('page-live-timing');
        if (!el) return;
        var mo = new MutationObserver(maybeToggle);
        mo.observe(el, { attributes: true, attributeFilter: ['class'] });
        // Also re-evaluate on history navigation (the SPA uses history.pushState).
        window.addEventListener('popstate', function () { setTimeout(maybeToggle, 0); });
        maybeToggle();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
