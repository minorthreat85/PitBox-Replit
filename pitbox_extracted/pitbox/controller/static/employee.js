/**
 * Employee Control – mobile-first. Sim grid, bottom sheet, AUTO/MANUAL + Back to Pits.
 */
(function () {
    var API = '/api';
    var agents = [];
    var currentAgentId = null;
    var gridEl = document.getElementById('sim-grid');
    var overlayEl = document.getElementById('sheet-overlay');
    var sheetNameEl = document.getElementById('sheet-name');
    var sheetDotEl = document.getElementById('sheet-dot');
    var sheetOfflineEl = document.getElementById('sheet-offline-msg');
    var sheetControlsEl = document.getElementById('sheet-controls');
    var pillAuto = document.getElementById('pill-auto');
    var pillManual = document.getElementById('pill-manual');
    var btnBackPits = document.getElementById('btn-back-pits');
    var sheetClose = document.getElementById('sheet-close');
    var toastEl = document.getElementById('toast');
    var refreshBtn = document.getElementById('refresh-btn');
    var logoutBtn = document.getElementById('logout-btn');

    function escapeHtml(s) {
        if (s == null) return '';
        var t = document.createElement('div');
        t.textContent = s;
        return t.innerHTML;
    }

    /** Display-only track name formatter (same rules as app.js). */
    function formatTrackName(raw) {
        if (raw == null || (typeof raw === 'string' && raw.trim() === '')) return 'N/A';
        var s = String(raw).trim();
        if (s.toLowerCase() === 'n/a' || s === '—') return 'N/A';
        if (s.indexOf('/') !== -1 || s.indexOf('\\') !== -1) {
            s = s.replace(/\\/g, '/').split('/').filter(Boolean).pop() || s;
        }
        s = s.replace(/^ks_/i, '').replace(/^layout_/i, '').replace(/[-_]+/g, ' ').replace(/\s+/g, ' ').trim();
        if (!s) return 'N/A';
        var stop = { and: 1, or: 1, the: 1, of: 1, in: 1, at: 1, to: 1, a: 1 };
        var acronyms = ['gp', 'f1', 'usa', 'uk', 'gt3', 'gt4', 'dtm', 'srp'];
        s = s.split(' ').map(function (w, i) {
            var low = w.toLowerCase();
            if (!low) return '';
            if (acronyms.indexOf(low) !== -1) return low.toUpperCase();
            if (i !== 0 && stop[low]) return low;
            return low.charAt(0).toUpperCase() + low.slice(1);
        }).join(' ');
        return s;
    }

    function formatLayoutName(raw) {
        if (raw == null || (typeof raw === 'string' && raw.trim() === '')) return 'N/A';
        var cleaned = String(raw).trim();
        if (cleaned.toLowerCase() === 'n/a' || cleaned === '—') return 'N/A';
        if (cleaned.indexOf('/') !== -1 || cleaned.indexOf('\\') !== -1) {
            cleaned = cleaned.replace(/\\/g, '/').split('/').filter(Boolean).pop() || cleaned;
        }
        cleaned = cleaned.replace(/^ks_/i, '').replace(/^layout_/i, '').replace(/[-_]+/g, ' ').replace(/\s+/g, ' ').trim();
        if (!cleaned) return 'N/A';
        if (cleaned.toLowerCase() === 'default') return 'Default';
        var stop = { and: 1, or: 1, the: 1, of: 1, in: 1, at: 1, to: 1, a: 1 };
        var acronyms = ['gp', 'f1', 'usa', 'uk', 'gt3', 'gt4', 'dtm', 'srp'];
        return cleaned.split(' ').map(function (w, i) {
            var low = w.toLowerCase();
            if (!low) return '';
            if (acronyms.indexOf(low) !== -1) return low.toUpperCase();
            if (i !== 0 && stop[low]) return low;
            return low.charAt(0).toUpperCase() + low.slice(1);
        }).join(' ');
    }

    /** Display-only car name formatter (same rules as app.js). */
    function formatCarName(raw) {
        if (raw == null || (typeof raw === 'string' && !raw.trim())) return '—';
        var s = String(raw).trim();
        if (s.indexOf('/') !== -1 || s.indexOf('\\') !== -1) s = s.replace(/\\/g, '/').split('/').filter(Boolean).pop() || s;
        s = s.replace(/^ks_+/i, '').replace(/^nohesi_+/i, '').replace(/^traffic_+/i, '');
        s = s.replace(/[_-]+/g, ' ').replace(/\s+/g, ' ').trim();
        if (!s) return '—';
        var acronyms = { gt3: 1, gt4: 1, gt2: 1, gtd: 1, dtm: 1, f1: 1, f2: 1, f3: 1, mx5: 1, 'mx-5': 1, gtr: 1, nsx: 1, amg: 1, bmw: 1, audi: 1, porsche: 1, usa: 1, uk: 1, jdm: 1, v8: 1, v10: 1, v12: 1, tcr: 1, cup: 1, nx: 1, ks: 1 };
        var words = s.split(' ');
        var out = words.map(function (w) {
            var low = w.toLowerCase();
            if (low === 'mx5') return 'MX-5';
            if (low === 'mx') return 'MX';
            if (low === 'cup') return 'Cup';
            if (low === 'model') return 'Model';
            if (acronyms[low]) return low.toUpperCase();
            if (/^\d+$/.test(low)) return low;
            return low.charAt(0).toUpperCase() + low.slice(1);
        });
        return out.join(' ');
    }

    function showToast(message, type) {
        toastEl.textContent = message;
        toastEl.className = 'toast ' + (type || 'success');
        toastEl.classList.add('show');
        setTimeout(function () { toastEl.classList.remove('show'); }, 3000);
    }

    function getAgent(id) {
        for (var i = 0; i < agents.length; i++) if (agents[i].agent_id === id) return agents[i];
        return null;
    }

    function sessionSubtext(a) {
        var ls = a.last_session;
        if (!ls) return '';
        var parts = [];
        var trackRaw = (ls.track_name || ls.track_id || '').trim();
        var carRaw = (ls.car_name || ls.car_id || '').trim();
        if (trackRaw) parts.push(formatTrackName(trackRaw));
        if (carRaw) parts.push(formatCarName(carRaw));
        return parts.slice(0, 2).join(' · ') || '—';
    }

    function renderGrid() {
        if (!gridEl) return;
        gridEl.innerHTML = agents.map(function (a) {
            var name = (a.display_name || a.agent_id || '').trim() || a.agent_id;
            var online = !!a.online;
            var mode = (a.control_mode || 'AUTO').toUpperCase();
            var sub = sessionSubtext(a);
            return (
                '<div class="sim-card" data-agent-id="' + escapeHtml(a.agent_id) + '" role="button" tabindex="0">' +
                '<span class="name"><span class="dot ' + (online ? 'online' : 'offline') + '"></span>' + escapeHtml(name) + '</span>' +
                '<span class="pill">' + escapeHtml(mode) + '</span>' +
                '<span class="sub">' + escapeHtml(sub) + '</span>' +
                '</div>'
            );
        }).join('');
        if (agents.length === 0) gridEl.innerHTML = '<p style="grid-column:1/-1;color:var(--muted);">No sims. Enroll rigs from the main PitBox UI.</p>';
        gridEl.querySelectorAll('.sim-card').forEach(function (card) {
            card.addEventListener('click', function () {
                var id = card.getAttribute('data-agent-id');
                if (id) openSheet(id);
            });
        });
    }

    function openSheet(agentId) {
        currentAgentId = agentId;
        var a = getAgent(agentId);
        if (!a) return;
        var name = (a.display_name || a.agent_id || '').trim() || a.agent_id;
        var online = !!a.online;
        var mode = (a.control_mode || 'AUTO').toUpperCase();
        sheetNameEl.textContent = name;
        sheetDotEl.className = 'dot ' + (online ? 'online' : 'offline');
        sheetOfflineEl.style.display = online ? 'none' : 'block';
        sheetControlsEl.style.display = online ? 'block' : 'none';
        pillAuto.classList.toggle('active', mode === 'AUTO');
        pillManual.classList.toggle('active', mode === 'MANUAL');
        pillAuto.disabled = !online;
        pillManual.disabled = !online;
        btnBackPits.disabled = !online;
        overlayEl.classList.add('open');
    }

    function closeSheet() {
        overlayEl.classList.remove('open');
        currentAgentId = null;
    }

    function setPillLoading(loading) {
        pillAuto.disabled = loading;
        pillManual.disabled = loading;
        if (loading) {
            pillAuto.innerHTML = '<span class="spinner"></span> Sending…';
            pillManual.innerHTML = '';
        } else {
            var a = currentAgentId ? getAgent(currentAgentId) : null;
            var mode = (a && a.control_mode ? a.control_mode : 'AUTO').toUpperCase();
            pillAuto.textContent = 'AUTO';
            pillManual.textContent = 'MANUAL';
            pillAuto.classList.toggle('active', mode === 'AUTO');
            pillManual.classList.toggle('active', mode === 'MANUAL');
        }
    }

    function sendHotkey(action) {
        if (!currentAgentId) return;
        setPillLoading(true);
        fetch(API + '/agents/' + encodeURIComponent(currentAgentId) + '/hotkey', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: action }),
            credentials: 'same-origin'
        })
            .then(function (r) {
                return r.json().then(function (data) {
                    if (!r.ok) throw new Error(data.detail || data.message || 'Request failed');
                    return data;
                });
            })
            .then(function (data) {
                var a = getAgent(currentAgentId);
                if (a) a.control_mode = data.control_mode || (action === 'toggle_manual' ? (a.control_mode === 'AUTO' ? 'MANUAL' : 'AUTO') : a.control_mode);
                var mode = (a && a.control_mode) ? a.control_mode : 'AUTO';
                showToast('Switched to ' + mode, 'success');
                setPillLoading(false);
            })
            .catch(function (err) {
                showToast(err.message || 'Failed', 'error');
                setPillLoading(false);
            });
    }

    function fetchStatus() {
        fetch(API + '/status', { credentials: 'same-origin' })
            .then(function (r) {
                if (r.status === 401) { window.location.href = '/employee/login'; return; }
                if (!r.ok) throw new Error('Status ' + r.status);
                return r.json();
            })
            .then(function (data) {
                agents = data.agents || [];
                renderGrid();
                if (currentAgentId) openSheet(currentAgentId);
            })
            .catch(function (err) {
                if (gridEl) gridEl.innerHTML = '<p style="grid-column:1/-1;color:#e66;">Failed to load: ' + escapeHtml(err.message) + '</p>';
            });
    }

    pillAuto.addEventListener('click', function () {
        if (pillAuto.classList.contains('active')) return;
        sendHotkey('toggle_manual');
    });
    pillManual.addEventListener('click', function () {
        if (pillManual.classList.contains('active')) return;
        sendHotkey('toggle_manual');
    });
    btnBackPits.addEventListener('click', function () {
        if (!currentAgentId) return;
        btnBackPits.disabled = true;
        btnBackPits.textContent = 'Sending…';
        fetch(API + '/agents/' + encodeURIComponent(currentAgentId) + '/hotkey', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'back_to_pits' }),
            credentials: 'same-origin'
        })
            .then(function (r) {
                return r.json().then(function (data) {
                    if (!r.ok) throw new Error(data.detail || data.message || 'Request failed');
                    return data;
                });
            })
            .then(function () {
                showToast('Back to pits sent', 'success');
                btnBackPits.textContent = 'Back to Pits (Ctrl+P)';
                btnBackPits.disabled = false;
            })
            .catch(function (err) {
                showToast(err.message || 'Failed', 'error');
                btnBackPits.textContent = 'Back to Pits (Ctrl+P)';
                btnBackPits.disabled = false;
            });
    });
    sheetClose.addEventListener('click', closeSheet);
    overlayEl.addEventListener('click', function (e) {
        if (e.target === overlayEl) closeSheet();
    });
    refreshBtn.addEventListener('click', function () { fetchStatus(); });
    document.getElementById('sheet-refresh-btn').addEventListener('click', function () {
        fetchStatus();
        showToast('Status refreshed', 'success');
    });
    logoutBtn.addEventListener('click', function () {
        fetch(API + '/employee/logout', { method: 'POST', credentials: 'same-origin' })
            .then(function () { window.location.href = '/employee/login'; });
    });

    fetchStatus();
})();
