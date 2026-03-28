/**
 * PitBox - Rigs page
 * Stats, rig selector grid, preset dropdowns (steering/shifting), sim cards (available/offline).
 */

(function () {
  const API_BASE = '/api';

  var operatorSessionCache = { loaded: false, employee_login_enabled: false, logged_in: false };

  function getLoginNextUrl() {
    return encodeURIComponent(window.location.pathname + window.location.search);
  }

  function redirectToEmployeeLogin() {
    var p = window.location.pathname || '';
    if (p === '/employee/login' || p.indexOf('/employee/login') === 0) return;
    window.location.href = '/employee/login?next=' + getLoginNextUrl();
  }

  /** Session probe only: avoids pitboxFetch 401/403 redirect recursion. */
  function pitboxFetchBare(url, init) {
    init = init || {};
    if (init.credentials === undefined) init.credentials = 'same-origin';
    return fetch(url, init);
  }

  function loadOperatorSession() {
    return pitboxFetchBare(API_BASE + '/employee/session')
      .then(function (r) {
        return r.ok ? r.json() : { employee_login_enabled: false, logged_in: false, login_required_for_control: false };
      })
      .then(function (s) {
        operatorSessionCache.loaded = true;
        operatorSessionCache.employee_login_enabled = !!s.employee_login_enabled;
        operatorSessionCache.logged_in = !!s.logged_in;
        return operatorSessionCache;
      })
      .catch(function () {
        operatorSessionCache.loaded = true;
        operatorSessionCache.employee_login_enabled = false;
        operatorSessionCache.logged_in = false;
        return operatorSessionCache;
      });
  }

  function operatorControlBlocked() {
    return operatorSessionCache.loaded && operatorSessionCache.employee_login_enabled && !operatorSessionCache.logged_in;
  }

  function ensureOperatorOrRedirect() {
    return loadOperatorSession().then(function (s) {
      if (s.employee_login_enabled && !s.logged_in) {
        redirectToEmployeeLogin();
        return false;
      }
      return true;
    });
  }

  /** Same-origin cookies (operator /employee session) for all PitBox API calls. */
  function pitboxFetch(url, init) {
    init = init || {};
    if (init.credentials === undefined) init.credentials = 'same-origin';
    var u = typeof url === 'string' ? url : '';
    var skipAuthRedirect =
      u.indexOf(API_BASE + '/employee/session') !== -1 ||
      u.indexOf(API_BASE + '/employee/login') !== -1;
    return fetch(url, init).then(function (r) {
      if ((r.status === 401 || r.status === 403) && u.indexOf(API_BASE) === 0 && !skipAuthRedirect) {
        if ((window.location.pathname || '').indexOf('/employee/login') === 0) return r;
        return loadOperatorSession().then(function (s) {
          if (s.employee_login_enabled) {
            redirectToEmployeeLogin();
          }
          return r;
        });
      }
      return r;
    });
  }

  const grid = document.getElementById('sim-grid');
  const lastUpdateEl = document.getElementById('last-update');
  const toastContainer = document.getElementById('toast-container');
  const connectionBanner = document.getElementById('connection-banner');
  var lastFetchTime = null;

  var liveTimingPollTimer = null;
  var LIVE_TIMING_POLL_MS = 500;

  /** When true, background polling (timing, enrollment, revision, logs, status) is skipped to avoid competing with launch/exit. */
  var launchBusy = false;

  var PITBOX_DEBUG = false;

  var statusPollTimer = null;
  var statusPollInFlight = false;
  var STATUS_POLL_VISIBLE_MS = 3000;
  var STATUS_POLL_HIDDEN_MS = 15000;

  /** Debounced UI-triggered status refresh; coalesces with at most one immediate rerun when a fetch is already in flight. */
  var fetchStatusScheduleTimer = null;
  var FETCH_STATUS_SCHEDULE_DEBOUNCE_MS = 200;
  var fetchStatusCoreInFlight = false;
  var fetchStatusPendingCoalesced = false;

  var presetDiskStateCache = new Map();
  var presetDiskStateInflight = new Map();
  var presetDiskStatesBatchInflight = new Map();
  var PRESET_DISK_STATE_TTL_MS = 5000;
  var PRESET_DISK_STATE_BATCH_MAX = 64;

  var dashboardServerConfigCache = { ts: 0, sid: null, data: null };
  var dashboardProcessStatusCache = { ts: 0, data: null };
  var DASHBOARD_SUBFETCH_TTL_MS = 5000;

  var SELECTED_SERVER_STORAGE_KEY = 'pitbox_selected_server_id';

  /** Debounce timer for AC server process-status refresh (avoids double-fetch on start/stop). */
  var acServerStatusRefreshTimer = null;
  /** Schedule a single refresh of /server-config/process-status after delay ms. Replaces repeated immediate refreshes. */
  function scheduleAcServerStatusRefresh(delay) {
    if (typeof delay === 'undefined') delay = 250;
    if (acServerStatusRefreshTimer) clearTimeout(acServerStatusRefreshTimer);
    acServerStatusRefreshTimer = setTimeout(function () {
      acServerStatusRefreshTimer = null;
      if (typeof refreshAcServerStatus === 'function') refreshAcServerStatus();
    }, delay);
  }

  function invalidatePresetDiskStateClient(presetId) {
    if (presetId == null || !String(presetId).trim()) return;
    presetDiskStateCache.delete(String(presetId).trim());
    presetDiskStateInflight.delete(String(presetId).trim());
    presetDiskStatesBatchInflight.clear();
  }

  function getPresetDiskStatesCached(presetIds, options) {
    options = options || {};
    var raw = Array.isArray(presetIds) ? presetIds : [];
    var seen = new Set();
    var unique = [];
    for (var i = 0; i < raw.length; i++) {
      var s = String(raw[i] || '').trim();
      if (!s || seen.has(s)) continue;
      seen.add(s);
      unique.push(s);
    }
    if (unique.length === 0) return Promise.resolve({});

    var now = Date.now();
    var out = {};
    var need = [];
    for (var j = 0; j < unique.length; j++) {
      var pid = unique[j];
      if (!options.noCache) {
        var hit = presetDiskStateCache.get(pid);
        if (hit && (now - hit.ts) < PRESET_DISK_STATE_TTL_MS) {
          out[pid] = hit.data;
          continue;
        }
      }
      need.push(pid);
    }
    if (need.length === 0) return Promise.resolve(out);

    if (need.length > PRESET_DISK_STATE_BATCH_MAX) {
      var chunksNeed = [];
      for (var cn = 0; cn < need.length; cn += PRESET_DISK_STATE_BATCH_MAX) {
        chunksNeed.push(need.slice(cn, cn + PRESET_DISK_STATE_BATCH_MAX));
      }
      return Promise.all(chunksNeed.map(function (ch) {
        return getPresetDiskStatesCached(ch, options);
      })).then(function (parts) {
        var mergedAll = Object.assign({}, out);
        for (var pi = 0; pi < parts.length; pi++) {
          Object.assign(mergedAll, parts[pi]);
        }
        return mergedAll;
      });
    }

    if (need.length === 1) {
      var one = need[0];
      var inflightOne = presetDiskStateInflight.get(one);
      if (inflightOne) {
        return inflightOne.then(function (data) {
          var r = Object.assign({}, out);
          r[one] = data;
          return r;
        });
      }
      var p1 = pitboxFetch(API_BASE + '/preset/' + encodeURIComponent(one) + '/disk_state')
        .then(function (r) {
          if (!r.ok) {
            return r.json().then(function (d) {
              throw new Error((d && (d.detail || d.message)) || 'Failed');
            });
          }
          return r.json();
        })
        .then(function (data) {
          presetDiskStateCache.set(one, { ts: Date.now(), data: data });
          return data;
        })
        .finally(function () {
          presetDiskStateInflight.delete(one);
        });
      presetDiskStateInflight.set(one, p1);
      return p1.then(function (data) {
        var r = Object.assign({}, out);
        r[one] = data;
        return r;
      });
    }

    var sortedKey = need.slice().sort().join('\t');
    var existingBatch = presetDiskStatesBatchInflight.get(sortedKey);
    if (existingBatch) {
      return existingBatch.then(function (batchMap) {
        return Object.assign({}, out, batchMap);
      });
    }
    var q = 'ids=' + need.map(function (id) { return encodeURIComponent(id); }).join(',');
    var pb = pitboxFetch(API_BASE + '/presets/disk_state?' + q)
      .then(function (r) {
        if (!r.ok) {
          return r.json().then(function (d) {
            throw new Error((d && (d.detail || d.message)) || 'Failed');
          });
        }
        return r.json();
      })
      .then(function (body) {
        var results = body && body.results ? body.results : {};
        var merged = {};
        for (var k = 0; k < need.length; k++) {
          var pk = need[k];
          if (results[pk] != null) {
            merged[pk] = results[pk];
            presetDiskStateCache.set(pk, { ts: Date.now(), data: results[pk] });
          }
        }
        return merged;
      })
      .finally(function () {
        presetDiskStatesBatchInflight.delete(sortedKey);
      });
    presetDiskStatesBatchInflight.set(sortedKey, pb);
    return pb.then(function (batchMap) {
      return Object.assign({}, out, batchMap);
    });
  }

  function getPresetDiskStateCached(presetId, options) {
    var pid = String(presetId || '').trim();
    if (!pid) return Promise.reject(new Error('Invalid preset id'));
    return getPresetDiskStatesCached([pid], options).then(function (m) {
      var d = m[pid];
      if (d == null) return Promise.reject(new Error('Failed'));
      return d;
    });
  }

  function getDashboardServerConfigCached(serverId) {
    var sid = String(serverId || 'default').trim() || 'default';
    var now = Date.now();
    if (dashboardServerConfigCache.sid === sid && (now - dashboardServerConfigCache.ts) < DASHBOARD_SUBFETCH_TTL_MS && dashboardServerConfigCache.data != null) {
      return Promise.resolve(dashboardServerConfigCache.data);
    }
    return pitboxFetch(API_BASE + '/server-config?server_id=' + encodeURIComponent(sid))
      .then(function (r) { return r.ok ? r.json() : {}; })
      .then(function (d) {
        dashboardServerConfigCache = { ts: Date.now(), sid: sid, data: d };
        return d;
      })
      .catch(function () { return {}; });
  }

  function getDashboardProcessStatusCached() {
    var now = Date.now();
    if (dashboardProcessStatusCache.data != null && (now - dashboardProcessStatusCache.ts) < DASHBOARD_SUBFETCH_TTL_MS) {
      return Promise.resolve(dashboardProcessStatusCache.data);
    }
    return pitboxFetch(API_BASE + '/server-config/process-status')
      .then(function (r) { return r.ok ? r.json() : {}; })
      .then(function (d) {
        dashboardProcessStatusCache = { ts: Date.now(), data: d };
        return d;
      })
      .catch(function () { return {}; });
  }

  function getSelectedServerId() {
    try {
      var id = localStorage.getItem(SELECTED_SERVER_STORAGE_KEY);
      return (id && String(id).trim()) ? String(id).trim() : null;
    } catch (e) { return null; }
  }
  function setSelectedServerId(id) {
    try {
      if (id && String(id).trim()) localStorage.setItem(SELECTED_SERVER_STORAGE_KEY, String(id).trim());
      else localStorage.removeItem(SELECTED_SERVER_STORAGE_KEY);
    } catch (e) {}
    if (typeof serverConfigData !== 'undefined') serverConfigData.server_id = id || null;
  }

  var _baSubTabsBound = false;

  function loadBookingSubPanel(sub) {
    var pageEl = document.getElementById('page-bookings');
    if (!pageEl) return;
    sub = sub || 'list';
    pageEl.setAttribute('data-ba-active-sub', sub);

    // Show/hide panels
    var panels = { list: 'ba-panel-list', customers: 'ba-panel-customers', memberships: 'ba-panel-memberships', tiers: 'ba-panel-tiers' };
    Object.keys(panels).forEach(function (key) {
      var el = document.getElementById(panels[key]);
      if (el) el.classList.toggle('hidden', key !== sub);
    });

    // Update active tab class
    pageEl.querySelectorAll('.ba-sub-tab').forEach(function (btn) {
      btn.classList.toggle('active', btn.getAttribute('data-ba-sub') === sub);
    });

    // Load data for the active panel
    if (sub === 'customers' && window.loadCustomersPage) window.loadCustomersPage();
    if (sub === 'memberships' && window.loadMembershipsPage) window.loadMembershipsPage();
    if (sub === 'tiers' && window.loadTiersPage) window.loadTiersPage();
  }

  function initBookingSubTabs() {
    if (_baSubTabsBound) return;
    _baSubTabsBound = true;
    var pageEl = document.getElementById('page-bookings');
    if (!pageEl) return;
    pageEl.querySelectorAll('.ba-sub-tab').forEach(function (btn) {
      btn.addEventListener('click', function () {
        loadBookingSubPanel(btn.getAttribute('data-ba-sub'));
      });
    });
  }

  function showPage(pageId) {
    document.querySelectorAll('.content-page').forEach(function (el) {
      el.classList.toggle('hidden', el.getAttribute('data-page') !== pageId);
    });
    if (pageId === 'config') loadConfigPage();
    if (pageId === 'dashboard') loadDashboardPage();
    if (pageId === 'settings') loadSettingsPage();
    if (pageId === 'server-config') loadServerConfigPage();
    if (pageId === 'live-timing') {
      if (getSelectedServerId()) {
        var main = document.getElementById('page-live-timing-main');
        var empty = document.getElementById('page-live-timing-server-empty');
        if (main) main.classList.remove('hidden');
        if (empty) empty.classList.add('hidden');
        loadLiveTimingPage();
        if (liveTimingPollTimer) clearInterval(liveTimingPollTimer);
        liveTimingPollTimer = setInterval(loadLiveTimingPage, LIVE_TIMING_POLL_MS);
      } else {
        var mainEl = document.getElementById('page-live-timing-main');
        var emptyEl = document.getElementById('page-live-timing-server-empty');
        if (mainEl) mainEl.classList.add('hidden');
        if (emptyEl) emptyEl.classList.remove('hidden');
      }
    } else {
      if (liveTimingPollTimer) { clearInterval(liveTimingPollTimer); liveTimingPollTimer = null; }
      var mainEl = document.getElementById('page-live-timing-main');
      var emptyEl = document.getElementById('page-live-timing-server-empty');
      if (mainEl) mainEl.classList.remove('hidden');
      if (emptyEl) emptyEl.classList.add('hidden');
    }
    if (pageId === 'system-logs') {
      populateLogsRigDropdown();
      loadSystemLogsPage();
      if (window._logsPollTimer) clearInterval(window._logsPollTimer);
      window._logsPollTimer = setInterval(loadSystemLogsPage, 5000);
    } else {
      if (window._logsPollTimer) { clearInterval(window._logsPollTimer); window._logsPollTimer = null; }
    }
    if (pageId === 'bookings') { initBookingSubTabs(); loadBookingSubPanel('list'); }
    if (pageId !== 'server-config' && sessionsRevisionPollTimer) {
      clearInterval(sessionsRevisionPollTimer);
      sessionsRevisionPollTimer = null;
    }
    updateServerScopedEmptyStates(pageId);
  }

  /** Path-based routes: pathname -> pageId (URL controls the view). */
  var ROUTES = {
    '/garage': 'dashboard',
    '/sims': 'rigs',
    '/presets': 'presets',
    '/server-config': 'server-config',
    '/entry-list': 'entry-list',
    '/bookings': 'bookings',
    '/schedule': 'bookings',
    '/checkin': 'bookings',
    '/analytics': 'bookings',
    '/live-timing': 'live-timing',
    '/content': 'content',
    '/system-logs': 'system-logs',
    '/settings': 'settings',
  };
  var SERVER_SCOPED_PAGES = ['server-config', 'entry-list', 'live-timing', 'content'];
  var REDIRECTS = {
    '/server/entry-list': '/entry-list',
    '/server/live-timing': '/live-timing',
    '/server/content': '/content',
  };

  function getPathname() {
    var p = (location.pathname || '').replace(/\/+$/, '') || '/';
    return p || '/';
  }

  function updateSidebarActive(path) {
    document.querySelectorAll('.sidebar .nav-link[data-route]').forEach(function (link) {
      link.classList.toggle('active', link.getAttribute('data-route') === path);
    });
  }

  function updateServerScopedEmptyStates(pageId) {
    var serverId = getSelectedServerId();
    var ids = ['page-entry-list-server-empty', 'page-entry-list-content', 'page-content-server-empty', 'page-content-loaded'];
    var emptyEntry = document.getElementById('page-entry-list-server-empty');
    var contentEntry = document.getElementById('page-entry-list-content');
    var emptyContent = document.getElementById('page-content-server-empty');
    var loadedContent = document.getElementById('page-content-loaded');
    if (pageId === 'entry-list') {
      if (emptyEntry) emptyEntry.classList.toggle('hidden', !!serverId);
      if (contentEntry) contentEntry.classList.toggle('hidden', !serverId);
      populateServerScopedSelect('entry-list-server-select', serverId);
    }
    if (pageId === 'content') {
      if (emptyContent) emptyContent.classList.toggle('hidden', !!serverId);
      if (loadedContent) loadedContent.classList.toggle('hidden', !serverId);
      populateServerScopedSelect('content-server-select', serverId);
    }
    if (pageId === 'live-timing') {
      populateServerScopedSelect('live-timing-server-select', serverId);
    }
  }

  function populateServerScopedSelect(selectId, selectedId) {
    var sel = document.getElementById(selectId);
    if (!sel) return;
    var list = (typeof serverConfigData !== 'undefined' && serverConfigData.server_ids) ? serverConfigData.server_ids : [];
    var names = (typeof serverConfigData !== 'undefined' && serverConfigData.preset_names) ? serverConfigData.preset_names : {};
    if (list.length === 0) {
      pitboxFetch(API_BASE + '/server-config/meta').then(function (r) { return r.ok ? r.json() : null; }).then(function (data) {
        if (!data || !sel) return;
        list = data.server_ids || [];
        names = data.preset_names || {};
        var opts = '<option value="">— Current session —</option>' + (list || []).map(function (id) {
          return '<option value="' + escapeHtml(id) + '"' + (id === selectedId ? ' selected' : '') + '>' + escapeHtml(names[id] || id) + '</option>';
        }).join('');
        sel.innerHTML = opts;
      }).catch(function () {});
      return;
    }
    var opts = '<option value="">— Current session —</option>' + (list || []).map(function (id) {
      return '<option value="' + escapeHtml(id) + '"' + (id === selectedId ? ' selected' : '') + '>' + escapeHtml(names[id] || id) + '</option>';
    }).join('');
    sel.innerHTML = opts;
  }

  function applyRoute() {
    var path = getPathname();
    if (REDIRECTS[path]) {
      history.replaceState(null, '', REDIRECTS[path]);
      path = REDIRECTS[path];
    }
    if (!path || path === '/') {
      history.replaceState(null, '', '/garage');
      path = '/garage';
    }
    var pageId = ROUTES[path];
    if (!pageId) {
      history.replaceState(null, '', '/garage');
      path = '/garage';
      pageId = 'dashboard';
    }
    updateSidebarActive(path);
    showPage(pageId);
    retriggerPitboxAnimation();
  }

  function retriggerPitboxAnimation() {
    var el = document.getElementById('sbBrand-wordmark');
    if (!el) return;
    el.classList.remove('is-animating');
    void el.offsetHeight;
    el.classList.add('is-animating');
  }

  function formatUptime(sec) {
    if (sec == null || sec < 0) return '—';
    var m = Math.floor(sec / 60);
    var s = Math.floor(sec % 60);
    return m + 'm ' + s + 's';
  }

  /** Shared track/layout display formatters (display-only; do not change INI or stored values). */
  var TRACK_LAYOUT_STOP = { and: 1, or: 1, the: 1, of: 1, in: 1, at: 1, to: 1, a: 1 };
  var TRACK_LAYOUT_ACRONYMS = ['gp', 'f1', 'usa', 'uk', 'gt3', 'gt4', 'dtm', 'srp'];
  function titleCaseTrackLayout(s) {
    return s.split(' ').map(function (w, i) {
      var low = w.toLowerCase();
      if (!low) return '';
      if (TRACK_LAYOUT_ACRONYMS.indexOf(low) !== -1) return low.toUpperCase();
      if (i !== 0 && TRACK_LAYOUT_STOP[low]) return low;
      return low.charAt(0).toUpperCase() + low.slice(1);
    }).join(' ');
  }
  function normalizeRawTrackLayout(raw) {
    var s = String(raw).trim();
    if (s.includes('/') || s.includes('\\')) {
      var parts = s.replace(/\\/g, '/').split('/').filter(Boolean);
      s = parts[parts.length - 1] || s;
    }
    s = s.replace(/^ks_/i, '');
    s = s.replace(/^layout_/i, '');
    s = s.replace(/[_-]+/g, ' ');
    s = s.replace(/\s+/g, ' ').trim();
    return s;
  }
  function formatTrackName(raw) {
    if (raw == null || (typeof raw === 'string' && !raw.trim())) return 'N/A';
    var s = String(raw).trim();
    if (s === '—' || s.toLowerCase() === 'n/a') return 'N/A';
    var cleaned = normalizeRawTrackLayout(s);
    if (!cleaned) return 'N/A';
    return titleCaseTrackLayout(cleaned);
  }
  function formatLayoutName(raw) {
    if (raw == null || (typeof raw === 'string' && !raw.trim())) return 'N/A';
    var s = String(raw).trim();
    if (s === '—' || s.toLowerCase() === 'n/a') return 'N/A';
    var cleaned = normalizeRawTrackLayout(s);
    if (!cleaned) return 'N/A';
    if (cleaned.toLowerCase() === 'default') return 'Default';
    return titleCaseTrackLayout(cleaned);
  }

  /** Shared car display formatter (display-only; do not change INI or stored values). */
  var CAR_ACRONYMS = { gt3: 1, gt4: 1, gt2: 1, gtd: 1, dtm: 1, f1: 1, f2: 1, f3: 1, mx5: 1, 'mx-5': 1, gtr: 1, nsx: 1, amg: 1, bmw: 1, audi: 1, porsche: 1, usa: 1, uk: 1, jdm: 1, v8: 1, v10: 1, v12: 1, tcr: 1, cup: 1, nx: 1, ks: 1 };
  function titleCaseCarWord(w) {
    var low = w.toLowerCase();
    if (CAR_ACRONYMS[low]) return low.toUpperCase();
    if (/^\d+$/.test(low)) return low;
    return low.charAt(0).toUpperCase() + low.slice(1);
  }
  function normalizeCarRaw(raw) {
    var s = String(raw).trim();
    if (s.indexOf('/') !== -1 || s.indexOf('\\') !== -1) {
      var parts = s.replace(/\\/g, '/').split('/').filter(Boolean);
      s = parts[parts.length - 1] || s;
    }
    s = s.replace(/^ks_+/i, '');
    s = s.replace(/^nohesi_+/i, '');
    s = s.replace(/^traffic_+/i, '');
    s = s.replace(/[_-]+/g, ' ');
    s = s.replace(/\s+/g, ' ').trim();
    return s;
  }
  function formatCarName(raw) {
    if (raw == null || (typeof raw === 'string' && !raw.trim())) return '—';
    var cleaned = normalizeCarRaw(raw);
    if (!cleaned) return '—';
    var words = cleaned.split(' ');
    var normalizedWords = words.map(function (w) {
      var low = w.toLowerCase();
      if (low === 'mx5') return 'MX-5';
      if (low === 'mx') return 'MX';
      if (low === 'cup') return 'Cup';
      if (low === 'model') return 'Model';
      return titleCaseCarWord(w);
    });
    return normalizedWords.join(' ');
  }

  function formatCarDisplayName(carId, fallback) {
    if (fallback != null && String(fallback).trim()) return String(fallback).trim();
    return getCarDisplayName(carId);
  }

  function renderDashboard(agents) {
    var container = document.getElementById('dashboard-sim-list');
    var lastEl = document.getElementById('dashboard-last-update');
    if (!container) return;
    if (!Array.isArray(agents) || agents.length === 0) {
      container.innerHTML = '<p class="dashboard-empty">No sims configured. Add sims in controller config.</p>';
      return;
    }
    var rows = agents.map(function (a) {
      var online = !!a.online;
      var running = !!a.ac_running;
      var status = online ? (running ? 'Running' : 'Idle') : 'Offline';
      var statusClass = !online ? 'dashboard-status-offline' : (running ? 'dashboard-status-running' : 'dashboard-status-idle');
      var ls = a.last_session || {};
      var trackRaw = ls.track_name || ls.track_id || ls.track || '';
      var track = trackRaw ? formatTrackName(trackRaw) : '—';
      var car = formatCarDisplayName(ls.car_id || ls.car, ls.car_name || ls.car) || '—';
      var mode = ls.mode_kind || ls.mode || '—';
      var server = (ls.server_name && ls.server_name !== '—') ? ls.server_name : (ls.server && ls.server.name) ? ls.server.name : '—';
      var sessionTime = running && a.uptime_sec != null ? formatUptime(a.uptime_sec) : '—';
      var timeLeft = '—';
      var name = (a.display_name || a.agent_id || '').trim() || a.agent_id || '—';
      return (
        '<tr class="dashboard-row">' +
        '<td class="dashboard-cell dashboard-cell-sim"><span class="dashboard-sim-name">' + escapeHtml(name) + '</span></td>' +
        '<td class="dashboard-cell dashboard-cell-status"><span class="dashboard-status ' + statusClass + '">' + escapeHtml(status) + '</span></td>' +
        '<td class="dashboard-cell dashboard-cell-track">' + escapeHtml(track) + '</td>' +
        '<td class="dashboard-cell dashboard-cell-car">' + escapeHtml(car) + '</td>' +
        '<td class="dashboard-cell dashboard-cell-mode">' + escapeHtml(mode) + '</td>' +
        '<td class="dashboard-cell dashboard-cell-server">' + escapeHtml(server) + '</td>' +
        '<td class="dashboard-cell dashboard-cell-session-time">' + escapeHtml(sessionTime) + '</td>' +
        '<td class="dashboard-cell dashboard-cell-time-left">' + escapeHtml(timeLeft) + '</td>' +
        '</tr>'
      );
    });
    container.innerHTML = '<table class="dashboard-table"><thead><tr>' +
      '<th class="dashboard-th">Sim</th>' +
      '<th class="dashboard-th">Status</th>' +
      '<th class="dashboard-th">Track</th>' +
      '<th class="dashboard-th">Car</th>' +
      '<th class="dashboard-th">Mode</th>' +
      '<th class="dashboard-th">Server</th>' +
      '<th class="dashboard-th">Session time</th>' +
      '<th class="dashboard-th">Time left</th>' +
      '</tr></thead><tbody>' + rows.join('') + '</tbody></table>';
    if (lastEl && lastFetchTime) {
      var sec = Math.floor((Date.now() - lastFetchTime) / 1000);
      if (sec < 5) lastEl.textContent = 'Last update: ' + (sec <= 1 ? 'just now' : sec + 's ago');
      else lastEl.textContent = 'Last update: ' + new Date(lastFetchTime).toLocaleTimeString();
    }
  }

  function updateDashboardLastUpdate() {
    var lastEl = document.getElementById('dashboard-last-update');
    if (!lastEl || !lastFetchTime) return;
    var sec = Math.floor((Date.now() - lastFetchTime) / 1000);
    if (sec < 5) lastEl.textContent = 'Last update: ' + (sec <= 1 ? 'just now' : sec + 's ago');
    else lastEl.textContent = 'Last update: ' + new Date(lastFetchTime).toLocaleTimeString();
  }

  function emptyVal() { return 'N/A'; }
  function renderCommandCenterDashboard(statusData, serverConfig, processStatus) {
    var agents = (statusData && statusData.agents) ? statusData.agents : [];
    var serverIds = (statusData && statusData.server_ids) ? statusData.server_ids : (serverConfig && serverConfig.server_ids) ? serverConfig.server_ids : [];
    var selectedId = (document.getElementById('cc-server-select') && document.getElementById('cc-server-select').value) || (serverIds[0] || '');
    var onlineCount = agents.filter(function (a) { return !!a.online; }).length;
    var race = (serverConfig && serverConfig.race) ? serverConfig.race : {};
    var trackIdRaw = (race.track_id && String(race.track_id).trim()) ? String(race.track_id).trim() : '';
    var configTrackRaw = (race.config_track && String(race.config_track).trim()) ? String(race.config_track).trim() : '';
    var trackDisplay = trackIdRaw ? formatTrackName(trackIdRaw) : '';
    var layoutDisplay = configTrackRaw ? formatLayoutName(configTrackRaw) : '';
    var srv = (serverConfig && serverConfig.server_cfg && serverConfig.server_cfg.SERVER) ? serverConfig.server_cfg.SERVER : {};
    var maxClients = (srv.MAX_CLIENTS && String(srv.MAX_CLIENTS).trim()) ? String(srv.MAX_CLIENTS).trim() : '';
    var servers = (processStatus && processStatus.servers) ? processStatus.servers : [];
    var serverRunning = servers.some(function (s) { return s.server_id === selectedId; });

    var sel = document.getElementById('cc-server-select');
    if (sel && serverIds.length) {
      var currentVal = sel.value;
      sel.innerHTML = serverIds.map(function (id) {
        return '<option value="' + escapeHtml(id) + '"' + (id === selectedId ? ' selected' : '') + '>' + escapeHtml(id) + '</option>';
      }).join('');
      if (currentVal !== selectedId) sel.value = selectedId;
    }

    function setText(id, text, muted) {
      var el = document.getElementById(id);
      if (!el) return;
      el.textContent = text || emptyVal();
      el.classList.toggle('cc-muted', !!muted || !text);
    }
    setText('cc-kpi-track', trackDisplay || emptyVal(), !trackDisplay);
    setText('cc-kpi-layout', layoutDisplay || emptyVal(), !layoutDisplay);
    setText('cc-kpi-max-clients', maxClients || emptyVal(), !maxClients);
    setText('cc-kpi-agents', onlineCount.toString(), false);
    setText('cc-hero-track', trackDisplay || emptyVal(), !trackDisplay);
    setText('cc-hero-layout', layoutDisplay || emptyVal(), !layoutDisplay);

    var pillServer = document.getElementById('cc-pill-server');
    if (pillServer) {
      pillServer.textContent = 'Server: ' + (serverRunning ? 'Running' : 'Stopped');
      pillServer.className = 'cc-pill ' + (serverRunning ? 'cc-pill-ok' : 'cc-pill-muted');
    }
    var pillAgents = document.getElementById('cc-pill-agents');
    if (pillAgents) {
      pillAgents.textContent = 'Sims: ' + onlineCount + '/' + agents.length;
      pillAgents.className = 'cc-pill ' + (onlineCount > 0 ? 'cc-pill-ok' : 'cc-pill-warn');
    }

    var callout = document.getElementById('cc-agent-offline-callout');
    if (callout) callout.classList.toggle('hidden', onlineCount > 0);
    var startBtn = document.getElementById('cc-btn-start');
    var restartBtn = document.getElementById('cc-btn-restart');
    var stopBtn = document.getElementById('cc-btn-stop');
    var disabled = onlineCount === 0 || operatorControlBlocked();
    if (startBtn) startBtn.disabled = disabled;
    if (restartBtn) restartBtn.disabled = disabled;
    if (stopBtn) stopBtn.disabled = disabled;

    var statusEl = document.getElementById('cc-server-status');
    if (statusEl) {
      statusEl.textContent = serverRunning ? 'Server status: Running' : 'Server status: ' + (selectedId ? 'Stopped' : emptyVal());
      statusEl.classList.toggle('cc-muted', !serverRunning);
    }
  }

  function loadDashboardPage() {
    var serverSelect = document.getElementById('cc-server-select');
    var selectedServerId = (serverSelect && serverSelect.value) || '';
    if (window._commandCenterLastStatus && window._commandCenterLastStatus.agents) {
      renderDashboard(window._commandCenterLastStatus.agents);
    }
    Promise.all([
      pitboxFetch(API_BASE + '/status').then(function (r) { if (!r.ok) throw new Error('Status ' + r.status); return r.json(); }),
      getDashboardServerConfigCached(selectedServerId || 'default'),
      getDashboardProcessStatusCached()
    ]).then(function (arr) {
      lastFetchTime = Date.now();
      var statusData = arr[0];
      var serverConfig = arr[1];
      var processStatus = arr[2];
      var agents = statusData.agents || [];
      var serverIds = statusData.server_ids || (serverConfig.server_ids) || [];
      if (!selectedServerId && serverIds.length) {
        var sel = document.getElementById('cc-server-select');
        if (sel) sel.value = serverIds[0];
      }
      window._commandCenterLastStatus = statusData;
      renderCommandCenterDashboard(statusData, serverConfig, processStatus);
      renderDashboard(agents);
      updateDashboardLastUpdate();
    }).catch(function () {
      var lastEl = document.getElementById('dashboard-last-update');
      if (lastEl) lastEl.textContent = 'Last update: error';
      renderCommandCenterDashboard({ agents: [], server_ids: [] }, {}, { servers: [] });
    });
  }

  function loadSystemLogsPage() {
    if (launchBusy) return;
    var errorsOnly = document.getElementById('logs-errors-only');
    var rigSel = document.getElementById('logs-rig');
    var catSel = document.getElementById('logs-category');
    var sinceSel = document.getElementById('logs-since');
    var searchInput = document.getElementById('logs-search');
    var tbody = document.getElementById('logs-tbody');
    var pinnedSection = document.getElementById('logs-pinned');
    var pinnedList = document.getElementById('logs-pinned-list');
    var detailPanel = document.getElementById('logs-detail');
    var detailContent = document.getElementById('logs-detail-content');
    if (!tbody) return;
    var params = new URLSearchParams();
    if (errorsOnly && errorsOnly.checked) params.set('level', 'ERROR');
    if (rigSel && rigSel.value) params.set('rig_id', rigSel.value);
    if (catSel && catSel.value) params.set('category', catSel.value);
    params.set('since_minutes', (sinceSel && sinceSel.value) ? sinceSel.value : '60');
    params.set('limit', '300');
    if (searchInput && (searchInput.value || '').trim()) params.set('search', searchInput.value.trim());
    pitboxFetch(API_BASE + '/logs/events?' + params.toString())
      .then(function (r) { return r.ok ? r.json() : Promise.reject(new Error(r.status)); })
      .then(function (events) {
        if (!Array.isArray(events)) events = [];
        var errors = events.filter(function (e) { return e.level === 'ERROR'; });
        if (pinnedSection && pinnedList) {
          var pinItems = errors.slice(0, 10);
          if (pinItems.length === 0) {
            pinnedSection.classList.add('hidden');
          } else {
            pinnedSection.classList.remove('hidden');
            pinnedList.innerHTML = pinItems.map(function (e) {
              var t = (e.timestamp || '').replace('Z', ' UTC');
              return '<div class="logs-pinned-item" data-index="' + events.indexOf(e) + '">' +
                '<span class="logs-pinned-time">' + escapeHtml(t) + '</span> ' +
                '<span class="logs-pinned-rig">' + escapeHtml(e.rig_id || '—') + '</span> ' +
                '<span class="logs-pinned-msg">' + escapeHtml((e.message || '').slice(0, 80)) + (e.message && e.message.length > 80 ? '…' : '') + '</span>' +
                '</div>';
            }).join('');
          }
        }
        tbody.innerHTML = events.map(function (e, i) {
          var levelClass = e.level === 'ERROR' ? ' logs-level-error' : (e.level === 'WARN' ? ' logs-level-warn' : '');
          var timeStr = (e.timestamp || '').replace('Z', ' UTC');
          return '<tr class="logs-row' + levelClass + '" data-index="' + i + '" role="button" tabindex="0">' +
            '<td class="logs-td-time">' + escapeHtml(timeStr) + '</td>' +
            '<td class="logs-td-rig">' + escapeHtml(e.rig_id || '—') + '</td>' +
            '<td class="logs-td-cat">' + escapeHtml(e.category || '—') + '</td>' +
            '<td class="logs-td-msg">' + escapeHtml((e.message || '').slice(0, 120)) + (e.message && e.message.length > 120 ? '…' : '') + '</td>' +
            '<td class="logs-td-level">' + escapeHtml(e.level || '—') + '</td>' +
            '</tr>';
        }).join('');
        if (detailPanel) detailPanel.classList.add('hidden');
        window._logsLastEvents = events;
      })
      .catch(function (err) {
        tbody.innerHTML = '<tr><td colspan="5" class="logs-error-cell">Failed to load events: ' + escapeHtml(err.message || String(err)) + '</td></tr>';
        if (pinnedSection) pinnedSection.classList.add('hidden');
      });
  }

  function populateLogsRigDropdown() {
    var rigSel = document.getElementById('logs-rig');
    if (!rigSel) return;
    pitboxFetch(API_BASE + '/status')
      .then(function (r) { return r.ok ? r.json() : Promise.reject(); })
      .then(function (data) {
        var agents = data.agents || [];
        var current = rigSel.value;
        rigSel.innerHTML = '<option value="">All</option>' + agents.map(function (a) {
          var id = (a.agent_id || a.display_name || '').trim() || '—';
          var label = (a.display_name || a.agent_id || id).trim();
          return '<option value="' + escapeHtml(id) + '">' + escapeHtml(label) + '</option>';
        }).join('');
        if (current) rigSel.value = current;
      })
      .catch(function () {});
  }

  function formatLapMs(ms) {
    if (ms == null || ms < 0) return '—';
    var m = Math.floor(ms / 60000);
    var s = ((ms % 60000) / 1000).toFixed(3);
    return m > 0 ? m + ':' + (parseFloat(s) < 10 ? '0' : '') + s : s + 's';
  }

  function formatGapMs(ms) {
    if (ms == null || ms < 0) return '—';
    if (ms === 0) return '—';
    return '+' + formatLapMs(ms);
  }

  var DEMO_TIMING_CARS = [
    { pos: 1, driver: 'Jaiden', car_model: 'tatuusfa1', best_lap_ms: 73510, last_lap_ms: 74231, lap: 3, sector: 2, sector_time_ms: 25110, gap_ms: 0, pit: false, live: { normalized_pos: 0.6342, speed_kmh: 167.2, source: 'agent:Sim5', stale_ms: 80 } },
    { pos: 2, driver: 'Alex', car_model: 'tatuusfa1', best_lap_ms: 73820, last_lap_ms: 74100, lap: 3, sector: 2, sector_time_ms: 25400, gap_ms: 310, pit: false, live: { normalized_pos: 0.512, speed_kmh: 162.1, source: 'agent:Sim6', stale_ms: 120 } },
    { pos: 3, driver: 'Sam', car_model: 'tatuusfa1', best_lap_ms: 74100, last_lap_ms: null, lap: 2, sector: 1, sector_time_ms: 24800, gap_ms: 590, pit: true, live: { normalized_pos: 0.02, speed_kmh: 45, source: 'agent:Sim7', stale_ms: 200 } }
  ];

  function renderLiveTiming(snapshot, isDemo) {
    var serverName = document.getElementById('live-timing-server-name');
    var serverAddr = document.getElementById('live-timing-server-addr');
    var trackEl = document.getElementById('live-timing-track');
    var layoutEl = document.getElementById('live-timing-layout');
    var phaseEl = document.getElementById('live-timing-phase');
    var timeLeftEl = document.getElementById('live-timing-time-left');
    var updatedEl = document.getElementById('live-timing-updated');
    var tbody = document.getElementById('live-timing-tbody');
    var hint = document.getElementById('live-timing-hint');
    if (!snapshot || !tbody) return;
    var server = snapshot.server || {};
    var track = snapshot.track || {};
    var cars = snapshot.cars && snapshot.cars.length ? snapshot.cars : (isDemo ? DEMO_TIMING_CARS : []);
    if (serverName) serverName.textContent = server.name || '—';
    if (serverAddr) { serverAddr.textContent = server.addr ? '(' + server.addr + ')' : ''; serverAddr.className = 'live-timing-muted'; }
    if (trackEl) trackEl.textContent = formatTrackName(track.track_id || '') || '—';
    if (layoutEl) { layoutEl.textContent = track.layout ? ' / ' + formatLayoutName(track.layout) : ''; layoutEl.className = 'live-timing-muted'; }
    if (phaseEl) phaseEl.textContent = server.phase || '—';
    if (timeLeftEl) {
      var tl = server.time_left_ms;
      if (tl != null && tl >= 0) {
        var mins = Math.floor(tl / 60000);
        var secs = Math.floor((tl % 60000) / 1000);
        timeLeftEl.textContent = mins + ':' + (secs < 10 ? '0' : '') + secs;
      } else timeLeftEl.textContent = '—';
    }
    if (updatedEl) updatedEl.textContent = 'Updated: ' + (snapshot.ts_ms ? new Date(snapshot.ts_ms).toLocaleTimeString() : '—') + (isDemo ? ' (demo)' : '');
    var rows = cars.map(function (c) {
      var live = c.live || {};
      var liveStr = (live.normalized_pos != null ? (Math.round(live.normalized_pos * 100) + '%') : '—') + ' · ' + (live.speed_kmh != null ? Math.round(live.speed_kmh) + ' km/h' : '—');
      if (live.stale_ms != null && live.stale_ms > 500) liveStr += ' (stale ' + live.stale_ms + 'ms)';
      return '<tr class="live-timing-row' + (c.pit ? ' live-timing-pit' : '') + '">' +
        '<td class="lt-pos">' + (c.pos != null ? c.pos : '—') + '</td>' +
        '<td class="lt-driver">' + escapeHtml(c.driver || '—') + '</td>' +
        '<td class="lt-car">' + escapeHtml(formatCarName(c.car_model) || '—') + '</td>' +
        '<td class="lt-best">' + formatLapMs(c.best_lap_ms) + '</td>' +
        '<td class="lt-last">' + formatLapMs(c.last_lap_ms) + '</td>' +
        '<td class="lt-lap">' + (c.lap != null ? c.lap : '—') + '</td>' +
        '<td class="lt-sector">' + (c.sector != null ? c.sector : '—') + ' ' + (c.sector_time_ms != null ? '(' + formatLapMs(c.sector_time_ms) + ')' : '') + '</td>' +
        '<td class="lt-gap">' + formatGapMs(c.gap_ms) + '</td>' +
        '<td class="lt-pit">' + (c.pit ? 'PIT' : '—') + '</td>' +
        '<td class="lt-live">' + escapeHtml(liveStr) + '</td>' +
        '</tr>';
    });
    tbody.innerHTML = rows.join('');
    if (hint) hint.classList.toggle('hidden', !isDemo);

    var outlineImg = document.getElementById('live-timing-track-outline');
    var carDotsG = document.getElementById('live-timing-car-dots');
    var trackId = (track.track_id || '').trim();
    var layoutId = (track.layout || '').trim() || 'default';
    if (outlineImg) {
      if (trackId) {
        outlineImg.src = API_BASE + '/tracks/' + encodeURIComponent(trackId) + '/layouts/' + encodeURIComponent(layoutId) + '/outline';
        outlineImg.onerror = function () { outlineImg.style.display = 'none'; outlineImg.src = ''; };
        outlineImg.onload = function () { outlineImg.style.display = ''; };
      } else { outlineImg.style.display = 'none'; outlineImg.src = ''; }
    }
    if (carDotsG) {
      var cx = 100; var cy = 50; var rx = 86; var ry = 40;
      carDotsG.innerHTML = cars.map(function (c, i) {
        var pos = (c.live && c.live.normalized_pos != null) ? c.live.normalized_pos : (c.normalized_pos != null ? c.normalized_pos : 0);
        var angle = pos * 2 * Math.PI - Math.PI / 2;
        var x = cx + rx * Math.cos(angle);
        var y = cy + ry * Math.sin(angle);
        var posClass = 'lt-dot-pos' + (c.pos || (i + 1));
        return '<circle class="live-timing-car-dot ' + posClass + '" cx="' + x + '" cy="' + y + '" r="4" data-pos="' + (c.pos || i + 1) + '" aria-label="' + escapeHtml(c.driver || 'Car ' + (i + 1)) + ' position ' + (Math.round(pos * 100) + '%') + '" />';
      }).join('');
    }
  }

  function loadLiveTimingPage() {
    if (launchBusy) return;
    pitboxFetch(API_BASE + '/timing/snapshot')
      .then(function (r) { return r.ok ? r.json() : Promise.reject(new Error('Status ' + r.status)); })
      .then(function (data) {
        var isDemo = !data.cars || data.cars.length === 0;
        if (isDemo) {
          data = { type: 'timing_snapshot', v: 1, ts_ms: Date.now(), server: { name: 'Fastest Lap JR F1', addr: '192.168.1.218:9616', phase: 'QUALIFY', time_left_ms: 312000 }, track: { track_id: 'ks_red_bull_ring', layout: 'layout_national' }, cars: [] };
        }
        renderLiveTiming(data, isDemo);
      })
      .catch(function () {
        renderLiveTiming({ type: 'timing_snapshot', v: 1, ts_ms: Date.now(), server: { name: 'Fastest Lap JR F1', addr: '192.168.1.218:9616', phase: 'QUALIFY', time_left_ms: 312000 }, track: { track_id: 'ks_red_bull_ring', layout: 'layout_national' }, cars: [] }, true);
      });
  }

  function loadConfigPage() {
    var pathEl = document.getElementById('config-path-label');
    var jsonEl = document.getElementById('config-json');
    if (!pathEl || !jsonEl) return;
    pathEl.textContent = 'Config file: —';
    jsonEl.textContent = 'Loading…';
    pitboxFetch(API_BASE + '/config')
      .then(function (r) { return r.ok ? r.json() : Promise.reject(new Error('Status ' + r.status)); })
      .then(function (data) {
        pathEl.textContent = 'Config file: ' + (data.config_path || '(default / not set)');
        jsonEl.textContent = JSON.stringify(data.config || {}, null, 2);
      })
      .catch(function (err) {
        pathEl.textContent = 'Config file: —';
        jsonEl.textContent = 'Error: ' + err.message;
      });
  }

  document.querySelectorAll('.sidebar .nav-link[data-route]').forEach(function (link) {
    link.addEventListener('click', function (e) {
      var href = (link.getAttribute('href') || '').trim();
      if (href && href !== '#' && href.indexOf('#') !== 0) {
        e.preventDefault();
        history.pushState(null, '', href);
        applyRoute();
      }
    });
  });

  window.addEventListener('popstate', applyRoute);

  /* System Logs: filters, refresh, row click to expand details */
  (function () {
    var refreshBtn = document.getElementById('logs-refresh');
    var errorsOnly = document.getElementById('logs-errors-only');
    var rigSel = document.getElementById('logs-rig');
    var catSel = document.getElementById('logs-category');
    var sinceSel = document.getElementById('logs-since');
    var searchInput = document.getElementById('logs-search');
    var tbody = document.getElementById('logs-tbody');
    var detailPanel = document.getElementById('logs-detail');
    var detailContent = document.getElementById('logs-detail-content');
    function reload() { loadSystemLogsPage(); }
    if (refreshBtn) refreshBtn.addEventListener('click', reload);
    if (errorsOnly) errorsOnly.addEventListener('change', reload);
    if (rigSel) rigSel.addEventListener('change', reload);
    if (catSel) catSel.addEventListener('change', reload);
    if (sinceSel) sinceSel.addEventListener('change', reload);
    if (searchInput) {
      var searchTimeout;
      searchInput.addEventListener('input', function () {
        if (searchTimeout) clearTimeout(searchTimeout);
        searchTimeout = setTimeout(reload, 400);
      });
    }
    if (tbody && detailPanel && detailContent) {
      tbody.addEventListener('click', function (e) {
        var row = e.target && e.target.closest && e.target.closest('.logs-row');
        if (!row) return;
        var idx = row.getAttribute('data-index');
        if (idx == null) return;
        var events = window._logsLastEvents;
        if (!Array.isArray(events) || !events[parseInt(idx, 10)]) return;
        var ev = events[parseInt(idx, 10)];
        var ts = ev.timestamp || '';
        var localStr = '';
        try {
          if (ts) localStr = new Date(ts).toLocaleString();
        } catch (err) { localStr = ts; }
        var detailsJson = '';
        if (ev.details && Object.keys(ev.details).length > 0) {
          try { detailsJson = JSON.stringify(ev.details, null, 2); } catch (err) { detailsJson = String(ev.details); }
        }
        detailContent.innerHTML =
          '<p class="logs-detail-row"><strong>Time (UTC)</strong> ' + escapeHtml(ts) + '</p>' +
          (localStr ? '<p class="logs-detail-row"><strong>Time (local)</strong> ' + escapeHtml(localStr) + '</p>' : '') +
          '<p class="logs-detail-row"><strong>Message</strong> ' + escapeHtml(ev.message || '—') + '</p>' +
          (ev.event_code ? '<p class="logs-detail-row"><strong>Event code</strong> ' + escapeHtml(ev.event_code) + '</p>' : '') +
          (ev.session_id ? '<p class="logs-detail-row"><strong>Session ID</strong> ' + escapeHtml(ev.session_id) + '</p>' : '') +
          (detailsJson ? '<pre class="logs-detail-json">' + escapeHtml(detailsJson) + '</pre>' : '');
        detailPanel.classList.remove('hidden');
      });
    }
  })();

  /* Collapsible sidebar: icons only when collapsed (more space for sim cards) */
  (function () {
    var SIDEBAR_KEY = 'pitbox-sidebar-collapsed';
    var sidebar = document.getElementById('sidebar');
    var toggle = document.getElementById('sidebar-toggle');
    if (!sidebar || !toggle) return;
    function isCollapsed() { return sidebar.classList.contains('sidebar--collapsed'); }
    function applyCollapsed(collapsed) {
      if (collapsed) {
        sidebar.classList.add('sidebar--collapsed');
        toggle.textContent = '\u203A';
        toggle.setAttribute('aria-label', 'Expand sidebar');
        toggle.setAttribute('title', 'Expand sidebar');
      } else {
        sidebar.classList.remove('sidebar--collapsed');
        toggle.textContent = '\u2039';
        toggle.setAttribute('aria-label', 'Collapse sidebar');
        toggle.setAttribute('title', 'Collapse sidebar');
      }
      try { localStorage.setItem(SIDEBAR_KEY, collapsed ? '1' : '0'); } catch (e) {}
    }
    try {
      var saved = localStorage.getItem(SIDEBAR_KEY);
      if (saved === '1') applyCollapsed(true);
    } catch (e) {}
    toggle.addEventListener('click', function () { applyCollapsed(!isCollapsed()); });
  })();

  // On load: path-based route (bookmarkable, refresh-safe). Redirect / to /garage.
  applyRoute();

  (function bindServerScopedSelects() {
    function onSelect(selectId) {
      var sel = document.getElementById(selectId);
      if (!sel) return;
      sel.addEventListener('change', function () {
        var id = (sel.value || '').trim() || null;
        setSelectedServerId(id);
        var path = getPathname();
        var pageId = ROUTES[path] || '';
        updateServerScopedEmptyStates(pageId);
        if (id && selectId === 'live-timing-server-select') {
          var main = document.getElementById('page-live-timing-main');
          var empty = document.getElementById('page-live-timing-server-empty');
          if (main) main.classList.remove('hidden');
          if (empty) empty.classList.add('hidden');
          loadLiveTimingPage();
        }
      });
    }
    onSelect('entry-list-server-select');
    onSelect('content-server-select');
    onSelect('live-timing-server-select');
  })();

  var serverConfigData = { server_cfg: {}, entry_list: [], server_ids: [], preset_names: {}, server_cfg_path: null, server_id: null };
  var SERVER_ORDER_STORAGE_KEY = 'pitbox_server_order';
  var LAST_SERVER_PRESET_KEY = 'pitbox.lastServerPresetId';
  var RECENT_TRACKS_KEY = 'pitbox_recent_tracks';
  var RECENT_TRACKS_MAX = 5;
  function getRecentTrackIds() {
    try {
      var raw = localStorage.getItem(RECENT_TRACKS_KEY);
      if (!raw) return [];
      var arr = JSON.parse(raw);
      return Array.isArray(arr) ? arr.slice(0, RECENT_TRACKS_MAX) : [];
    } catch (e) { return []; }
  }
  function pushRecentTrack(trackValue) {
    if (!trackValue || !String(trackValue).trim()) return;
    var trackId = trackValue.indexOf('/') !== -1 ? trackValue.split('/')[0] : trackValue;
    var recent = getRecentTrackIds();
    recent = [trackId].concat(recent.filter(function (id) { return id !== trackId; })).slice(0, RECENT_TRACKS_MAX);
    try { localStorage.setItem(RECENT_TRACKS_KEY, JSON.stringify(recent)); } catch (e) {}
  }
  function getOrderedServerIds(apiIds) {
    if (!Array.isArray(apiIds) || apiIds.length === 0) return apiIds;
    var stored;
    try { stored = JSON.parse(localStorage.getItem(SERVER_ORDER_STORAGE_KEY) || '[]'); } catch (e) { stored = []; }
    if (!Array.isArray(stored)) stored = [];
    var set = new Set(apiIds);
    var ordered = stored.filter(function (id) { return set.has(id); });
    var rest = apiIds.filter(function (id) { return ordered.indexOf(id) === -1; });
    return ordered.concat(rest);
  }
  function saveServerOrder(ids) {
    try { localStorage.setItem(SERVER_ORDER_STORAGE_KEY, JSON.stringify(ids)); } catch (e) {}
  }

  // --- Enrollment Mode (toggle + countdown) ---
  function formatCountdown(sec) {
    if (sec <= 0) return '0:00';
    var m = Math.floor(sec / 60);
    var s = Math.floor(sec % 60);
    return m + ':' + (s < 10 ? '0' : '') + s;
  }
  function updateEnrollmentUI(state) {
    var toggle = document.getElementById('enrollment-toggle');
    var countdownEl = document.getElementById('enrollment-countdown');
    if (toggle) toggle.checked = !!(state && state.enabled);
    if (countdownEl) {
      var sec = (state && state.seconds_remaining != null) ? state.seconds_remaining : 0;
      countdownEl.textContent = formatCountdown(sec);
      countdownEl.classList.toggle('hidden', !(state && state.enabled && sec > 0));
    }
  }
  function fetchEnrollment() {
    if (launchBusy) return;
    pitboxFetch(API_BASE + '/enrollment')
      .then(function (r) { return r.ok ? r.json() : {}; })
      .then(updateEnrollmentUI)
      .catch(function () { updateEnrollmentUI({ enabled: false, seconds_remaining: 0 }); });
  }
  (function bindEnrollment() {
    var toggle = document.getElementById('enrollment-toggle');
    if (toggle) {
      toggle.addEventListener('change', function () {
        var enabled = !!toggle.checked;
        ensureOperatorOrRedirect().then(function (ok) {
          if (!ok) {
            toggle.checked = !enabled;
            return;
          }
          pitboxFetch(API_BASE + '/enrollment', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ enabled: enabled }),
          })
            .then(function (r) { return r.ok ? r.json() : {}; })
            .then(function (data) {
              updateEnrollmentUI(data);
              if (typeof scheduleFetchStatus === 'function') scheduleFetchStatus('enrollment');
            })
            .catch(function () { updateEnrollmentUI({ enabled: false }); });
        });
      });
    }
    fetchEnrollment();
    setInterval(fetchEnrollment, 2000);
  })();

  // --- Server config API (raw / PATCH / revision) for Sessions panel ---
  function getRawConfig(serverId, callback) {
    var sid = serverId || (serverConfigData.server_ids && serverConfigData.server_ids[0]) || '';
    pitboxFetch(API_BASE + '/server-config/raw?server_id=' + encodeURIComponent(sid || 'default'))
      .then(function (r) { return r.ok ? r.json() : Promise.reject(new Error(r.status === 404 ? 'Server cfg path not configured' : 'Status ' + r.status)); })
      .then(callback)
      .catch(function (err) { if (typeof callback === 'function') callback(null, err); });
  }
  function patchConfig(serverId, updates, callback) {
    var sid = serverId || (serverConfigData.server_ids && serverConfigData.server_ids[0]) || '';
    pitboxFetch(API_BASE + '/server-config', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ server_id: sid || 'default', updates: updates })
    })
      .then(function (r) {
        if (!r.ok) return r.json().then(function (b) { return Promise.reject({ status: r.status, body: b }); });
        return r.json();
      })
      .then(function (data) {
        invalidatePresetDiskStateClient(sid);
        if (typeof callback === 'function') callback(data);
      })
      .catch(function (err) { if (typeof callback === 'function') callback(null, err); });
  }
  function getRevision(serverId, callback) {
    var sid = serverId || (serverConfigData.server_ids && serverConfigData.server_ids[0]) || '';
    pitboxFetch(API_BASE + '/server-config/revision?server_id=' + encodeURIComponent(sid || 'default'))
      .then(function (r) { return r.ok ? r.json() : Promise.reject(new Error('Status ' + r.status)); })
      .then(callback)
      .catch(function (err) { if (typeof callback === 'function') callback(null, err); });
  }

  // Case-insensitive lookup in raw INI (sections/keys may be any case)
  function rawSection(data, name) {
    if (!data || !name) return {};
    var n = name.toUpperCase();
    for (var s in data) { if (s.toUpperCase() === n) return data[s]; }
    return {};
  }
  function rawVal(opts, key) {
    if (!opts) return undefined;
    var k = (key || '').toUpperCase();
    for (var p in opts) { if (p.toUpperCase() === k) return opts[p]; }
    return undefined;
  }
  // CM-style: session enabled = section [PRACTICE]; disabled = section [__CM_PRACTICE_OFF]. Returns { section: {...}, enabled: bool }.
  var SESSION_OFF_NAMES = { BOOK: '__CM_BOOK_OFF', PRACTICE: '__CM_PRACTICE_OFF', QUALIFY: '__CM_QUALIFY_OFF', RACE: '__CM_RACE_OFF' };
  function sessionSectionOrOff(data, baseName) {
    var offName = SESSION_OFF_NAMES[baseName] || ('__CM_' + baseName + '_OFF');
    var normal = rawSection(data, baseName);
    var off = rawSection(data, offName);
    if (Object.keys(normal).length > 0) return { section: normal, enabled: true };
    if (Object.keys(off).length > 0) return { section: off, enabled: false };
    return { section: {}, enabled: baseName === 'RACE' };
  }

  // Sessions panel: single source of truth and last-known-good for revert on error
  var sessionsVm = {};
  var sessionsLastKnownGood = {};
  var sessionsRevision = null;
  var sessionsRevisionPollTimer = null;
  var sessionsPatchDebounceTimer = null;
  var sessionsSliderDragging = false;

  function deriveSessionsVmFromIni(rawIni) {
    var s = rawSection(rawIni, 'SERVER');
    var bookSo = sessionSectionOrOff(rawIni, 'BOOK');
    var prSo = sessionSectionOrOff(rawIni, 'PRACTICE');
    var quSo = sessionSectionOrOff(rawIni, 'QUALIFY');
    var raSo = sessionSectionOrOff(rawIni, 'RACE');
    var book = bookSo.section;
    var pr = prSo.section;
    var qu = quSo.section;
    var ra = raSo.section;
    var pickup = rawVal(s, 'PICKUP_MODE_ENABLED');
    var locked = rawVal(s, 'LOCKED_ENTRY_LIST');
    var loop = rawVal(s, 'LOOP_MODE');
    var bookOpen = rawVal(book, 'IS_OPEN');
    var bookTime = parseInt(rawVal(book, 'TIME'), 10);
    var prOpen = rawVal(pr, 'IS_OPEN');
    var prTime = parseInt(rawVal(pr, 'TIME'), 10);
    var quOpen = rawVal(qu, 'IS_OPEN');
    var quTime = parseInt(rawVal(qu, 'TIME'), 10);
    var quLimit = parseInt(rawVal(s, 'QUALIFY_MAX_WAIT_PERC'), 10);
    var raOpen = rawVal(ra, 'IS_OPEN');
    var raLaps = parseInt(rawVal(ra, 'LAPS'), 10);
    var raTime = parseInt(rawVal(ra, 'TIME'), 10);
    var raceOverR = rawVal(ra, 'RACE_OVER_TIME');
    var raceOverS = rawVal(s, 'RACE_OVER_TIME');
    var resultR = rawVal(ra, 'RESULT_SCREEN_TIME');
    var resultS = rawVal(s, 'RESULT_SCREEN_TIME');
    var waitTime = parseInt(rawVal(ra, 'WAIT_TIME'), 10);
    var startRule = rawVal(s, 'START_RULE');
    var extraLap = rawVal(s, 'RACE_EXTRA_LAP');
    var reversed = parseInt(rawVal(s, 'REVERSED_GRID_RACE_POSITIONS'), 10);
    var pitFrom = rawVal(ra, 'PIT_FROM_LAP');
    var pitTo = rawVal(ra, 'PIT_TO_LAP');
    if (isNaN(bookTime)) bookTime = 10;
    if (isNaN(prTime)) prTime = 10;
    if (isNaN(quTime)) quTime = 5;
    if (isNaN(quLimit)) quLimit = 120;
    if (isNaN(raLaps)) raLaps = 5;
    if (isNaN(raTime)) raTime = 5;
    if (isNaN(waitTime)) waitTime = 0;
    if (isNaN(reversed)) reversed = 0;
    var raceOver = raceOverR !== undefined && raceOverR !== '' ? parseInt(raceOverR, 10) : (raceOverS !== undefined && raceOverS !== '' ? parseInt(raceOverS, 10) : 60);
    var resultScreen = resultR !== undefined && resultR !== '' ? parseInt(resultR, 10) : (resultS !== undefined && resultS !== '' ? parseInt(resultS, 10) : 60);
    if (isNaN(raceOver)) raceOver = 60;
    if (isNaN(resultScreen)) resultScreen = 60;
    var limitBy = (raLaps > 0) ? 'laps' : 'time';
    var joinType = (startRule === '1') ? 2 : 1;
    return {
      pickupMode: pickup === '1' || pickup === 1,
      lockedEntryList: locked === '1' || locked === 1,
      loopMode: loop === '1' || loop === 1,
      bookingEnabled: bookSo.enabled && (bookOpen === '1' || bookOpen === 1),
      bookingTime: isNaN(bookTime) ? 10 : Math.max(0, Math.min(60, bookTime)),
      practiceEnabled: prSo.enabled && (prOpen === '1' || prOpen === 1),
      practiceTime: isNaN(prTime) ? 10 : Math.max(0, Math.min(120, prTime)),
      practiceCanJoin: prSo.enabled && (prOpen === '1' || prOpen === 1),
      qualifyEnabled: quSo.enabled && (quOpen === '1' || quOpen === 1),
      qualifyTime: isNaN(quTime) ? 5 : Math.max(0, Math.min(120, quTime)),
      qualifyCanJoin: quSo.enabled && (quOpen === '1' || quOpen === 1),
      qualifyLimitPercent: isNaN(quLimit) ? 120 : Math.max(100, Math.min(150, quLimit)),
      raceEnabled: raSo.enabled && (raOpen === '1' || raOpen === 1),
      limitBy: limitBy,
      raceTime: limitBy === 'time' ? (isNaN(raTime) ? 5 : Math.max(0, Math.min(120, raTime))) : 5,
      raceLaps: limitBy === 'laps' ? (raLaps > 0 ? raLaps : 5) : 0,
      raceWaitTime: Math.max(0, Math.min(300, waitTime)),
      raceOverTime: Math.max(0, Math.min(120, raceOver)),
      resultScreenTime: Math.max(0, Math.min(120, resultScreen)),
      joinType: joinType,
      extraLapAfterLeader: extraLap === '1' || extraLap === 1,
      reversedGrid: reversed,
      pitFromLap: pitFrom !== undefined && pitFrom !== null ? String(pitFrom) : '',
      pitToLap: pitTo !== undefined && pitTo !== null ? String(pitTo) : ''
    };
  }

  function sessionsVmToIniUpdates(vm) {
    var u = [];
    u.push({ section: 'SERVER', key: 'PICKUP_MODE_ENABLED', value: vm.pickupMode ? 1 : 0 });
    u.push({ section: 'SERVER', key: 'LOCKED_ENTRY_LIST', value: vm.lockedEntryList ? 1 : 0 });
    u.push({ section: 'SERVER', key: 'LOOP_MODE', value: vm.loopMode ? 1 : 0 });
    u.push({ section: 'BOOK', key: 'IS_OPEN', value: vm.bookingEnabled ? 1 : 0 });
    u.push({ section: 'BOOK', key: 'TIME', value: vm.bookingTime });
    u.push({ section: 'PRACTICE', key: 'IS_OPEN', value: vm.practiceEnabled ? 1 : 0 });
    u.push({ section: 'PRACTICE', key: 'TIME', value: vm.practiceTime });
    u.push({ section: 'QUALIFY', key: 'IS_OPEN', value: vm.qualifyEnabled ? 1 : 0 });
    u.push({ section: 'QUALIFY', key: 'TIME', value: vm.qualifyTime });
    u.push({ section: 'SERVER', key: 'QUALIFY_MAX_WAIT_PERC', value: vm.qualifyLimitPercent });
    u.push({ section: 'RACE', key: 'IS_OPEN', value: vm.raceEnabled ? 1 : 0 });
    u.push({ section: 'RACE', key: 'LAPS', value: vm.limitBy === 'laps' ? (vm.raceLaps > 0 ? vm.raceLaps : 5) : 0 });
    u.push({ section: 'RACE', key: 'TIME', value: vm.limitBy === 'time' ? vm.raceTime : 0 });
    u.push({ section: 'RACE', key: 'WAIT_TIME', value: vm.raceWaitTime });
    u.push({ section: 'RACE', key: 'RACE_OVER_TIME', value: vm.raceOverTime });
    u.push({ section: 'RACE', key: 'RESULT_SCREEN_TIME', value: vm.resultScreenTime });
    u.push({ section: 'SERVER', key: 'START_RULE', value: vm.joinType === 2 ? 1 : 0 });
    u.push({ section: 'SERVER', key: 'RACE_EXTRA_LAP', value: vm.extraLapAfterLeader ? 1 : 0 });
    u.push({ section: 'SERVER', key: 'REVERSED_GRID_RACE_POSITIONS', value: vm.reversedGrid });
    u.push({ section: 'RACE', key: 'PIT_FROM_LAP', value: vm.pitFromLap });
    u.push({ section: 'RACE', key: 'PIT_TO_LAP', value: vm.pitToLap });
    return u;
  }

  function applySessionsVmToUI(vm) {
    if (!vm) return;
    var el = function (id) { return document.getElementById(id); };
    if (el('sc-pickup-mode')) el('sc-pickup-mode').checked = !!vm.pickupMode;
    if (el('sc-locked-entry-list')) { el('sc-locked-entry-list').checked = !!vm.lockedEntryList; el('sc-locked-entry-list').disabled = !vm.pickupMode; }
    if (el('sc-loop-mode')) el('sc-loop-mode').checked = !!vm.loopMode;
    var bookingEl = el('sc-booking-enabled');
    if (bookingEl) {
      bookingEl.disabled = !!vm.pickupMode;
      bookingEl.checked = !!vm.pickupMode ? false : !!vm.bookingEnabled;
    }
    setIniControl('sc-booking-time', vm.bookingTime);
    if (el('sc-practice-enabled')) el('sc-practice-enabled').checked = !!vm.practiceEnabled;
    setIniControl('sc-practice-time', vm.practiceTime);
    if (el('sc-practice-open')) el('sc-practice-open').checked = !!vm.practiceCanJoin;
    if (el('sc-qualify-enabled')) el('sc-qualify-enabled').checked = !!vm.qualifyEnabled;
    setIniControl('sc-qualify-time', vm.qualifyTime);
    if (el('sc-qualify-open')) el('sc-qualify-open').checked = !!vm.qualifyCanJoin;
    setIniControl('sc-qualify-limit', vm.qualifyLimitPercent);
    if (el('sc-race-enabled')) el('sc-race-enabled').checked = !!vm.raceEnabled;
    if (el('sc-race-limit-by')) el('sc-race-limit-by').value = vm.limitBy || 'laps';
    setIniControl('sc-race-time', vm.raceTime);
    setIniControl('sc-race-laps', vm.raceLaps);
    setIniControl('sc-race-wait', vm.raceWaitTime);
    setIniControl('sc-race-over', vm.raceOverTime);
    setIniControl('sc-race-result', vm.resultScreenTime);
    if (el('sc-race-open')) {
      var v = String(vm.joinType !== undefined ? vm.joinType : 1);
      if (el('sc-race-open').querySelector('option[value="' + v.replace(/"/g, '&quot;') + '"]')) el('sc-race-open').value = v;
    }
    if (el('sc-race-mandatory-pit')) el('sc-race-mandatory-pit').checked = !!vm.extraLapAfterLeader;
    setIniControl('sc-race-reversed', vm.reversedGrid);
    setIniControl('sc-race-from-lap', vm.pitFromLap);
    setIniControl('sc-race-to-lap', vm.pitToLap);
    updateSessionRaceLimitVisibility();
    updateSliderLabels();
  }

  function readSessionsVmFromUI() {
    var el = function (id) { return document.getElementById(id); };
    var num = function (id, def) { var e = el(id); var n = parseInt(e && e.value !== undefined ? e.value : '', 10); return isNaN(n) ? def : n; };
    sessionsVm.pickupMode = el('sc-pickup-mode') && el('sc-pickup-mode').checked;
    sessionsVm.lockedEntryList = el('sc-locked-entry-list') && el('sc-locked-entry-list').checked;
    sessionsVm.loopMode = el('sc-loop-mode') && el('sc-loop-mode').checked;
    sessionsVm.bookingEnabled = !sessionsVm.pickupMode && el('sc-booking-enabled') && el('sc-booking-enabled').checked;
    sessionsVm.bookingTime = num('sc-booking-time', 10);
    sessionsVm.practiceEnabled = el('sc-practice-enabled') && el('sc-practice-enabled').checked;
    sessionsVm.practiceTime = num('sc-practice-time', 10);
    sessionsVm.practiceCanJoin = el('sc-practice-open') && el('sc-practice-open').checked;
    sessionsVm.qualifyEnabled = el('sc-qualify-enabled') && el('sc-qualify-enabled').checked;
    sessionsVm.qualifyTime = num('sc-qualify-time', 5);
    sessionsVm.qualifyCanJoin = el('sc-qualify-open') && el('sc-qualify-open').checked;
    sessionsVm.qualifyLimitPercent = num('sc-qualify-limit', 120);
    sessionsVm.raceEnabled = el('sc-race-enabled') && el('sc-race-enabled').checked;
    sessionsVm.limitBy = (el('sc-race-limit-by') && el('sc-race-limit-by').value) || 'laps';
    sessionsVm.raceTime = num('sc-race-time', 5);
    sessionsVm.raceLaps = num('sc-race-laps', 5);
    sessionsVm.raceWaitTime = num('sc-race-wait', 0);
    sessionsVm.raceOverTime = num('sc-race-over', 60);
    sessionsVm.resultScreenTime = num('sc-race-result', 60);
    sessionsVm.joinType = num('sc-race-open', 1);
    sessionsVm.extraLapAfterLeader = el('sc-race-mandatory-pit') && el('sc-race-mandatory-pit').checked;
    sessionsVm.reversedGrid = num('sc-race-reversed', 0);
    sessionsVm.pitFromLap = (el('sc-race-from-lap') && el('sc-race-from-lap').value !== undefined) ? String(el('sc-race-from-lap').value).trim() : '';
    sessionsVm.pitToLap = (el('sc-race-to-lap') && el('sc-race-to-lap').value !== undefined) ? String(el('sc-race-to-lap').value).trim() : '';
    sessionsVm.practiceCanJoin = sessionsVm.practiceEnabled;
    sessionsVm.qualifyCanJoin = sessionsVm.qualifyEnabled;
  }

  function loadSessionsFromRaw(serverId) {
    getRawConfig(serverId, function (raw, err) {
      if (err || !raw) return;
      sessionsVm = deriveSessionsVmFromIni(raw);
      sessionsLastKnownGood = JSON.parse(JSON.stringify(sessionsVm));
      applySessionsVmToUI(sessionsVm);
      getRevision(serverId, function (rev) { if (rev && rev.revision) sessionsRevision = rev.revision; });
    });
  }

  function loadSessionsFromRawPromise(serverId) {
    var sid = serverId || (serverConfigData.server_ids && serverConfigData.server_ids[0]) || '';
    return new Promise(function (resolve) {
      getRawConfig(sid, function (raw, err) {
        if (err || !raw) {
          resolve();
          return;
        }
        sessionsVm = deriveSessionsVmFromIni(raw);
        sessionsLastKnownGood = JSON.parse(JSON.stringify(sessionsVm));
        applySessionsVmToUI(sessionsVm);
        getRevision(sid, function (rev) { if (rev && rev.revision) sessionsRevision = rev.revision; });
        resolve();
      });
    });
  }

  function fetchCarsForModalIfNeededPromise() {
    if (getCarsForModal().length > 0) return Promise.resolve();
    return pitboxFetch(API_BASE + '/cars')
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (carData) {
        var list = Array.isArray(carData) ? carData : (carData && carData.cars) ? carData.cars : [];
        setCarsForModal(list || []);
      })
      .catch(function () {});
  }

  function commitSessionsPatch() {
    if (sessionsPatchDebounceTimer) { clearTimeout(sessionsPatchDebounceTimer); sessionsPatchDebounceTimer = null; }
    readSessionsVmFromUI();
    var serverId = (document.getElementById('sc-instance') && document.getElementById('sc-instance').value) || (serverConfigData.server_ids && serverConfigData.server_ids[0]);
    var updates = sessionsVmToIniUpdates(sessionsVm);
    patchConfig(serverId, updates, function (updatedRaw, err) {
      if (err) {
        applySessionsVmToUI(sessionsLastKnownGood);
        if (typeof showToast === 'function') showToast('Failed to save session settings', 'error');
        return;
      }
      if (updatedRaw) {
        sessionsVm = deriveSessionsVmFromIni(updatedRaw);
        sessionsLastKnownGood = JSON.parse(JSON.stringify(sessionsVm));
        applySessionsVmToUI(sessionsVm);
      }
      getRevision(serverId, function (r) { if (r && r.revision) sessionsRevision = r.revision; });
    });
  }

  function scheduleSessionsPatch(isSlider) {
    if (sessionsPatchDebounceTimer) clearTimeout(sessionsPatchDebounceTimer);
    if (isSlider) {
      sessionsPatchDebounceTimer = setTimeout(commitSessionsPatch, 350);
    } else {
      sessionsPatchDebounceTimer = setTimeout(commitSessionsPatch, 100);
    }
  }

  function bindSessionsPanel() {
    if (document.getElementById('page-server-config').dataset.sessionsBound) return;
    document.getElementById('page-server-config').dataset.sessionsBound = '1';

    var sessionIds = [
      'sc-pickup-mode', 'sc-locked-entry-list', 'sc-loop-mode',
      'sc-booking-enabled', 'sc-booking-time', 'sc-practice-enabled', 'sc-practice-time', 'sc-practice-open',
      'sc-qualify-enabled', 'sc-qualify-time', 'sc-qualify-open', 'sc-qualify-limit',
      'sc-race-enabled', 'sc-race-limit-by', 'sc-race-time', 'sc-race-laps', 'sc-race-wait',
      'sc-race-over', 'sc-race-result', 'sc-race-open', 'sc-race-mandatory-pit', 'sc-race-reversed',
      'sc-race-from-lap', 'sc-race-to-lap'
    ];
    sessionIds.forEach(function (id) {
      var el = document.getElementById(id);
      if (!el) return;
      var isSlider = el.type === 'range';
      function onChange() {
        readSessionsVmFromUI();
        updateSessionRaceLimitVisibility();
        updateSliderLabels();
        scheduleSessionsPatch(isSlider);
      }
      if (isSlider) {
        el.addEventListener('input', function () { updateSliderLabels(); });
        el.addEventListener('change', onChange);
        el.addEventListener('mousedown', function () { sessionsSliderDragging = true; });
        el.addEventListener('mouseup', function () { sessionsSliderDragging = false; commitSessionsPatch(); });
        el.addEventListener('mouseleave', function () { sessionsSliderDragging = false; });
        el.addEventListener('touchstart', function () { sessionsSliderDragging = true; }, { passive: true });
        el.addEventListener('touchend', function () { sessionsSliderDragging = false; commitSessionsPatch(); }, { passive: true });
      } else {
        el.addEventListener('change', onChange);
        if (el.type === 'checkbox' && (id === 'sc-pickup-mode')) {
          el.addEventListener('change', function () {
            var locked = document.getElementById('sc-locked-entry-list');
            var booking = document.getElementById('sc-booking-enabled');
            if (locked) locked.disabled = !el.checked;
            if (booking) {
              booking.disabled = !!el.checked;
              if (el.checked) booking.checked = false;
            }
          });
        }
      }
    });

    var instanceSelect = document.getElementById('sc-instance');
    if (instanceSelect && !instanceSelect.dataset.sessionsInstanceBound) {
      instanceSelect.dataset.sessionsInstanceBound = '1';
      instanceSelect.addEventListener('change', function () {
        loadSessionsFromRaw(instanceSelect.value);
      });
    }

    if (sessionsRevisionPollTimer) clearInterval(sessionsRevisionPollTimer);
    sessionsRevisionPollTimer = setInterval(function () {
      if (launchBusy || sessionsSliderDragging) return;
      var serverId = (document.getElementById('sc-instance') && document.getElementById('sc-instance').value) || (serverConfigData.server_ids && serverConfigData.server_ids[0]);
      getRevision(serverId, function (rev, err) {
        if (err || !rev || rev.revision === sessionsRevision) return;
        getRawConfig(serverId, function (raw) {
          if (!raw) return;
          sessionsVm = deriveSessionsVmFromIni(raw);
          sessionsLastKnownGood = JSON.parse(JSON.stringify(sessionsVm));
          applySessionsVmToUI(sessionsVm);
          sessionsRevision = rev.revision;
        });
      });
    }, 2000);
  }

  function setServerConfigLoadedPath(msg, isError) {
    var el = document.getElementById('sc-loaded-path');
    if (!el) return;
    el.textContent = msg || '—';
    el.classList.toggle('sc-loaded-path-error', !!isError);
  }
  function loadServerConfigPage() {
    bindServerConfigAcServerButtons();
    bindServerConfigSaveReload();
    var routeId = getSelectedServerId();
    setServerConfigLoadedPath('Loading…', false);
    var initialId = routeId || 'default';
    pitboxFetch(API_BASE + '/server-config?server_id=' + encodeURIComponent(initialId))
      .then(function (r) { return r.ok ? r.json() : Promise.reject({ status: r.status, message: r.status === 404 ? 'Server cfg path not configured' : 'Status ' + r.status }); })
      .then(function (configData) {
        var apiIds = configData.server_ids || [];
        serverConfigData.server_ids = getOrderedServerIds(apiIds);
        serverConfigData.presets = serverConfigData.server_ids;
        serverConfigData.preset_names = configData.preset_names || {};
        var presetIds = (serverConfigData.server_ids || []).map(function (p) { return String(p); });
        var names = serverConfigData.preset_names || {};
        var byId = new Map(presetIds.map(function (id) { return [id, { id: id, name: names[id] || id }]; }));
        var byIdLower = new Map(presetIds.map(function (id) { return [id.toLowerCase(), id]; }));

        if (presetIds.length === 0) {
          setSelectedServerId(null);
          showServerConfigEmptyState();
          return;
        }

        var chosenId = configData.server_id || null;
        if (routeId && byId.has(routeId)) chosenId = routeId;
        if (!chosenId && routeId && byIdLower.has(routeId.toLowerCase())) chosenId = byIdLower.get(routeId.toLowerCase());
        if (!chosenId) {
          try { var last = localStorage.getItem(LAST_SERVER_PRESET_KEY); if (last && byId.has(last)) chosenId = last; } catch (e) {}
        }
        if (!chosenId && presetIds.length) chosenId = presetIds[0];

        if (chosenId) {
          setSelectedServerId(chosenId);
          serverConfigData.server_id = chosenId;
        } else {
          showServerConfigEmptyState();
          return;
        }

        if (PITBOX_DEBUG) {
          console.log('[PitBox] presets ids:', presetIds, 'routeId:', routeId, 'chosenId:', chosenId);
        }

        function applyConfigAndShow(data) {
          serverConfigData.server_cfg = data.server_cfg || {};
          serverConfigData.entry_list = Array.isArray(data.entry_list) ? data.entry_list : [];
          serverConfigData.server_root = data.server_root || null;
          serverConfigData.blacklist_path = data.blacklist_path || null;
          serverConfigData.server_cfg_path = data.server_cfg_path || null;
          var inst = document.getElementById('sc-instance');
          if (inst && serverConfigData.server_ids.length > 0) {
            var names = serverConfigData.preset_names || {};
            inst.innerHTML = serverConfigData.server_ids.map(function (id) {
              var label = names[id] || id;
              return '<option value="' + escapeHtml(id) + '"' + (id === serverConfigData.server_id ? ' selected' : '') + '>' + escapeHtml(label) + '</option>';
            }).join('');
            inst.value = serverConfigData.server_id;
          }
          setServerConfigLoadedPath(serverConfigData.server_cfg_path ? 'Read from: ' + serverConfigData.server_cfg_path : 'Loaded', false);
          var emptyMsgEl = document.getElementById('sc-empty-presets-msg');
          if (emptyMsgEl) emptyMsgEl.style.display = 'none';
          fillServerConfigUI();
          bindServerConfigTabs();
          bindServerConfigControls();
          bindSessionsPanel();
          bindServerConfigPreset();
          bindServerConfigSettings();
          bindUpdatesApply();
          bindCarsPicker();
          bindTrackPicker();
          var sid = serverConfigData.server_id;
          Promise.allSettled([
            refreshAcServerStatus(),
            loadSessionsFromRawPromise(sid),
            fetchCarsForModalIfNeededPromise()
          ]);
        }

        if (chosenId === configData.server_id) {
          applyConfigAndShow(configData);
        } else {
          return pitboxFetch(API_BASE + '/server-config?server_id=' + encodeURIComponent(chosenId))
            .then(function (r) { return r.ok ? r.json() : Promise.reject(new Error('Status ' + r.status)); })
            .then(applyConfigAndShow);
        }
      })
      .catch(function (err) {
        var msg = (err && err.message) ? err.message : 'Could not load server config';
        serverConfigData.server_cfg = {};
        serverConfigData.entry_list = [];
        serverConfigData.preset_names = {};
        serverConfigData.server_cfg_path = null;
        serverConfigData.server_id = null;
        serverConfigData.server_ids = [];
        setServerConfigLoadedPath(msg + '. Set AC server cfg path in SETTINGS, then click Reload.', true);
        showServerConfigEmptyState();
        if (typeof showToast === 'function') showToast('Server Config: ' + msg, 'error');
      });
  }

  function showServerConfigEmptyState() {
    setServerConfigLoadedPath('—', false);
    var inst = document.getElementById('sc-instance');
    if (inst) {
      inst.innerHTML = '<option value="">Select a server…</option>';
      inst.value = '';
    }
    var emptyMsg = document.getElementById('sc-empty-presets-msg');
    if (!emptyMsg && document.getElementById('page-server-config')) {
      var wrap = document.querySelector('#page-server-config .sc-server-config-main');
      if (wrap) {
        var p = document.createElement('p');
        p.id = 'sc-empty-presets-msg';
        p.className = 'sc-placeholder';
        p.textContent = 'No server presets found. Add preset folders under the AC server presets root.';
        wrap.insertBefore(p, wrap.firstChild);
      }
      emptyMsg = document.getElementById('sc-empty-presets-msg');
    }
    if (emptyMsg) {
      emptyMsg.style.display = (serverConfigData.server_ids && serverConfigData.server_ids.length > 0) ? 'none' : 'block';
      emptyMsg.textContent = 'No server presets found. Add preset folders under the AC server presets root.';
    }
    fillServerConfigUI();
    bindServerConfigTabs();
    bindServerConfigControls();
    bindSessionsPanel();
    bindServerConfigPreset();
        bindServerConfigSettings();
        bindUpdatesApply();
        bindCarsPicker();
    bindTrackPicker();
    refreshAcServerStatus();
  }
  function renderServersList(presets, servers) {
    var sidebarList = document.getElementById('sc-presets-list');
    var instanceSelect = document.getElementById('sc-instance');
    var selectVal = instanceSelect && instanceSelect.value;
    var currentId = (selectVal && presets.indexOf(selectVal) !== -1) ? selectVal : (serverConfigData.server_id || selectVal || (presets && presets[0]));
    var serversById = new Map((servers || []).map(function (s) { return [s.server_id, s]; }));
    if (sidebarList) {
      if (!Array.isArray(presets) || presets.length === 0) {
        sidebarList.innerHTML = '<p class="sc-placeholder">No presets. Add preset folders under the AC server presets root.</p>';
      } else {
        var presetNames = serverConfigData.preset_names || {};
        var items = presets.map(function (presetId) {
          var s = serversById.get(presetId);
          var running = !!s;
          var activeClass = presetId === currentId ? ' sc-preset-item-active' : '';
          var displayName = presetNames[presetId] || presetId;
          return '<div class="sc-preset-item' + (running ? ' running' : '') + activeClass + '" data-server-id="' + escapeHtml(presetId) + '" draggable="true" role="listitem" tabindex="0" aria-selected="' + (presetId === currentId) + '" title="Click to select · Double-click to start/stop · Drag to reorder">' +
            '<span class="sc-preset-dot ' + (running ? 'running' : 'stopped') + '" aria-hidden="true"></span>' +
            '<span class="sc-preset-name">' + escapeHtml(displayName) + '</span>' +
            '<button type="button" class="sc-preset-gear" data-server-id="' + escapeHtml(presetId) + '" title="Open folder in Explorer" aria-label="Open preset folder">&#9881;</button>' +
            '</div>';
        });
        sidebarList.innerHTML = items.join('');
        var serverListClickTimeout = null;
        sidebarList.querySelectorAll('.sc-preset-item').forEach(function (row) {
          var id = row.getAttribute('data-server-id');
          row.addEventListener('click', function (e) {
            if (!instanceSelect) return;
            if (serverListClickTimeout) clearTimeout(serverListClickTimeout);
            serverListClickTimeout = setTimeout(function () {
              serverListClickTimeout = null;
              instanceSelect.value = id;
              instanceSelect.dispatchEvent(new Event('change', { bubbles: true }));
            }, 250);
          });
          row.addEventListener('dblclick', function (e) {
            e.preventDefault();
            if (serverListClickTimeout) {
              clearTimeout(serverListClickTimeout);
              serverListClickTimeout = null;
            }
            var dot = row.querySelector('.sc-preset-dot');
            var isRunning = dot && dot.classList.contains('running');
            if (isRunning) {
              doServerConfigPost('/server-config/stop', 'Stop', id);
            } else {
              doServerConfigPost('/server-config/start', 'Start', id);
            }
          });
          row.addEventListener('dragstart', function (e) {
            e.dataTransfer.effectAllowed = 'move';
            e.dataTransfer.setData('text/plain', id);
            e.dataTransfer.setData('application/x-pitbox-server-id', id);
            row.classList.add('sc-preset-item-dragging');
          });
          row.addEventListener('dragend', function () {
            row.classList.remove('sc-preset-item-dragging');
            sidebarList.querySelectorAll('.sc-preset-item-drag-over').forEach(function (r) { r.classList.remove('sc-preset-item-drag-over'); });
          });
          row.addEventListener('dragover', function (e) {
            e.preventDefault();
            e.dataTransfer.dropEffect = 'move';
            if (row.classList.contains('sc-preset-item-dragging')) return;
            row.classList.add('sc-preset-item-drag-over');
          });
          row.addEventListener('dragleave', function () { row.classList.remove('sc-preset-item-drag-over'); });
          row.addEventListener('drop', function (e) {
            e.preventDefault();
            row.classList.remove('sc-preset-item-drag-over');
            var draggedId = e.dataTransfer.getData('text/plain') || e.dataTransfer.getData('application/x-pitbox-server-id');
            if (!draggedId || draggedId === id) return;
            var current = serverConfigData.server_ids || [];
            var idx = current.indexOf(draggedId);
            if (idx === -1) return;
            var targetIdx = current.indexOf(id);
            if (targetIdx === -1) return;
            var newOrder = current.slice();
            newOrder.splice(idx, 1);
            var newTargetIdx = newOrder.indexOf(id);
            newOrder.splice(newTargetIdx, 0, draggedId);
            saveServerOrder(newOrder);
            serverConfigData.server_ids = newOrder;
            serverConfigData.presets = newOrder;
            scheduleAcServerStatusRefresh(250);
          });
          var gearBtn = row.querySelector('.sc-preset-gear');
          if (gearBtn) {
            gearBtn.addEventListener('click', function (e) {
              e.preventDefault();
              e.stopPropagation();
              doServerConfigPost('/server-config/open-preset-folder', 'Open folder', id);
            });
          }
        });
        sidebarList.addEventListener('keydown', function (e) {
          var row = e.target.closest && e.target.closest('.sc-preset-item');
          if (!row || e.target.closest('.sc-preset-gear')) return;
          var list = row.parentNode;
          var items = list ? [].slice.call(list.querySelectorAll('.sc-preset-item')) : [];
          var idx = items.indexOf(row);
          if (e.key === 'ArrowDown' && idx < items.length - 1) {
            e.preventDefault();
            items[idx + 1].focus();
          } else if (e.key === 'ArrowUp' && idx > 0) {
            e.preventDefault();
            items[idx - 1].focus();
          } else if (e.key === 'Enter') {
            e.preventDefault();
            var sid = row.getAttribute('data-server-id');
            if (instanceSelect && sid) {
              instanceSelect.value = sid;
              instanceSelect.dispatchEvent(new Event('change', { bubbles: true }));
            }
          }
        });
      }
    }
    if (instanceSelect && Array.isArray(presets) && presets.length > 0) {
      var names = serverConfigData.preset_names || {};
      var opts = presets.map(function (p) { var label = names[p] || p; return '<option value="' + escapeHtml(p) + '">' + escapeHtml(label) + '</option>'; });
      instanceSelect.innerHTML = opts.join('');
      instanceSelect.value = (presets.indexOf(currentId) !== -1) ? currentId : (presets[0] || '');
    }
  }
  function fillScStatusFromServers(servers) {
    var stateEl = document.getElementById('sc-status-state');
    var detailEl = document.getElementById('sc-status-detail');
    var inst = document.getElementById('sc-instance');
    var sid = (inst && inst.value) ? String(inst.value).trim() : '';
    if (!stateEl) return;
    if (!sid) {
      stateEl.textContent = 'No preset';
      stateEl.className = 'sc-status-badge sc-status-muted';
      if (detailEl) detailEl.textContent = '';
      return;
    }
    var list = Array.isArray(servers) ? servers : [];
    var row = list.filter(function (s) { return s.server_id === sid; })[0];
    if (row && String(row.status) !== 'crashed' && row.pid) {
      stateEl.textContent = 'Running';
      stateEl.className = 'sc-status-badge sc-status-running';
      if (detailEl) {
        var ports = [];
        if (row.udp_port != null) ports.push('UDP ' + row.udp_port);
        if (row.tcp_port != null) ports.push('TCP ' + row.tcp_port);
        detailEl.textContent = 'PID ' + row.pid + (ports.length ? ' · ' + ports.join(' · ') : '');
      }
    } else {
      stateEl.textContent = 'Stopped';
      stateEl.className = 'sc-status-badge sc-status-stopped';
      if (detailEl) detailEl.textContent = '';
    }
  }
  function updateScStatusPanel() {
    var stateEl = document.getElementById('sc-status-state');
    if (!stateEl) return;
    pitboxFetch(API_BASE + '/server-config/process-status')
      .then(function (r) { return r.ok ? r.json() : Promise.reject(new Error('Status ' + r.status)); })
      .then(function (data) { fillScStatusFromServers((data && data.servers) || []); })
      .catch(function () {
        stateEl.textContent = 'Unknown';
        stateEl.className = 'sc-status-badge sc-status-muted';
        var detailEl = document.getElementById('sc-status-detail');
        if (detailEl) detailEl.textContent = '';
      });
  }
  function loadServerConfigDetailsRaw() {
    var pre = document.getElementById('sc-details-raw');
    if (!pre) return;
    var sid = (serverConfigData.server_id || (document.getElementById('sc-instance') && document.getElementById('sc-instance').value) || 'default').trim() || 'default';
    pre.textContent = 'Loading…';
    pitboxFetch(API_BASE + '/server-config/raw?server_id=' + encodeURIComponent(sid))
      .then(function (r) { return r.ok ? r.json() : Promise.reject(new Error('HTTP ' + r.status)); })
      .then(function (data) {
        try {
          pre.textContent = JSON.stringify(data, null, 2);
        } catch (e) {
          pre.textContent = String(data);
        }
      })
      .catch(function (err) {
        pre.textContent = 'Failed to load: ' + (err.message || err);
      });
  }
  function refreshAcServerStatus() {
    return pitboxFetch(API_BASE + '/server-config/process-status')
      .then(function (r) { return r.ok ? r.json() : Promise.reject(new Error('Status ' + r.status)); })
      .then(function (data) {
        var servers = data.servers || [];
        var presets = serverConfigData.presets || serverConfigData.server_ids || [];
        renderServersList(presets, servers);
        fillScStatusFromServers(servers);
      })
      .catch(function (err) {
        var container = document.getElementById('sc-presets-list');
        if (container) container.innerHTML = '<p class="sc-placeholder">Could not load server status. Check that the controller is running and the API is reachable.</p>';
        var stateEl = document.getElementById('sc-status-state');
        if (stateEl) {
          stateEl.textContent = 'Unknown';
          stateEl.className = 'sc-status-badge sc-status-muted';
        }
        var detailEl = document.getElementById('sc-status-detail');
        if (detailEl) detailEl.textContent = '';
      });
  }
  function doServerConfigPost(path, actionLabel, serverId, onSuccess) {
    ensureOperatorOrRedirect().then(function (ok) {
      if (!ok) return;
      return pitboxFetch(API_BASE + path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ server_id: serverId })
      })
        .then(function (r) {
          return r.text().then(function (text) {
            var body = {};
            try { body = text ? JSON.parse(text) : {}; } catch (e) { body = { detail: r.statusText }; }
            return { ok: r.ok, status: r.status, body: body };
          });
        })
        .then(function (res) {
          if (res.ok) {
            if (typeof showToast === 'function') showToast(res.body.message || actionLabel, 'success');
            if (typeof onSuccess === 'function') onSuccess();
          } else {
            var msg = (res.body && res.body.detail) || ('HTTP ' + res.status);
            if (res.status === 404 && (msg === 'Not Found' || msg === 'not found')) {
              msg = 'Endpoint not found. Restart the PitBox controller so it loads the latest code and config.';
            }
            if (typeof showToast === 'function') showToast(actionLabel + ': ' + msg, 'error');
          }
        })
        .catch(function (err) {
          if (typeof showToast === 'function') showToast(actionLabel + ': ' + (err.message || 'request failed'), 'error');
        });
    }).finally(function () {
      scheduleAcServerStatusRefresh(250);
    });
  }
  function saveThenStartOrRestartForServer(path, actionLabel, serverId) {
    ensureOperatorOrRedirect().then(function (ok) {
      if (!ok) return;
    var serverCfg = buildServerCfgFromForm();
    var entryList = collectEntryListFromTable();
    pitboxFetch(API_BASE + '/server-config', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ server_id: serverId, server_cfg: serverCfg, entry_list: entryList })
    })
      .then(function (r) {
        return r.text().then(function (text) {
          var body = {};
          try { body = text ? JSON.parse(text) : {}; } catch (e) { body = {}; }
          return { ok: r.ok, status: r.status, body: body };
        });
      })
      .then(function (res) {
        if (res.ok) {
          serverConfigData.server_cfg = serverCfg;
          serverConfigData.entry_list = entryList;
          invalidatePresetDiskStateClient(serverId);
          doServerConfigPost(path, actionLabel, serverId);
        } else {
          var msg = (res.body && res.body.detail) || ('Save failed: ' + res.status);
          if (typeof showToast === 'function') showToast('Save settings first: ' + msg, 'error');
          scheduleAcServerStatusRefresh(250);
        }
      })
      .catch(function (err) {
        if (typeof showToast === 'function') showToast('Save settings failed: ' + (err.message || 'request failed'), 'error');
        scheduleAcServerStatusRefresh(250);
      });
    });
  }
  // Start/Stop: double-click on preset row (see renderServersList dblclick handler).
  function bindServerConfigAcServerButtons() {}

  var carsPickerBound = false;
  var modalSelectedIds = [];
  var hoveredCarId = null;
  var activeCarId = null;
  var modalVisibleCarIds = [];
  var modalActiveIndex = 0;
  function updateCarsSelected() {
    var el = document.getElementById('sc-cars-selected');
    var input = document.getElementById('sc-cars');
    if (!el || !input) return;
    var raw = (input.value || '').trim();
    var ids = raw ? raw.split(',').map(function (s) { return s.trim(); }).filter(Boolean) : [];
    el.innerHTML = ids.map(function (id) {
      var label = formatCarName(id) || id;
      return '<span class="sc-cars-chip" data-car-id="' + escapeHtml(id) + '">' +
        escapeHtml(label) + ' <button type="button" class="sc-cars-chip-remove" aria-label="Remove">×</button></span>';
    }).join('');
    el.querySelectorAll('.sc-cars-chip-remove').forEach(function (btn) {
      btn.addEventListener('click', function (e) {
        e.preventDefault();
        e.stopPropagation();
        var chip = btn.closest('.sc-cars-chip');
        var id = chip && chip.getAttribute('data-car-id');
        if (id && input) {
          var list = (input.value || '').split(',').map(function (s) { return s.trim(); }).filter(Boolean);
          var idx = list.indexOf(id);
          if (idx !== -1) list.splice(idx, 1);
          input.value = list.join(', ');
          updateCarsSelected();
        }
      });
    });
  }
  function addCarToSelection(carId) {
    var input = document.getElementById('sc-cars');
    if (!input) return;
    var list = (input.value || '').split(',').map(function (s) { return s.trim(); }).filter(Boolean);
    if (list.indexOf(carId) !== -1) return;
    list.push(carId);
    input.value = list.join(', ');
    updateCarsSelected();
  }
  function skinFolder(skinItem) {
    if (skinItem == null) return '';
    return typeof skinItem === 'object' && skinItem.folder != null ? skinItem.folder : String(skinItem);
  }
  function getFirstSkinForCar(carId) {
    var car = getCarById(carId);
    return (car && car.skins && car.skins.length) ? skinFolder(car.skins[0]) : '';
  }
  function addCarToEntryList(carId) {
    var list = serverConfigData.entry_list;
    var firstSkin = getFirstSkinForCar(carId);
    list.push({
      MODEL: carId,
      SKIN: firstSkin,
      GUID: '',
      DRIVERNAME: '',
      TEAM: '',
      BALLAST: '',
      RESTRICTOR: '',
      SPECTATOR_MODE: '0'
    });
    renderEntryListTable();
    var cnt = document.getElementById('sc-entry-count');
    if (cnt) cnt.textContent = serverConfigData.entry_list.length + ' entries';
  }
  function randomizeEntryListSkins() {
    function doRandomize() {
      var list = serverConfigData.entry_list;
      list.forEach(function (entry) {
        var model = (entry && entry.MODEL) ? String(entry.MODEL).trim() : '';
        if (!model) return;
        var car = getCarById(model);
        if (car && car.skins && car.skins.length > 0) {
          entry.SKIN = skinFolder(car.skins[Math.floor(Math.random() * car.skins.length)]);
        }
      });
      renderEntryListTable();
    }
    var cache = getCarsForModal();
    if (cache.length === 0) {
      pitboxFetch(API_BASE + '/cars')
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (data) {
          var list = Array.isArray(data) ? data : (data && data.cars) ? data.cars : [];
          setCarsForModal(list || []);
          doRandomize();
        })
        .catch(function () { doRandomize(); });
    } else {
      doRandomize();
    }
  }
  function deleteAllEntryList() {
    serverConfigData.entry_list.length = 0;
    renderEntryListTable();
    var cnt = document.getElementById('sc-entry-count');
    if (cnt) cnt.textContent = '0 entries';
  }
  function getCarsForModal() {
    return (typeof window !== 'undefined' && window.__pitboxCarsCache) || [];
  }
  function setCarsForModal(list) {
    if (typeof window !== 'undefined') window.__pitboxCarsCache = list;
  }
  function setCarsPathForModal(path) {
    if (typeof window !== 'undefined') window.__pitboxCarsPath = path;
  }
  function getCarsPathForModal() {
    return (typeof window !== 'undefined' && window.__pitboxCarsPath) || '';
  }

  /** Returns official display name from catalog (ui_car.json "name") or null. Uses full carId, never truncated. */
  function resolveCarName(carId) {
    if (!carId || typeof carId !== 'string') return null;
    var id = carId.trim();
    if (!id) return null;
    var list = getCarsForModal();
    for (var i = 0; i < list.length; i++) {
      if ((list[i].car_id || '') === id) return (list[i].name || '').trim() || null;
    }
    return null;
  }

  /** Display fallback: use canonical formatCarName when no catalog name. */
  function formatCarIdFallback(carId) {
    if (!carId || !(carId = String(carId).trim())) return '';
    var formatted = formatCarName(carId);
    return formatted === '—' ? '' : formatted;
  }

  /** Final display name: catalog name ?? prettify fallback. Use this everywhere for Car column / labels. */
  function getCarDisplayName(carId) {
    if (!carId) return '';
    var id = String(carId).trim();
    if (!id) return '';
    var resolved = resolveCarName(id);
    if (resolved != null && resolved !== '') return resolved;
    return formatCarIdFallback(id) || id;
  }

  function formatCarSpecs(c) {
    var parts = [];
    if (c.bhp != null && c.bhp !== '') parts.push(c.bhp + ' bhp');
    if (c.weight != null && c.weight !== '') parts.push(c.weight + ' kg');
    if (c.topspeed != null && c.topspeed !== '') parts.push(c.topspeed + ' km/h');
    return parts.length ? parts.join(', ') : '—';
  }
  function getCarById(carId) {
    var list = getCarsForModal();
    for (var i = 0; i < list.length; i++) if (list[i].car_id === carId) return list[i];
    return null;
  }
  function updatePreviewPanel() {
    var displayId = hoveredCarId || activeCarId || (modalVisibleCarIds.length ? modalVisibleCarIds[0] : null);
    var previewImg = document.getElementById('sc-cars-modal-preview-img');
    var previewFallback = document.getElementById('sc-cars-modal-preview-fallback');
    var previewName = document.getElementById('sc-cars-modal-preview-name');
    var previewClass = document.getElementById('sc-cars-modal-preview-class');
    var previewSpecs = document.getElementById('sc-cars-modal-preview-specs');
    if (!previewName) return;
    if (!displayId) {
      if (previewImg) { previewImg.removeAttribute('src'); previewImg.style.display = 'none'; }
      if (previewFallback) previewFallback.style.display = 'flex';
      previewName.textContent = '—';
      previewClass.textContent = '';
      previewSpecs.textContent = '';
      return;
    }
    var car = getCarById(displayId);
    if (previewImg) {
      previewImg.src = '/api/cars/' + encodeURIComponent(displayId) + '/preview';
      previewImg.style.display = 'block';
      previewImg.onerror = function () {
        previewImg.style.display = 'none';
        if (previewFallback) previewFallback.style.display = 'flex';
      };
    }
    if (previewFallback) previewFallback.style.display = 'none';
    previewName.textContent = formatCarDisplayName(displayId, car ? car.name : null) || displayId || '—';
    previewClass.textContent = car && car.class ? 'Class: ' + car.class : '';
    previewSpecs.textContent = car ? formatCarSpecs(car) : '—';
  }
  function updateModalFooter() {
    var countEl = document.getElementById('sc-cars-modal-selected-count');
    var addBtn = document.getElementById('sc-cars-modal-add');
    var n = modalSelectedIds.length;
    if (countEl) countEl.textContent = 'Selected: ' + n;
    if (addBtn) addBtn.disabled = n === 0;
  }
  function setActiveRowByIndex(index) {
    modalActiveIndex = Math.max(0, Math.min(index, modalVisibleCarIds.length - 1));
    activeCarId = modalVisibleCarIds[modalActiveIndex] || null;
    var listEl = document.getElementById('sc-cars-modal-list');
    if (listEl) {
      listEl.querySelectorAll('.car-row').forEach(function (row) {
        row.classList.toggle('active', row.getAttribute('data-car-id') === activeCarId);
      });
      var activeRow = listEl.querySelector('.car-row.active');
      if (activeRow) activeRow.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }
    updatePreviewPanel();
  }
  function renderModalCarsList(filter) {
    var listEl = document.getElementById('sc-cars-modal-list');
    if (!listEl) return;
    var list = getCarsForModal();
    var q = (filter || '').toLowerCase().trim();
    var filtered = list.filter(function (c) {
      if (!q) return true;
      var name = (c.name || '').toLowerCase();
      var carId = (c.car_id || '').toLowerCase();
      var cls = (c.class || '').toLowerCase();
      var num = q.replace(/\s/g, '');
      return name.indexOf(q) !== -1 || carId.indexOf(q) !== -1 || cls.indexOf(q) !== -1 ||
        (num && (String(c.bhp || '').indexOf(num) !== -1 || String(c.weight || '').indexOf(num) !== -1 || String(c.topspeed || '').indexOf(num) !== -1));
    });
    modalVisibleCarIds = filtered.map(function (c) { return c.car_id; });
    if (modalVisibleCarIds.length && (activeCarId === null || modalVisibleCarIds.indexOf(activeCarId) === -1)) {
      modalActiveIndex = 0;
      activeCarId = modalVisibleCarIds[0];
    } else if (modalVisibleCarIds.length) {
      modalActiveIndex = modalVisibleCarIds.indexOf(activeCarId);
      if (modalActiveIndex < 0) modalActiveIndex = 0;
    } else {
      modalActiveIndex = 0;
      activeCarId = null;
    }
    if (filtered.length === 0) {
      var path = getCarsPathForModal();
      listEl.innerHTML = '<div class="sc-cars-modal-empty">No cars found. ' +
        (path ? 'Path used: <code>' + escapeHtml(path) + '</code>. ' : '') +
        'Set <code>ac_content_root</code> in controller config to your AC install folder (e.g. …\\assettocorsa).</div>';
      updatePreviewPanel();
      updateModalFooter();
      return;
    }
    listEl.innerHTML = filtered.map(function (c) {
      var thumbSrc = '/api/cars/' + encodeURIComponent(c.car_id) + '/preview';
      var thumb = '<img class="car-thumb" src="' + escapeHtml(thumbSrc) + '" alt="" loading="lazy" onerror="this.style.display=\'none\';this.nextElementSibling.style.display=\'flex\';">';
      var fallback = '<div class="car-thumb-fallback" style="display:none">—</div>';
      var selected = modalSelectedIds.indexOf(c.car_id) !== -1 ? ' selected' : '';
      var active = c.car_id === activeCarId ? ' active' : '';
      var displayName = formatCarDisplayName(c.car_id, c.name);
      return '<div class="car-row' + selected + active + '" data-car-id="' + escapeHtml(c.car_id) + '" role="option">' +
        thumb + fallback +
        '<div class="car-meta">' +
        '<div class="car-name">' + escapeHtml(displayName) + '</div>' +
        (c.class ? '<div class="car-class">Class: ' + escapeHtml(c.class) + '</div>' : '') +
        '<div class="car-specs">' + escapeHtml(formatCarSpecs(c)) + '</div></div>' +
        '<div class="car-icon">🐎</div></div>';
    }).join('');
    listEl.querySelectorAll('.car-row').forEach(function (row) {
      var carId = row.getAttribute('data-car-id');
      row.addEventListener('mouseenter', function () {
        hoveredCarId = carId;
        updatePreviewPanel();
      });
      row.addEventListener('mouseleave', function () {
        hoveredCarId = null;
        updatePreviewPanel();
      });
      row.addEventListener('click', function (e) {
        if (e.detail === 2) {
          e.preventDefault();
          addCarToEntryList(carId);
          return;
        }
        activeCarId = carId;
        modalActiveIndex = modalVisibleCarIds.indexOf(carId);
        listEl.querySelectorAll('.car-row').forEach(function (r) { r.classList.remove('active'); });
        row.classList.add('active');
        var idx = modalSelectedIds.indexOf(carId);
        if (idx !== -1) {
          modalSelectedIds.splice(idx, 1);
          row.classList.remove('selected');
        } else {
          modalSelectedIds.push(carId);
          row.classList.add('selected');
        }
        updatePreviewPanel();
        updateModalFooter();
      });
    });
    updatePreviewPanel();
    updateModalFooter();
  }
  function openCarsModal() {
    var modal = document.getElementById('sc-cars-modal');
    var filterEl = document.getElementById('sc-cars-modal-filter');
    var listEl = document.getElementById('sc-cars-modal-list');
    if (!modal || !listEl) return;
    modalSelectedIds = [];
    hoveredCarId = null;
    activeCarId = null;
    modalVisibleCarIds = [];
    modalActiveIndex = 0;
    modal.classList.remove('hidden');
    var savedFilter = (typeof window !== 'undefined' && window.__pitboxCarsFilter) || '';
    if (filterEl) {
      filterEl.value = savedFilter;
      filterEl.focus();
    }
    if (getCarsForModal().length === 0) {
      listEl.innerHTML = '';
      if (listEl.classList) listEl.classList.add('sc-cars-list-error');
      pitboxFetch(API_BASE + '/cars')
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (data) {
          var list = Array.isArray(data) ? data : (data && data.cars) ? data.cars : [];
          var path = (data && data.cars_path) ? data.cars_path : '';
          setCarsForModal(list || []);
          setCarsPathForModal(path || '');
          if (listEl.classList) listEl.classList.remove('sc-cars-list-error');
          renderModalCarsList(savedFilter);
          var savedScroll = (typeof window !== 'undefined' && window.__pitboxCarsScroll) || 0;
          listEl.scrollTop = savedScroll;
        })
        .catch(function (err) {
          setCarsForModal([]);
          setCarsPathForModal('');
          if (typeof showToast === 'function') showToast('Could not load cars list. Check controller config (ac_content_root / content path) and that the API is reachable.', 'error');
        });
    } else {
      renderModalCarsList(savedFilter);
      var savedScroll = (typeof window !== 'undefined' && window.__pitboxCarsScroll) || 0;
      listEl.scrollTop = savedScroll;
    }
    updateModalFooter();
  }
  function closeCarsModal() {
    var modal = document.getElementById('sc-cars-modal');
    var filterEl = document.getElementById('sc-cars-modal-filter');
    var listEl = document.getElementById('sc-cars-modal-list');
    if (typeof window !== 'undefined' && filterEl) window.__pitboxCarsFilter = filterEl.value || '';
    if (typeof window !== 'undefined' && listEl) window.__pitboxCarsScroll = listEl.scrollTop || 0;
    if (modal) modal.classList.add('hidden');
  }
  function bindCarsPicker() {
    updateCarsSelected();
    var openBtn = document.getElementById('sc-cars-open-picker');
    var modal = document.getElementById('sc-cars-modal');
    var backdrop = document.getElementById('sc-cars-modal-backdrop');
    var closeBtn = document.getElementById('sc-cars-modal-close');
    var addBtn = document.getElementById('sc-cars-modal-add');
    var cancelBtn = document.getElementById('sc-cars-modal-cancel');
    var filterEl = document.getElementById('sc-cars-modal-filter');
    if (openBtn && !openBtn.dataset.bound) {
      openBtn.dataset.bound = '1';
      openBtn.addEventListener('click', function () { openCarsModal(); });
    }
    if (backdrop) backdrop.addEventListener('click', closeCarsModal);
    if (closeBtn) closeBtn.addEventListener('click', closeCarsModal);
    if (cancelBtn) cancelBtn.addEventListener('click', closeCarsModal);
    if (addBtn && !addBtn.dataset.bound) {
      addBtn.dataset.bound = '1';
      addBtn.addEventListener('click', function () {
        modalSelectedIds.forEach(function (id) { addCarToEntryList(id); });
        modalSelectedIds.length = 0;
        var list = document.getElementById('sc-cars-modal-list');
        if (list) list.querySelectorAll('.car-row').forEach(function (r) { r.classList.remove('selected'); });
        updateModalFooter();
      });
    }
    if (filterEl && !filterEl.dataset.bound) {
      filterEl.dataset.bound = '1';
      filterEl.addEventListener('input', function () { renderModalCarsList(filterEl.value); });
      filterEl.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') { closeCarsModal(); return; }
        if (e.key === 'ArrowDown') {
          e.preventDefault();
          setActiveRowByIndex(modalActiveIndex + 1);
          return;
        }
        if (e.key === 'ArrowUp') {
          e.preventDefault();
          setActiveRowByIndex(modalActiveIndex - 1);
          return;
        }
        if (e.key === 'Enter') {
          e.preventDefault();
          if (e.ctrlKey || e.metaKey || e.shiftKey) {
            if (activeCarId) addCarToEntryList(activeCarId);
            return;
          }
          if (activeCarId) {
            var idx = modalSelectedIds.indexOf(activeCarId);
            if (idx !== -1) {
              modalSelectedIds.splice(idx, 1);
            } else {
              modalSelectedIds.push(activeCarId);
            }
            var listEl = document.getElementById('sc-cars-modal-list');
            if (listEl) {
              var row = listEl.querySelector('.car-row[data-car-id="' + (window.CSS && window.CSS.escape ? window.CSS.escape(activeCarId) : activeCarId) + '"]');
              if (row) row.classList.toggle('selected', modalSelectedIds.indexOf(activeCarId) !== -1);
            }
            updateModalFooter();
          }
          return;
        }
      });
    }
    if (modal && !carsPickerBound) {
      carsPickerBound = true;
      document.addEventListener('keydown', function (e) {
        if (e.key !== 'Escape') return;
        if (modal && !modal.classList.contains('hidden')) closeCarsModal();
      });
    }
  }
  function normalizeTrackKey(raw) {
    var s = String(raw || '').trim();
    if (s.indexOf('/') !== -1 || s.indexOf('\\') !== -1) s = s.replace(/\\/g, '/').split('/').filter(Boolean).pop() || s;
    return s.toLowerCase();
  }
  function normalizeLayoutKey(raw) {
    var s = String(raw || '').trim().toLowerCase();
    return s.replace(/^layout_/, '') || 'default';
  }

  var trackPickerState = { tracks: [], selectedTrack: null, selectedLayoutIndex: 0, filterCountry: 'All', searchDebounceTimer: null };
  function openTrackPickerModal() {
    var modal = document.getElementById('sc-track-picker-modal');
    var listEl = document.getElementById('sc-track-picker-list');
    var searchEl = document.getElementById('sc-track-picker-search');
    var warningEl = document.getElementById('sc-track-picker-warning');
    var trackEl = document.getElementById('sc-track');
    var layoutEl = document.getElementById('sc-config-track');
    var serverId = (serverConfigData && serverConfigData.server_id) ? String(serverConfigData.server_id).trim() : 'default';
    if (!modal || !listEl) return;
    modal.classList.remove('hidden');
    if (searchEl) searchEl.value = '';
    if (warningEl) { warningEl.classList.add('hidden'); warningEl.textContent = ''; }
    trackPickerState.selectedTrack = null;
    trackPickerState.selectedLayoutIndex = 0;

    var serverEl = document.getElementById('sc-track-picker-server');
    var trackLabelEl = document.getElementById('sc-track-picker-current-track');
    if (serverEl) {
      var names = (serverConfigData && serverConfigData.preset_names) || {};
      serverEl.textContent = serverId ? ('Server: ' + (names[serverId] || serverId)) : 'Server: Select a server…';
    }
    if (trackLabelEl) trackLabelEl.textContent = 'Current track: …';

    function applyInitialSelection(initialTrack, initialLayout) {
      var displayTrack = (initialTrack && initialTrack.trim()) ? formatTrackName(initialTrack) : '';
      var displayLayout = (initialLayout && initialLayout.trim() && initialLayout.toLowerCase() !== 'default') ? formatLayoutName(initialLayout) : '';
      if (trackLabelEl) {
        var currentDisplay = displayTrack + (displayLayout ? ' – ' + displayLayout : '') || '—';
        trackLabelEl.textContent = currentDisplay ? ('Current track: ' + currentDisplay) : 'Current track: —';
      }
      trackPickerState.tracks = Array.isArray(trackPickerState.tracks) ? trackPickerState.tracks : [];
      buildTrackPickerFilterOptions();
      var tr = null;
      var layoutIndex = 0;
      if (initialTrack && initialTrack.trim()) {
        var wantTrackKey = normalizeTrackKey(initialTrack);
        for (var i = 0; i < trackPickerState.tracks.length; i++) {
          if (normalizeTrackKey(trackPickerState.tracks[i].track_id) === wantTrackKey) {
            tr = trackPickerState.tracks[i];
            break;
          }
        }
        if (tr && initialLayout && tr.layouts && tr.layouts.length) {
          var wantLayoutKey = normalizeLayoutKey(initialLayout);
          for (var j = 0; j < tr.layouts.length; j++) {
            if (normalizeLayoutKey(tr.layouts[j].layout_id) === wantLayoutKey) {
              layoutIndex = j;
              break;
            }
          }
        }
      }
      if (tr) {
        trackPickerState.selectedTrack = tr;
        trackPickerState.selectedLayoutIndex = layoutIndex;
        if (warningEl) warningEl.classList.add('hidden');
      } else if (initialTrack && initialTrack.trim()) {
        var rawDisplay = [initialTrack, (initialLayout && initialLayout.trim()) ? initialLayout : ''].filter(Boolean).join(' / ');
        if (warningEl) {
          warningEl.textContent = 'Current server track not found in catalog: ' + rawDisplay;
          warningEl.classList.remove('hidden');
        }
      }
      renderTrackPickerList();
      updateTrackPickerDetail();
      if (tr && listEl) {
        requestAnimationFrame(function () {
          var id = tr.track_id || '';
          var activeItem = listEl.querySelector('.sc-track-picker-item[data-track-id="' + (window.CSS && window.CSS.escape ? window.CSS.escape(id) : id.replace(/\\/g, '\\\\').replace(/"/g, '\\"')) + '"]');
          if (activeItem) activeItem.scrollIntoView({ block: 'center', behavior: 'auto' });
        });
      }
    }

    Promise.all([
      pitboxFetch(API_BASE + '/servers/' + encodeURIComponent(serverId) + '/current_config').then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; }),
      pitboxFetch(API_BASE + '/catalogs/tracks').then(function (r) { return r.ok ? r.json() : Promise.reject(new Error('Failed to load tracks')); })
    ]).then(function (results) {
      var currentConfig = results[0];
      var catalogData = results[1];
      trackPickerState.tracks = Array.isArray(catalogData.tracks) ? catalogData.tracks : [];
      var initialTrack = '';
      var initialLayout = '';
      if (currentConfig && (currentConfig.track != null || currentConfig.layout != null)) {
        initialTrack = (currentConfig.track != null) ? String(currentConfig.track).trim() : '';
        initialLayout = (currentConfig.layout != null) ? String(currentConfig.layout).trim() : '';
      } else {
        var tid = (trackEl && trackEl.value) ? String(trackEl.value).trim() : '';
        var lid = (layoutEl && layoutEl.value) ? String(layoutEl.value).trim() : '';
        initialTrack = tid || '';
        initialLayout = lid || '';
      }
      applyInitialSelection(initialTrack, initialLayout);
    }).catch(function () {
      trackPickerState.tracks = [];
      buildTrackPickerFilterOptions();
      var tid = (trackEl && trackEl.value) ? String(trackEl.value).trim() : '';
      var lid = (layoutEl && layoutEl.value) ? String(layoutEl.value).trim() : '';
      if (trackLabelEl) trackLabelEl.textContent = 'Current track: —';
      renderTrackPickerList();
      updateTrackPickerDetail();
      if (warningEl) warningEl.classList.add('hidden');
    });
  }
  function closeTrackPickerModal() {
    var modal = document.getElementById('sc-track-picker-modal');
    if (modal) modal.classList.add('hidden');
  }
  function buildTrackPickerFilterOptions() {
    var tracks = trackPickerState.tracks || [];
    var countries = [];
    tracks.forEach(function (t) {
      if (t.disabled) return;
      var c = (t.country || '').trim();
      if (c && c !== 'Unknown') countries.push(c);
    });
    countries = countries.filter(function (c, i, a) { return a.indexOf(c) === i; }).sort();
    var countryEl = document.getElementById('sc-track-picker-country');
    if (countryEl) {
      var opts = '<option value="All">All</option>';
      countries.forEach(function (c) { opts += '<option value="' + escapeHtml(c) + '">' + escapeHtml(c) + '</option>'; });
      countryEl.innerHTML = opts;
      countryEl.value = trackPickerState.filterCountry === 'All' ? 'All' : (countries.indexOf(trackPickerState.filterCountry) !== -1 ? trackPickerState.filterCountry : 'All');
      if (!countryEl.dataset.bound) {
        countryEl.dataset.bound = '1';
        countryEl.addEventListener('change', function () {
          trackPickerState.filterCountry = countryEl.value;
          renderTrackPickerList();
        });
      }
    }
  }
  function applyTrackPickerFilters(tracks) {
    var searchEl = document.getElementById('sc-track-picker-search');
    var countryEl = document.getElementById('sc-track-picker-country');
    var q = (searchEl && searchEl.value) ? searchEl.value.trim().toLowerCase() : '';
    var country = (countryEl && countryEl.value) ? countryEl.value : 'All';
    return tracks.filter(function (t) {
      if (t.disabled) return false;
      if (q) {
        var name = (t.name || '').toLowerCase();
        var id = (t.track_id || '').toLowerCase();
        var layoutMatch = false;
        if (t.layouts && Array.isArray(t.layouts)) {
          layoutMatch = t.layouts.some(function (l) { return ((l.name || '').toLowerCase().indexOf(q) !== -1 || (l.layout_id || '').toLowerCase().indexOf(q) !== -1); });
        }
        if (name.indexOf(q) === -1 && id.indexOf(q) === -1 && !layoutMatch) return false;
      }
      if (country !== 'All') {
        var tCountry = (t.country || '').trim() || 'Unknown';
        if (tCountry !== country) return false;
      }
      return true;
    });
  }
  function renderTrackPickerList() {
    var listEl = document.getElementById('sc-track-picker-list');
    var searchEl = document.getElementById('sc-track-picker-search');
    var tracks = applyTrackPickerFilters(trackPickerState.tracks);
    var recentIds = getRecentTrackIds();
    if (recentIds.length > 0) {
      tracks = tracks.slice().sort(function (a, b) {
        var ai = recentIds.indexOf(a.track_id);
        var bi = recentIds.indexOf(b.track_id);
        if (ai === -1 && bi === -1) return 0;
        if (ai === -1) return 1;
        if (bi === -1) return -1;
        return ai - bi;
      });
    }
    var selectedId = trackPickerState.selectedTrack ? trackPickerState.selectedTrack.track_id : null;
    listEl.innerHTML = tracks.map(function (t) {
      var n = (t.layouts && t.layouts.length) || 0;
      var label = (t.name || formatTrackName(t.track_id) || '—') + (n > 0 ? ' (' + n + ')' : '');
      var active = t.track_id === selectedId ? ' sc-track-picker-item-active' : '';
      return '<div class="sc-track-picker-item' + active + '" data-track-id="' + escapeHtml(t.track_id) + '" role="option">' + escapeHtml(label) + '</div>';
    }).join('');
    listEl.querySelectorAll('.sc-track-picker-item').forEach(function (item) {
      item.addEventListener('click', function () {
        var id = item.getAttribute('data-track-id');
        var tr = trackPickerState.tracks.filter(function (t) { return t.track_id === id; })[0];
        if (tr) {
          trackPickerState.selectedTrack = tr;
          trackPickerState.selectedLayoutIndex = 0;
          updateTrackPickerDetail();
          listEl.querySelectorAll('.sc-track-picker-item').forEach(function (el) { el.classList.toggle('sc-track-picker-item-active', el.getAttribute('data-track-id') === id); });
        }
      });
    });
  }
  function updateTrackPickerDetail() {
    var tr = trackPickerState.selectedTrack;
    var idx = trackPickerState.selectedLayoutIndex;
    var detailImg = document.getElementById('sc-track-picker-detail-img');
    var detailFallback = document.getElementById('sc-track-picker-detail-fallback');
    var detailLayouts = document.getElementById('sc-track-picker-detail-layouts');
    var pickerName = document.getElementById('sc-track-picker-info-name');
    var pickerLayout = document.getElementById('sc-track-picker-info-layout');
    var pickerDescription = document.getElementById('sc-track-picker-description');
    var pickerDescriptionWrap = document.getElementById('sc-track-picker-description-wrap');
    var pickerDetails = document.getElementById('sc-track-picker-details');
    var titleEl = document.getElementById('sc-track-picker-title');
    var emptyVal = '—';
    if (!tr) {
      if (titleEl) titleEl.textContent = 'Select track';
      if (detailImg) { detailImg.src = ''; detailImg.style.display = 'none'; }
      if (detailFallback) { detailFallback.style.display = 'flex'; detailFallback.textContent = '—'; }
      if (detailLayouts) detailLayouts.innerHTML = '';
      if (pickerName) pickerName.textContent = emptyVal;
      if (pickerLayout) pickerLayout.textContent = emptyVal;
      if (pickerDescription) pickerDescription.innerHTML = '<p class="sc-track-hero-info-para">' + escapeHtml(emptyVal) + '</p>';
      if (pickerDetails) pickerDetails.innerHTML = '';
      return;
    }
    var layout = (tr.layouts && tr.layouts[idx]) || (tr.layouts && tr.layouts[0]) || null;
    var layoutId = layout ? (layout.layout_id || 'default') : 'default';
    var name = layout ? (layout.name || tr.name || formatTrackName(tr.track_id)) : (tr.name || formatTrackName(tr.track_id));
    if (titleEl) titleEl.textContent = name || formatTrackName(tr.track_id) || '—';
    var previewUrl = API_BASE + '/tracks/' + encodeURIComponent(tr.track_id) + '/layouts/' + encodeURIComponent(layoutId) + '/preview';
    if (detailImg) {
      detailImg.src = previewUrl;
      detailImg.alt = name;
      detailImg.style.display = '';
      detailImg.onerror = function () { this.style.display = 'none'; if (detailFallback) detailFallback.style.display = 'flex'; };
    }
    if (detailFallback) detailFallback.style.display = 'none';
    if (detailLayouts && tr.layouts && tr.layouts.length) {
      detailLayouts.innerHTML = tr.layouts.map(function (l, i) {
        var lid = l.layout_id || 'default';
        var mapUrl = API_BASE + '/tracks/' + encodeURIComponent(tr.track_id) + '/layouts/' + encodeURIComponent(lid) + '/map';
        var previewUrl = API_BASE + '/tracks/' + encodeURIComponent(tr.track_id) + '/layouts/' + encodeURIComponent(lid) + '/preview';
        var activeClass = i === idx ? ' active' : '';
        return '<div class="sc-track-layout-thumb' + activeClass + '" data-layout-index="' + i + '" role="button" tabindex="0"><img src="' + escapeHtml(mapUrl) + '" alt="" onerror="if(this.src!==\'' + escapeHtml(previewUrl) + '\'){this.src=\'' + escapeHtml(previewUrl) + '\';}" /></div>';
      }).join('');
      detailLayouts.querySelectorAll('.sc-track-layout-thumb').forEach(function (thumb) {
        thumb.addEventListener('click', function () {
          var i = parseInt(thumb.getAttribute('data-layout-index'), 10);
          if (i === trackPickerState.selectedLayoutIndex) return;
          trackPickerState.selectedLayoutIndex = i;
          updateTrackPickerDetail();
        });
      });
    } else if (detailLayouts) detailLayouts.innerHTML = '';
    if (pickerName) pickerName.textContent = name || emptyVal;
    if (pickerLayout) pickerLayout.textContent = formatLayoutName(layoutId);
    if (pickerDescription) pickerDescription.innerHTML = '<p class="sc-track-hero-info-para">' + escapeHtml(emptyVal) + '</p>';
    if (pickerDetails) pickerDetails.innerHTML = '';
    pitboxFetch(API_BASE + '/tracks/' + encodeURIComponent(tr.track_id) + '/layouts/' + encodeURIComponent(layoutId) + '/info')
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (info) {
        if (!info || !tr) return;
        if (pickerName) pickerName.textContent = info.name || name || emptyVal;
        if (pickerLayout) pickerLayout.textContent = (info.layout_name && String(info.layout_name).trim()) ? String(info.layout_name).trim() : formatLayoutName(layoutId);
        if (pickerDescription) {
          var rawDesc = info.description;
          var formatted = formatTrackDescription(rawDesc);
          var displayText = (formatted != null && formatted !== '') ? formatted : 'No description available.';
          if (displayText.indexOf('\n\n') !== -1) {
            var parts = displayText.split(/\n\n/);
            pickerDescription.innerHTML = parts.map(function (p) {
              return '<p class="sc-track-hero-info-para">' + escapeHtml(p) + '</p>';
            }).join('');
          } else {
            pickerDescription.innerHTML = '<p class="sc-track-hero-info-para">' + escapeHtml(displayText) + '</p>';
          }
        }
        if (pickerDetails) {
          var detailLabels = { country: 'Country', city: 'City', length_km: 'Length', length: 'Length', pits: 'Pits', author: 'Author', year: 'Year' };
          var detailOrder = ['country', 'city', 'length_km', 'length', 'pits', 'author', 'year'];
          var html = [];
          for (var i = 0; i < detailOrder.length; i++) {
            var key = detailOrder[i];
            if (key === 'length' && info.length_km != null && info.length_km !== '') continue;
            var val = info[key];
            if (val === undefined || val === null || val === '') continue;
            if (key === 'length_km') val = val + ' km';
            else if (key === 'length' && typeof val === 'number' && val >= 100) val = (val / 1000).toFixed(2) + ' km';
            else if (key === 'length' && typeof val === 'number') val = val + ' m';
            var label = detailLabels[key] || key;
            html.push('<div class="sc-track-hero-info-row"><span class="sc-track-hero-info-label">' + escapeHtml(label) + '</span><span class="sc-track-hero-info-value">' + escapeHtml(String(val)) + '</span></div>');
          }
          pickerDetails.innerHTML = html.length ? html.join('') : '<div class="sc-track-hero-info-row"><span class="sc-track-hero-info-value">' + emptyVal + '</span></div>';
        }
      })
      .catch(function () {
        if (pickerName && name) pickerName.textContent = name;
      });
  }
  function bindTrackPicker() {
    var hero = document.getElementById('sc-track-hero');
    var modal = document.getElementById('sc-track-picker-modal');
    var backdrop = document.getElementById('sc-track-picker-backdrop');
    var okBtn = document.getElementById('sc-track-picker-ok');
    var cancelBtn = document.getElementById('sc-track-picker-cancel');
    var searchEl = document.getElementById('sc-track-picker-search');
    var listEl = document.getElementById('sc-track-picker-list');
    var heroBtn = document.getElementById('sc-track-hero-image-btn');
    if (heroBtn && !heroBtn.dataset.bound) {
      heroBtn.dataset.bound = '1';
      heroBtn.addEventListener('click', function (e) { e.preventDefault(); openTrackPickerModal(); });
      heroBtn.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openTrackPickerModal(); } });
    }
    if (backdrop) backdrop.addEventListener('click', closeTrackPickerModal);
    if (cancelBtn) cancelBtn.addEventListener('click', closeTrackPickerModal);
    if (okBtn && !okBtn.dataset.bound) {
      okBtn.dataset.bound = '1';
      okBtn.addEventListener('click', function () {
        var tr = trackPickerState.selectedTrack;
        if (!tr) { closeTrackPickerModal(); return; }
        var layout = (tr.layouts && tr.layouts[trackPickerState.selectedLayoutIndex]) || (tr.layouts && tr.layouts[0]);
        var trackId = tr.track_id;
        var layoutId = (layout && layout.layout_id && layout.layout_id !== 'default') ? layout.layout_id : '';
        var trackInput = document.getElementById('sc-track');
        var layoutInput = document.getElementById('sc-config-track');
        if (trackInput) { trackInput.value = trackId; trackInput.dispatchEvent(new Event('change', { bubbles: true })); }
        if (layoutInput) layoutInput.value = layoutId;
        var combined = trackId + (layoutId ? '/' + layoutId : '');
        pushRecentTrack(combined);
        updateTrackHero(combined);
        closeTrackPickerModal();
      });
    }
    if (searchEl && !searchEl.dataset.bound) {
      searchEl.dataset.bound = '1';
      searchEl.addEventListener('input', function () {
        if (trackPickerState.searchDebounceTimer) clearTimeout(trackPickerState.searchDebounceTimer);
        trackPickerState.searchDebounceTimer = setTimeout(function () {
          trackPickerState.searchDebounceTimer = null;
          renderTrackPickerList();
        }, 200);
      });
      searchEl.addEventListener('keydown', function (e) { if (e.key === 'Escape') closeTrackPickerModal(); });
    }
    if (modal && !modal.dataset.trackPickerEscBound) {
      modal.dataset.trackPickerEscBound = '1';
      document.addEventListener('keydown', function (e) {
        if (e.key !== 'Escape') return;
        if (modal && !modal.classList.contains('hidden')) closeTrackPickerModal();
      });
    }
  }
  function section(sc, names) {
    if (!sc) return {};
    for (var i = 0; i < names.length; i++) if (sc[names[i]]) return sc[names[i]];
    return {};
  }
  function val(opts, keys) {
    if (!opts) return undefined;
    for (var i = 0; i < keys.length; i++) if (opts[keys[i]] !== undefined && opts[keys[i]] !== '') return opts[keys[i]];
    return undefined;
  }
  /** Build URL for track outline image (layout-specific). Server returns 404 if outline.png missing; use img onerror to hide. */
  function resolveTrackOutline(trackId, layout) {
    if (!trackId || !String(trackId).trim()) return null;
    var lid = (layout != null && String(layout).trim()) ? String(layout).trim() : 'default';
    return API_BASE + '/tracks/' + encodeURIComponent(trackId) + '/layouts/' + encodeURIComponent(lid) + '/outline';
  }

  /**
   * Normalize track id for display/lookup. CM/CSP path-style (e.g. "csp/3749/../H/../rt_lime_rock_park")
   * is reduced to the last segment. Backslashes normalized; .ini/.json stripped.
   */
  function normalizeTrackId(raw) {
    if (!raw) return '';
    var s = String(raw).trim().replace(/\\/g, '/');
    var parts = s.split('/').filter(Boolean);
    var last = parts.length ? parts[parts.length - 1] : s;
    last = last.replace(/\.(ini|json)$/i, '');
    return last;
  }
  var _trackHeroAbortController = null;
  function updateTrackHero(trackValue) {
    if (_trackHeroAbortController) {
      _trackHeroAbortController.abort();
      _trackHeroAbortController = null;
    }
    var trackId = '';
    var layoutId = 'default';
    if (trackValue && (trackValue = String(trackValue).trim())) {
      if (trackValue.indexOf('/') !== -1) {
        var parts = trackValue.split('/');
        trackId = parts[0] || '';
        layoutId = (parts[1] || '').trim() || 'default';
      } else {
        trackId = trackValue;
      }
    }
    var heroImg = document.getElementById('sc-track-hero-img');
    var heroFallback = document.getElementById('sc-track-hero-fallback');
    var heroName = document.getElementById('sc-track-hero-name');
    var heroLayouts = document.getElementById('sc-track-hero-layouts');
    var infoName = document.getElementById('sc-track-hero-info-name');
    var infoLayout = document.getElementById('sc-track-hero-info-layout');
    var infoDescription = document.getElementById('sc-track-hero-info-description');
    var infoDescriptionWrap = document.getElementById('sc-track-hero-info-description-wrap');
    var infoDetails = document.getElementById('sc-track-hero-info-details');
    var outlineWrap = document.getElementById('sc-track-hero-outline-wrap');
    var outlineImg = document.getElementById('sc-track-hero-outline');
    var heroMapImg = document.getElementById('sc-track-hero-map');
    var emptyVal = '—';
    if (heroName) heroName.textContent = trackValue ? '…' : emptyVal;
    if (infoName) infoName.textContent = trackId ? formatTrackName(trackId) : emptyVal;
    if (infoLayout) {
      infoLayout.textContent = (trackId ? formatLayoutName(layoutId) : emptyVal);
      infoLayout.title = (trackId && layoutId) ? ('Layout ID: ' + (layoutId === 'default' ? 'default' : layoutId)) : '';
    }
    if (infoDescription) infoDescription.innerHTML = '<p class="sc-track-hero-info-para">' + escapeHtml(emptyVal) + '</p>';
    if (infoDescriptionWrap) infoDescriptionWrap.classList.remove('sc-track-hero-info-hidden');
    if (infoDetails) infoDetails.innerHTML = '';
    var heroEl = document.getElementById('sc-track-hero');
    if (outlineWrap) outlineWrap.classList.remove('sc-track-hero-outline-visible');
    if (heroEl) heroEl.classList.remove('sc-track-hero-has-outline');
    if (outlineImg) outlineImg.src = '';
    if (heroFallback) heroFallback.style.display = trackId ? 'none' : 'flex';
    if (trackId && outlineImg) {
      var outlineUrl = resolveTrackOutline(trackId, layoutId);
      if (outlineUrl) {
        outlineImg.onload = function () {
          if (outlineWrap) outlineWrap.classList.add('sc-track-hero-outline-visible');
          if (heroEl) heroEl.classList.add('sc-track-hero-has-outline');
        };
        outlineImg.onerror = function () {
          if (outlineWrap) outlineWrap.classList.remove('sc-track-hero-outline-visible');
          if (heroEl) heroEl.classList.remove('sc-track-hero-has-outline');
          outlineImg.src = '';
        };
        outlineImg.src = outlineUrl;
      }
    }
    if (heroImg) {
      if (trackId) {
        var mapUrl = API_BASE + '/tracks/' + encodeURIComponent(trackId) + '/layouts/' + encodeURIComponent(layoutId) + '/map';
        var previewUrl = API_BASE + '/tracks/' + encodeURIComponent(trackId) + '/layouts/' + encodeURIComponent(layoutId) + '/preview';
        heroImg.src = mapUrl;
        heroImg.alt = trackValue;
        heroImg.style.display = '';
        heroImg.onerror = function () {
          if (this.src.indexOf('/map') !== -1) {
            this.src = previewUrl;
          } else {
            this.style.display = 'none';
            if (heroFallback) heroFallback.style.display = 'flex';
          }
        };
        if (heroMapImg) {
          var outlineUrl = API_BASE + '/tracks/' + encodeURIComponent(trackId) + '/layouts/' + encodeURIComponent(layoutId) + '/outline';
          heroMapImg.onerror = function () { this.src = ''; };
          heroMapImg.src = outlineUrl;
        }
        _trackHeroAbortController = new AbortController();
        var signal = _trackHeroAbortController.signal;
        pitboxFetch(API_BASE + '/tracks/' + encodeURIComponent(trackId) + '/layouts/' + encodeURIComponent(layoutId) + '/info', { signal: signal })
          .then(function (r) { return r.ok ? r.json() : null; })
          .then(function (info) {
            if (!info || signal.aborted) return;
            var name = (info.name || formatTrackName(trackId) || emptyVal);
            if (heroName) heroName.textContent = name;
            if (infoName) infoName.textContent = name;
            if (infoLayout) {
              var layoutDisplay = (info.layout_name && String(info.layout_name).trim()) ? String(info.layout_name).trim() : formatLayoutName(layoutId);
              infoLayout.textContent = layoutDisplay;
              infoLayout.title = 'Layout ID: ' + (layoutId === 'default' ? 'default' : layoutId);
            }
            if (outlineImg) {
              var trackLabel = (info.name || formatTrackName(trackId) || '').trim() || 'Track';
              var layoutPart = (layoutId && layoutId !== 'default') ? ' – ' + formatLayoutName(layoutId) : '';
              outlineImg.alt = trackLabel + ' outline' + layoutPart;
            }
            if (infoDescription) {
              var rawDesc = info.description;
              var formatted = formatTrackDescription(rawDesc);
              var displayText = (formatted != null && formatted !== '') ? formatted : 'No description available.';
              if (displayText.indexOf('\n\n') !== -1) {
                var parts = displayText.split(/\n\n/);
                infoDescription.innerHTML = parts.map(function (p) {
                  return '<p class="sc-track-hero-info-para">' + escapeHtml(p) + '</p>';
                }).join('');
              } else {
                infoDescription.innerHTML = '<p class="sc-track-hero-info-para">' + escapeHtml(displayText) + '</p>';
              }
            }
            if (infoDescriptionWrap) infoDescriptionWrap.classList.remove('sc-track-hero-info-hidden');
            var detailLabels = { country: 'Country', city: 'City', length_km: 'Length', length: 'Length', pits: 'Pits', author: 'Author', year: 'Year' };
            var detailOrder = ['country', 'city', 'length_km', 'length', 'pits', 'author', 'year'];
            if (infoDetails) {
              var html = [];
              for (var i = 0; i < detailOrder.length; i++) {
                var key = detailOrder[i];
                if (key === 'length' && info.length_km != null && info.length_km !== '') continue;
                var val = info[key];
                if (val === undefined || val === null || val === '') continue;
                if (key === 'length_km') val = val + ' km';
                else if (key === 'length' && typeof val === 'number' && val >= 100) val = (val / 1000).toFixed(2) + ' km';
                else if (key === 'length' && typeof val === 'number') val = val + ' m';
                var label = detailLabels[key] || key;
                html.push('<div class="sc-track-hero-info-row"><span class="sc-track-hero-info-label">' + escapeHtml(label) + '</span><span class="sc-track-hero-info-value">' + escapeHtml(String(val)) + '</span></div>');
              }
              infoDetails.innerHTML = html.length ? html.join('') : '<div class="sc-track-hero-info-row"><span class="sc-track-hero-info-value">' + emptyVal + '</span></div>';
            }
          })
          .catch(function (err) {
            if (err && err.name === 'AbortError') return;
            if (heroName && trackId) heroName.textContent = formatTrackName(trackId);
            if (infoName && trackId) infoName.textContent = formatTrackName(trackId);
            if (infoLayout) infoLayout.textContent = formatLayoutName(layoutId);
          });
      } else {
        heroImg.src = '';
        heroImg.alt = '';
        heroImg.style.display = 'none';
        heroImg.onerror = null;
        if (heroMapImg) heroMapImg.src = '';
        if (heroName) heroName.textContent = emptyVal;
      }
    }
    if (heroLayouts) heroLayouts.innerHTML = '';
  }
  function fillServerConfigUI() {
    var sc = serverConfigData.server_cfg;
    var so = sessionSectionOrOff(sc, 'SERVER');
    var s = so.section || {};
    var nameEl = document.getElementById('sc-server-name');
    var names = serverConfigData.preset_names || {};
    if (nameEl) nameEl.textContent = s.NAME || (serverConfigData.server_id ? (names[serverConfigData.server_id] || serverConfigData.server_id) : 'Select a server…');
    var inst = document.getElementById('sc-instance');
    if (inst) {
      var ids = serverConfigData.server_ids || [];
      if (ids.length === 0) {
        inst.innerHTML = '<option value="">Select a server…</option>';
      } else {
        inst.innerHTML = ids.map(function (id) {
          var label = names[id] || id;
          return '<option value="' + escapeHtml(id) + '"' + (id === serverConfigData.server_id ? ' selected' : '') + '>' + escapeHtml(label) + '</option>';
        }).join('');
      }
    }
    var presetSelect = document.getElementById('sc-preset');
    if (presetSelect) {
      presetSelect.innerHTML = '<option value="">Load preset…</option>' + (serverConfigData.presets || []).map(function (name) {
        return '<option value="' + escapeHtml(name) + '">' + escapeHtml(name) + '</option>';
      }).join('');
    }
    setIniControl('sc-password', s.PASSWORD);
    setIniControl('sc-admin-password', s.ADMIN_PASSWORD);
    setIniControl('sc-name', s.NAME);
    var rawTrack = String(val(s, ['TRACK']) || '').trim();
    var layoutId = String(val(s, ['CONFIG_TRACK']) || '').trim();
    setIniControl('sc-track', rawTrack);
    setIniControl('sc-config-track', layoutId);
    var normTrack = normalizeTrackId(rawTrack);
    var displayId = normTrack || rawTrack;
    var combined = displayId ? (layoutId ? displayId + '/' + layoutId : displayId) : '';
    setIniControl('sc-cars', s.CARS);
    if (typeof updateCarsSelected === 'function') updateCarsSelected();
    updateTrackHero(combined);
    setIniControl('sc-udp-port', s.UDP_PORT);
    setIniControl('sc-tcp-port', s.TCP_PORT);
    setIniControl('sc-http-port', s.HTTP_PORT);
    setIniControl('sc-packets', s.CLIENT_SEND_INTERVAL_HZ);
    setIniControl('sc-manager-desc', s.MANAGER_DESCRIPTION);
    setIniControl('sc-welcome-file', s.WELCOME_MESSAGE_FILE);
    setIniControl('sc-welcome-msg', s.WELCOME_MESSAGE);
    setIniControlCheck('sc-register-lobby', s.REGISTER_TO_LOBBY, '1');
    setIniControlCheck('sc-integrity', s.DISABLE_INTEGRITY, '1');
    setIniControlCheck('sc-stability', s.STABILITY_ALLOWED, '1');
    setIniControlCheck('sc-autoclutch', s.AUTOCLUTCH_ALLOWED, '1');
    setIniControlCheck('sc-tyre-blankets', s.TYRE_BLANKETS_ALLOWED, '1');
    setIniControlCheck('sc-virtual-mirror', s.FORCE_VIRTUAL_MIRROR, '1');
    setIniControl('sc-kick-quorum', s.KICK_QUORUM);
    setIniControl('sc-vote-quorum', s.VOTING_QUORUM);
    setIniControl('sc-vote-duration', s.VOTE_DURATION);
    (function setBlacklistMode() {
      var el = document.getElementById('sc-blacklist-mode');
      if (!el) return;
      var v = String(s.BLACKLIST_MODE != null && s.BLACKLIST_MODE !== '' ? s.BLACKLIST_MODE : '0');
      if (v !== '0' && v !== '1' && v !== '2') v = '0';
      el.value = v;
    })();
    setIniControl('sc-sleep-time', s.SLEEP_TIME);
    setIniControl('sc-send-buffer', s.SEND_BUFFER_SIZE);
    setIniControl('sc-recv-buffer', s.RECV_BUFFER_SIZE);
    setIniControl('sc-max-ballast', s.MAX_BALLAST_KG);
    setIniControl('sc-legal-tyres', s.LEGAL_TYRES);
    setIniControl('sc-udp-plugin-port', s.UDP_PLUGIN_LOCAL_PORT);
    setIniControl('sc-udp-plugin-addr', s.UDP_PLUGIN_ADDRESS);
    setIniControl('sc-auth-plugin-addr', s.AUTH_PLUGIN_ADDRESS);
    setIniControl('sc-fuel', s.FUEL_RATE);
    setIniControl('sc-damage', s.DAMAGE_MULTIPLIER);
    setIniControl('sc-tyre-wear', s.TYRE_WEAR_RATE);
    setIniControl('sc-tyres-out', s.ALLOWED_TYRES_OUT);
    setIniControlSelect('sc-abs', s.ABS_ALLOWED);
    setIniControlSelect('sc-tc', s.TC_ALLOWED);
    var dt = section(sc, ['DYNAMIC_TRACK', 'Dynamic_Track']) || {};
    setIniControl('sc-dyn-start', dt.SESSION_START);
    setIniControl('sc-dyn-transfer', dt.SESSION_TRANSFER);
    setIniControl('sc-dyn-rand', dt.RANDOMNESS);
    setIniControl('sc-dyn-laps', dt.LAP_GAIN);
    setIniControlCheck('sc-pickup-mode', s.PICKUP_MODE_ENABLED, '1');
    setIniControlCheck('sc-loop-mode', s.LOOP_MODE, '1');
    setIniControlCheck('sc-locked-entry-list', s.LOCKED_ENTRY_LIST, '1');
    var bookSo = sessionSectionOrOff(sc, 'BOOK');
    var book = bookSo.section;
    setIniControl('sc-booking-time', val(book, ['TIME']));
    setIniControlCheck('sc-booking-enabled', bookSo.enabled ? val(book, ['IS_OPEN', 'OPEN']) : '0', '1');
    var prSo = sessionSectionOrOff(sc, 'PRACTICE');
    var pr = prSo.section;
    setIniControl('sc-practice-time', val(pr, ['TIME']));
    setIniControlCheck('sc-practice-open', val(pr, ['IS_OPEN', 'OPEN']), '1');
    var prEl = document.getElementById('sc-practice-enabled');
    if (prEl) prEl.checked = prSo.enabled;
    var quSo = sessionSectionOrOff(sc, 'QUALIFY');
    var qu = quSo.section;
    setIniControl('sc-qualify-time', val(qu, ['TIME']));
    setIniControlCheck('sc-qualify-open', val(qu, ['IS_OPEN', 'OPEN']), '1');
    setIniControl('sc-qualify-limit', val(s, ['QUALIFY_MAX_WAIT_PERC', 'QUALIFY_LIMIT_PERCENT']));
    var quEl = document.getElementById('sc-qualify-enabled');
    if (quEl) quEl.checked = quSo.enabled;
    var raSo = sessionSectionOrOff(sc, 'RACE');
    var ra = raSo.section;
    var limitBy = document.getElementById('sc-race-limit-by');
    if (limitBy) limitBy.value = (Number(val(ra, ['TIME'])) > 0) ? 'time' : 'laps';
    setIniControl('sc-race-laps', val(ra, ['LAPS']));
    setIniControl('sc-race-time', val(ra, ['TIME']));
    setIniControl('sc-race-wait', val(ra, ['WAIT_TIME']));
    setIniControl('sc-race-over', val(s, ['RACE_OVER_TIME']));
    setIniControl('sc-race-result', val(s, ['RESULT_SCREEN_TIME']));
    setIniControl('sc-race-open', val(ra, ['IS_OPEN', 'OPEN']));
    setIniControlCheck('sc-race-mandatory-pit', val(s, ['RACE_EXTRA_LAP']), '1');
    setIniControl('sc-race-from-lap', val(s, ['RACE_PIT_WINDOW_START']));
    setIniControl('sc-race-reversed', val(s, ['REVERSED_GRID_RACE_POSITIONS', 'REVERSED_GRID']));
    setIniControl('sc-race-to-lap', val(s, ['RACE_PIT_WINDOW_END']));
    var raEl = document.getElementById('sc-race-enabled');
    if (raEl) raEl.checked = raSo.enabled;
    updateSessionRaceLimitVisibility();
    updateSliderLabels();
    renderEntryListTable();
    var cnt = document.getElementById('sc-entry-count');
    if (cnt) cnt.textContent = serverConfigData.entry_list.length + ' entries';
  }
  function setIniControl(id, val) {
    var el = document.getElementById(id);
    if (!el) return;
    if (el.type === 'range') el.value = val !== undefined && val !== '' ? val : (el.getAttribute('value') || el.min || 0);
    else el.value = val !== undefined && val !== null ? String(val) : '';
  }
  function setIniControlCheck(id, val, onVal) {
    var el = document.getElementById(id);
    if (!el) return;
    el.checked = val === onVal || val === '1' || val === true;
  }
  function setIniControlSelect(id, val) {
    var el = document.getElementById(id);
    if (!el) return;
    var v = String(val !== undefined && val !== null ? val : '');
    if (el.querySelector('option[value="' + v.replace(/"/g, '&quot;') + '"]')) el.value = v;
  }
  function formatMinutesToTime(min) {
    var m = Math.max(0, parseInt(min, 10) || 0);
    var h = Math.floor(m / 60);
    m = m % 60;
    return (h < 10 ? '0' : '') + h + ':' + (m < 10 ? '0' : '') + m + ':00';
  }
  function formatSecondsToTime(sec) {
    var s = Math.max(0, parseInt(sec, 10) || 0);
    var m = Math.floor(s / 60);
    s = s % 60;
    return '00:' + (m < 10 ? '0' : '') + m + ':' + (s < 10 ? '0' : '') + s;
  }
  function formatSecondsToShort(sec) {
    var s = Math.max(0, parseInt(sec, 10) || 0);
    var m = Math.floor(s / 60);
    s = s % 60;
    return (m < 10 ? '0' : '') + m + ':' + (s < 10 ? '0' : '') + s;
  }
  function updateSessionRaceLimitVisibility() {
    var limitBy = document.getElementById('sc-race-limit-by');
    var lapsPanel = document.querySelector('.sc-race-duration-laps');
    var timePanel = document.querySelector('.sc-race-duration-time');
    if (!limitBy) return;
    if (limitBy.value === 'time') {
      if (lapsPanel) lapsPanel.classList.add('hidden');
      if (timePanel) timePanel.classList.remove('hidden');
    } else {
      if (lapsPanel) lapsPanel.classList.remove('hidden');
      if (timePanel) timePanel.classList.add('hidden');
    }
  }
  function updateSliderLabels() {
    var pairs = [
      ['sc-packets', 'sc-packets-val', null],
      ['sc-kick-quorum', 'sc-kick-quorum-val', null],
      ['sc-vote-quorum', 'sc-vote-quorum-val', null],
      ['sc-vote-duration', 'sc-vote-duration-val', null],
      ['sc-fuel', 'sc-fuel-val', null],
      ['sc-damage', 'sc-damage-val', null],
      ['sc-tyre-wear', 'sc-tyre-wear-val', null],
      ['sc-tyres-out', 'sc-tyres-out-val', null],
      ['sc-dyn-start', 'sc-dyn-start-val', null],
      ['sc-dyn-transfer', 'sc-dyn-transfer-val', null],
      ['sc-dyn-rand', 'sc-dyn-rand-val', null],
      ['sc-dyn-laps', 'sc-dyn-laps-val', null],
      ['sc-booking-time', 'sc-booking-time-val', 'min'],
      ['sc-practice-time', 'sc-practice-time-val', 'min'],
      ['sc-qualify-time', 'sc-qualify-time-val', 'min'],
      ['sc-qualify-limit', 'sc-qualify-limit-val', 'pct'],
      ['sc-race-laps', 'sc-race-laps-val', 'laps'],
      ['sc-race-time', 'sc-race-time-val', 'min'],
      ['sc-race-wait', 'sc-race-wait-val', 'secshort'],
      ['sc-race-over', 'sc-race-over-val', 'sec'],
      ['sc-race-result', 'sc-race-result-val', 'sec']
    ];
    pairs.forEach(function (p) {
      var sl = document.getElementById(p[0]);
      var la = document.getElementById(p[1]);
      if (!sl || !la) return;
      var fmt = p[2];
      if (fmt === 'min') la.textContent = formatMinutesToTime(sl.value);
      else if (fmt === 'sec') la.textContent = formatSecondsToTime(sl.value);
      else if (fmt === 'secshort') la.textContent = formatSecondsToShort(sl.value);
      else if (fmt === 'pct') la.textContent = sl.value + '%';
      else if (fmt === 'laps') la.textContent = sl.value + ' laps';
      else la.textContent = sl.value;
    });
  }
  function getSectionKey(el) {
    if (!el) return null;
    var section = el.getAttribute('data-ini');
    var key = el.getAttribute('data-key');
    return section && key ? { section: section, key: key } : null;
  }
  function applyControlToServerCfg(el) {
    var sk = getSectionKey(el);
    if (!sk) return;
    var sc = serverConfigData.server_cfg;
    if (!sc[sk.section]) sc[sk.section] = {};
    if (el.type === 'checkbox') sc[sk.section][sk.key] = el.checked ? '1' : '0';
    else sc[sk.section][sk.key] = el.value.trim();
  }
  function buildServerCfgFromForm() {
    var existing = serverConfigData.server_cfg;
    var sc = {};
    for (var section in existing) {
      sc[section] = {};
      for (var k in existing[section]) sc[section][k] = existing[section][k];
    }
    document.querySelectorAll('#page-server-config [data-ini][data-key]').forEach(function (el) {
      var section = el.getAttribute('data-ini');
      var key = el.getAttribute('data-key');
      if (!sc[section]) sc[section] = {};
      if (el.type === 'checkbox') sc[section][key] = el.checked ? '1' : '0';
      else sc[section][key] = (el.value || '').trim();
    });
    var limitBy = document.getElementById('sc-race-limit-by');
    var raceTimeEl = document.getElementById('sc-race-time');
    var raceLapsEl = document.getElementById('sc-race-laps');
    if (sc.RACE && limitBy) {
      if (limitBy.value === 'time') {
        sc.RACE.TIME = (raceTimeEl ? (raceTimeEl.value || '').trim() : '') || '0';
        sc.RACE.LAPS = '0';
      } else {
        sc.RACE.LAPS = (raceLapsEl ? (raceLapsEl.value || '').trim() : '') || '0';
        sc.RACE.TIME = '0';
      }
    }
    // CM-style: enabled = [PRACTICE], disabled = [__CM_PRACTICE_OFF]. Apply session section names from checkboxes.
    function pickSessionSection(data, baseName) {
      var offName = SESSION_OFF_NAMES[baseName];
      var normal = data[baseName] || (baseName === 'QUALIFY' ? data.QUALIFYING : null);
      var off = data[offName];
      var norm = (normal && typeof normal === 'object' && Object.keys(normal).length > 0) ? normal : null;
      var offObj = (off && typeof off === 'object' && Object.keys(off).length > 0) ? off : null;
      return norm || offObj || {};
    }
    function writeSessionSection(enabled, baseName, content) {
      var offName = SESSION_OFF_NAMES[baseName];
      if (enabled) {
        sc[baseName] = content;
        delete sc[offName];
        if (baseName === 'QUALIFY') delete sc.QUALIFYING;
      } else {
        sc[offName] = content;
        delete sc[baseName];
        if (baseName === 'QUALIFY') delete sc.QUALIFYING;
      }
    }
    var pickupOn = document.getElementById('sc-pickup-mode') && document.getElementById('sc-pickup-mode').checked;
    var bookEnabled = !pickupOn && document.getElementById('sc-booking-enabled') && document.getElementById('sc-booking-enabled').checked;
    var bookContent = pickSessionSection(sc, 'BOOK');
    bookContent.NAME = bookContent.NAME || 'Booking';
    bookContent.TIME = String(document.getElementById('sc-booking-time') ? document.getElementById('sc-booking-time').value : bookContent.TIME || '10');
    bookContent.IS_OPEN = bookEnabled ? '1' : '0';
    writeSessionSection(bookEnabled, 'BOOK', bookContent);
    var practiceEnabled = document.getElementById('sc-practice-enabled') && document.getElementById('sc-practice-enabled').checked;
    var prContent = pickSessionSection(sc, 'PRACTICE');
    prContent.NAME = prContent.NAME || 'Practice';
    prContent.TIME = String(document.getElementById('sc-practice-time') ? document.getElementById('sc-practice-time').value : prContent.TIME || '10');
    prContent.IS_OPEN = (document.getElementById('sc-practice-open') && document.getElementById('sc-practice-open').checked) ? '1' : '0';
    writeSessionSection(practiceEnabled, 'PRACTICE', prContent);
    var qualifyEnabled = document.getElementById('sc-qualify-enabled') && document.getElementById('sc-qualify-enabled').checked;
    var quContent = pickSessionSection(sc, 'QUALIFY');
    quContent.NAME = quContent.NAME || 'Qualify';
    quContent.TIME = String(document.getElementById('sc-qualify-time') ? document.getElementById('sc-qualify-time').value : quContent.TIME || '5');
    quContent.IS_OPEN = (document.getElementById('sc-qualify-open') && document.getElementById('sc-qualify-open').checked) ? '1' : '0';
    writeSessionSection(qualifyEnabled, 'QUALIFY', quContent);
    var raceEnabled = document.getElementById('sc-race-enabled') && document.getElementById('sc-race-enabled').checked;
    var raContent = pickSessionSection(sc, 'RACE');
    raContent.NAME = raContent.NAME || 'Race';
    raContent.TIME = limitBy && limitBy.value === 'time' ? (raceTimeEl ? (raceTimeEl.value || '').trim() : '0') : (raContent.TIME || '0');
    raContent.LAPS = limitBy && limitBy.value === 'laps' ? (raceLapsEl ? (raceLapsEl.value || '').trim() : '0') : (raContent.LAPS || '0');
    raContent.IS_OPEN = (document.getElementById('sc-race-open') ? document.getElementById('sc-race-open').value : '1');
    raContent.WAIT_TIME = (document.getElementById('sc-race-wait') ? document.getElementById('sc-race-wait').value : raContent.WAIT_TIME || '0');
    raContent.RACE_OVER_TIME = (document.getElementById('sc-race-over') ? document.getElementById('sc-race-over').value : raContent.RACE_OVER_TIME || '60');
    raContent.RESULT_SCREEN_TIME = (document.getElementById('sc-race-result') ? document.getElementById('sc-race-result').value : raContent.RESULT_SCREEN_TIME || '60');
    raContent.PIT_FROM_LAP = (document.getElementById('sc-race-from-lap') ? document.getElementById('sc-race-from-lap').value : raContent.PIT_FROM_LAP || '');
    raContent.PIT_TO_LAP = (document.getElementById('sc-race-to-lap') ? document.getElementById('sc-race-to-lap').value : raContent.PIT_TO_LAP || '');
    writeSessionSection(raceEnabled, 'RACE', raContent);
    return sc;
  }
  function renderEntryListTable() {
    var tbody = document.getElementById('sc-entry-tbody');
    if (!tbody) return;
    var list = serverConfigData.entry_list;
    if (list.length > 0 && getCarsForModal().length === 0) {
      pitboxFetch(API_BASE + '/cars')
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (data) {
          var cars = Array.isArray(data) ? data : (data && data.cars) ? data.cars : [];
          setCarsForModal(cars || []);
          renderEntryListTable();
        })
        .catch(function () {});
    }
    tbody.innerHTML = list.map(function (car, i) {
      var model = escapeHtml(car.MODEL || '');
      var skin = escapeHtml(car.SKIN || '');
      var modelRaw = (car.MODEL || '').trim();
      var skinRaw = (car.SKIN || '').trim();
      var liveryUrl = (modelRaw && skinRaw) ? (API_BASE + '/cars/' + encodeURIComponent(car.MODEL) + '/skins/' + encodeURIComponent(car.SKIN) + '/livery') : '';
      var carData = getCarById(modelRaw);
      var skins = (carData && carData.skins && carData.skins.length) ? carData.skins : [];
      var skinControl;
      if (skins.length > 0) {
        var hasCurrent = skinRaw && skins.some(function (s) { return skinFolder(s) === skinRaw; });
        var opts = '<option value="">—</option>';
        if (skinRaw && !hasCurrent) {
          opts += '<option value="' + escapeHtml(skinRaw) + '" selected>' + escapeHtml(skinRaw) + '</option>';
        }
        opts += skins.map(function (s) {
          var folder = skinFolder(s);
          var label = (typeof s === 'object' && s.name != null) ? s.name : folder;
          return '<option value="' + escapeHtml(folder) + '"' + (folder === skinRaw ? ' selected' : '') + '>' + escapeHtml(label) + '</option>';
        }).join('');
        skinControl = '<select class="sc-entry-skin sc-entry-skin-select">' + opts + '</select>';
      } else {
        skinControl = '<input type="text" class="sc-entry-skin" value="' + skin + '" placeholder="Skin">';
      }
      var guid = escapeHtml(car.GUID || '');
      var name = escapeHtml(car.DRIVERNAME || '');
      var team = escapeHtml(car.TEAM || '');
      var ballast = escapeHtml(car.BALLAST !== undefined ? car.BALLAST : '');
      var rest = escapeHtml(car.RESTRICTOR !== undefined ? car.RESTRICTOR : '');
      var displayName = getCarDisplayName(modelRaw);
      var displayEscaped = escapeHtml(displayName || '—');
      var displayTitle = escapeHtml(displayName || modelRaw || '');
      return '<tr data-index="' + i + '">' +
        '<td class="sc-entry-model-cell">' +
        '<div class="sc-entry-model-wrap">' +
        '<span class="sc-entry-model-display" title="' + displayTitle + '">' + displayEscaped + '</span>' +
        '<input type="text" class="sc-entry-model" value="' + model + '" placeholder="Car model (ID)" list="sc-cars-datalist">' +
        '</div></td>' +
        '<td class="sc-entry-skin-cell">' +
        '<div class="sc-entry-livery-wrap">' +
        '<img class="sc-entry-livery" ' + (liveryUrl ? 'data-src="' + escapeHtml(liveryUrl) + '"' : 'style="display:none"') + ' alt="' + skin + '" title="' + skin + '">' +
        skinControl +
        '</div></td>' +
        '<td><input type="text" class="sc-entry-guid" value="' + guid + '" placeholder="Any"></td>' +
        '<td><input type="text" class="sc-entry-name" value="' + name + '" placeholder="Client-defined"></td>' +
        '<td><input type="text" class="sc-entry-team" value="' + team + '" placeholder="None"></td>' +
        '<td><input type="text" class="sc-entry-ballast" value="' + ballast + '" placeholder="0"></td>' +
        '<td><input type="text" class="sc-entry-restrictor" value="' + rest + '" placeholder="0%"></td>' +
        '<td class="sc-entry-actions"><button type="button" class="sc-entry-clone">Clone</button><button type="button" class="sc-entry-delete">Delete</button></td></tr>';
    }).join('');
    var cars = getCarsForModal();
    var dl = document.getElementById('sc-cars-datalist');
    if (!dl) {
      dl = document.createElement('datalist');
      dl.id = 'sc-cars-datalist';
      document.body.appendChild(dl);
    }
    dl.innerHTML = cars.map(function (c) {
      var label = formatCarDisplayName(c.car_id, c.name);
      return '<option value="' + escapeHtml(c.car_id) + '">' + escapeHtml(label) + '</option>';
    }).join('');
    tbody.querySelectorAll('.sc-entry-livery').forEach(function (img) {
      img.onerror = function () { img.style.display = 'none'; };
    });
    if (typeof IntersectionObserver !== 'undefined') {
      var liveryObserver = new IntersectionObserver(function (entries) {
        entries.forEach(function (entry) {
          if (!entry.isIntersecting) return;
          var img = entry.target;
          var src = img.getAttribute('data-src');
          if (src) {
            img.setAttribute('src', src);
            img.removeAttribute('data-src');
            liveryObserver.unobserve(img);
          }
        });
      }, { root: null, rootMargin: '100px', threshold: 0.01 });
      tbody.querySelectorAll('.sc-entry-livery[data-src]').forEach(function (img) { liveryObserver.observe(img); });
    } else {
      tbody.querySelectorAll('.sc-entry-livery[data-src]').forEach(function (img) {
        var src = img.getAttribute('data-src');
        if (src) { img.setAttribute('src', src); img.removeAttribute('data-src'); }
      });
    }
    tbody.querySelectorAll('.sc-entry-clone').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var tr = btn.closest('tr');
        var i = parseInt(tr.getAttribute('data-index'), 10);
        var car = Object.assign({}, list[i]);
        list.splice(i + 1, 0, car);
        renderEntryListTable();
      });
    });
    tbody.querySelectorAll('.sc-entry-delete').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var tr = btn.closest('tr');
        var i = parseInt(tr.getAttribute('data-index'), 10);
        list.splice(i, 1);
        renderEntryListTable();
        var cnt = document.getElementById('sc-entry-count');
        if (cnt) cnt.textContent = list.length + ' entries';
      });
    });
    var knownCarIds = getCarsForModal().reduce(function (o, c) { o[c.car_id] = true; return o; }, {});
    tbody.querySelectorAll('.sc-entry-model-wrap').forEach(function (wrap) {
      var display = wrap.querySelector('.sc-entry-model-display');
      var input = wrap.querySelector('.sc-entry-model');
      if (!display || !input) return;
      display.addEventListener('click', function () {
        wrap.classList.add('edit-mode');
        input.focus();
      });
      input.addEventListener('blur', function () {
        wrap.classList.remove('edit-mode');
        var v = (input.value || '').trim();
        var name = getCarDisplayName(v);
        display.textContent = name || '—';
        display.setAttribute('title', name || v || '');
        var known = getCarsForModal().some(function (c) { return (c.car_id || '') === v; });
        input.classList.toggle('sc-entry-model-unknown', !!v && !known);
        input.setAttribute('aria-invalid', v && !known ? 'true' : 'false');
      });
      var val = (input.value || '').trim();
      input.classList.toggle('sc-entry-model-unknown', !!val && !knownCarIds[val]);
      input.setAttribute('aria-invalid', val && !knownCarIds[val] ? 'true' : 'false');
    });
    tbody.querySelectorAll('.sc-entry-model, .sc-entry-skin, .sc-entry-guid, .sc-entry-name, .sc-entry-team, .sc-entry-ballast, .sc-entry-restrictor').forEach(function (input) {
      input.addEventListener('change', function () {
        var tr = input.closest('tr');
        var i = parseInt(tr.getAttribute('data-index'), 10);
        var car = list[i] || {};
        var cls = input.className;
        var isModel = cls.indexOf('sc-entry-model') >= 0;
        if (isModel) {
          input.classList.remove('sc-entry-model-unknown');
          input.setAttribute('aria-invalid', 'false');
          car.MODEL = input.value;
          list[i] = car;
          renderEntryListTable();
          return;
        }
        if (cls.indexOf('sc-entry-skin') >= 0) car.SKIN = input.value;
        else if (cls.indexOf('sc-entry-guid') >= 0) car.GUID = input.value;
        else if (cls.indexOf('sc-entry-name') >= 0) car.DRIVERNAME = input.value;
        else if (cls.indexOf('sc-entry-team') >= 0) car.TEAM = input.value;
        else if (cls.indexOf('sc-entry-ballast') >= 0) car.BALLAST = input.value;
        else if (cls.indexOf('sc-entry-restrictor') >= 0) car.RESTRICTOR = input.value;
        list[i] = car;
        var modelInput = tr.querySelector('.sc-entry-model');
        var skinInput = tr.querySelector('.sc-entry-skin');
        var liveryImg = tr.querySelector('.sc-entry-livery');
        if (liveryImg && modelInput && skinInput) {
          var m = (modelInput.value || '').trim();
          var s = (skinInput.value || '').trim();
          if (m && s) {
            var url = API_BASE + '/cars/' + encodeURIComponent(m) + '/skins/' + encodeURIComponent(s) + '/livery';
            liveryImg.setAttribute('src', url);
            liveryImg.removeAttribute('data-src');
            liveryImg.style.display = '';
            liveryImg.alt = s;
            liveryImg.title = s;
          } else {
            liveryImg.style.display = 'none';
          }
        }
      });
    });
  }
  function collectEntryListFromTable() {
    var list = [];
    var tbody = document.getElementById('sc-entry-tbody');
    var existing = serverConfigData.entry_list || [];
    if (!tbody) return list;
    tbody.querySelectorAll('tr').forEach(function (tr, i) {
      var model = tr.querySelector('.sc-entry-model');
      var skin = tr.querySelector('.sc-entry-skin');
      var guid = tr.querySelector('.sc-entry-guid');
      var name = tr.querySelector('.sc-entry-name');
      var team = tr.querySelector('.sc-entry-team');
      var ballast = tr.querySelector('.sc-entry-ballast');
      var restrictor = tr.querySelector('.sc-entry-restrictor');
      var prev = existing[i];
      var carName = (prev && (prev.CAR_NAME || prev.car_name)) ? (prev.CAR_NAME || prev.car_name) : '';
      list.push({
        MODEL: model ? model.value : '',
        SKIN: skin ? skin.value : '',
        GUID: guid ? guid.value : '',
        DRIVERNAME: name ? name.value : '',
        TEAM: team ? team.value : '',
        BALLAST: ballast ? ballast.value : '',
        RESTRICTOR: restrictor ? restrictor.value : '',
        SPECTATOR_MODE: '0',
        CAR_NAME: carName
      });
    });
    return list;
  }
  function getCurrentServerId() {
    var sel = document.getElementById('sc-instance');
    if (sel && sel.value) return sel.value;
    return (serverConfigData && serverConfigData.server_id) || (serverConfigData.server_ids && serverConfigData.server_ids[0]) || '';
  }
  function loadServerProcessLog() {
    var serverId = getCurrentServerId();
    var pre = document.getElementById('sc-log-content');
    if (!pre) return;
    if (!serverId) {
      pre.textContent = 'Select a server to view process log.';
      return;
    }
    pre.textContent = 'Loading…';
    pitboxFetch(API_BASE + '/server-config/process-log?server_id=' + encodeURIComponent(serverId) + '&tail=1500')
      .then(function (r) { return r.ok ? r.json() : Promise.reject(new Error('Status ' + r.status)); })
      .then(function (data) {
        var lines = data.lines || [];
        var msg = data.message;
        if (msg && lines.length === 0) {
          pre.textContent = msg;
          return;
        }
        pre.textContent = lines.join('\n') || '(No output yet. Start the server to capture console log.)';
        pre.scrollTop = pre.scrollHeight;
      })
      .catch(function (err) {
        pre.textContent = 'Failed to load log: ' + (err.message || err);
      });
  }
  function bindServerConfigTabs() {
    var logRefresh = document.getElementById('sc-log-refresh');
    if (logRefresh) logRefresh.addEventListener('click', function () { loadServerProcessLog(); });
    document.querySelectorAll('.sc-tab').forEach(function (tab) {
      tab.addEventListener('click', function () {
        var t = tab.getAttribute('data-tab');
        document.querySelectorAll('.sc-tab').forEach(function (x) { x.classList.remove('sc-tab-active'); });
        document.querySelectorAll('.sc-panel').forEach(function (p) { p.classList.add('hidden'); });
        tab.classList.add('sc-tab-active');
        var panel = document.querySelector('.sc-panel[data-tab="' + t + '"]');
        if (panel) panel.classList.remove('hidden');
        if (t === 'settings') loadControllerConfigForm();
        if (t === 'log') loadServerProcessLog();
        if (t === 'status') updateScStatusPanel();
        if (t === 'details') loadServerConfigDetailsRaw();
      });
    });
    var detRef = document.getElementById('sc-details-refresh');
    if (detRef && !detRef.dataset.bound) {
      detRef.dataset.bound = '1';
      detRef.addEventListener('click', function () { loadServerConfigDetailsRaw(); });
    }
  }
  var updateCheckLastTime = null;
  var updateCheckLastError = null;
  var updateCheckLastSuccessAt = null;
  var updateCheckLastKnownLatest = null;
  var updateStatusData = null;

  function formatUnixSeconds(epochSeconds) {
    if (epochSeconds == null || typeof epochSeconds !== 'number') return '—';
    var d = new Date(epochSeconds * 1000);
    var months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    var h = d.getHours();
    var m = String(d.getMinutes()).padStart(2, '0');
    var ampm = h >= 12 ? 'PM' : 'AM';
    h = h % 12; h = h ? h : 12;
    return months[d.getMonth()] + ' ' + d.getDate() + ', ' + d.getFullYear() + ' ' + h + ':' + m + ' ' + ampm;
  }
  function formatIso(isoString) {
    if (!isoString || typeof isoString !== 'string') return '—';
    var d = new Date(isoString);
    if (isNaN(d.getTime())) return '—';
    var months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    var h = d.getHours();
    var m = String(d.getMinutes()).padStart(2, '0');
    var ampm = h >= 12 ? 'PM' : 'AM';
    h = h % 12; h = h ? h : 12;
    return months[d.getMonth()] + ' ' + d.getDate() + ', ' + d.getFullYear() + ' ' + h + ':' + m + ' ' + ampm;
  }

  function checkForUpdates() {
    var now = new Date();
    pitboxFetch(API_BASE + '/update/status')
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        updateCheckLastError = null;
        updateCheckLastTime = now;
        if (!data) return;
        if (!data.error) {
          updateCheckLastSuccessAt = data.last_successful_check_at != null ? new Date(data.last_successful_check_at * 1000) : now;
          updateCheckLastKnownLatest = data.latest_version || null;
        }
        updateStatusData = data;
        var badge = document.getElementById('update-available-badge');
        var pill = document.getElementById('cc-pill-update');
        var show = data && data.update_available === true && !data.error;
        if (badge) badge.classList.toggle('hidden', !show);
        if (pill) pill.classList.toggle('hidden', !show);
      })
      .catch(function (err) {
        updateCheckLastError = err && err.message ? err.message : 'network error';
        updateCheckLastTime = now;
        updateStatusData = null;
      });
  }
  function versionString(v) {
    if (v == null || v === '') return '—';
    return String(v);
  }
  function renderUpdatesPanel(data) {
    var pillEl = document.getElementById('updates-pill');
    var versionsLineEl = document.getElementById('updates-versions-line');
    var btnEl = document.getElementById('updates-btn-apply');
    var noUpdateHint = document.getElementById('updates-no-update-hint');
    var lastCheckedEl = document.getElementById('updates-last-checked');
    var lastSuccessRow = document.getElementById('updates-last-success-row');
    var lastSuccessEl = document.getElementById('updates-last-success');
    var errorBox = document.getElementById('updates-error-box');
    var errorBody = document.getElementById('updates-error-body');
    var errorFooterTime = document.getElementById('updates-error-footer-time');
    var releaseWrap = document.getElementById('updates-release-wrap');
    var releaseLink = document.getElementById('updates-release-link');
    var publishedEl = document.getElementById('updates-published');
    var notesWrap = document.getElementById('updates-notes-wrap');
    var notesEl = document.getElementById('updates-notes');

    var s = data || {};
    var current = versionString(s.current_version);
    var latest = versionString(s.latest_version || s.last_known_latest_version);
    var updateAvailable = s.update_available === true;
    var hasInstaller = !!(s.controller_installer && (s.controller_installer.api_url || s.controller_installer.url));
    var hasZip = !!(s.controller_zip && (s.controller_zip.api_url || s.controller_zip.url));
    var hasUnified = !!(s.unified_installer && (s.unified_installer.api_url || s.unified_installer.url));
    var hasReleaseUrl = !!(s.html_url);
    var updaterState = s.state || 'idle';
    var isUpdating = updaterState !== 'idle' && updaterState !== 'done' && updaterState !== 'error';
    var canUpdate = (updateAvailable === true) && (hasZip || hasUnified || hasReleaseUrl) && !isUpdating;

    var hasError = !!(s.error);
    var lastOk = (s.last_successful_check_at != null) ? s.last_successful_check_at : (updateCheckLastSuccessAt ? updateCheckLastSuccessAt.getTime() / 1000 : null);
    var lastChecked = updateCheckLastTime ? (updateCheckLastTime.getTime() / 1000) : null;

    if (hasError) {
      if (pillEl) {
        pillEl.textContent = 'Up to date';
        pillEl.className = 'pill-up-to-date';
      }
      if (versionsLineEl) versionsLineEl.textContent = current !== '—' ? 'Installed: ' + current : '—';
    } else if (updateAvailable) {
      if (pillEl) {
        pillEl.textContent = 'Update available';
        pillEl.className = 'pill-update-available';
      }
      if (versionsLineEl) versionsLineEl.textContent = 'Installed: ' + current + ' · Latest: ' + latest;
    } else {
      if (pillEl) {
        pillEl.textContent = 'Up to date';
        pillEl.className = 'pill-up-to-date';
      }
      if (versionsLineEl) versionsLineEl.textContent = current !== '—' ? 'Installed: ' + current : '—';
    }

    if (btnEl) {
      btnEl.disabled = !canUpdate;
      btnEl.textContent = 'Download update & restart';
      btnEl.classList.toggle('hidden', false);
    }
    if (noUpdateHint) {
      if (!canUpdate) {
        noUpdateHint.classList.remove('hidden');
        if (updateAvailable && !hasZip && !hasUnified) {
          noUpdateHint.textContent = 'This release has no installer asset (ZIP or PitBoxInstaller_*.exe).';
        } else if (updateAvailable && (hasZip || hasUnified) && isUpdating) {
          noUpdateHint.textContent = 'Update in progress…';
        } else if (!updateAvailable) {
          noUpdateHint.textContent = 'No updates available.';
        } else if (isUpdating) {
          noUpdateHint.textContent = 'Update in progress…';
        } else {
          noUpdateHint.textContent = 'No updates available.';
        }
      } else {
        noUpdateHint.classList.add('hidden');
      }
    }

    if (lastCheckedEl) lastCheckedEl.textContent = updateCheckLastTime ? formatUnixSeconds(lastChecked) : (lastOk != null ? formatUnixSeconds(lastOk) : 'Never');
    if (lastSuccessRow && lastSuccessEl) {
      if (lastOk != null) {
        lastSuccessEl.textContent = formatUnixSeconds(lastOk);
        lastSuccessRow.classList.remove('hidden');
      } else {
        lastSuccessRow.classList.add('hidden');
      }
    }

    if (errorBox && errorBody && errorFooterTime) {
      if (hasError) {
        errorBody.textContent = s.error || 'Unable to check for updates.';
        errorFooterTime.textContent = updateCheckLastTime ? formatUnixSeconds(lastChecked) : '—';
        errorBox.classList.remove('hidden');
      } else {
        errorBox.classList.add('hidden');
      }
    }

    var showRelease = updateAvailable || (s.release_name);
    if (releaseWrap) releaseWrap.classList.toggle('hidden', !showRelease);
    if (showRelease && s.release_name) {
      if (releaseLink) {
        releaseLink.textContent = s.release_name || 'Release';
        if (releaseLink.removeAttribute) releaseLink.removeAttribute('href');
      }
      if (publishedEl) publishedEl.textContent = formatIso(s.published_at);
      if (notesWrap && notesEl) {
        if (s.notes_markdown && s.notes_markdown.trim()) {
          notesEl.textContent = s.notes_markdown;
          notesWrap.classList.remove('hidden');
        } else {
          notesWrap.classList.add('hidden');
        }
      }
    }
  }
  function loadUpdatesPage() {
    var lastCheckedEl = document.getElementById('updates-last-checked');
    if (lastCheckedEl && updateCheckLastTime) lastCheckedEl.textContent = formatUnixSeconds(updateCheckLastTime.getTime() / 1000);
    else if (lastCheckedEl) lastCheckedEl.textContent = updateCheckLastSuccessAt ? formatUnixSeconds(updateCheckLastSuccessAt.getTime() / 1000) : 'Never';

    pitboxFetch(API_BASE + '/update/status?refresh=true')
      .then(function (r) { return r.ok ? r.json() : Promise.reject(new Error('Status ' + r.status)); })
      .then(function (data) {
        updateCheckLastTime = new Date();
        if (data.last_successful_check_at != null) {
          updateCheckLastSuccessAt = new Date(data.last_successful_check_at * 1000);
        }
        if (data.last_known_latest_version != null) updateCheckLastKnownLatest = data.last_known_latest_version;
        updateStatusData = data;
        renderUpdatesPanel(data);
      })
      .catch(function (err) {
        updateCheckLastTime = new Date();
        renderUpdatesPanel({
          current_version: updateStatusData ? updateStatusData.current_version : null,
          latest_version: null,
          update_available: false,
          error: 'Unable to check for updates: ' + (err.message || 'network error'),
          last_successful_check_at: updateCheckLastSuccessAt ? updateCheckLastSuccessAt.getTime() / 1000 : null,
          release_name: null,
          published_at: null,
          html_url: null,
          notes_markdown: null
        });
      });
    if (updateStatusData) renderUpdatesPanel(updateStatusData);
    bindUpdatesApply();
  }
  function bindUpdatesApply() {
    var btn = document.getElementById('updates-btn-apply');
    var progressWrap = document.getElementById('updates-progress');
    var progressMsg = document.getElementById('updates-progress-message');
    var progressPct = document.getElementById('updates-progress-percent');
    if (!btn || btn.dataset.updatesApplyBound === '1') return;
    btn.dataset.updatesApplyBound = '1';
    btn.addEventListener('click', function () {
      if (btn.disabled) return;
      var data = updateStatusData;
      var hasZip = !!(data && data.controller_zip && (data.controller_zip.api_url || data.controller_zip.url));
      var hasUnified = !!(data && data.unified_installer && (data.unified_installer.api_url || data.unified_installer.url));
      if (!data || data.update_available !== true) return;
      
      var url;
      var body;
      var releaseUrl = data.html_url || '';

      if (hasUnified) {
        url = API_BASE + '/update/run-installer';
        body = '{}';
      } else if (hasZip) {
        url = API_BASE + '/update/apply';
        body = JSON.stringify({ target: 'controller' });
      } else {
        if (releaseUrl && typeof window.open === 'function') window.open(releaseUrl, '_blank', 'noopener,noreferrer');
        if (typeof showToast === 'function') showToast('No installer asset in this release. Opening release page.', 'info');
        return;
      }

      btn.disabled = true;
      if (progressWrap) progressWrap.classList.remove('hidden');
      if (progressMsg) progressMsg.textContent = 'Starting download…';
      if (progressPct) progressPct.textContent = '';

      pitboxFetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: body
      })
        .then(function (r) { return r.json().then(function (res) { if (!r.ok) throw new Error(res.detail || res.message || r.statusText); return res; }); })
        .then(function () {
          if (typeof showToast === 'function') showToast('Downloading update…', 'success');
          btn.disabled = true;
          btn.textContent = 'Updating…';
          startUpdateProgressPolling(progressWrap, progressMsg, progressPct, btn);
        })
        .catch(function (err) {
          if (typeof showToast === 'function') showToast('Update failed: ' + (err.message || err), 'error');
          btn.disabled = false;
          btn.textContent = 'Download update & restart';
          if (progressWrap) progressWrap.classList.add('hidden');
        });
    });
  }
  function startUpdateProgressPolling(progressWrap, progressMsg, progressPct, btn) {
    var pollMs = 1500;
    var maxMs = 600000;
    var start = Date.now();
    var wasDown = false;
    var downSince = 0;
    function poll() {
      if (Date.now() - start > maxMs) {
        if (progressMsg) progressMsg.textContent = 'Update timed out after 10 minutes.';
        if (btn) { btn.disabled = false; btn.textContent = 'Download update & restart'; }
        if (progressWrap) progressWrap.classList.add('hidden');
        return;
      }
      pitboxFetch(API_BASE + '/update/status')
        .then(function (r) { return r.ok ? r.json() : Promise.reject(new Error('Status ' + r.status)); })
        .then(function (data) {
          var state = data.state || 'idle';
          var message = data.message || '';
          var percent = data.percent != null ? data.percent : 0;

          if (wasDown) {
            wasDown = false;
            downSince = 0;
            if (progressMsg) progressMsg.textContent = 'PitBox restarted — reloading…';
            if (progressPct) progressPct.textContent = '';
            setTimeout(function () { window.location.reload(); }, 1200);
            return;
          }

          if (progressMsg) progressMsg.textContent = message || state;
          if (progressPct) progressPct.textContent = percent > 0 ? percent + '%' : '';

          if (state === 'done') {
            if (progressMsg) progressMsg.textContent = 'Install complete — reloading…';
            setTimeout(function () { window.location.reload(); }, 1500);
            return;
          }
          if (state === 'error') {
            if (typeof showToast === 'function') showToast('Update error: ' + (message || 'Unknown'), 'error');
            if (btn) { btn.disabled = false; btn.textContent = 'Download update & restart'; }
            if (progressWrap) progressWrap.classList.add('hidden');
            return;
          }
          setTimeout(poll, pollMs);
        })
        .catch(function () {
          if (!wasDown) {
            wasDown = true;
            downSince = Date.now();
            if (progressMsg) progressMsg.textContent = 'Installing… PitBox will restart shortly.';
            if (progressPct) progressPct.textContent = '';
          }
          setTimeout(poll, pollMs);
        });
    }
    setTimeout(poll, pollMs);
  }
  function loadSettingsPage() {
    bindSettingsTabs();
    bindSettingsAccessAndAdvanced();
    bindSettingsConfigSave();
    loadSettingsConfigForm();
    loadUpdatesPage();
  }
  function bindSettingsTabs() {
    document.querySelectorAll('.settings-tab').forEach(function (tab) {
      if (tab.dataset.settingsTabBound === '1') return;
      tab.dataset.settingsTabBound = '1';
      tab.addEventListener('click', function () {
        var t = tab.getAttribute('data-tab');
        document.querySelectorAll('.settings-tab').forEach(function (x) { x.classList.remove('settings-tab-active'); });
        document.querySelectorAll('.settings-panel').forEach(function (p) { p.classList.add('hidden'); });
        tab.classList.add('settings-tab-active');
        var panel = document.querySelector('.settings-panel[data-tab="' + t + '"]');
        if (panel) panel.classList.remove('hidden');
        if (t === 'config') loadSettingsConfigForm();
        if (t === 'updates') loadUpdatesPage();
      });
    });
  }
  function loadSettingsConfigForm() {
    var portEl = document.getElementById('settings-config-ui-port');
    var localBtn = document.getElementById('settings-access-local');
    var lanBtn = document.getElementById('settings-access-lan');
    if (portEl) portEl.value = '';
    if (localBtn && lanBtn) {
      localBtn.classList.add('settings-access-opt-active');
      lanBtn.classList.remove('settings-access-opt-active');
    }
    pitboxFetch(API_BASE + '/config')
      .then(function (r) { return r.ok ? r.json() : Promise.reject(new Error('Status ' + r.status)); })
      .then(function (data) {
        var c = data.config || {};
        if (portEl) portEl.value = c.ui_port != null ? String(c.ui_port) : '';
        if (localBtn && lanBtn) {
          if (c.allow_lan_ui) {
            lanBtn.classList.add('settings-access-opt-active');
            localBtn.classList.remove('settings-access-opt-active');
          } else {
            localBtn.classList.add('settings-access-opt-active');
            lanBtn.classList.remove('settings-access-opt-active');
          }
        }
      })
      .catch(function (err) {
        if (typeof showToast === 'function') showToast('Config: ' + err.message, 'error');
      });
  }
  function bindSettingsAccessAndAdvanced() {
    var localBtn = document.getElementById('settings-access-local');
    var lanBtn = document.getElementById('settings-access-lan');
    if (localBtn && lanBtn) {
      [localBtn, lanBtn].forEach(function (btn) {
        if (btn.dataset.settingsAccessBound === '1') return;
        btn.dataset.settingsAccessBound = '1';
        btn.addEventListener('click', function () {
          localBtn.classList.remove('settings-access-opt-active');
          lanBtn.classList.remove('settings-access-opt-active');
          btn.classList.add('settings-access-opt-active');
        });
      });
    }
  }
  function bindSettingsConfigSave() {
    var btn = document.getElementById('settings-config-save');
    if (!btn || btn.dataset.settingsSaveBound === '1') return;
    btn.dataset.settingsSaveBound = '1';
    btn.addEventListener('click', function () {
      var portEl = document.getElementById('settings-config-ui-port');
      var lanBtn = document.getElementById('settings-access-lan');
      var updates = {};
      if (portEl && portEl.value !== '') updates.ui_port = parseInt(portEl.value, 10);
      updates.allow_lan_ui = !!(lanBtn && lanBtn.classList.contains('settings-access-opt-active'));
      pitboxFetch(API_BASE + '/config', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(updates)
      })
        .then(function (r) { return r.ok ? r.json() : Promise.reject(new Error(r.status === 403 ? 'Config updates allowed only from localhost' : 'Status ' + r.status)); })
        .then(function (res) {
          if (typeof showToast === 'function') showToast(res.message || 'Config saved.', 'success');
          loadSettingsConfigForm();
        })
        .catch(function (err) { if (typeof showToast === 'function') showToast('Save failed: ' + err.message, 'error'); });
    });
  }
  function loadControllerConfigForm() {
    var pathEl = document.getElementById('sc-config-path-label');
    var versionEl = document.getElementById('sc-config-version');
    var hostEl = document.getElementById('sc-config-ui-host');
    var portEl = document.getElementById('sc-config-ui-port');
    var acPathEl = document.getElementById('sc-config-ac-cfg-path');
    var allowLanEl = document.getElementById('sc-config-allow-lan');
    if (!pathEl) return;
    pathEl.textContent = 'Config file: —';
    if (versionEl) versionEl.textContent = '—';
    if (hostEl) hostEl.value = '';
    if (portEl) portEl.value = '';
    if (acPathEl) acPathEl.value = '';
    if (allowLanEl) allowLanEl.checked = false;
    pitboxFetch(API_BASE + '/version').then(function (r) { return r.ok ? r.json() : null; }).then(function (v) {
      if (versionEl && v && v.version) versionEl.textContent = v.version;
    }).catch(function () {});
    pitboxFetch(API_BASE + '/config')
      .then(function (r) { return r.ok ? r.json() : Promise.reject(new Error('Status ' + r.status)); })
      .then(function (data) {
        pathEl.textContent = 'Config file: ' + (data.config_path || '(default / not set)');
        var c = data.config || {};
        if (hostEl) hostEl.value = c.ui_host != null ? c.ui_host : '';
        if (portEl) portEl.value = c.ui_port != null ? c.ui_port : '';
        if (acPathEl) acPathEl.value = c.ac_server_cfg_path != null ? c.ac_server_cfg_path : '';
        if (allowLanEl) allowLanEl.checked = !!c.allow_lan_ui;
      })
      .catch(function (err) {
        pathEl.textContent = 'Config file: —';
        if (typeof showToast === 'function') showToast('Config: ' + err.message, 'error');
      });
  }
  function bindServerConfigSettings() {
    var btn = document.getElementById('sc-config-save');
    if (!btn) return;
    btn.addEventListener('click', function () {
      var hostEl = document.getElementById('sc-config-ui-host');
      var portEl = document.getElementById('sc-config-ui-port');
      var acPathEl = document.getElementById('sc-config-ac-cfg-path');
      var allowLanEl = document.getElementById('sc-config-allow-lan');
      var updates = {};
      if (hostEl && hostEl.value.trim() !== '') updates.ui_host = hostEl.value.trim();
      if (portEl && portEl.value !== '') updates.ui_port = parseInt(portEl.value, 10);
      if (acPathEl) updates.ac_server_cfg_path = acPathEl.value.trim() || null;
      if (allowLanEl) updates.allow_lan_ui = allowLanEl.checked;
      pitboxFetch(API_BASE + '/config', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(updates)
      })
        .then(function (r) { return r.ok ? r.json() : Promise.reject(new Error(r.status === 400 ? 'Bad request' : 'Status ' + r.status)); })
        .then(function (res) {
          if (typeof showToast === 'function') showToast(res.message || 'Config saved.', 'success');
          loadControllerConfigForm();
        })
        .catch(function (err) { if (typeof showToast === 'function') showToast('Save failed: ' + err.message, 'error'); });
    });
  }
  function bindServerConfigControls() {
    document.querySelectorAll('#page-server-config input, #page-server-config select, #page-server-config textarea').forEach(function (el) {
      var sk = getSectionKey(el);
      if (!sk) return;
      function update() {
        applyControlToServerCfg(el);
        updateSliderLabels();
      }
      el.addEventListener('change', update);
      el.addEventListener('input', function () { if (el.type === 'range') updateSliderLabels(); });
    });
    document.getElementById('sc-name').addEventListener('input', function () {
      var h = document.getElementById('sc-server-name');
      if (h) h.textContent = this.value || 'Select a server…';
    });
    var limitBy = document.getElementById('sc-race-limit-by');
    if (limitBy && !limitBy.dataset.bound) {
      limitBy.dataset.bound = '1';
      limitBy.addEventListener('change', function () {
        updateSessionRaceLimitVisibility();
        updateSliderLabels();
      });
    }
    var randomizeSkinsBtn = document.getElementById('sc-entry-randomize-skins');
    if (randomizeSkinsBtn && !randomizeSkinsBtn.dataset.bound) {
      randomizeSkinsBtn.dataset.bound = '1';
      randomizeSkinsBtn.addEventListener('click', randomizeEntryListSkins);
    }
    var deleteAllBtn = document.getElementById('sc-entry-delete-all');
    if (deleteAllBtn && !deleteAllBtn.dataset.bound) {
      deleteAllBtn.dataset.bound = '1';
      deleteAllBtn.addEventListener('click', deleteAllEntryList);
    }
    var exportBtn = document.getElementById('sc-entry-export');
    var importBtn = document.getElementById('sc-entry-import');
    var importFile = document.getElementById('sc-entry-import-file');
    if (exportBtn && !exportBtn.dataset.bound) {
      exportBtn.dataset.bound = '1';
      exportBtn.addEventListener('click', function () {
        var list = collectEntryListFromTable();
        var json = JSON.stringify(list, null, 2);
        var blob = new Blob([json], { type: 'application/json' });
        var a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = 'entry_list_' + (serverConfigData.server_id || 'preset') + '.json';
        a.click();
        URL.revokeObjectURL(a.href);
        if (typeof showToast === 'function') showToast('Entry list exported.', 'success');
      });
    }
    var stStart = document.getElementById('sc-status-start');
    var stRestart = document.getElementById('sc-status-restart');
    var stStop = document.getElementById('sc-status-stop');
    function currentScServerId() {
      var i = document.getElementById('sc-instance');
      return (i && i.value) ? String(i.value).trim() : (serverConfigData.server_id || 'default');
    }
    if (stStart && !stStart.dataset.bound) {
      stStart.dataset.bound = '1';
      stStart.addEventListener('click', function () { doServerConfigPost('/server-config/start', 'Start', currentScServerId()); });
    }
    if (stRestart && !stRestart.dataset.bound) {
      stRestart.dataset.bound = '1';
      stRestart.addEventListener('click', function () { doServerConfigPost('/server-config/restart', 'Restart', currentScServerId()); });
    }
    if (stStop && !stStop.dataset.bound) {
      stStop.dataset.bound = '1';
      stStop.addEventListener('click', function () { doServerConfigPost('/server-config/stop', 'Stop', currentScServerId()); });
    }
    if (importBtn && importFile && !importBtn.dataset.bound) {
      importBtn.dataset.bound = '1';
      importBtn.addEventListener('click', function () { importFile.click(); });
      importFile.addEventListener('change', function () {
        var file = importFile.files && importFile.files[0];
        if (!file) return;
        var reader = new FileReader();
        reader.onload = function () {
          try {
            var data = JSON.parse(reader.result);
            var list = Array.isArray(data) ? data : (data && data.entry_list) ? data.entry_list : null;
            if (!list || !Array.isArray(list)) {
              if (typeof showToast === 'function') showToast('Invalid file: expected JSON array of cars.', 'error');
              return;
            }
            serverConfigData.entry_list = list;
            fillEntryListTable();
            if (typeof showToast === 'function') showToast('Entry list imported (' + list.length + ' entries).', 'success');
          } catch (e) {
            if (typeof showToast === 'function') showToast('Import failed: ' + (e.message || 'invalid JSON'), 'error');
          }
          importFile.value = '';
        };
        reader.readAsText(file);
      });
    }
  }
  function bindServerConfigSaveReload() {
    var page = document.getElementById('page-server-config');
    if (page && page.dataset.scSaveBound) return;
    if (page) page.dataset.scSaveBound = '1';
    var instanceSelect = document.getElementById('sc-instance');
    if (instanceSelect && !instanceSelect.dataset.instanceChangeBound) {
      instanceSelect.dataset.instanceChangeBound = '1';
      instanceSelect.addEventListener('change', function () {
        var selectedId = (instanceSelect.value || '').trim() || null;
        setSelectedServerId(selectedId);
        loadServerConfigPage();
      });
    }
    var saveBtn = document.getElementById('sc-save');
    var reloadBtn = document.getElementById('sc-reload');
    if (saveBtn) {
      saveBtn.addEventListener('click', function () {
        var serverCfg = buildServerCfgFromForm();
        var entryList = collectEntryListFromTable();
        pitboxFetch(API_BASE + '/server-config', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ server_id: serverConfigData.server_id, server_cfg: serverCfg, entry_list: entryList })
        })
          .then(function (r) {
            return r.text().then(function (text) {
              var body = {};
              try { body = text ? JSON.parse(text) : {}; } catch (e) { body = {}; }
              return { ok: r.ok, status: r.status, body: body };
            });
          })
          .then(function (res) {
            if (res.ok) {
              serverConfigData.server_cfg = serverCfg;
              serverConfigData.entry_list = entryList;
              invalidatePresetDiskStateClient(serverConfigData.server_id);
              if (typeof showToast === 'function') showToast('Server config saved.', 'success');
            } else {
              var msg = (res.body && res.body.detail) || ('HTTP ' + res.status);
              if (typeof showToast === 'function') showToast('Save failed: ' + msg, 'error');
            }
          })
          .catch(function (err) { if (typeof showToast === 'function') showToast('Save failed: ' + (err.message || 'request failed'), 'error'); });
      });
    }
    if (reloadBtn) reloadBtn.addEventListener('click', loadServerConfigPage);
  }
  function bindServerConfigPreset() {
    var presetSelect = document.getElementById('sc-preset');
    if (!presetSelect) return;
    presetSelect.addEventListener('change', function () {
      var name = (presetSelect.value || '').trim();
      if (!name) return;
      pitboxFetch(API_BASE + '/server-config/load-preset', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ server_id: serverConfigData.server_id, preset_name: name })
      })
        .then(function (r) { return r.ok ? r.json() : Promise.reject(new Error(r.status === 404 ? 'Preset not found' : 'Status ' + r.status)); })
        .then(function () {
          presetSelect.value = '';
          invalidatePresetDiskStateClient(serverConfigData.server_id);
          loadServerConfigPage();
          if (typeof showToast === 'function') showToast('Loaded preset: ' + name, 'success');
        })
        .catch(function (err) { if (typeof showToast === 'function') showToast('Load preset failed: ' + err.message, 'error'); });
    });
  }

  var currentAgents = [];
  var TEST_SIM_AGENT_ID = '__test_sim__';
  var TEST_SIM_AGENT = {
    agent_id: TEST_SIM_AGENT_ID,
    display_name: 'Test Sim',
    online: false,
    ac_running: false,
    steering_presets: [],
    shifting_presets: []
  };
  var lastSimCardsAgentIds = null;  /* only re-render grid when agent list changes (keeps dropdowns open) */
  var driverNames = {};
  var steeringPreset = {};  /* agent_id -> selected steering preset (AC savedsetups -> controls.ini) */
  var shiftingPreset = {};  /* agent_id -> selected shifting preset (CM .cmpreset -> assists.ini) */
  var SHIFTING_PRESET_FALLBACK = [
    { v: 'H-Pattern', label: 'H-Pattern' },
    { v: 'Sequential', label: 'Sequential' },
    { v: 'Automatic', label: 'Automatic' },
    { v: 'H-Pattern No Assist', label: 'H-Pattern No Assist' },
    { v: 'Sequential No Assist', label: 'Sequential No Assist' },
    { v: 'Drifting', label: 'Drifting' }
  ];
  var selectedForLaunch = {};  /* agent_id -> true when card selected to launch */
  var batchState = {};         /* agent_id -> { action: 'launch'|'exit', status: 'pending'|'ok'|'failed', error?: string } */
  var displayServerIds = [];   /* server list for sim display dropdown (from /api/status) */
  var displayAssignments = {}; /* agent_id -> server_id for sim display (from /api/status) */
  var DEFAULT_RESET_SERVER_ID = 'Fastest Lap MX5 Cup';  /* server assignment when Reset Rig is clicked */
  /** Session timer: limit in minutes when launch was clicked (for countdown). Cleared when session ends. */
  var sessionTimerMinutesByAgent = {};
  /** Session start time (ms) for countdown/elapsed display. Set on launch success or from status uptime_sec. */
  var sessionStartTimeByAgent = {};
  var sessionTimeDisplayTimer = null;  /* 1s interval for countdown/elapsed */

  /* Online join: server + car selection (no CM dependency) */
  var CAR_TILE_THRESHOLD = 16;
  var serverList = [];  /* ServerSummary[] from GET /api/servers */
  var onlineJoinState = {};  /* agent_id -> OnlineJoinState */

  function shouldUseTileGrid(cars, thumbnailsAvailable, threshold) {
    if (typeof threshold === 'undefined') threshold = CAR_TILE_THRESHOLD;
    return !!(thumbnailsAvailable && cars && cars.length > 0 && cars.length <= threshold);
  }

  function getOnlineJoinState(agentId) {
    if (!onlineJoinState[agentId]) {
      onlineJoinState[agentId] = {
        selectedServerId: null,
        selectedCarId: null,
        serverStatus: 'idle',
        carsStatus: 'idle',
        launchStatus: 'idle',
        errorMessage: null,
        thumbnailsAvailable: false,
        carOptions: [],
        serverDetails: null
      };
    }
    return onlineJoinState[agentId];
  }

  /** Build map of car model id -> count in server (from entry_list.cars). */
  function carCountsFromEntryList(entryListCars) {
    var counts = {};
    if (!Array.isArray(entryListCars)) return counts;
    entryListCars.forEach(function (e) {
      var model = (e && (e.MODEL || e.model || '')) && String(e.MODEL || e.model).trim();
      if (model) { counts[model] = (counts[model] || 0) + 1; }
    });
    return counts;
  }

  /** Re-render car pickers for all sims on the same server and re-attach listeners (so "N left" updates). */
  function refreshCarPickersForServer(serverId) {
    if (!(serverId && String(serverId).trim())) return;
    var sid = String(serverId).trim();
    currentAgents.forEach(function (a) {
      var ojs = getOnlineJoinState(a.agent_id);
      if ((ojs.selectedServerId || '').trim() !== sid) return;
      var card = grid.querySelector('.sim-card[data-agent-id="' + (window.CSS && window.CSS.escape ? window.CSS.escape(a.agent_id) : a.agent_id.replace(/"/g, '\\"')) + '"]');
      if (!card) return;
      var picker = card.querySelector('.sim-card-car-picker');
      if (!picker) return;
      var disabled = !a.online || ojs.launchStatus === 'joining' || operatorControlBlocked();
      picker.innerHTML = getCarPickerContent(a.agent_id, ojs, disabled);
      attachCarPickerListeners(card, picker, a.agent_id);
    });
  }

  /** Attach click/change listeners for car tiles and car dropdown in a single picker. */
  function attachCarPickerListeners(card, pickerEl, agentId) {
    var errEl = card ? card.querySelector('.sim-card-online-error') : null;
    pickerEl.querySelectorAll('.car-tile').forEach(function (tile) {
      tile.addEventListener('click', function () {
        if (tile.disabled) return;
        var cid = tile.getAttribute('data-car-id');
        var ojsTile = getOnlineJoinState(agentId);
        ojsTile.selectedCarId = cid;
        updateSimCardSelectionImages(agentId);
        if (card) card.querySelectorAll('.car-tile').forEach(function (t) { t.classList.remove('car-tile-selected'); });
        tile.classList.add('car-tile-selected');
        if (errEl) { errEl.textContent = ''; errEl.classList.add('hidden'); }
        pitboxFetch(API_BASE + '/agents/' + encodeURIComponent(agentId) + '/update_race_selection', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ server_id: ojsTile.selectedServerId, car_id: cid, skin_id: 'default' })
        }).catch(function () {});
        refreshCarPickersForServer(ojsTile.selectedServerId);
      });
    });
    var comboSelect = pickerEl.querySelector('.car-combobox-select');
    if (comboSelect) {
      comboSelect.addEventListener('change', function () {
        var ojsCombo = getOnlineJoinState(agentId);
        ojsCombo.selectedCarId = (comboSelect.value || '').trim() || null;
        updateSimCardSelectionImages(agentId);
        if (errEl) { errEl.textContent = ''; errEl.classList.add('hidden'); }
        pitboxFetch(API_BASE + '/agents/' + encodeURIComponent(agentId) + '/update_race_selection', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ server_id: ojsCombo.selectedServerId, car_id: ojsCombo.selectedCarId, skin_id: 'default' })
        }).catch(function () {});
        refreshCarPickersForServer(ojsCombo.selectedServerId);
      });
    }
  }

  /** For a given server, how many sims have selected each car (agent_id -> selectedCarId aggregated by car). */
  /** Count how many *online* sims have selected each car on this server (offline sims do not use a slot). */
  function selectedCountByCarForServer(serverId) {
    var count = {};
    if (!(serverId && String(serverId).trim())) return count;
    currentAgents.forEach(function (a) {
      if (!a.online) return;
      var o = getOnlineJoinState(a.agent_id);
      var sid = (o.selectedServerId || '').trim();
      var cid = (o.selectedCarId || '').trim();
      if (sid === String(serverId).trim() && cid) {
        count[cid] = (count[cid] || 0) + 1;
      }
    });
    return count;
  }

  /** First car that has at least one slot available on this server (not all taken by other sims). */
  function getDefaultCarIdForServer(rawCars, counts, serverId) {
    var selectedByCar = selectedCountByCarForServer(serverId);
    for (var i = 0; i < rawCars.length; i++) {
      var m = (rawCars[i] && String(rawCars[i]).trim()) || '';
      if (!m) continue;
      var total = counts[m] || 0;
      var selected = selectedByCar[m] || 0;
      if (total === 0) return m;
      if (selected < total) return m;
    }
    return rawCars.length > 0 ? (rawCars[0] && String(rawCars[0]).trim()) || null : null;
  }

  /** Keep current selection if it's still in the new car list; otherwise use default. Prevents overwriting user choice when car list is reloaded. */
  function resolveCarIdAfterLoad(rawCars, currentSelectedCarId, counts, serverId) {
    var current = (currentSelectedCarId && String(currentSelectedCarId).trim()) || '';
    var inList = rawCars.some(function (m) { return (m && String(m).trim()) === current; });
    if (current && inList) return current;
    return getDefaultCarIdForServer(rawCars, counts, serverId);
  }

  function getCarPickerContent(agentId, state, disabled) {
    if (state.carsStatus === 'loading') return '<span class="sim-card-car-picker-msg">Loading cars…</span>';
    if (state.carsStatus === 'error') return '<span class="sim-card-car-picker-msg sim-card-car-picker-error">Failed to load</span>';
    if (!state.selectedServerId || state.carsStatus !== 'ready') return '<span class="sim-card-car-picker-msg">— Select server —</span>';
    var cars = state.carOptions || [];
    if (cars.length === 0) return '<span class="sim-card-car-picker-msg">No cars found in preset (CARS / entry_list.ini)</span>';
    var selectedByCar = selectedCountByCarForServer(state.selectedServerId);
    var carsWithSlots = cars.map(function (opt) {
      var id = opt.id || '';
      var countInServer = opt.countInServer != null ? opt.countInServer : 0;
      var selectedBySims = selectedByCar[id] || 0;
      var slotsLeft = Math.max(0, countInServer - selectedBySims);
      return { id: opt.id, displayName: opt.displayName, thumbnailUrl: opt.thumbnailUrl, isTaken: opt.isTaken, countInServer: opt.countInServer, selectedBySims: selectedBySims, slotsLeft: slotsLeft };
    });
    var useTiles = shouldUseTileGrid(carsWithSlots, state.thumbnailsAvailable, CAR_TILE_THRESHOLD);
    if (useTiles) return renderCarTileGridHTML(carsWithSlots, state.selectedCarId, disabled, agentId);
    return renderCarDropdownHTML(carsWithSlots, state.selectedCarId, disabled, agentId);
  }

  function renderCarTileGridHTML(options, value, disabled, agentId) {
    var tiles = options.map(function (opt) {
      var id = opt.id || '';
      var name = (opt.displayName && opt.displayName !== id) ? opt.displayName : (formatCarName(id) || id);
      var label = name.trim() || formatCarName(id) || id;
      var slotsLeft = opt.slotsLeft;
      var total = opt.countInServer != null ? opt.countInServer : 0;
      var available = slotsLeft != null ? slotsLeft : total;
      var hasEntryList = total > 0;
      var countLabel = hasEntryList ? ' (' + available + '/' + total + ')' : '';
      var noSlots = hasEntryList && slotsLeft != null && slotsLeft <= 0 && value !== id;
      var tileDisabled = disabled || noSlots;
      var taken = (opt.isTaken || noSlots) ? ' car-tile-taken' : '';
      var sel = value === id ? ' car-tile-selected' : '';
      var thumbUrl = (opt.thumbnailUrl && String(opt.thumbnailUrl).trim()) || '';
      var thumb = thumbUrl
        ? ('<span class="car-tile-thumb-wrap"><img class="car-tile-img" src="' + escapeHtml(thumbUrl) + '" alt="" loading="lazy" onerror="this.style.display=\'none\';var n=this.nextElementSibling;if(n)n.style.display=\'flex\';">' +
           '<div class="car-tile-placeholder" style="display:none">' + escapeHtml(label) + '</div></span>')
        : '<span class="car-tile-thumb-wrap"><div class="car-tile-placeholder">' + escapeHtml(label) + '</div></span>';
      var takenLabel = noSlots ? '<span class="car-tile-taken-label">No slots left</span>' : (opt.isTaken ? '<span class="car-tile-taken-label">Taken</span>' : '');
      return '<button type="button" class="car-tile' + taken + sel + '" data-agent-id="' + escapeHtml(agentId) + '" data-car-id="' + escapeHtml(id) + '"' + (tileDisabled ? ' disabled' : '') + '>' + thumb + '<span class="car-tile-name">' + escapeHtml(label) + countLabel + '</span>' + takenLabel + '</button>';
    });
    return '<div class="car-tile-grid" data-agent-id="' + escapeHtml(agentId) + '">' + tiles.join('') + '</div>';
  }

  /** Single car dropdown only (no filter input). Shows (available/total) per car; disables and greys when 0 available (unless current selection). */
  function renderCarDropdownHTML(options, value, disabled, agentId) {
    var opts = options.map(function (opt) {
      var id = opt.id || '';
      var name = (opt.displayName && opt.displayName !== id) ? opt.displayName : (formatCarName(id) || id);
      var label = name.trim() || formatCarName(id) || id;
      var slotsLeft = opt.slotsLeft;
      var total = opt.countInServer != null ? opt.countInServer : 0;
      var available = slotsLeft != null ? slotsLeft : total;
      var hasEntryList = total > 0;
      if (hasEntryList) label = label + ' (' + available + '/' + total + ')';
      else if (opt.isTaken) label = label + ' (Taken)';
      var sel = value === id ? ' selected' : '';
      var noSlots = hasEntryList && slotsLeft != null && slotsLeft <= 0 && value !== id;
      var optDisabled = noSlots ? ' disabled' : '';
      var fullClass = noSlots ? ' car-option-no-slots' : '';
      var taken = (opt.isTaken || noSlots) ? ' data-taken="true"' : '';
      return '<option value="' + escapeHtml(id) + '"' + sel + taken + optDisabled + fullClass + '>' + escapeHtml(label) + '</option>';
    });
    return '<select class="sim-card-dropdown car-combobox-select sim-card-car-select" data-agent-id="' + escapeHtml(agentId) + '"' + (disabled ? ' disabled' : '') + '>' +
      '<option value="">— Select car —</option>' + opts.join('') +
    '</select>';
  }

  function statusKind(a) {
    if (!a.online) return 'offline';
    return a.ac_running ? 'session' : 'idle';
  }

  /** Display-only: strip leading numeric prefix (e.g. "1 Race" → "Race"). Does not change stored/INI values. */
  function presetDisplayLabel(raw) {
    if (raw == null || (typeof raw === 'string' && !raw.trim())) return '';
    var s = String(raw).trim();
    var without = s.replace(/^\d+\s+/, '').trim();
    return without || s;
  }

  /** Build summary HTML for collapsed sim card: wrap-friendly pills (Key: Value), ellipsis, muted when missing. */
  function getSimCardSummaryHtml(agentId, ojs, serverLabel, carLabel, steeringVal, shiftingVal, driverVal) {
    function pill(key, value, muted) {
      var val = (value != null && String(value).trim()) ? escapeHtml(String(value).trim()) : '—';
      var cls = 'sim-card-pill' + (muted || !value || (typeof value === 'string' && !value.trim()) ? ' sim-card-pill-muted' : '');
      return '<span class="' + cls + '" title="' + escapeHtml(key) + ': ' + (val === '—' ? '—' : (value != null ? String(value).trim() : '')) + '">' + escapeHtml(key) + ': ' + val + '</span>';
    }
    var steeringDisplay = steeringVal ? presetDisplayLabel(steeringVal) : '';
    var pills = [
      pill('Server', serverLabel, !(serverLabel && serverLabel.trim())),
      pill('Car', carLabel, !(carLabel && carLabel.trim())),
      pill('Steering', steeringDisplay, !steeringDisplay),
      pill('Shifting', shiftingVal, !(shiftingVal && String(shiftingVal).trim())),
      pill('Driver', driverVal, !(driverVal && driverVal.trim()))
    ].join('');
    return '<div class="sim-card-summary-content">' + pills + '</div><p class="sim-card-summary-hint">Double-click to edit</p>';
  }

  function statusLabel(kind) {
    if (kind === 'idle') return 'Idle';
    if (kind === 'session') return 'Running';
    return 'Error / Offline';
  }

  function truncate(str, maxLen) {
    if (str == null) return '';
    var s = String(str);
    return s.length <= maxLen ? s : s.slice(0, maxLen - 1) + '\u2026';
  }

  function renderServerPanel(sim) {
    var server = sim.server || {};
    var state = server.state || 'UNAVAILABLE';
    var source = (server.source || 'UNKNOWN').toUpperCase();
    var badgeClass = 'pb-badge--unknown';
    if (source === 'PITBOX') badgeClass = 'pb-badge--pitbox';
    else if (source === 'EXTERNAL' || source === 'CONTENT_MANAGER' || source === 'EMPEROR' || source === 'ACSM') badgeClass = 'pb-badge--external';

    var lastSession = sim.last_session;
    var hasSessionCard = lastSession && (lastSession.car || lastSession.car_id || lastSession.track || lastSession.track_id || lastSession.layout || lastSession.layout_id);

    /* When not connected, show session card: track left, car right, fill box; overlay badge Online/Single; names below */
    if (state !== 'CONNECTED' && hasSessionCard) {
      var carId = (lastSession.car || lastSession.car_id || '').trim();
      var skin = (lastSession.skin || lastSession.skin_id || '').trim();
      var trackId = (lastSession.track || lastSession.track_id || '').trim();
      var layoutPart = (lastSession.layout || lastSession.layout_id || '').trim();
      var layoutForUrl = layoutPart && layoutPart !== '—' ? layoutPart : 'default';
      var carPreviewUrl = '';
      if (carId && carId !== '—') {
        if (skin && skin !== '—') {
          carPreviewUrl = API_BASE + '/cars/' + encodeURIComponent(carId) + '/skins/' + encodeURIComponent(skin) + '/preview';
        } else {
          carPreviewUrl = API_BASE + '/cars/' + encodeURIComponent(carId) + '/preview';
        }
      }
      var trackMapUrl = (trackId && trackId !== '—') ? (API_BASE + '/tracks/' + encodeURIComponent(trackId) + '/layouts/' + encodeURIComponent(layoutForUrl) + '/map') : '';
      var trackPreviewUrl = (trackId && trackId !== '—') ? (API_BASE + '/tracks/' + encodeURIComponent(trackId) + '/layouts/' + encodeURIComponent(layoutForUrl) + '/preview') : '';
      var trackOutlineUrl = (trackId && trackId !== '—') ? (API_BASE + '/tracks/' + encodeURIComponent(trackId) + '/layouts/' + encodeURIComponent(layoutForUrl) + '/outline') : '';
      var sessionLabel = sim.ac_running ? 'CURRENT SESSION' : 'LAST SESSION';
      var isOnline = !!(lastSession && lastSession.mode === 'online');
      var badgeText = isOnline ? 'Online' : 'Single';
      var badgeClass = isOnline ? 'pb-badge pb-session-badge--online' : 'pb-badge pb-session-badge--single';
      var trackRaw = (lastSession.track_name || lastSession.track || '').trim();
      var trackName = trackRaw ? formatTrackName(trackRaw) : '—';
      var carName = formatCarDisplayName(lastSession.car || lastSession.car_id, lastSession.car_name || lastSession.car) || '—';
      var trackImgHtml = (trackMapUrl || trackPreviewUrl)
        ? ('<div class="pb-session-track-preview-wrap">' +
           '<img class="pb-session-track-img" src="' + (trackMapUrl || trackPreviewUrl) + '" alt="" loading="lazy" data-fallback="' + escapeHtml(trackPreviewUrl || '') + '" onerror="var f=this.getAttribute(\'data-fallback\');if(f&&this.src!==f){this.src=f}else{this.style.display=\'none\';var ph=this.parentNode&&this.parentNode.nextElementSibling;if(ph)ph.style.display=\'flex\'}">' +
           (trackOutlineUrl ? ('<img class="pb-session-track-map" src="' + trackOutlineUrl + '" alt="" loading="lazy" onerror="this.src=\'\';">') : '') +
           '</div>' +
           '<div class="pb-session-placeholder" style="display:none">No track</div>')
        : '<div class="pb-session-placeholder">No track</div>';
      var carPlaceholderText = carId ? (carName !== '—' ? carName : 'Car selected') : 'No car';
      var carImgHtml = carPreviewUrl
        ? ('<img class="pb-session-car-img" src="' + escapeHtml(carPreviewUrl) + '" alt="" loading="lazy" onerror="this.style.display=\'none\';var w=this.nextElementSibling;if(w)w.style.display=\'flex\';">' +
           '<div class="pb-session-placeholder pb-session-car-placeholder" style="display:none">' + escapeHtml(carPlaceholderText) + '</div>')
        : '<div class="pb-session-placeholder pb-session-car-placeholder">' + escapeHtml(carPlaceholderText) + '</div>';
      return (
        '<div class="pb-panel pb-panel--session-card pb-panel--session-images" data-state="SESSION_CARD">' +
          '<div class="pb-panel__header">' +
            '<div class="pb-panel__title"><span class="pb-dot"></span><span>' + sessionLabel + '</span></div>' +
            '<span class="pb-session-badge-header ' + badgeClass + '">' + escapeHtml(badgeText) + '</span>' +
          '</div>' +
          '<div class="pb-panel__main pb-session-images-main">' +
            '<div class="pb-session-images-box">' +
              '<div class="pb-session-track-wrap">' + trackImgHtml + '</div>' +
              '<div class="pb-session-car-wrap">' + carImgHtml + '</div>' +
            '</div>' +
            '<div class="pb-session-names">' +
              '<span class="pb-session-track-name">' + escapeHtml(trackName) + '</span>' +
              '<span class="pb-session-car-name">' + escapeHtml(carName) + '</span>' +
            '</div>' +
          '</div>' +
        '</div>'
      );
    }

    if (!sim.online || state === 'UNAVAILABLE') {
      var reason = !sim.online ? 'Sim offline' : (sim.error || 'AC not running');
      return (
        '<div class="pb-panel" data-state="UNAVAILABLE">' +
          '<div class="pb-panel__header">' +
            '<div class="pb-panel__title"><span class="pb-dot"></span><span>SERVER</span></div>' +
            '<div class="pb-panel__badge pb-badge ' + badgeClass + '">' + escapeHtml(source) + '</div>' +
          '</div>' +
          '<div class="pb-panel__main">' +
            '<div class="pb-serverName">Unavailable</div>' +
          '</div>' +
          '<div class="pb-panel__note">' + escapeHtml(reason) + '</div>' +
        '</div>'
      );
    }

    if (state === 'CONNECTED') {
      var name = truncate(server.name || 'Unknown server', 22);
      var serverId = server.server_id || '';
      var trackObj = server.track;
      var trackName = (trackObj && trackObj.name) ? trackObj.name : (trackObj && trackObj.id) ? formatTrackName(trackObj.id) : 'Unknown track';
      var players = server.players;
      var playersStr = (players && players.current != null && players.max != null) ? (players.current + '/' + players.max) : (players && players.current != null) ? String(players.current) : '—';
      var pingMs = (server.net && server.net.ping_ms != null) ? server.net.ping_ms + 'ms' : '—';
      var role = (server.role || 'UNKNOWN').replace(/_/g, ' ');
      var ep = server.endpoint;
      var endpointStr = (ep && ep.ip != null && ep.port != null) ? (ep.ip + ':' + ep.port) : (ep && ep.ip) ? ep.ip : '—';
      return (
        '<div class="pb-panel" data-state="CONNECTED">' +
          '<div class="pb-panel__header">' +
            '<div class="pb-panel__title"><span class="pb-dot"></span><span>SERVER</span></div>' +
            '<div class="pb-panel__badge pb-badge ' + badgeClass + '">' + escapeHtml(source) + '</div>' +
          '</div>' +
          '<div class="pb-panel__main">' +
            '<div class="pb-serverName">' + escapeHtml(name) + '</div>' +
            '<div class="pb-subLine">' +
              (serverId ? '<span class="pb-serverId">' + escapeHtml(serverId) + '</span><span class="pb-sep">•</span>' : '') +
              '<span class="pb-track">' + escapeHtml(trackName) + '</span>' +
            '</div>' +
          '</div>' +
          '<div class="pb-panel__footer">' +
            '<div class="pb-metrics">' +
              '<div class="pb-metric"><span class="pb-label">Players</span><span class="pb-value">' + escapeHtml(playersStr) + '</span></div>' +
              '<div class="pb-metric"><span class="pb-label">Ping</span><span class="pb-value">' + escapeHtml(pingMs) + '</span></div>' +
              '<div class="pb-metric"><span class="pb-label">Role</span><span class="pb-value">' + escapeHtml(role) + '</span></div>' +
            '</div>' +
            '<div class="pb-endpoint">' + escapeHtml(endpointStr) + '</div>' +
          '</div>' +
        '</div>'
      );
    }

    if (state === 'DISCONNECTED') {
      var last = server.last;
      var noteHtml = 'Not connected';
      if (last && last.name) {
        noteHtml = 'Last: ' + escapeHtml(truncate(last.name, 18));
        if (last.server_id) noteHtml += ' (' + escapeHtml(last.server_id) + ')';
        if (last.endpoint && last.endpoint.ip != null && last.endpoint.port != null) {
          noteHtml += ' ' + escapeHtml(last.endpoint.ip + ':' + last.endpoint.port);
        }
      }
      return (
        '<div class="pb-panel" data-state="DISCONNECTED">' +
          '<div class="pb-panel__header">' +
            '<div class="pb-panel__title"><span class="pb-dot"></span><span>SERVER</span></div>' +
            '<div class="pb-panel__badge pb-badge ' + badgeClass + '">' + escapeHtml(source) + '</div>' +
          '</div>' +
          '<div class="pb-panel__main">' +
            '<div class="pb-serverName">Not connected</div>' +
          '</div>' +
          (noteHtml !== 'Not connected' ? '<div class="pb-panel__note">' + noteHtml + '</div>' : '') +
        '</div>'
      );
    }

    return (
      '<div class="pb-panel" data-state="UNAVAILABLE">' +
        '<div class="pb-panel__header">' +
          '<div class="pb-panel__title"><span class="pb-dot"></span><span>SERVER</span></div>' +
          '<div class="pb-panel__badge pb-badge pb-badge--unknown">' + escapeHtml(source) + '</div>' +
        '</div>' +
        '<div class="pb-panel__main"><div class="pb-serverName">Unknown</div></div>' +
      '</div>'
    );
  }

  function escapeHtml(s) {
    if (s == null) return '';
    var div = document.createElement('div');
    div.textContent = s;
    return div.innerHTML;
  }

  /**
   * Format track description for display: trim, collapse whitespace (preserve paragraph breaks),
   * fix punctuation spacing, sentence case, and normalize ALL CAPS (keep 2–5 letter acronyms).
   * Returns null if input is empty/null.
   */
  function formatTrackDescription(str) {
    if (str == null || typeof str !== 'string') return null;
    var s = str.trim();
    if (s === '') return null;
    var paras = s.split(/\n\s*\n/);
    s = paras.map(function (p) { return p.replace(/\s+/g, ' ').trim(); }).filter(Boolean).join('\n\n');
    if (s === '') return null;
    s = s.replace(/\s+([,.?!])/g, '$1');
    s = s.replace(/([.!?])([^\s])/g, '$1 $2');
    var acronyms = [];
    var titles = [];
    s = s.replace(/\b([A-Z]{2,5})\b/g, function (w) {
      acronyms.push(w);
      return '\uE000A' + (acronyms.length - 1) + '\uE001';
    });
    s = s.replace(/\b([A-Z]+)\b/g, function (w) {
      titles.push(w.charAt(0) + w.slice(1).toLowerCase());
      return '\uE000T' + (titles.length - 1) + '\uE001';
    });
    s = s.toLowerCase();
    s = s.replace(/(^|[.!?\n])\s*([a-z])/g, function (_, end, letter) {
      return end + (end === '\n' ? ' ' : '') + letter.toUpperCase();
    });
    s = s.replace(/\uE000A(\d+)\uE001/g, function (_, i) { return acronyms[parseInt(i, 10)]; });
    s = s.replace(/\uE000T(\d+)\uE001/g, function (_, i) { return titles[parseInt(i, 10)]; });
    return s;
  }

  function showToast(message, type, asHtml) {
    type = type || 'success';
    var toast = document.createElement('div');
    toast.className = 'toast ' + type;
    if (asHtml) toast.innerHTML = message;
    else toast.textContent = message;
    toastContainer.appendChild(toast);
    setTimeout(function () {
      toast.classList.add('removing');
      setTimeout(function () {
        if (toast.parentNode) toast.parentNode.removeChild(toast);
      }, 250);
    }, 4000);
  }

  function showCommandErrorToast(prefix, err) {
    if (err && err.pitboxOperatorHtml) {
      showToast(err.pitboxOperatorHtml, 'error', true);
      return;
    }
    var msg = (err && err.message) ? err.message : (err ? String(err) : 'Failed');
    showToast((prefix || '') + msg, 'error');
  }

  function getSelectedIds() {
    return Object.keys(selectedForLaunch);
  }

  function renderSessionImagesPanel(trackId, layoutId, carId, panelLabel, useSkin) {
    var layout = (layoutId && String(layoutId).trim() && String(layoutId).trim() !== '—') ? String(layoutId).trim() : 'default';
    var trackMapUrl = trackId && trackId !== '—' ? (API_BASE + '/tracks/' + encodeURIComponent(trackId) + '/layouts/' + encodeURIComponent(layout) + '/map') : '';
    var trackPreviewUrl = trackId && trackId !== '—' ? (API_BASE + '/tracks/' + encodeURIComponent(trackId) + '/layouts/' + encodeURIComponent(layout) + '/preview') : '';
    var trackOutlineUrl = trackId && trackId !== '—' ? (API_BASE + '/tracks/' + encodeURIComponent(trackId) + '/layouts/' + encodeURIComponent(layout) + '/outline') : '';
    var carUrl = '';
    if (carId && carId !== '—') {
      if (useSkin && useSkin !== '—') {
        carUrl = API_BASE + '/cars/' + encodeURIComponent(carId) + '/skins/' + encodeURIComponent(useSkin) + '/preview';
      } else {
        carUrl = API_BASE + '/cars/' + encodeURIComponent(carId) + '/preview';
      }
    }
    var trackHtml = (trackMapUrl || trackPreviewUrl)
      ? ('<div class="pb-session-track-preview-wrap">' +
         '<img class="pb-session-track-img" src="' + escapeHtml(trackMapUrl || trackPreviewUrl) + '" alt="" loading="lazy" data-fallback="' + escapeHtml(trackPreviewUrl || '') + '" onerror="var f=this.getAttribute(\'data-fallback\');if(f&&this.src!==f){this.src=f}else{this.style.display=\'none\';var ph=this.parentNode&&this.parentNode.nextElementSibling;if(ph)ph.style.display=\'flex\'}">' +
         (trackOutlineUrl ? ('<img class="pb-session-track-map" src="' + escapeHtml(trackOutlineUrl) + '" alt="" loading="lazy" onerror="this.src=\'\';">') : '') +
         '</div>' +
         '<div class="pb-session-placeholder" style="display:none">Track</div>')
      : '<div class="pb-session-placeholder">Track</div>';
    var carHtml = carUrl
      ? ('<img class="pb-session-car-img" src="' + escapeHtml(carUrl) + '" alt="" loading="lazy" onerror="this.style.display=\'none\';var w=this.nextElementSibling;if(w)w.style.display=\'flex\';">' +
         '<div class="pb-session-placeholder pb-session-car-placeholder" style="display:none">Car</div>')
      : '<div class="pb-session-placeholder pb-session-car-placeholder">Car</div>';
    return (
      '<div class="pb-panel pb-panel--session-card pb-panel--session-images" data-state="' + (panelLabel === 'CURRENT' ? 'CURRENT' : 'SELECTION') + '">' +
        '<div class="pb-panel__header"><div class="pb-panel__title"><span class="pb-dot"></span><span>' + escapeHtml(panelLabel) + '</span></div></div>' +
        '<div class="pb-panel__main pb-session-images-main">' +
          '<div class="pb-session-images-box">' +
            '<div class="pb-session-track-wrap">' + trackHtml + '</div>' +
            '<div class="pb-session-car-wrap">' + carHtml + '</div>' +
          '</div>' +
        '</div>' +
      '</div>'
    );
  }

  function renderSelectionImages(agentId, ojs) {
    var trackId = (ojs.trackId && String(ojs.trackId).trim()) || '';
    var layoutId = (ojs.trackLayout && String(ojs.trackLayout).trim()) || 'default';
    var carId = (ojs.selectedCarId && String(ojs.selectedCarId).trim()) || '';
    return renderSessionImagesPanel(trackId, layoutId, carId, 'SELECTED', null);
  }

  function renderCurrentSessionImages(agent) {
    var ls = agent.last_session;
    if (!ls || !agent.ac_running) return null;
    var trackId = (ls.track_id || ls.track || '').trim();
    var layoutId = (ls.layout_id || ls.layout || '').trim();
    var carId = (ls.car_id || ls.car || '').trim();
    var skinId = (ls.skin_id || ls.skin || '').trim();
    if ((!trackId || trackId === '—') && (!carId || carId === '—')) return null;
    return renderSessionImagesPanel(trackId, layoutId, carId, 'CURRENT', skinId);
  }

  function getSimCardThumbContent(agent, ojs) {
    if (!agent) return '';
    var currentHtml = renderCurrentSessionImages(agent);
    if (currentHtml) return currentHtml;
    if (ojs && ojs.selectedServerId && (ojs.trackId || ojs.selectedCarId)) {
      return renderSelectionImages(agent.agent_id, ojs);
    }
    return renderServerPanel(agent);
  }

  function updateSimCardSelectionImages(agentId) {
    if (!grid) return;
    var card = grid.querySelector('.sim-card[data-agent-id="' + (window.CSS && window.CSS.escape ? window.CSS.escape(agentId) : agentId) + '"]');
    if (!card) return;
    var thumb = card.querySelector('.sim-card-thumb');
    if (!thumb) return;
    var ojs = getOnlineJoinState(agentId);
    var agent = currentAgents.filter(function (a) { return a.agent_id === agentId; })[0];
    thumb.innerHTML = getSimCardThumbContent(agent, ojs);
  }

  function updateBatchStatusEls() {
    if (!grid) return;
    grid.querySelectorAll('.sim-card-batch-status').forEach(function (el) {
      var id = el.getAttribute('data-agent-id');
      var state = id ? batchState[id] : null;
      if (!state) {
        el.classList.add('hidden');
        el.textContent = '';
        el.className = 'sim-card-batch-status hidden';
        return;
      }
      el.classList.remove('hidden');
      if (state.status === 'pending') {
        el.className = 'sim-card-batch-status sim-card-batch-pending';
        el.innerHTML = '<span class="sim-card-batch-spinner" aria-hidden="true"></span> ' + (state.action === 'launch' ? 'Launching…' : 'Exiting…');
      } else if (state.status === 'ok') {
        el.className = 'sim-card-batch-status sim-card-batch-ok';
        el.innerHTML = '<span class="sim-card-batch-check" aria-hidden="true">✓</span> ' + (state.action === 'launch' ? 'Launched' : 'Exited');
      } else {
        el.className = 'sim-card-batch-status sim-card-batch-failed';
        el.innerHTML = '<span class="sim-card-batch-fail" aria-hidden="true">✗</span> ' + (state.error || (state.action === 'launch' ? 'Launch failed' : 'Exit failed'));
      }
    });
  }

  function toggleCardSelected(agentId) {
    if (selectedForLaunch[agentId]) delete selectedForLaunch[agentId];
    else selectedForLaunch[agentId] = true;
  }

  function renderStats(agents) {
    if (!agents || !agents.length) {
      document.getElementById('stat-total').textContent = '0';
      document.getElementById('stat-online').textContent = '0';
      document.getElementById('stat-online-total').textContent = ' / 0';
      document.getElementById('stat-running').textContent = '0';
      return;
    }
    var total = agents.length;
    var online = agents.filter(function (a) { return a.online; }).length;
    var running = agents.filter(function (a) { return a.ac_running; }).length;
    document.getElementById('stat-total').textContent = total;
    document.getElementById('stat-online').textContent = online;
    document.getElementById('stat-online-total').textContent = ' / ' + total;
    document.getElementById('stat-running').textContent = running;
  }

  function getTimerMinutesFromCard(card, agentId) {
    if (!card) return 0;
    var input = card.querySelector('.sim-card-timer-input');
    if (!input) return 0;
    var n = parseInt(input.value, 10);
    return isNaN(n) || n < 0 ? 0 : n;
  }

  function formatMmSs(sec) {
    var m = Math.floor(sec / 60);
    var s = Math.floor(sec % 60);
    return m + ':' + (s < 10 ? '0' : '') + s;
  }

  function setPillText(pill, text) {
    if (!pill) return;
    var dot = pill.querySelector('.status-pill-dot');
    var textNode = dot && dot.nextSibling && dot.nextSibling.nodeType === 3 ? dot.nextSibling : null;
    if (textNode) textNode.textContent = text;
    else pill.appendChild(document.createTextNode(text));
  }

  function updateSessionTimeDisplays() {
    if (!grid) return;
    var ids = Object.keys(sessionStartTimeByAgent);
    for (var i = 0; i < ids.length; i++) {
      var id = ids[i];
      var card = grid.querySelector('.sim-card[data-agent-id="' + (window.CSS && window.CSS.escape ? window.CSS.escape(id) : id.replace(/"/g, '\\"')) + '"]');
      if (!card) continue;
      var wrap = card.querySelector('.sim-card-header-status-wrap');
      var pill = wrap ? wrap.querySelector('.status-pill') : null;
      var badge = wrap ? wrap.querySelector('.sim-card-countdown-badge') : null;
      var startMs = sessionStartTimeByAgent[id];
      if (startMs == null) {
        if (badge) { badge.textContent = ''; badge.className = 'sim-card-countdown-badge hidden'; }
        continue;
      }
      var elapsedSec = (Date.now() - startMs) / 1000;
      var limitMin = sessionTimerMinutesByAgent[id];
      var limitSec = (limitMin != null && limitMin > 0) ? limitMin * 60 : null;

      if (limitSec != null) {
        var remaining = limitSec - elapsedSec;
        if (remaining <= 0) {
          if (pill) { pill.className = 'status-pill status-pill-ending-soon'; setPillText(pill, 'Session ended'); }
          if (badge) { badge.textContent = 'Session ended'; badge.className = 'sim-card-countdown-badge badge-ending-soon'; }
          continue;
        }
        var remainingClamped = Math.floor(remaining);
        var mmss = formatMmSs(remainingClamped);
        if (remaining > 120) {
          if (pill) { pill.className = 'status-pill status-pill-running'; setPillText(pill, 'Running'); }
          if (badge) { badge.textContent = mmss; badge.className = 'sim-card-countdown-badge badge-running'; }
        } else if (remaining > 10) {
          if (pill) { pill.className = 'status-pill status-pill-ending-soon'; setPillText(pill, 'Ending Soon'); }
          if (badge) { badge.textContent = mmss; badge.className = 'sim-card-countdown-badge badge-ending-soon'; }
        } else {
          if (pill) { pill.className = 'status-pill status-pill-final status-pill-flash'; setPillText(pill, mmss); }
          if (badge) { badge.textContent = mmss; badge.className = 'sim-card-countdown-badge badge-final status-pill-flash'; }
        }
      } else {
        var mmssElapsed = formatMmSs(Math.floor(elapsedSec));
        if (pill) { pill.className = 'status-pill status-pill-running'; setPillText(pill, 'Running'); }
        if (badge) { badge.textContent = mmssElapsed; badge.className = 'sim-card-countdown-badge badge-running'; }
      }
    }
  }

  function updateSimCardsStatus(agents) {
    if (!grid || !agents || !agents.length) return;
    agents.forEach(function (a) {
      var id = a.agent_id;
      if (a.ac_running && a.uptime_sec != null) {
        if (sessionStartTimeByAgent[id] == null) {
          sessionStartTimeByAgent[id] = Date.now() - (a.uptime_sec * 1000);
        }
      } else {
        delete sessionStartTimeByAgent[id];
      }
      var card = grid.querySelector('.sim-card[data-agent-id="' + (window.CSS && window.CSS.escape ? window.CSS.escape(id) : id) + '"]');
      if (!card) return;
      if (sessionStartTimeByAgent[id] == null) {
        var kind = statusKind(a);
        var wrap = card.querySelector('.sim-card-header-status-wrap');
        var pill = wrap ? wrap.querySelector('.status-pill') : null;
        var badge = wrap ? wrap.querySelector('.sim-card-countdown-badge') : null;
        if (pill) {
          pill.className = 'status-pill status-pill-' + kind;
          setPillText(pill, statusLabel(kind));
        }
        if (badge) { badge.textContent = ''; badge.className = 'sim-card-countdown-badge hidden'; }
      }
      var infoEl = card.querySelector('.sim-card-info');
      if (infoEl) infoEl.innerHTML = '';
      var thumb = card.querySelector('.sim-card-thumb');
      if (thumb) {
        var ojs = getOnlineJoinState(a.agent_id);
        thumb.innerHTML = getSimCardThumbContent(a, ojs);
      }
    });
    updateSessionTimeDisplays();
  }

  function renderSimCards(agents) {
    if (!grid) return;
    if (!agents || !agents.length) {
      grid.innerHTML = '<p class="status-text">No rigs enrolled. Turn on Enrollment Mode, then start PitBox on each sim to add them.</p>';
      lastSimCardsAgentIds = null;
      return;
    }
    var opLocked = operatorControlBlocked();
    var agentIdsKey = agents.map(function (a) { return a.agent_id; }).join(',') + '|' + (opLocked ? 'L' : 'U');
    if (lastSimCardsAgentIds === agentIdsKey) {
      updateSimCardsStatus(agents);
      return;
    }
    lastSimCardsAgentIds = agentIdsKey;
    currentAgents = agents;
    var onlyTestCard = agents.length === 1 && agents[0].agent_id === TEST_SIM_AGENT_ID;
    var allUnreachable = !onlyTestCard && agents.every(function (a) { return !a.online; });
    if (connectionBanner) {
      connectionBanner.classList.toggle('hidden', !allUnreachable);
    }
    var activeEl = document.activeElement;
    var activeCard = activeEl && activeEl.closest ? activeEl.closest('.sim-card') : null;
    var activeAgentId = activeCard ? activeCard.getAttribute('data-agent-id') : null;
    grid.innerHTML = agents.map(function (a, i) {
      var kind = statusKind(a);
      var num = i + 1;
      var label = (a.display_name && String(a.display_name).trim()) ? String(a.display_name).trim() : ('SIM ' + num);
      var trackName = a.ac_running ? '—' : '—';
      var agentId = escapeHtml(a.agent_id);
      var defaultDriver = (a.display_name && String(a.display_name).trim()) ? String(a.display_name).trim() : ('Sim ' + num);
      var driverValue = driverNames[a.agent_id] !== undefined ? driverNames[a.agent_id] : defaultDriver;
      var steeringVal = steeringPreset[a.agent_id] !== undefined ? steeringPreset[a.agent_id] : '';
      var shiftingVal = shiftingPreset[a.agent_id] !== undefined ? shiftingPreset[a.agent_id] : 'H-Pattern';
      var selectedClass = selectedForLaunch[a.agent_id] ? ' sim-card-selected' : '';
      var isTestCard = a.agent_id === TEST_SIM_AGENT_ID;
      var testCardClass = isTestCard ? ' sim-card-test' : '';
      /* Steering: AC savedsetups only (from agent steering_presets). Option value=raw; label=presetDisplayLabel for display. */
      var steeringList = Array.isArray(a.steering_presets) && a.steering_presets.length > 0
        ? a.steering_presets.map(function (name) { return { v: name, label: presetDisplayLabel(name) }; })
        : [{ v: '', label: '— No preset —' }];
      if (steeringVal === '' && steeringList.length > 0) steeringVal = steeringList[0].v;
      steeringPreset[a.agent_id] = steeringVal;
      var steeringOpts = steeringList.map(function (o) {
        var sel = o.v === steeringVal ? ' selected' : '';
        return '<option value="' + escapeHtml(o.v) + '"' + sel + '>' + escapeHtml(o.label || o.v) + '</option>';
      }).join('');
      /* Shifting: CM assists .cmpreset list from agent (shifting_presets). Do NOT use steering_presets. */
      var shiftingList = Array.isArray(a.shifting_presets) && a.shifting_presets.length > 0
        ? a.shifting_presets.map(function (name) { return { v: name, label: name }; })
        : SHIFTING_PRESET_FALLBACK.slice();
      if (shiftingVal === '' && shiftingList.length > 0) shiftingVal = shiftingList[0].v;
      shiftingPreset[a.agent_id] = shiftingVal;
      var shiftingOpts = shiftingList.map(function (o) {
        var sel = o.v === shiftingVal ? ' selected' : '';
        return '<option value="' + escapeHtml(o.v) + '"' + sel + '>' + escapeHtml(o.label) + '</option>';
      }).join('');
      var ojs = getOnlineJoinState(a.agent_id);
      var agentOnline = !!a.online;
      var effectiveServerId = ojs.selectedServerId || displayAssignments[a.agent_id] || '';
      var serverById = new Map((serverList || []).map(function (s) { return [s.id, s]; }));
      var idsForDropdown = (displayServerIds && displayServerIds.length) ? displayServerIds : (serverList || []).map(function (s) { return s.id; });
      var serverDropdownOpts = '<option value="">— Current session —</option>' + (idsForDropdown.map(function (id) {
        var s = serverById.get(id);
        var name = (s && s.name) ? s.name : id;
        var sourceIndicator = (s && s.source === 'favorite') ? ' ★' : '';
        var title = (s && s.source === 'favorite') ? 'Content Manager favourite' : 'Local preset';
        var sel = effectiveServerId === id ? ' selected' : '';
        return '<option value="' + escapeHtml(id) + '"' + sel + ' data-source="' + (s && s.source ? escapeHtml(s.source) : '') + '" title="' + title + '">' + escapeHtml(name) + sourceIndicator + '</option>';
      }).join(''));
      var serverDisabled = !agentOnline || ojs.launchStatus === 'joining' || opLocked ? ' disabled' : '';
      var carPickerContent = getCarPickerContent(a.agent_id, ojs, !agentOnline || ojs.launchStatus === 'joining' || opLocked);
      var presetDis = opLocked ? ' disabled' : '';
      var ctrlLockClass = opLocked ? ' sim-card-controls--locked' : '';
      var serverLabel = (function () {
        var s = serverById.get(effectiveServerId);
        return s ? (s.name || s.id) : (effectiveServerId || '');
      })();
      var selectedServerSource = (function () {
        var s = serverById.get(effectiveServerId);
        return (s && s.source) ? s.source : '';
      })();
      var serverSourceBadge = selectedServerSource === 'favorite' ? '<span class="sim-card-server-source sim-card-server-source--favorite" title="Content Manager favourite">★ Favourite</span>' : (selectedServerSource === 'preset' ? '<span class="sim-card-server-source sim-card-server-source--preset" title="Local preset">Preset</span>' : '');
      return (
        '<div class="sim-card' + selectedClass + testCardClass + ' sim-card-expanded" data-agent-id="' + agentId + '" role="button" tabindex="0">' +
          '<div class="sim-card-header">' +
            '<h3 class="sim-card-title">' + escapeHtml(label) + '</h3>' +
            '<div class="sim-card-header-status-wrap">' +
              '<span class="status-pill status-pill-' + kind + '">' +
                '<span class="status-pill-dot"></span>' +
                escapeHtml(statusLabel(kind)) +
              '</span>' +
              '<span class="sim-card-countdown-badge hidden" data-agent-id="' + escapeHtml(a.agent_id) + '" aria-live="polite"></span>' +
            '</div>' +
          '</div>' +
          '<div class="sim-card-batch-status hidden" data-agent-id="' + agentId + '" aria-live="polite"></div>' +
          '<div class="sim-card-thumb">' +
            getSimCardThumbContent(a, ojs) +
          '</div>' +
          '<div class="sim-card-info" aria-hidden="true"></div>' +
          '<div class="sim-card-controls' + ctrlLockClass + '">' +
            '<div class="sim-card-online-row">' +
              '<label class="sim-card-label">Server</label>' +
              '<span class="sim-card-server-select-wrap">' +
                '<select class="sim-card-dropdown preset-server" data-agent-id="' + agentId + '" title="Online server"' + serverDisabled + '>' +
                  serverDropdownOpts +
                '</select>' +
                serverSourceBadge +
              '</span>' +
            '</div>' +
            '<div class="sim-card-car-picker-wrap">' +
              '<label class="sim-card-label">Car</label>' +
              '<div class="sim-card-car-picker" data-agent-id="' + agentId + '">' + carPickerContent + '</div>' +
            '</div>' +
            '<p class="sim-card-online-error hidden" data-agent-id="' + agentId + '" aria-live="polite"></p>' +
            '<div class="sim-card-presets-row">' +
              '<div class="sim-card-preset-col"><label class="sim-card-label">Steering</label><select class="sim-card-dropdown preset-steering" data-agent-id="' + agentId + '" title="Steering Preset"' + presetDis + '>' + steeringOpts + '</select></div>' +
              '<div class="sim-card-preset-col"><label class="sim-card-label">Shifting</label><select class="sim-card-dropdown preset-shifting" data-agent-id="' + agentId + '" title="Shifting Preset"' + presetDis + '>' + shiftingOpts + '</select></div>' +
              '<div class="sim-card-preset-col"><label class="sim-card-label">Name</label><input type="text" class="sim-card-driver-input" data-agent-id="' + escapeHtml(a.agent_id) + '" value="' + escapeHtml(driverValue) + '" placeholder="Driver name" title="Driver name"' + presetDis + '></div>' +
              '<div class="sim-card-preset-col sim-card-timer-col"><label class="sim-card-label">Timer (min)</label><input type="number" class="sim-card-timer-input" data-agent-id="' + escapeHtml(a.agent_id) + '" value="' + (sessionTimerMinutesByAgent[a.agent_id] !== undefined ? String(Math.max(0, parseInt(sessionTimerMinutesByAgent[a.agent_id], 10) || 0)) : '0') + '" min="0" step="1" placeholder="0" title="Session time limit in minutes (0 = no limit)"' + presetDis + '></div>' +
            '</div>' +
            '<div class="sim-card-buttons">' +
              '<button type="button" class="btn-primary btn-card-launch" data-agent-id="' + agentId + '"' + presetDis + '>Launch Session</button>' +
              '<button type="button" class="btn-danger btn-card-end" data-agent-id="' + agentId + '"' + presetDis + '>End Session</button>' +
              '<button type="button" class="btn-secondary btn-card-reset" data-agent-id="' + agentId + '"' + presetDis + '>Reset Rig</button>' +
              (isTestCard ? '' : ('<button type="button" class="btn-secondary btn-card-remove" data-agent-id="' + agentId + '" title="Remove this sim from controller (unpair)"' + presetDis + '>Remove</button>')) +
            '</div>' +
          '</div>' +
        '</div>'
      );
    }).join('');
    if (!sessionTimeDisplayTimer) {
      sessionTimeDisplayTimer = setInterval(updateSessionTimeDisplays, 1000);
    }
    grid.querySelectorAll('.btn-card-launch').forEach(function (btn) {
      btn.addEventListener('click', function () {
        ensureOperatorOrRedirect().then(function (ok) {
          if (!ok) return;
        var id = btn.getAttribute('data-agent-id');
        var card = btn.closest('.sim-card');
        var agent = currentAgents.filter(function (a) { return a.agent_id === id; })[0];
        var ojs = getOnlineJoinState(id);
        var errEl = card ? card.querySelector('.sim-card-online-error') : null;
        function setErr(msg) { if (errEl) { errEl.textContent = msg || ''; errEl.classList.toggle('hidden', !msg); } }
        setErr('');
        if (!agent || !agent.online) { setErr('Sim offline'); return; }
        if (!ojs.selectedServerId) { setErr('Select a server'); return; }
        if (ojs.carsStatus !== 'ready') { setErr('Cars not loaded'); return; }
        var carId = getCarIdFromCard(card, id) || (ojs.selectedCarId && String(ojs.selectedCarId).trim()) || null;
        if (!carId) { setErr('Select a car'); return; }
        ojs.selectedCarId = carId;
        var driverInput = card ? card.querySelector('.sim-card-driver-input') : null;
        var driverName = (driverInput && (driverInput.value || '').trim()) || (driverNames[id] !== undefined ? String(driverNames[id]).trim() : '') || ('Sim ' + id);
        var presetId = (card && card.querySelector('.preset-steering')) ? (card.querySelector('.preset-steering').value || '').trim() : (steeringPreset[id] || '');
        var shifterMode = (card && card.querySelector('.preset-shifting')) ? (card.querySelector('.preset-shifting').value || '').trim() : (shiftingPreset[id] || '');
        var simDisplay = (ojs.selectedServerId || '').trim() || null;
        var details = ojs.serverDetails || {};
        var timerMinutes = getTimerMinutesFromCard(card, id);
        ojs.launchStatus = 'joining';
        ojs.errorMessage = null;
        setErr('');
        pitboxFetch(API_BASE + '/set-driver-name', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ sim_id: id, driver_name: driverName })
        })
          .then(function (r) { return r.json().then(function (data) { if (!r.ok) throw new Error(data.detail || data.message || r.statusText); return data; }); })
          .then(function () {
            return pitboxFetch(API_BASE + '/agents/' + encodeURIComponent(id) + '/launch_online', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                server_id: ojs.selectedServerId || null,
                server_ip: details.ip || null,
                server_port: details.port != null ? details.port : null,
                car_id: carId,
                preset_id: presetId || null,
                shifter_mode: shifterMode || null,
                sim_display: simDisplay,
                max_running_time_minutes: timerMinutes
              })
            });
          })
          .then(function (r) {
            return r.json().then(function (data) {
              if (!r.ok) {
                var msg = (data.detail && typeof data.detail === 'object' && data.detail.detail) ? data.detail.detail : (data.detail || data.message || r.statusText);
                if (typeof msg !== 'string') msg = (data.detail && data.detail.error) ? data.detail.error : String(msg);
                throw new Error(msg);
              }
              return data;
            });
          })
          .then(function () {
            sessionStartTimeByAgent[id] = Date.now();
            sessionTimerMinutesByAgent[id] = timerMinutes;
            ojs.launchStatus = 'joined';
            setErr('');
            scheduleFetchStatus('launch');
            showToast('Launch sent: ' + id, 'success');
          })
          .catch(function (e) {
            ojs.launchStatus = 'error';
            ojs.errorMessage = e.message || String(e);
            setErr(ojs.errorMessage);
            showToast('Launch failed: ' + (e.message || e), 'error');
          });
        });
      });
    });
    grid.querySelectorAll('.preset-server').forEach(function (sel) {
      sel.addEventListener('change', function () {
        var id = sel.getAttribute('data-agent-id');
        var serverId = (sel.value || '').trim() || null;
        var opt = sel.options[sel.selectedIndex];
        var source = (opt && opt.getAttribute('data-source')) || '';
        var wrap = sel.closest('.sim-card-server-select-wrap');
        var badge = wrap ? wrap.querySelector('.sim-card-server-source') : null;
        if (badge) {
          badge.textContent = source === 'favorite' ? '★ Favourite' : (source === 'preset' ? 'Preset' : '');
          badge.className = 'sim-card-server-source' + (source === 'favorite' ? ' sim-card-server-source--favorite' : (source === 'preset' ? ' sim-card-server-source--preset' : ''));
          badge.title = source === 'favorite' ? 'Content Manager favourite' : (source === 'preset' ? 'Local preset' : '');
          badge.style.display = (source === 'favorite' || source === 'preset') ? '' : 'none';
        }
        var ojs = getOnlineJoinState(id);
        ojs.selectedServerId = serverId;
        ojs.selectedCarId = null;
        ojs.errorMessage = null;
        var card = sel.closest('.sim-card');
        var pickerEl = card ? card.querySelector('.sim-card-car-picker') : null;
        var errEl = card ? card.querySelector('.sim-card-online-error') : null;
        if (errEl) { errEl.textContent = ''; errEl.classList.add('hidden'); }
        if (!serverId) {
          ojs.carsStatus = 'idle';
          ojs.carOptions = [];
          ojs.serverDetails = null;
          ojs.trackId = null;
          ojs.trackLayout = null;
          ojs.selectedCarId = null;
          if (pickerEl) pickerEl.innerHTML = '<span class="sim-card-car-picker-msg">— Select server —</span>';
          updateSimCardSelectionImages(id);
          pitboxFetch(API_BASE + '/assignments/' + encodeURIComponent(id), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ server_id: '' })
          }).catch(function () {});
          return;
        }
        pitboxFetch(API_BASE + '/assignments/' + encodeURIComponent(id), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ server_id: serverId })
        }).catch(function () {});
        ojs.carsStatus = 'loading';
        if (pickerEl) pickerEl.innerHTML = '<span class="sim-card-car-picker-msg">Loading cars…</span>';
        getPresetDiskStateCached(serverId)
          .then(function (data) {
            ojs.carsStatus = 'ready';
            var rawCars = Array.isArray(data.cars) ? data.cars : [];
            var counts = carCountsFromEntryList(data.entry_list && data.entry_list.cars);
            ojs.carOptions = rawCars.map(function (model) {
              var m = (model && String(model).trim()) || '';
              return { id: m, displayName: m, thumbnailUrl: '', isTaken: null, countInServer: counts[m] || 0 };
            });
            ojs.selectedCarId = resolveCarIdAfterLoad(rawCars, ojs.selectedCarId, counts, serverId);
            var track = data.track || {};
            ojs.trackId = (track.track_id || '').trim() || null;
            ojs.trackLayout = (track.layout_id || '').trim() || null;
            var join = data.server_join || {};
            ojs.serverDetails = { ip: join.host || null, port: join.port != null ? join.port : null };
            var agent = currentAgents.filter(function (a) { return a.agent_id === id; })[0];
            var disabled = !agent || !agent.online || ojs.launchStatus === 'joining' || operatorControlBlocked();
            var cardNow = grid.querySelector('.sim-card[data-agent-id="' + (window.CSS && window.CSS.escape ? window.CSS.escape(id) : id.replace(/"/g, '\\"')) + '"]');
            var pickerNow = cardNow ? cardNow.querySelector('.sim-card-car-picker') : null;
            if (pickerNow) {
              pickerNow.innerHTML = getCarPickerContent(id, ojs, disabled);
              attachCarPickerListeners(cardNow, pickerNow, id);
            }
            updateSimCardSelectionImages(id);
          })
          .catch(function (err) {
            ojs.carsStatus = 'error';
            ojs.carOptions = [];
            ojs.serverDetails = null;
            ojs.trackId = null;
            ojs.trackLayout = null;
            var cardErr = grid.querySelector('.sim-card[data-agent-id="' + (window.CSS && window.CSS.escape ? window.CSS.escape(id) : id) + '"]');
            var pickerErr = cardErr ? cardErr.querySelector('.sim-card-car-picker') : null;
            var errElNow = cardErr ? cardErr.querySelector('.sim-card-online-error') : null;
            if (pickerErr) pickerErr.innerHTML = '<span class="sim-card-car-picker-msg sim-card-car-picker-error">Failed to load</span>';
            if (errElNow) { errElNow.textContent = err.message || 'Failed to load'; errElNow.classList.remove('hidden'); }
          });
      });
    });
    grid.querySelectorAll('.sim-card-car-picker').forEach(function (pickerEl) {
      var id = pickerEl.getAttribute('data-agent-id');
      var card = pickerEl.closest('.sim-card');
      attachCarPickerListeners(card, pickerEl, id);
      var comboSelect = pickerEl.querySelector('.car-combobox-select');
      var comboFilter = pickerEl.querySelector('.car-combobox-filter');
      if (comboFilter && comboSelect) {
        comboFilter.addEventListener('input', function () {
          var q = (comboFilter.value || '').toLowerCase().trim();
          [].slice.call(comboSelect.options).forEach(function (opt) {
            if (opt.value === '') { opt.hidden = false; return; }
            var text = (opt.textContent || '').toLowerCase();
            opt.hidden = q ? text.indexOf(q) === -1 : false;
          });
        });
      }
    });
    grid.querySelectorAll('.btn-card-end').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var id = btn.getAttribute('data-agent-id');
        if (id === TEST_SIM_AGENT_ID) { showToast('Test card – no sim connected.', 'warning'); return; }
        delete sessionStartTimeByAgent[id];
        postCommand('/stop', { sim_ids: [id] }).then(function () { scheduleFetchStatus('stop'); showToast('End session sent: ' + id, 'success'); }).catch(function (e) { showCommandErrorToast('End failed: ', e); });
      });
    });
    grid.querySelectorAll('.sim-card-driver-input').forEach(function (input) {
      var id = input.getAttribute('data-agent-id');
      input.addEventListener('input', function () { driverNames[id] = input.value; });
      input.addEventListener('change', function () {
        driverNames[id] = input.value;
        var name = (input.value || '').trim();
        if (!name) return;
        pitboxFetch(API_BASE + '/set-driver-name', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ sim_id: id, driver_name: name })
        })
          .then(function (r) { return r.json(); })
          .then(function (data) {
            if (data.success && typeof showToast === 'function') showToast('Driver name saved: ' + name, 'success');
          })
          .catch(function (err) {
            if (typeof showToast === 'function') showToast('Driver name failed: ' + (err.message || err), 'error');
          });
      });
    });
    if (activeAgentId && activeEl && activeEl.classList && activeEl.classList.contains('sim-card-driver-input')) {
      try {
        var card = grid.querySelector('.sim-card[data-agent-id="' + CSS.escape(activeAgentId) + '"]');
        var input = card ? card.querySelector('.sim-card-driver-input') : null;
        if (input) {
          input.focus();
          var len = input.value.length;
          input.setSelectionRange(len, len);
        }
      } catch (e) {}
    }
    grid.querySelectorAll('.sim-card').forEach(function (card) {
      card.addEventListener('click', function (e) {
        if (e.target.closest('button, input, select, .sim-card-online-row, .sim-card-car-picker-wrap')) return;
        var id = card.getAttribute('data-agent-id');
        if (!id) return;
        toggleCardSelected(id);
        card.classList.toggle('sim-card-selected', !!selectedForLaunch[id]);
      });
    });
    currentAgents.forEach(function (a) {
      var id = a.agent_id;
      if (a.ac_running && a.uptime_sec != null) {
        if (sessionStartTimeByAgent[id] == null) sessionStartTimeByAgent[id] = Date.now() - (a.uptime_sec * 1000);
      } else {
        delete sessionStartTimeByAgent[id];
      }
    });
    updateSessionTimeDisplays();
    updateBatchStatusEls();
  }

  function updateLastUpdateText() {
    if (!lastUpdateEl) return;
    if (lastFetchTime == null) {
      lastUpdateEl.textContent = 'Last update: --';
      return;
    }
    var sec = Math.floor((Date.now() - lastFetchTime) / 1000);
    if (sec < 5) {
      lastUpdateEl.textContent = 'Last update: ' + (sec <= 1 ? 'just now' : sec + 's ago');
    } else {
      lastUpdateEl.textContent = 'Last update: ' + new Date(lastFetchTime).toLocaleTimeString();
    }
  }

  function fetchStatusCore() {
    var statusPromise = pitboxFetch(API_BASE + '/status').then(function (r) {
      if (!r.ok) throw new Error('Status ' + r.status);
      return r.json();
    });
    var serversPromise = pitboxFetch(API_BASE + '/servers').then(function (r) { return r.ok ? r.json() : []; }).catch(function () { return []; });

    function applyStatusData(data) {
      lastFetchTime = Date.now();
      var pollSec = data.poll_interval_sec;
      if (typeof pollSec === 'number' && pollSec > 0 && isFinite(pollSec)) {
        STATUS_POLL_VISIBLE_MS = Math.max(3000, Math.round(pollSec * 1000));
        stopStatusPolling();
        startStatusPolling();
      }
      var agents = data.agents || [];
      if (agents.length === 0) agents = [TEST_SIM_AGENT];  /* show one card for UI editing when no rigs enrolled */
      displayServerIds = data.server_ids || [];
      displayAssignments = data.assignments || {};
      var diskStateWork = [];
      Object.keys(displayAssignments || {}).forEach(function (id) {
        var o = getOnlineJoinState(id);
        var assigned = (displayAssignments[id] || '').trim();
        if (assigned && !(o.selectedServerId || '').trim()) {
          o.selectedServerId = assigned;
          o.carsStatus = 'loading';
          diskStateWork.push({ o: o, assigned: assigned });
        }
      });
      if (diskStateWork.length > 0) {
        var presetIdsForBatch = diskStateWork.map(function (w) { return w.assigned; });
        getPresetDiskStatesCached(presetIdsForBatch)
          .then(function (m) {
            diskStateWork.forEach(function (w) {
              var d = m[w.assigned];
              if (d == null) {
                w.o.carsStatus = 'error';
                w.o.carOptions = [];
                w.o.selectedCarId = null;
                return;
              }
              w.o.carsStatus = 'ready';
              var rawCars = Array.isArray(d.cars) ? d.cars : [];
              var counts = carCountsFromEntryList(d.entry_list && d.entry_list.cars);
              w.o.carOptions = rawCars.map(function (model) {
                var mname = (model && String(model).trim()) || '';
                return { id: mname, displayName: mname, thumbnailUrl: '', isTaken: null, countInServer: counts[mname] || 0 };
              });
              w.o.selectedCarId = resolveCarIdAfterLoad(rawCars, w.o.selectedCarId, counts, w.assigned);
              var track = d.track || {};
              w.o.trackId = (track.track_id || '').trim() || null;
              w.o.trackLayout = (track.layout_id || '').trim() || null;
              var join = d.server_join || {};
              w.o.serverDetails = { ip: join.host || null, port: join.port != null ? join.port : null };
            });
            lastSimCardsAgentIds = null;
            renderSimCards(currentAgents);
          })
          .catch(function () {
            diskStateWork.forEach(function (w) {
              w.o.carsStatus = 'error';
              w.o.carOptions = [];
              w.o.selectedCarId = null;
            });
            renderSimCards(currentAgents);
          });
      }
      renderStats(agents);
      renderSimCards(agents);
      updateLastUpdateText();
      window._commandCenterLastStatus = data;
      var dashPage = document.getElementById('page-dashboard');
      if (dashPage && !dashPage.classList.contains('hidden')) {
        renderDashboard(agents);
        updateDashboardLastUpdate();
        var ccSel = document.getElementById('cc-server-select');
        var sid = (ccSel && ccSel.value) ? ccSel.value.trim() : 'default';
        Promise.all([
          getDashboardServerConfigCached(sid),
          getDashboardProcessStatusCached()
        ]).then(function (arr) { renderCommandCenterDashboard(data, arr[0], arr[1]); });
      }
      return data;
    }

    var statusHandled = statusPromise.then(function (data) {
      return applyStatusData(data);
    }).catch(function (err) {
      if (lastUpdateEl) lastUpdateEl.textContent = 'Last update: error';
      throw err;
    });

    serversPromise.then(function (servers) {
      serverList = Array.isArray(servers) ? servers : [];
      if (serverList.length > 0 && displayServerIds && displayServerIds.length > 0) {
        var sortedIds = serverList.map(function (s) { return s.id; });
        var sortedSet = {};
        sortedIds.forEach(function (id) { sortedSet[id] = true; });
        var extras = displayServerIds.filter(function (id) { return !sortedSet[id]; });
        displayServerIds = sortedIds.concat(extras);
      }
      if (currentAgents && currentAgents.length > 0) {
        lastSimCardsAgentIds = null;
        renderSimCards(currentAgents);
      }
    });

    return statusHandled;
  }

  /**
   * Runs one status fetch; if callers requested a refresh while it was in flight, runs at most one immediate follow-up.
   * Further demand after that follow-up is re-queued via scheduleFetchStatus (debounced) to avoid unbounded chains.
   */
  function runFetchStatusWithCoalesce(fromCoalescedRerun) {
    if (launchBusy) return Promise.resolve();
    fetchStatusCoreInFlight = true;
    return fetchStatusCore().finally(function () {
      fetchStatusCoreInFlight = false;
      if (fetchStatusPendingCoalesced) {
        fetchStatusPendingCoalesced = false;
        if (fromCoalescedRerun) {
          scheduleFetchStatus('after-in-flight');
          return;
        }
        return runFetchStatusWithCoalesce(true);
      }
    });
  }

  function scheduleFetchStatus(reason) {
    if (PITBOX_DEBUG && reason) console.debug('[scheduleFetchStatus]', reason);
    if (fetchStatusScheduleTimer) clearTimeout(fetchStatusScheduleTimer);
    fetchStatusScheduleTimer = setTimeout(function () {
      fetchStatusScheduleTimer = null;
      if (fetchStatusCoreInFlight) {
        fetchStatusPendingCoalesced = true;
        return;
      }
      runFetchStatusWithCoalesce(false);
    }, FETCH_STATUS_SCHEDULE_DEBOUNCE_MS);
  }

  function fetchStatus() {
    return runFetchStatusWithCoalesce(false);
  }

  function getStatusPollInterval() {
    return document.visibilityState === 'visible' ? STATUS_POLL_VISIBLE_MS : STATUS_POLL_HIDDEN_MS;
  }
  function stopStatusPolling() {
    if (statusPollTimer !== null) {
      clearTimeout(statusPollTimer);
      statusPollTimer = null;
    }
  }
  function startStatusPolling() {
    stopStatusPolling();
    statusPollTimer = setTimeout(runFetchStatusTick, getStatusPollInterval());
  }
  function runFetchStatusTick() {
    if (statusPollInFlight) {
      statusPollTimer = setTimeout(runFetchStatusTick, getStatusPollInterval());
      return;
    }
    statusPollInFlight = true;
    Promise.resolve(fetchStatus())
      .catch(function () {})
      .finally(function () {
        statusPollInFlight = false;
        stopStatusPolling();
        statusPollTimer = setTimeout(runFetchStatusTick, getStatusPollInterval());
      });
  }

  function updateToolbarOperatorLock() {
    var locked = operatorControlBlocked();
    var launchSel = document.getElementById('launch-selected');
    var exitSel = document.getElementById('exit-selected');
    if (launchSel) launchSel.disabled = locked;
    if (exitSel) exitSel.disabled = locked;
  }

  function refreshOperatorLoginBanner() {
    return loadOperatorSession()
      .then(function (s) {
        updateToolbarOperatorLock();
        if (!s || !s.employee_login_enabled || s.logged_in) {
          var ex = document.getElementById('pitbox-operator-login-banner');
          if (ex) ex.classList.add('hidden');
          return;
        }
        var nextEnc = getLoginNextUrl();
        var banner = document.getElementById('pitbox-operator-login-banner');
        if (!banner) {
          banner = document.createElement('div');
          banner.id = 'pitbox-operator-login-banner';
          banner.className = 'connection-banner pitbox-operator-login-banner';
          var main = document.querySelector('.main-content');
          if (main) main.insertBefore(banner, main.firstChild);
          else document.body.insertBefore(banner, document.body.firstChild);
        }
        banner.innerHTML =
          '<span class="pitbox-operator-banner-msg">Operator login required to control sims.</span> ' +
          '<a class="pitbox-operator-banner-signin" href="/employee/login?next=' + nextEnc + '">Sign in</a>';
        banner.classList.remove('hidden');
      })
      .catch(function () { updateToolbarOperatorLock(); });
  }

  function postCommand(path, body) {
    return ensureOperatorOrRedirect().then(function (ok) {
      if (!ok) return new Promise(function () {});
      return pitboxFetch(API_BASE + path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }).then(function (r) {
        if (r.status === 401 || r.status === 403) {
          refreshOperatorLoginBanner();
          return r.json().catch(function () { return {}; }).then(function (d) {
            var detail = (typeof d.detail === 'string') ? d.detail : '';
            var msg = detail || (r.status === 401 ? 'Operator login required.' : 'Request forbidden.');
            var err = new Error(msg);
            if (operatorSessionCache.employee_login_enabled && r.status === 401) {
              err.pitboxOperatorHtml =
                'Operator login required to control this rig. <a class="pitbox-signin-link" href="/employee/login?next=' +
                getLoginNextUrl() +
                '">Sign in</a>.';
            }
            throw err;
          });
        }
        if (!r.ok) throw new Error('Request failed: ' + r.status);
        return r.json();
      });
    });
  }

  /** Run up to concurrency promises at a time; returns Promise<results[]>. Used for batch launch to avoid flooding the API. */
  function runWithConcurrency(ids, fn, concurrency) {
    concurrency = Math.max(1, concurrency || 3);
    var results = new Array(ids.length);
    var index = 0;
    function runNext() {
      if (index >= ids.length) return Promise.resolve();
      var i = index++;
      return Promise.resolve(fn(ids[i])).then(function (r) {
        results[i] = r;
        return runNext();
      });
    }
    var workers = [];
    for (var w = 0; w < Math.min(concurrency, ids.length); w++) workers.push(runNext());
    return Promise.all(workers).then(function () { return results; });
  }

  /** Read current car selection from DOM (dropdown or selected tile) so we send what the user sees. */
  function getCarIdFromCard(card, agentId) {
    if (!card) return null;
    var picker = card.querySelector('.sim-card-car-picker');
    if (!picker) return null;
    var select = picker.querySelector('.car-combobox-select');
    if (select && (select.value || '').trim()) return (select.value || '').trim();
    var selectedTile = picker.querySelector('.car-tile.car-tile-selected');
    if (selectedTile) {
      var cid = selectedTile.getAttribute('data-car-id');
      if (cid && String(cid).trim()) return String(cid).trim();
    }
    return null;
  }

  /** Read launch payload for one sim from DOM (sync). */
  function buildLaunchPayload(id) {
    var card = grid.querySelector('.sim-card[data-agent-id="' + (window.CSS && window.CSS.escape ? window.CSS.escape(id) : id.replace(/"/g, '\\"')) + '"]');
    var ojs = getOnlineJoinState(id);
    var carId = getCarIdFromCard(card, id) || (ojs.selectedCarId && String(ojs.selectedCarId).trim()) || null;
    if (carId) ojs.selectedCarId = carId;
    var driverInput = card ? card.querySelector('.sim-card-driver-input') : null;
    var driverName = (driverInput && (driverInput.value || '').trim()) || (driverNames[id] !== undefined ? String(driverNames[id]).trim() : '') || ('Sim ' + id);
    var presetId = (card && card.querySelector('.preset-steering')) ? (card.querySelector('.preset-steering').value || '').trim() : (steeringPreset[id] || '');
    var shifterMode = (card && card.querySelector('.preset-shifting')) ? (card.querySelector('.preset-shifting').value || '').trim() : (shiftingPreset[id] || '');
    var simDisplay = (ojs.selectedServerId || '').trim() || null;
    var details = ojs.serverDetails || {};
    var timerMinutes = getTimerMinutesFromCard(card, id);
    return {
      driverName: driverName,
      timerMinutes: timerMinutes,
      launchPayload: {
        server_id: ojs.selectedServerId || null,
        server_ip: details.ip || null,
        server_port: details.port != null ? details.port : null,
        car_id: carId,
        preset_id: presetId || null,
        shifter_mode: shifterMode || null,
        sim_display: simDisplay,
        max_running_time_minutes: timerMinutes
      }
    };
  }

  /** Send set-driver-name for one sim. Returns Promise<{ success, error? }>. */
  function setDriverNameFor(id, driverName) {
    return pitboxFetch(API_BASE + '/set-driver-name', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sim_id: id, driver_name: driverName })
    })
      .then(function (r) {
        if (r.ok) return { success: true };
        return r.json().then(function (d) { return { success: false, error: d.detail || d.message || 'Set name failed' }; });
      })
      .catch(function (e) { return { success: false, error: e.message || String(e) }; });
  }

  /** Send launch_online for one sim. Returns Promise<{ success, error?, timerMinutes? }>. */
  function launchOnlineWith(id, payload, timerMinutes) {
    return pitboxFetch(API_BASE + '/agents/' + encodeURIComponent(id) + '/launch_online', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    })
      .then(function (r) {
        return r.json().then(function (data) {
          if (r.ok) {
            sessionStartTimeByAgent[id] = Date.now();
            sessionTimerMinutesByAgent[id] = timerMinutes;
            return { success: true };
          }
          var msg = (data.detail && typeof data.detail === 'object' && data.detail.detail) ? data.detail.detail : (data.detail || data.message || r.statusText);
          if (typeof msg !== 'string') msg = (data.detail && data.detail.error) ? data.detail.error : String(msg);
          return { success: false, error: msg };
        });
      })
      .catch(function (e) { return { success: false, error: e.message || String(e) }; });
  }

  /** Run set-driver-name + launch_online for one sim (used by single-card Launch Session button). */
  function applyAndLaunchOne(id) {
    return ensureOperatorOrRedirect().then(function (ok) {
      if (!ok) return { success: false, error: 'Sign-in required' };
      var p = buildLaunchPayload(id);
      return setDriverNameFor(id, p.driverName)
        .then(function (nameResult) {
          if (!nameResult.success) return { success: false, error: nameResult.error };
          return launchOnlineWith(id, p.launchPayload, p.timerMinutes);
        });
    });
  }

  function launchSelected() {
    ensureOperatorOrRedirect().then(function (ok) {
      if (!ok) return;
      var ids = getSelectedIds().filter(function (id) { return id !== TEST_SIM_AGENT_ID; });
      if (!ids.length) {
        showToast('Select at least one rig.', 'warning');
        return;
      }
      var invalid = [];
      ids.forEach(function (id) {
        var ojs = getOnlineJoinState(id);
        if (!(ojs.selectedServerId || '').trim()) invalid.push(id + ': no server');
        else if (ojs.carsStatus !== 'ready') invalid.push(id + ': cars not loaded');
        else if (!(ojs.selectedCarId || '').trim()) invalid.push(id + ': no car');
      });
      if (invalid.length) {
        showToast('Fix selection: ' + invalid.slice(0, 2).join('; ') + (invalid.length > 2 ? '…' : ''), 'warning');
        return;
      }

      launchBusy = true;
      var launchBtn = document.getElementById('launch-selected');
      if (launchBtn) launchBtn.disabled = true;
      ids.forEach(function (id) { batchState[id] = { action: 'launch', status: 'pending' }; });
      updateBatchStatusEls();
      ids.forEach(function (id) {
        var ojs = getOnlineJoinState(id);
        ojs.launchStatus = 'joining';
        ojs.errorMessage = null;
      });

      // Read all payloads synchronously from DOM before any async work
      var payloads = {};
      ids.forEach(function (id) { payloads[id] = buildLaunchPayload(id); });

      // Phase 1: set driver names for ALL sims simultaneously
      Promise.all(ids.map(function (id) {
        return setDriverNameFor(id, payloads[id].driverName);
      }))
        // Phase 2: fire launch_online for ALL sims simultaneously the instant names are done
        .then(function (nameResults) {
          return Promise.all(ids.map(function (id, i) {
            if (!nameResults[i].success) {
              return Promise.resolve({ success: false, error: nameResults[i].error });
            }
            return launchOnlineWith(id, payloads[id].launchPayload, payloads[id].timerMinutes);
          }));
        })
        .then(function (results) {
          ids.forEach(function (id, i) {
            var r = results[i];
            if (!batchState[id]) batchState[id] = { action: 'launch' };
            batchState[id].status = r && r.success ? 'ok' : 'failed';
            batchState[id].error = (r && r.error) || 'Unknown error';
            var ojs = getOnlineJoinState(id);
            ojs.launchStatus = r && r.success ? 'joined' : 'error';
            if (r && !r.success) ojs.errorMessage = r.error;
          });
          updateBatchStatusEls();
          var failed = ids.filter(function (id) { return !(batchState[id] && batchState[id].status === 'ok'); });
          var ok = ids.length - failed.length;
          if (failed.length) showToast('Launched ' + ok + '; failed: ' + failed.length, 'warning');
          else showToast('Launched ' + ids.length + ' rig(s).', 'success');
          launchBusy = false;
          if (launchBtn) launchBtn.disabled = false;
          scheduleFetchStatus('batch-launch');
          setTimeout(function () {
            ids.forEach(function (id) { delete batchState[id]; });
            updateBatchStatusEls();
          }, 5000);
        })
        .catch(function (err) {
          ids.forEach(function (id) {
            if (batchState[id]) { batchState[id].status = 'failed'; batchState[id].error = err.message || 'Request failed'; }
            var ojs = getOnlineJoinState(id);
            ojs.launchStatus = 'error';
            ojs.errorMessage = err.message;
          });
          updateBatchStatusEls();
          showToast('Launch failed: ' + err.message, 'error');
          launchBusy = false;
          if (launchBtn) launchBtn.disabled = false;
          scheduleFetchStatus('batch-launch-error');
        });
    });
  }

  function exitSelected() {
    ensureOperatorOrRedirect().then(function (ok) {
      if (!ok) return;
    var ids = getSelectedIds();
    if (!ids.length) {
      showToast('Select at least one rig.', 'warning');
      return;
    }
    launchBusy = true;
    var exitBtn = document.getElementById('exit-selected');
    if (exitBtn) exitBtn.disabled = true;
    ids.forEach(function (id) {
      batchState[id] = { action: 'exit', status: 'pending' };
      delete sessionStartTimeByAgent[id];
    });
    updateBatchStatusEls();
    postCommand('/stop', { sim_ids: ids })
      .then(function (data) {
        var results = data.results || {};
        var dispatched = data.dispatched || [];
        ids.forEach(function (id) {
          var r = results[id];
          if (!batchState[id]) batchState[id] = { action: 'exit' };
          batchState[id].status = r && r.success ? 'ok' : 'failed';
          batchState[id].error = (r && (r.error || r.message)) || 'Unknown error';
        });
        updateBatchStatusEls();
        var okCount = dispatched.length ? dispatched.filter(function (d) { return d.ok; }).length : ids.filter(function (id) { return results[id] && results[id].success; }).length;
        var failCount = ids.length - okCount;
        if (failCount) showToast('Dispatched: ' + okCount + ' ok, ' + failCount + ' failed', 'warning');
        else showToast('Dispatched: ' + okCount + ' rig(s). Cards will update as sessions end.', 'success');
        launchBusy = false;
        if (exitBtn) exitBtn.disabled = false;
        scheduleFetchStatus('batch-exit');
        return Promise.resolve();
      })
      .catch(function (err) {
        ids.forEach(function (id) {
          if (batchState[id]) { batchState[id].status = 'failed'; batchState[id].error = err.message || 'Request failed'; }
        });
        updateBatchStatusEls();
        showCommandErrorToast('Exit failed: ', err);
        launchBusy = false;
        if (exitBtn) exitBtn.disabled = false;
        scheduleFetchStatus('batch-exit-error');
        return Promise.resolve();
      })
      .then(function () {
        setTimeout(function () {
          ids.forEach(function (id) { delete batchState[id]; });
          updateBatchStatusEls();
        }, 3000);
      })
      .catch(function () {});
    });
  }

  document.getElementById('select-all').addEventListener('click', function () {
    currentAgents.forEach(function (a) { selectedForLaunch[a.agent_id] = true; });
    grid.querySelectorAll('.sim-card').forEach(function (card) {
      var id = card.getAttribute('data-agent-id');
      card.classList.toggle('sim-card-selected', !!id && selectedForLaunch[id]);
    });
  });
  document.getElementById('select-none').addEventListener('click', function () {
    selectedForLaunch = {};
    grid.querySelectorAll('.sim-card').forEach(function (card) { card.classList.remove('sim-card-selected'); });
  });
  document.getElementById('launch-selected').addEventListener('click', launchSelected);
  document.getElementById('exit-selected').addEventListener('click', exitSelected);
  document.getElementById('refresh').addEventListener('click', function () {
    scheduleFetchStatus('refresh');
    showToast('Refreshed.', 'success');
  });
  var addCmRigForm = document.getElementById('add-cm-rig-form');
  var addCmRigResult = document.getElementById('add-cm-rig-result');
  if (addCmRigForm && addCmRigResult) {
    addCmRigForm.addEventListener('submit', function (e) {
      e.preventDefault();
      var agentId = (document.getElementById('cm-rig-agent-id') && document.getElementById('cm-rig-agent-id').value || '').trim();
      var host = (document.getElementById('cm-rig-host') && document.getElementById('cm-rig-host').value || '').trim();
      var port = parseInt(document.getElementById('cm-rig-port') && document.getElementById('cm-rig-port').value, 10) || 11777;
      var password = (document.getElementById('cm-rig-password') && document.getElementById('cm-rig-password').value || '').trim();
      var displayName = (document.getElementById('cm-rig-display-name') && document.getElementById('cm-rig-display-name').value || '').trim();
      addCmRigResult.classList.add('hidden');
      ensureOperatorOrRedirect().then(function (ok) {
        if (!ok) return;
      pitboxFetch(API_BASE + '/rigs/cm', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ agent_id: agentId, host: host, cm_port: port, cm_password: password || undefined, display_name: displayName || undefined })
      }).then(function (r) {
        return r.json().then(function (data) {
          if (r.ok) {
            addCmRigResult.textContent = 'CM rig added or updated. Refresh to see the card.';
            addCmRigResult.classList.remove('hidden');
            if (typeof showToast === 'function') showToast('CM rig added.', 'success');
            scheduleFetchStatus('cm-rig');
          } else {
            addCmRigResult.textContent = data.detail || data.message || 'Failed';
            addCmRigResult.classList.remove('hidden');
            if (typeof showToast === 'function') showToast(addCmRigResult.textContent, 'error');
          }
        });
      }).catch(function (err) {
        addCmRigResult.textContent = err.message || 'Request failed';
        addCmRigResult.classList.remove('hidden');
        if (typeof showToast === 'function') showToast(addCmRigResult.textContent, 'error');
      });
      });
    });
  }
  var dashboardRefreshBtn = document.getElementById('dashboard-refresh');
  if (dashboardRefreshBtn) {
    dashboardRefreshBtn.addEventListener('click', function () {
      loadDashboardPage();
      if (typeof showToast === 'function') showToast('Garage refreshed.', 'success');
    });
  }
  (function bindCommandCenterDashboard() {
    var sel = document.getElementById('cc-server-select');
    if (sel && !sel.dataset.ccBound) {
      sel.dataset.ccBound = '1';
      sel.addEventListener('change', function () {
        var id = (sel.value || '').trim() || 'default';
        Promise.all([
          getDashboardServerConfigCached(id),
          getDashboardProcessStatusCached()
        ]).then(function (arr) {
          var statusData = window._commandCenterLastStatus || { agents: [], server_ids: [] };
          renderCommandCenterDashboard(statusData, arr[0], arr[1]);
        });
      });
    }
    function ccServerId() { var s = document.getElementById('cc-server-select'); return (s && s.value) ? s.value.trim() : 'default'; }
    function ccPost(path) {
      var id = ccServerId();
      postCommand(path, { server_id: id }).then(function () { loadDashboardPage(); if (typeof showToast === 'function') showToast('Done.', 'success'); }).catch(function (e) { if (typeof showCommandErrorToast === 'function') showCommandErrorToast('', e); });
    }
    var startBtn = document.getElementById('cc-btn-start');
    var restartBtn = document.getElementById('cc-btn-restart');
    var stopBtn = document.getElementById('cc-btn-stop');
    if (startBtn && !startBtn.dataset.ccBound) { startBtn.dataset.ccBound = '1'; startBtn.addEventListener('click', function () { ccPost('/server-config/start'); }); }
    if (restartBtn && !restartBtn.dataset.ccBound) { restartBtn.dataset.ccBound = '1'; restartBtn.addEventListener('click', function () { ccPost('/server-config/restart'); }); }
    if (stopBtn && !stopBtn.dataset.ccBound) { stopBtn.dataset.ccBound = '1'; stopBtn.addEventListener('click', function () { ccPost('/server-config/stop'); }); }
  })();

  /* Delegated handler: Remove sim (unenroll) – deletes rig from controller, card disappears after refresh. */
  grid.addEventListener('click', function (e) {
    var removeBtn = (e.target && e.target.closest) ? e.target.closest('.btn-card-remove') : null;
    if (removeBtn) {
      e.preventDefault();
      e.stopPropagation();
      var id = removeBtn.getAttribute('data-agent-id');
      if (!id) return;
      ensureOperatorOrRedirect().then(function (ok) {
        if (!ok) return;
        if (!confirm('Remove this sim from the controller? It will need to re-enroll to appear again.')) return;
      pitboxFetch(API_BASE + '/agents/' + encodeURIComponent(id), { method: 'DELETE' })
        .then(function (r) {
          return r.json().then(function (data) {
            if (!r.ok) throw new Error(data.detail || data.message || r.statusText);
            return data;
          });
        })
        .then(function () {
          lastSimCardsAgentIds = null;
          scheduleFetchStatus('remove-sim');
          showToast('Sim removed.', 'success');
        })
        .catch(function (err) {
          showToast('Remove failed: ' + (err.message || err), 'error');
        });
      });
      return;
    }
    var btn = (e.target && e.target.closest) ? e.target.closest('.btn-card-reset') : null;
    if (!btn) return;
    e.preventDefault();
    e.stopPropagation();
    ensureOperatorOrRedirect().then(function (ok) {
      if (!ok) return;
    var id = btn.getAttribute('data-agent-id');
    if (id === TEST_SIM_AGENT_ID) { showToast('Test card – no agent connected.', 'warning'); return; }
    var card = btn.closest('.sim-card');
    if (card) {
      var timerInput = card.querySelector('.sim-card-timer-input');
      if (timerInput) timerInput.value = '0';
      delete sessionTimerMinutesByAgent[id];
      delete sessionStartTimeByAgent[id];
    }
    var firstSteering = 'Race';
    var firstShifting = 'H-Pattern';
    var agent = currentAgents.filter(function (a) { return a.agent_id === id; })[0];
    var num = agent ? (currentAgents.indexOf(agent) + 1) : 1;
    var defaultName = (agent && agent.display_name && String(agent.display_name).trim()) ? String(agent.display_name).trim() : ('Sim ' + num);
    pitboxFetch(API_BASE + '/reset-rig', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sim_id: id, steering_preset: firstSteering, shifting_preset: firstShifting, display_name: defaultName })
    })
      .then(function (r) {
        return r.json().then(function (data) {
          if (!r.ok) throw new Error(data.detail || data.message || r.statusText);
          return data;
        });
      })
      .then(function (data) {
        steeringPreset[id] = firstSteering;
        shiftingPreset[id] = firstShifting;
        driverNames[id] = defaultName;
        var defaultServerId = DEFAULT_RESET_SERVER_ID;
        return pitboxFetch(API_BASE + '/assignments/' + encodeURIComponent(id), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ server_id: defaultServerId })
        }).then(function (r) { if (!r.ok) throw new Error('Assignment failed'); return r; }).catch(function () {
          return Promise.resolve({ skipDefaultServer: true });
        }).then(function (assignResp) {
          if (assignResp && assignResp.skipDefaultServer) return data;
          var ojs = getOnlineJoinState(id);
          ojs.selectedServerId = defaultServerId;
          ojs.carsStatus = 'loading';
          ojs.trackId = null;
          ojs.trackLayout = null;
          ojs.selectedCarId = null;
          ojs.carOptions = [];
          ojs.serverDetails = null;
          return getPresetDiskStateCached(defaultServerId)
            .then(function (diskData) {
              ojs.carsStatus = 'ready';
              var rawCars = Array.isArray(diskData.cars) ? diskData.cars : [];
              var counts = carCountsFromEntryList(diskData.entry_list && diskData.entry_list.cars);
              ojs.carOptions = rawCars.map(function (model) {
                var m = (model && String(model).trim()) || '';
                return { id: m, displayName: m, thumbnailUrl: '', isTaken: null, countInServer: counts[m] || 0 };
              });
              ojs.selectedCarId = resolveCarIdAfterLoad(rawCars, ojs.selectedCarId, counts, defaultServerId);
              var track = diskData.track || {};
              ojs.trackId = (track.track_id || '').trim() || null;
              ojs.trackLayout = (track.layout_id || '').trim() || null;
              var join = diskData.server_join || {};
              ojs.serverDetails = { ip: join.host || null, port: join.port != null ? join.port : null };
              var card = grid.querySelector('.sim-card[data-agent-id="' + (window.CSS && window.CSS.escape ? window.CSS.escape(id) : id.replace(/"/g, '\\"')) + '"]');
              if (card) {
                var serverSelect = card.querySelector('.preset-server');
                if (serverSelect) serverSelect.value = defaultServerId;
                var pickerEl = card.querySelector('.sim-card-car-picker');
                var agentObj = currentAgents.filter(function (a) { return a.agent_id === id; })[0];
                var disabled = !agentObj || !agentObj.online || ojs.launchStatus === 'joining' || operatorControlBlocked();
                if (pickerEl) {
                  pickerEl.innerHTML = getCarPickerContent(id, ojs, disabled);
                  attachCarPickerListeners(card, pickerEl, id);
                }
                updateSimCardSelectionImages(id);
              }
              return data;
            })
            .catch(function () { return data; });
        }).then(function (resolvedData) {
          lastSimCardsAgentIds = null;
          scheduleFetchStatus('reset-rig');
          showToast(resolvedData.requires_restart ? 'Applied; will take effect next launch.' : 'Rig reset applied.', 'success');
        });
      })
      .catch(function (e) {
        showToast('Reset failed: ' + (e.message || (e && e.detail) || e), 'error');
      });
    });
  });

  /* Delegated change: one .preset-steering and one .preset-shifting per sim card; route to apply_steering / apply_shifting */
  grid.addEventListener('change', function (e) {
    var sel = e.target;
    if (!sel || !sel.getAttribute || !sel.getAttribute('data-agent-id')) return;
    var id = sel.getAttribute('data-agent-id');
    var value = (sel.value || '').trim();
    if (!value) return;
    if (sel.classList.contains('preset-steering')) {
      var prev = steeringPreset[id];
      if (prev === value) return;
      steeringPreset[id] = value;
      pitboxFetch(API_BASE + '/apply-steering', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sim_id: id, preset_name: value })
      })
        .then(function (r) { return r.json().then(function (data) { if (!r.ok) throw new Error(data.detail || data.message || r.statusText); return data; }); })
        .then(function (data) {
          var msg = data.message || 'Steering applied';
          if (data.requires_restart) msg += ' Will take effect next launch.';
          showToast(msg, 'success');
        })
        .catch(function (err) { showToast('Steering failed: ' + (err.message || err), 'error'); });
      return;
    }
    if (sel.classList.contains('preset-shifting')) {
      var prevShift = shiftingPreset[id];
      if (prevShift === value) return;
      shiftingPreset[id] = value;
      pitboxFetch(API_BASE + '/apply-shifting', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sim_id: id, preset_name: value })
      })
        .then(function (r) { return r.json().then(function (data) { if (!r.ok) throw new Error(data.detail || data.message || r.statusText); return data; }); })
        .then(function (data) {
          var msg = data.message || 'Shifting applied';
          if (data.requires_restart) msg += ' Will take effect next launch.';
          showToast(msg, 'success');
        })
        .catch(function (err) { showToast('Shifting failed: ' + (err.message || err), 'error'); });
    }
  });

  refreshOperatorLoginBanner().then(function () {
    statusPollInFlight = true;
    return Promise.resolve(fetchStatus())
      .catch(function () {})
      .finally(function () {
        statusPollInFlight = false;
        startStatusPolling();
      });
  });
  checkForUpdates();
  setInterval(checkForUpdates, 600000);
  setInterval(updateLastUpdateText, 2000);
  document.addEventListener('visibilitychange', function () {
    stopStatusPolling();
    startStatusPolling();
  });
})();
