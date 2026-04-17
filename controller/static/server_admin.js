/* PitBox native server-admin panel.
 *
 * Wires the ADMIN tab on /server-config to the /api/server/* command
 * endpoints (chat, kick, next-session, restart-session, grid reverse /
 * swap, generic /admin command). Polls /api/server/info every 2s while
 * the tab is visible to keep the live driver list and connection state
 * fresh. All mutations route through PitBox's own backend; nothing here
 * talks to acServer or any third-party server directly.
 */
(function () {
    'use strict';

    var REFRESH_MS = 2000;

    var state = {
        active: false,
        serverId: null,
        timer: null,
        info: null,
    };

    function $(id) { return document.getElementById(id); }

    function withBusy(button, promise) {
        // Disable a button (and add an in-progress class) for the lifetime
        // of an API request, so users get visual feedback. Caller is the
        // owner of the promise; we always re-enable.
        if (!button) return promise;
        button.disabled = true;
        button.classList.add('sc-admin-busy');
        var done = function () {
            button.disabled = false;
            button.classList.remove('sc-admin-busy');
        };
        promise.then(done, done);
        return promise;
    }

    function api(method, path, body) {
        var opts = { method: method, credentials: 'same-origin', headers: {} };
        if (body !== undefined) {
            opts.headers['Content-Type'] = 'application/json';
            opts.body = JSON.stringify(body);
        }
        return fetch(path, opts).then(function (r) {
            return r.json().then(function (data) {
                if (!r.ok) {
                    var msg = (data && data.detail) ? data.detail : ('HTTP ' + r.status);
                    var err = new Error(msg); err.status = r.status; err.data = data;
                    throw err;
                }
                return data;
            }, function () {
                if (!r.ok) {
                    var err = new Error('HTTP ' + r.status); err.status = r.status; throw err;
                }
                return null;
            });
        });
    }

    function setError(msg) {
        var box = $('sc-admin-error');
        if (!box) return;
        if (!msg) { box.classList.add('hidden'); box.textContent = ''; return; }
        box.classList.remove('hidden');
        box.textContent = msg;
    }

    function flashOK(label) {
        // Briefly show a green confirmation in the same banner slot.
        var box = $('sc-admin-error');
        if (!box) return;
        box.classList.remove('hidden');
        box.classList.add('sc-admin-ok');
        box.textContent = label;
        setTimeout(function () {
            box.classList.add('hidden');
            box.classList.remove('sc-admin-ok');
            box.textContent = '';
        }, 1800);
    }

    function getActiveServerId() {
        // The server-config SPA holds the selected preset in the
        // #sc-instance dropdown (see app.js). Fall back to dataset attrs
        // on the page wrapper if a future refactor exposes them there.
        var inst = $('sc-instance');
        if (inst && inst.value) return inst.value;
        var pg = $('page-server-config');
        if (pg && (pg.dataset.activeServerId || pg.dataset.serverId)) {
            return pg.dataset.activeServerId || pg.dataset.serverId;
        }
        return null;
    }

    // ------------------------------------------------------------------ //
    // Render
    // ------------------------------------------------------------------ //
    function renderInfo(info) {
        state.info = info;
        var stateEl = $('sc-admin-state');
        var targetEl = $('sc-admin-target');
        var running = info && info.process && info.process.running;
        if (stateEl) {
            stateEl.classList.remove('sc-status-stopped', 'sc-status-running', 'sc-status-error');
            if (running) {
                stateEl.classList.add('sc-status-running');
                stateEl.textContent = 'Running (PID ' + (info.process.pid || '?') + ')';
            } else if (info && info.process && info.process.status === 'crashed') {
                stateEl.classList.add('sc-status-error');
                stateEl.textContent = 'Crashed';
            } else {
                stateEl.classList.add('sc-status-stopped');
                stateEl.textContent = 'Stopped';
            }
        }
        if (targetEl) {
            if (info && info.udp_admin_target) {
                targetEl.textContent = 'admin UDP \u2192 ' + info.udp_admin_target.host + ':' + info.udp_admin_target.port;
            } else if (info && info.udp_admin_target_error) {
                targetEl.textContent = info.udp_admin_target_error;
            } else {
                targetEl.textContent = '';
            }
        }
        renderDrivers(info);
    }

    function renderDrivers(info) {
        var tbody = $('sc-admin-drivers');
        var sel = $('sc-admin-chat-target');
        if (!tbody) return;
        var drivers = (info && info.telemetry && info.telemetry.drivers) || [];
        var connected = drivers.filter(function (d) { return d.connected; });
        if (connected.length === 0) {
            tbody.innerHTML = '<tr><td colspan="6" class="sc-admin-empty">No connected drivers.</td></tr>';
        } else {
            var html = '';
            connected.forEach(function (d) {
                var status = d.loaded ? 'On Track' : 'Loading';
                html += '<tr>'
                    + '<td>' + d.car_id + '</td>'
                    + '<td>' + escapeHtml(d.driver_name || ('Car ' + d.car_id)) + '</td>'
                    + '<td>' + escapeHtml(d.car_model || '\u2014') + '</td>'
                    + '<td>' + (d.total_laps || 0) + '</td>'
                    + '<td>' + status + '</td>'
                    + '<td><button type="button" class="sc-header-btn sc-admin-kick" data-car-id="' + d.car_id + '">Kick</button></td>'
                    + '</tr>';
            });
            tbody.innerHTML = html;
            // Wire kick buttons (delegation per refresh)
            tbody.querySelectorAll('.sc-admin-kick').forEach(function (btn) {
                btn.addEventListener('click', function () {
                    var cid = parseInt(btn.getAttribute('data-car-id'), 10);
                    doKick(cid, btn);
                });
            });
        }
        if (sel) {
            // Preserve current selection
            var prev = sel.value;
            var opts = ['<option value="">Broadcast (everyone)</option>'];
            connected.forEach(function (d) {
                opts.push('<option value="' + d.car_id + '">Car ' + d.car_id + ' \u2014 ' + escapeHtml(d.driver_name || '') + '</option>');
            });
            sel.innerHTML = opts.join('');
            if (prev) sel.value = prev;
        }
    }

    function renderGrid(payload) {
        var tbody = $('sc-admin-grid');
        var selA = $('sc-admin-grid-a');
        var selB = $('sc-admin-grid-b');
        if (!tbody) return;
        var entries = (payload && payload.entries) || [];
        if (!entries.length) {
            tbody.innerHTML = '<tr><td colspan="4" class="sc-admin-empty">No entries.</td></tr>';
        } else {
            var html = '';
            entries.forEach(function (e) {
                html += '<tr>'
                    + '<td>' + e.slot + '</td>'
                    + '<td>' + escapeHtml(e.drivername || '\u2014') + '</td>'
                    + '<td>' + escapeHtml(e.model || '\u2014') + '</td>'
                    + '<td>' + escapeHtml(e.skin || '\u2014') + '</td>'
                    + '</tr>';
            });
            tbody.innerHTML = html;
        }
        var optsHtml = entries.map(function (e) {
            var label = 'Slot ' + e.slot + ' \u2014 ' + (e.drivername || ('CAR_' + e.car_id_original));
            return '<option value="' + e.slot + '">' + escapeHtml(label) + '</option>';
        }).join('');
        if (selA) selA.innerHTML = optsHtml;
        if (selB) selB.innerHTML = optsHtml;
        if (selB && entries.length > 1) selB.value = String(entries[entries.length - 1].slot);
    }

    function escapeHtml(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    // ------------------------------------------------------------------ //
    // Actions
    // ------------------------------------------------------------------ //
    function refreshInfo() {
        var sid = state.serverId;
        if (!sid) return Promise.resolve();
        return api('GET', '/api/server/info?server_id=' + encodeURIComponent(sid))
            .then(renderInfo)
            .catch(function (e) { setError('Info: ' + e.message); });
    }

    function refreshGrid() {
        var sid = state.serverId;
        if (!sid) return Promise.resolve();
        return api('GET', '/api/server/grid?server_id=' + encodeURIComponent(sid))
            .then(renderGrid)
            .catch(function (e) { setError('Grid: ' + e.message); });
    }

    function sendChat() {
        var sid = state.serverId; if (!sid) return;
        var msg = ($('sc-admin-chat-msg') || {}).value || '';
        msg = msg.trim();
        if (!msg) return;
        var target = ($('sc-admin-chat-target') || {}).value || '';
        var body = { server_id: sid, message: msg };
        if (target !== '') body.car_id = parseInt(target, 10);
        withBusy($('sc-admin-chat-send'),
            api('POST', '/api/server/chat', body)
                .then(function () { $('sc-admin-chat-msg').value = ''; flashOK('Chat sent'); })
                .catch(function (e) { setError('Chat: ' + e.message); }));
    }

    function doKick(carId, btn) {
        if (carId === undefined || carId === null || isNaN(carId)) return;
        if (!confirm('Kick car ' + carId + '?')) return;
        var sid = state.serverId; if (!sid) return;
        if (btn) btn.disabled = true;
        api('POST', '/api/server/kick', { server_id: sid, car_id: carId })
            .then(function () { flashOK('Kicked car ' + carId); refreshInfo(); })
            .catch(function (e) { setError('Kick: ' + e.message); })
            .then(function () { if (btn) btn.disabled = false; });
    }

    function nextSession() {
        var sid = state.serverId; if (!sid) return;
        if (!confirm('Advance to the next session?')) return;
        withBusy($('sc-admin-next-session'),
            api('POST', '/api/server/next-session', { server_id: sid })
                .then(function () { flashOK('Next session triggered'); refreshInfo(); })
                .catch(function (e) { setError('Next session: ' + e.message); }));
    }

    function restartSession() {
        var sid = state.serverId; if (!sid) return;
        if (!confirm('Restart the current session?')) return;
        withBusy($('sc-admin-restart-session'),
            api('POST', '/api/server/restart-session', { server_id: sid })
                .then(function () { flashOK('Session restarted'); refreshInfo(); })
                .catch(function (e) { setError('Restart session: ' + e.message); }));
    }

    function reverseGrid() {
        var sid = state.serverId; if (!sid) return;
        if (!confirm('Reverse the grid in entry_list.ini? Takes effect on next start.')) return;
        withBusy($('sc-admin-grid-reverse'),
            api('POST', '/api/server/grid/reverse', { server_id: sid })
                .then(function (r) { flashOK('Grid reversed' + (r.warning ? ' (server running \u2014 restart needed)' : '')); refreshGrid(); })
                .catch(function (e) { setError('Reverse: ' + e.message); }));
    }

    function swapGrid() {
        var sid = state.serverId; if (!sid) return;
        var a = parseInt(($('sc-admin-grid-a') || {}).value, 10);
        var b = parseInt(($('sc-admin-grid-b') || {}).value, 10);
        if (isNaN(a) || isNaN(b) || a === b) { setError('Pick two different slots to swap.'); return; }
        withBusy($('sc-admin-grid-swap'),
            api('POST', '/api/server/grid/swap', { server_id: sid, slot_a: a, slot_b: b })
                .then(function (r) { flashOK('Swapped slots ' + a + ' \u2194 ' + b + (r.warning ? ' (restart needed)' : '')); refreshGrid(); })
                .catch(function (e) { setError('Swap: ' + e.message); }));
    }

    function sendAdminRaw() {
        var sid = state.serverId; if (!sid) return;
        var cmd = ($('sc-admin-raw-cmd') || {}).value || '';
        cmd = cmd.trim();
        if (!cmd) return;
        withBusy($('sc-admin-raw-send'),
            api('POST', '/api/server/admin', { server_id: sid, command: cmd })
                .then(function () { $('sc-admin-raw-cmd').value = ''; flashOK('Admin command sent: ' + cmd); })
                .catch(function (e) { setError('Admin: ' + e.message); }));
    }

    // ------------------------------------------------------------------ //
    // Lifecycle
    // ------------------------------------------------------------------ //
    function activate() {
        if (state.active) return;
        state.active = true;
        state.serverId = getActiveServerId();
        setError('');
        refreshInfo();
        refreshGrid();
        if (state.timer) clearInterval(state.timer);
        state.timer = setInterval(refreshInfo, REFRESH_MS);
    }

    function deactivate() {
        if (!state.active) return;
        state.active = false;
        if (state.timer) { clearInterval(state.timer); state.timer = null; }
    }

    function isAdminTabVisible() {
        var panel = $('sc-panel-admin');
        return !!(panel && !panel.classList.contains('hidden'));
    }

    function maybeToggle() {
        if (isAdminTabVisible()) activate();
        else deactivate();
    }

    function bindClicks() {
        var bind = function (id, fn) { var el = $(id); if (el) el.addEventListener('click', fn); };
        bind('sc-admin-refresh', function () { setError(''); refreshInfo(); refreshGrid(); });
        bind('sc-admin-chat-send', sendChat);
        bind('sc-admin-next-session', nextSession);
        bind('sc-admin-restart-session', restartSession);
        bind('sc-admin-grid-refresh', function () { setError(''); refreshGrid(); });
        bind('sc-admin-grid-reverse', reverseGrid);
        bind('sc-admin-grid-swap', swapGrid);
        bind('sc-admin-raw-send', sendAdminRaw);
        var msg = $('sc-admin-chat-msg');
        if (msg) msg.addEventListener('keydown', function (ev) { if (ev.key === 'Enter') sendChat(); });
        var raw = $('sc-admin-raw-cmd');
        if (raw) raw.addEventListener('keydown', function (ev) { if (ev.key === 'Enter') sendAdminRaw(); });
    }

    function init() {
        var panel = $('sc-panel-admin');
        if (!panel) return;
        bindClicks();
        var mo = new MutationObserver(maybeToggle);
        mo.observe(panel, { attributes: true, attributeFilter: ['class'] });
        // The selected server is tracked on #page-server-config; re-evaluate
        // when its dataset changes so we follow preset switches.
        // Watch the actual SPA selector and any dataset hints on the page.
        var inst = $('sc-instance');
        var onSelectionChange = function () {
            var sid = getActiveServerId();
            if (sid !== state.serverId) {
                state.serverId = sid;
                if (state.active) { setError(''); refreshInfo(); refreshGrid(); }
            }
        };
        if (inst) inst.addEventListener('change', onSelectionChange);
        var pg = $('page-server-config');
        if (pg) {
            var pgMo = new MutationObserver(onSelectionChange);
            pgMo.observe(pg, { attributes: true, attributeFilter: ['data-active-server-id', 'data-server-id'] });
        }
        maybeToggle();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
