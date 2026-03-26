/* Native Bookings admin UI (PitBox) — plain HTML/JS/CSS.
 * Source: Fastest-Lap-Hub booking admin API (localhost service)
 */
(function () {
  'use strict';

  var BOOKING_API = '/api';

  var $ = function (id) { return document.getElementById(id); };
  var $$ = function (sel, root) { return (root || document).querySelectorAll(sel); };

  function escapeHtml(s) {
    if (s == null) return '';
    return String(s).replace(/[&<>"']/g, function (ch) {
      return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch];
    });
  }

  function parseMoney(s) {
    var n = parseFloat(s);
    if (isNaN(n)) return 0;
    return n;
  }

  function centsToDollars(cents) {
    var c = parseInt(String(cents || 0), 10);
    if (isNaN(c)) c = 0;
    return (c / 100).toFixed(2);
  }

  function todayISO() {
    var d = new Date();
    return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
  }

  function setBusy(el, isBusy) {
    if (!el) return;
    el.disabled = !!isBusy;
    el.setAttribute('aria-busy', isBusy ? 'true' : 'false');
    if (isBusy) el.dataset._baText = el.dataset._baText || el.textContent;
    if (isBusy) el.textContent = 'Loading…';
    if (!isBusy && el.dataset._baText) el.textContent = el.dataset._baText;
  }

  async function api(path, options) {
    var res = await fetch(BOOKING_API + path, options || {});
    if (!res.ok) {
      var text = '';
      try {
        var data = await res.json();
        text = data && (data.message || data.error || data.detail) ? (data.message || data.error || data.detail) : '';
      } catch (e) {}
      if (!text) text = 'Request failed (' + res.status + ')';
      throw new Error(text);
    }
    if (res.status === 204) return null;
    return res.json();
  }

  function statusBadge(status) {
    var s = (status || '').toLowerCase();
    if (s === 'pending_deposit' || s === 'pending') return { cls: 'ba-badge--red', label: 'Pending Deposit' };
    if (s === 'confirmed' || s === 'checked_in') return { cls: 'ba-badge--green', label: s === 'checked_in' ? 'Checked In' : 'Confirmed' };
    if (s === 'completed' || s === 'cancelled' || s === 'no_show') return { cls: 'ba-badge--gray', label: s === 'no_show' ? 'No Show' : (s.charAt(0).toUpperCase() + s.slice(1).replace(/_/g, ' ')) };
    return { cls: 'ba-badge--gray', label: s ? s.replace(/_/g, ' ') : '—' };
  }

  function depositBadge(booking) {
    // FLH admin booking includes paymentStatus + depositAmount.
    var paymentStatus = (booking && booking.paymentStatus) ? String(booking.paymentStatus).toLowerCase() : '';
    var depositAmount = parseMoney(booking && booking.depositAmount);
    if (depositAmount <= 0) return { cls: 'ba-badge--gray', label: 'Deposit N/A' };
    if (paymentStatus === 'deposit_paid') return { cls: 'ba-badge--green', label: 'Deposit Paid' };
    if (paymentStatus === 'refunded') return { cls: 'ba-badge--gray', label: 'Deposit Refunded' };
    if (booking.status && String(booking.status).toLowerCase() === 'pending_deposit') return { cls: 'ba-badge--red', label: 'Deposit Due' };
    return { cls: 'ba-badge--red', label: paymentStatus ? paymentStatus.replace(/_/g, ' ') : 'Deposit Due' };
  }

  var baState = {
    bookings: [],
    bookingsById: {},
    currentDetail: null,
    detailSelectedSims: [],
    cachePeriod: '30d',
    _bound: false,
  };

  function showError(el, message) {
    if (!el) return;
    if (!message) {
      el.textContent = '';
      el.classList.remove('show');
      return;
    }
    el.textContent = message;
    el.classList.add('show');
  }

  function hideError(el) {
    if (!el) return;
    el.textContent = '';
    el.classList.remove('show');
  }

  function openOverlay(overlayEl) {
    if (!overlayEl) return;
    overlayEl.classList.add('show');
    overlayEl.setAttribute('aria-hidden', 'false');
  }

  function closeOverlay(overlayEl) {
    if (!overlayEl) return;
    overlayEl.classList.remove('show');
    overlayEl.setAttribute('aria-hidden', 'true');
  }

  function bindSharedEventsOnce() {
    if (baState._bound) return;
    baState._bound = true;

    // Walk-in modal
    var walkinClose = $('ba-walkin-close');
    var walkinCancel = $('ba-walkin-cancel');
    if (walkinClose) {
      walkinClose.addEventListener('click', function () {
        closeOverlay($('ba-walkin-modal'));
      });
    }
    if (walkinCancel) {
      walkinCancel.addEventListener('click', function () {
        closeOverlay($('ba-walkin-modal'));
      });
    }
    var walkinOverlay = $('ba-walkin-modal');
    if (walkinOverlay) {
      walkinOverlay.addEventListener('click', function (e) {
        if (e.target === walkinOverlay) closeOverlay(walkinOverlay);
      });
    }

    // Detail modal
    var detailClose = $('ba-detail-close');
    if (detailClose) {
      detailClose.addEventListener('click', function () {
        closeOverlay($('ba-detail-modal'));
      });
    }
    var detailOverlay = $('ba-detail-modal');
    if (detailOverlay) {
      detailOverlay.addEventListener('click', function (e) {
        if (e.target === detailOverlay) closeOverlay(detailOverlay);
      });
    }

    // Action buttons (event delegation). We bind to a root that exists at load time
    // so dynamically-rendered modal buttons still work.
    var detailRoot = $('ba-detail-modal') || $('ba-detail-body') || document;
    if (detailRoot && detailRoot.addEventListener) {
      detailRoot.addEventListener('click', async function (e) {
        var btn = e.target && e.target.closest ? e.target.closest('[data-ba-action]') : null;
        if (!btn) return;
        var action = btn.getAttribute('data-ba-action');
        if (!baState.currentDetail) return;
        var id = baState.currentDetail.id;
        var errEl = $('ba-detail-error');
        showError(errEl, '');

        try {
          if (action === 'mark-deposit-paid') {
            setBusy(btn, true);
            await api('/admin/bookings/' + encodeURIComponent(id) + '/mark-deposit-paid', { method: 'POST' });
            await refreshAfterBookingMutation();
          } else if (action === 'checkin') {
            setBusy(btn, true);
            await api('/admin/checkin/' + encodeURIComponent(id), { method: 'POST' });
            await refreshAfterBookingMutation();
          } else if (action === 'cancel') {
            setBusy(btn, true);
            var payload = { status: 'cancelled' };
            await api('/admin/bookings/' + encodeURIComponent(id), { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
            await refreshAfterBookingMutation();
          } else if (action === 'resend-email') {
            setBusy(btn, true);
            var out = await api('/admin/bookings/' + encodeURIComponent(id) + '/notifications/resend-email', { method: 'POST' });
            showError(errEl, out && out.message ? out.message : 'Email resent.');
          } else if (action === 'assign-simulators') {
            var required = Number(baState.currentDetail.numberOfRacers || 0);
            if (baState.detailSelectedSims.length !== required) {
              showError(errEl, 'Select exactly ' + required + ' simulator(s).');
              return;
            }
            setBusy(btn, true);
            await api('/admin/bookings/' + encodeURIComponent(id) + '/simulators', { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ simulatorNumbers: baState.detailSelectedSims }) });
            await refreshAfterBookingMutation();
          } else if (action === 'reschedule') {
            var timeEl = $('ba-detail-time');
            var durEl = $('ba-detail-duration-minutes');
            var timeVal = timeEl ? timeEl.value : '';
            var durVal = durEl ? Number(durEl.value) : NaN;
            if (!timeVal || isNaN(durVal)) {
              showError(errEl, 'Time and duration are required.');
              return;
            }
            setBusy(btn, true);
            var req = { time: timeVal, durationMinutes: durVal };
            if (baState.detailSelectedSims && baState.detailSelectedSims.length === Number(baState.currentDetail.numberOfRacers || 0)) {
              req.simulatorNumbers = baState.detailSelectedSims;
            }
            await api('/admin/bookings/' + encodeURIComponent(id) + '/reschedule', { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(req) });
            await refreshAfterBookingMutation();
          }
        } catch (ex) {
          showError(errEl, ex && ex.message ? ex.message : String(ex));
        } finally {
          setBusy(btn, false);
        }
      });
    }
  }

  function getVisiblePageId() {
    var el = document.querySelector('.content-page:not(.hidden)');
    if (!el) return null;
    return el.getAttribute('data-page');
  }

  async function refreshAfterBookingMutation() {
    // Re-fetch the visible booking-related page to keep UI consistent.
    var pageId = getVisiblePageId();
    if (pageId === 'bookings' && window.loadBookingsListPage) {
      await window.loadBookingsListPage();
    } else if (pageId === 'checkin' && window.loadCheckinPage) {
      await window.loadCheckinPage();
    } else if (pageId === 'schedule' && window.loadSchedulePage) {
      await window.loadSchedulePage();
    } else if (pageId === 'analytics' && window.loadAnalyticsPage) {
      await window.loadAnalyticsPage();
    } else {
      // fallback: update both list and detail
      await loadBookingsForState();
      if (baState.currentDetail) await openBookingDetail(baState.currentDetail.id, { keepOpen: true });
    }
  }

  async function loadBookingsForState(dateISO, status) {
    var qs = [];
    if (dateISO) qs.push('date=' + encodeURIComponent(dateISO));
    if (status) qs.push('status=' + encodeURIComponent(status));
    var url = '/admin/bookings' + (qs.length ? ('?' + qs.join('&')) : '');
    var data = await api(url, { method: 'GET' });
    if (!Array.isArray(data)) data = [];
    baState.bookings = data;
    baState.bookingsById = {};
    data.forEach(function (b) { if (b && b.id != null) baState.bookingsById[String(b.id)] = b; });
    return data;
  }

  function renderBookingsTable(bookings) {
    var tbody = $('ba-bookings-tbody');
    var errEl = $('ba-bookings-error');
    hideError(errEl);

    if (!tbody) return;
    if (!bookings || bookings.length === 0) {
      tbody.innerHTML = '<tr><td colspan="8"><div class="ba-loading">No bookings found.</div></td></tr>';
      return;
    }

    tbody.innerHTML = bookings.map(function (b) {
      var id = String(b.id);
      var status = statusBadge(b.status);
      var dep = depositBadge(b);
      var sims = Array.isArray(b.simulatorNumbers) ? b.simulatorNumbers.join(', ') : '';

      return (
        '<tr class="ba-row-click" data-ba-booking-id="' + escapeHtml(id) + '">' +
          '<td>' + escapeHtml(b.confirmationNumber || '') + '</td>' +
          '<td>' + escapeHtml(b.customerName || '') + '</td>' +
          '<td>' + escapeHtml(b.date || '') + '</td>' +
          '<td>' + escapeHtml(b.time || '') + '</td>' +
          '<td>' + escapeHtml(b.durationMinutes != null ? String(b.durationMinutes) + ' min' : '') + '</td>' +
          '<td>' + escapeHtml(b.numberOfRacers != null ? String(b.numberOfRacers) : '') + '</td>' +
          '<td><span class="ba-badge ' + status.cls + '">' + escapeHtml(status.label) + '</span></td>' +
          '<td><span class="ba-badge ' + dep.cls + '">' + escapeHtml(dep.label) + '</span></td>' +
        '</tr>'
      );
    }).join('');
  }

  async function openBookingDetail(bookingId, opts) {
    opts = opts || {};
    bindSharedEventsOnce();

    var modal = $('ba-detail-modal');
    var body = $('ba-detail-body');
    var errEl = $('ba-detail-error');

    if (!modal || !body) return;
    showError(errEl, '');

    // Load from cache or fetch by date (best-effort).
    var cached = baState.bookingsById[String(bookingId)];
    var booking = cached || null;

    // Ensure we have full booking info for actions (notes/payment/waiver/amounts).
    if (!booking || booking.notes == null || booking.paymentStatus == null) {
      // Try loading bookings for a known date hint (schedule provides date at page-level).
      // Otherwise fall back to cached booking.date or today.
      var dateHint = opts.dateHint || (booking && booking.date ? booking.date : todayISO());
      try {
        var data = await loadBookingsForState(dateHint, null);
        booking = (data || []).find(function (x) { return String(x.id) === String(bookingId); }) || booking;
      } catch (e) {
        // keep partial booking if any
      }
    }

    if (!booking) {
      showError(errEl, 'Booking not found.');
      return;
    }

    baState.currentDetail = booking;
    baState.detailSelectedSims = Array.isArray(booking.simulatorNumbers) ? booking.simulatorNumbers.slice() : [];

    // Build detail HTML
    var status = statusBadge(booking.status);
    var dep = depositBadge(booking);
    var simsHtml = (Array.isArray(booking.simulatorNumbers) && booking.simulatorNumbers.length)
      ? '<div class="ba-sim-badges">' + booking.simulatorNumbers.map(function (n) {
          return '<span class="ba-sim-badge">' + escapeHtml(n) + '</span>';
        }).join('') + '</div>'
      : '<span class="ba-badge ba-badge--gray">None</span>';

    var notes = booking.notes != null && booking.notes !== '' ? booking.notes : '—';
    var durationLabel = booking.durationMinutes != null ? (String(booking.durationMinutes) + ' min') : '—';
    var paymentStatus = booking.paymentStatus ? String(booking.paymentStatus).replace(/_/g, ' ') : '—';
    var depositPaidAt = booking.depositPaidAt ? new Date(booking.depositPaidAt).toLocaleString() : '—';
    var remaining = booking.remainingBalance != null ? booking.remainingBalance : '—';

    body.innerHTML =
      '<div class="ba-detail-grid">' +
        '<div class="ba-detail-item">' +
          '<div class="ba-k">Confirmation #</div>' +
          '<div class="ba-v">' + escapeHtml(booking.confirmationNumber || '') + '</div>' +
        '</div>' +
        '<div class="ba-detail-item">' +
          '<div class="ba-k">Status</div>' +
          '<div class="ba-v"><span class="ba-badge ' + status.cls + '">' + escapeHtml(status.label) + '</span></div>' +
        '</div>' +
        '<div class="ba-detail-item">' +
          '<div class="ba-k">Customer</div>' +
          '<div class="ba-v">' + escapeHtml(booking.customerName || '') + '</div>' +
        '</div>' +
        '<div class="ba-detail-item">' +
          '<div class="ba-k">Contact</div>' +
          '<div class="ba-v">' + escapeHtml(booking.customerPhone || '') + (booking.customerEmail ? (' · ' + escapeHtml(booking.customerEmail)) : '') + '</div>' +
        '</div>' +
        '<div class="ba-detail-item">' +
          '<div class="ba-k">When</div>' +
          '<div class="ba-v">' + escapeHtml(booking.date || '') + ' · ' + escapeHtml(booking.time || '') + '</div>' +
        '</div>' +
        '<div class="ba-detail-item">' +
          '<div class="ba-k">Duration</div>' +
          '<div class="ba-v">' + escapeHtml(durationLabel) + '</div>' +
        '</div>' +
        '<div class="ba-detail-item">' +
          '<div class="ba-k">Racers</div>' +
          '<div class="ba-v">' + escapeHtml(booking.numberOfRacers != null ? String(booking.numberOfRacers) : '') + '</div>' +
        '</div>' +
        '<div class="ba-detail-item">' +
          '<div class="ba-k">Deposit</div>' +
          '<div class="ba-v"><span class="ba-badge ' + dep.cls + '">' + escapeHtml(dep.label) + '</span><div style="margin-top:8px;color:#888;font-size:0.8rem;font-weight:700;">Payment: ' + escapeHtml(paymentStatus) + '</div></div>' +
        '</div>' +
        '<div class="ba-detail-item" style="grid-column: span 2;">' +
          '<div class="ba-k">Simulators</div>' +
          '<div class="ba-v">' + simsHtml + '</div>' +
        '</div>' +
        '<div class="ba-detail-item" style="grid-column: span 2;">' +
          '<div class="ba-k">Notes</div>' +
          '<div class="ba-v" style="font-weight:600;color:#fff;">' + escapeHtml(notes) + '</div>' +
        '</div>' +
      '</div>';

    // Action sections
    body.innerHTML +=
      '<div class="ba-section">' +
        '<div class="ba-section-title">Actions</div>' +
        '<div class="ba-btn-row" id="ba-detail-actions">' +
          (booking.status === 'pending_deposit' ? '<button type="button" class="btn-primary" data-ba-action="mark-deposit-paid">Mark Deposit Paid</button>' : '') +
          '<button type="button" class="btn-secondary" data-ba-action="assign-simulators">Assign Simulators</button>' +
          '<button type="button" class="btn-secondary" data-ba-action="reschedule">Reschedule</button>' +
          '<button type="button" class="btn-primary" data-ba-action="checkin">Check In</button>' +
          '<button type="button" class="btn-secondary" data-ba-action="cancel">Cancel</button>' +
          '<button type="button" class="btn-secondary" data-ba-action="resend-email">Resend Email</button>' +
        '</div>' +
      '</div>';

    // Simulator picker section (used by Assign + Reschedule)
    body.innerHTML +=
      '<div class="ba-section" id="ba-sim-assign-section">' +
        '<div class="ba-section-title">Simulator Assignment</div>' +
        '<div style="color:#888;font-weight:700;font-size:0.85rem;margin-bottom:10px;">Select exactly ' + escapeHtml(String(booking.numberOfRacers || 0)) + ' simulator(s).</div>' +
        '<div class="ba-sim-picker" id="ba-sim-picker" aria-label="Select simulators"></div>' +
      '</div>';

    // Reschedule section
    body.innerHTML +=
      '<div class="ba-section">' +
        '<div class="ba-section-title">Reschedule</div>' +
        '<div class="ba-toolbar" style="margin:0 0 12px;">' +
          '<div class="ba-field">' +
            '<div class="ba-label">Time</div>' +
            '<input type="time" id="ba-detail-time" class="ba-input" value="' + escapeHtml(booking.time || '') + '">' +
          '</div>' +
          '<div class="ba-field">' +
            '<div class="ba-label">Duration (minutes)</div>' +
            '<input type="number" id="ba-detail-duration-minutes" class="ba-input" min="1" value="' + escapeHtml(booking.durationMinutes != null ? String(booking.durationMinutes) : '60') + '">' +
          '</div>' +
        '</div>' +
        '<div style="color:#888;font-size:0.85rem;">Uses simulator assignment above (when it matches the racer count).</div>' +
      '</div>';

    // Build simulator buttons
    var picker = $('ba-sim-picker');
    if (picker) {
      picker.innerHTML = '';
      var required = Number(booking.numberOfRacers || 0);
      for (var i = 1; i <= 8; i++) {
        (function (sim) {
          var btn = document.createElement('button');
          btn.type = 'button';
          btn.className = 'ba-sim-btn' + (baState.detailSelectedSims.indexOf(sim) >= 0 ? ' active' : '');
          btn.textContent = sim;
          btn.addEventListener('click', function () {
            var idx = baState.detailSelectedSims.indexOf(sim);
            if (idx >= 0) baState.detailSelectedSims.splice(idx, 1);
            else {
              // allow temporary over-select; validation happens on action
              if (baState.detailSelectedSims.length < required) baState.detailSelectedSims.push(sim);
              else {
                // If full, toggle replace behavior by first removing earliest selection
                baState.detailSelectedSims.shift();
                baState.detailSelectedSims.push(sim);
              }
            }
            // Update active classes
            $$('button.ba-sim-btn', picker).forEach(function (b) {
              var n = Number(b.textContent);
              b.classList.toggle('active', baState.detailSelectedSims.indexOf(n) >= 0);
            });
          });
          picker.appendChild(btn);
        })(i);
      }
    }

    openOverlay(modal);

    // When we open for the first time, also ensure action panel exists.
    var actions = $('ba-detail-actions');
    if (!actions) actions = document.getElementById('ba-detail-actions');
  }

  function closeBookingDetail() {
    closeOverlay($('ba-detail-modal'));
    baState.currentDetail = null;
    baState.detailSelectedSims = [];
  }

  async function loadBookingsListPage() {
    bindSharedEventsOnce();

    var pageEl = $('page-bookings');
    if (!pageEl) return;

    var tbody = $('ba-bookings-tbody');
    if (tbody) tbody.innerHTML = '<tr><td colspan="8"><div class="ba-loading">Loading…</div></td></tr>';

    var statusSel = $('ba-filter-status');
    var dateInput = $('ba-filter-date');
    var refreshBtn = $('ba-bookings-refresh');
    var errEl = $('ba-bookings-error');

    var statusVal = statusSel ? statusSel.value : '';
    var dateVal = dateInput ? dateInput.value : '';
    if (!dateVal) dateVal = todayISO();
    if (dateInput && !dateInput.value) dateInput.value = dateVal;

    hideError(errEl);
    if (refreshBtn) setBusy(refreshBtn, true);

    try {
      var data = await loadBookingsForState(dateVal, statusVal || null);
      renderBookingsTable(data);

      // Bind row clicks
      $$('tr.ba-row-click', $('ba-bookings-table')).forEach(function (tr) {
        tr.addEventListener('click', function () {
          var id = tr.getAttribute('data-ba-booking-id');
          openBookingDetail(id, {});
        });
      });
    } catch (ex) {
      showError(errEl, ex && ex.message ? ex.message : String(ex));
    } finally {
      if (refreshBtn) setBusy(refreshBtn, false);
    }
  }

  function bindBookingsPageEventsOnce() {
    if (window._baBookingsEventsBound) return;
    window._baBookingsEventsBound = true;

    var walkinOpen = $('ba-walkin-open');
    var walkinModal = $('ba-walkin-modal');
    if (walkinOpen && walkinModal) {
      walkinOpen.addEventListener('click', function () {
        // default fields
        var dateInput = $('ba-walkin-date');
        var timeInput = $('ba-walkin-time');
        var durInput = $('ba-walkin-duration');
        var racersInput = $('ba-walkin-racers');
        var nameInput = $('ba-walkin-customer-name');
        if (dateInput && !dateInput.value) dateInput.value = todayISO();
        if (timeInput && !timeInput.value) timeInput.value = '14:00';
        openOverlay(walkinModal);
      });
    }

    var walkinForm = $('ba-walkin-form');
    if (walkinForm) {
      walkinForm.addEventListener('submit', async function (e) {
        e.preventDefault();
        var errEl = $('ba-walkin-error');

        var dateVal = ($('ba-walkin-date') || {}).value;
        var timeVal = ($('ba-walkin-time') || {}).value;
        var durVal = Number(($('ba-walkin-duration') || {}).value);
        var racersVal = Number(($('ba-walkin-racers') || {}).value);
        var nameVal = ($('ba-walkin-customer-name') || {}).value;

        hideError(errEl);
        var submitBtn = $('ba-walkin-submit');
        setBusy(submitBtn, true);
        try {
          await api('/admin/bookings/walk-in', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              date: dateVal,
              time: timeVal,
              durationMinutes: durVal,
              numberOfRacers: racersVal,
              customerName: nameVal
            })
          });
          closeOverlay($('ba-walkin-modal'));
          await loadBookingsListPage();
        } catch (ex) {
          showError(errEl, ex && ex.message ? ex.message : String(ex));
        } finally {
          setBusy(submitBtn, false);
        }
      });
    }

    var refreshBtn = $('ba-bookings-refresh');
    if (refreshBtn) {
      refreshBtn.addEventListener('click', function () {
        loadBookingsListPage();
      });
    }

    var statusSel = $('ba-filter-status');
    var dateInput = $('ba-filter-date');
    if (statusSel) statusSel.addEventListener('change', function () { loadBookingsListPage(); });
    if (dateInput) dateInput.addEventListener('change', function () { loadBookingsListPage(); });
  }

  function renderCheckinCards(bookings) {
    var container = $('ba-checkin-list');
    var errEl = $('ba-checkin-error');
    hideError(errEl);
    if (!container) return;
    if (!bookings || bookings.length === 0) {
      container.innerHTML = '<div class="ba-loading">No bookings for today.</div>';
      return;
    }

    // sort by time string lexicographically (HH:MM)
    var sorted = bookings.slice().sort(function (a, b) {
      return String(a.time || '').localeCompare(String(b.time || ''));
    });

    container.innerHTML = sorted.map(function (b) {
      var status = statusBadge(b.status);
      var sims = Array.isArray(b.simulatorNumbers) && b.simulatorNumbers.length ? b.simulatorNumbers.join(', ') : '—';
      return (
        '<div class="ba-checkin-card">' +
          '<div class="ba-checkin-card-top">' +
            '<div>' +
              '<div class="ba-checkin-name">' + escapeHtml(b.customerName || '') + '</div>' +
              '<div class="ba-checkin-meta">' + escapeHtml(b.time || '') + ' · ' + escapeHtml(String(b.numberOfRacers || 0)) + ' racer(s)' + '</div>' +
              '<div class="ba-checkin-meta">Sims: ' + escapeHtml(sims) + '</div>' +
            '</div>' +
            '<div>' +
              '<span class="ba-badge ' + status.cls + '">' + escapeHtml(status.label) + '</span>' +
            '</div>' +
          '</div>' +
          '<div class="ba-checkin-actions">' +
            '<button type="button" class="btn-primary" data-ba-action="checkin-one" data-ba-booking-id="' + escapeHtml(String(b.id)) + '">CHECK IN</button>' +
          '</div>' +
        '</div>'
      );
    }).join('');

    // Bind buttons
    $$('button[data-ba-action="checkin-one"]', container).forEach(function (btn) {
      btn.addEventListener('click', async function () {
        var bookingId = btn.getAttribute('data-ba-booking-id');
        if (!bookingId) return;
        btn.disabled = true;
        try {
          await api('/admin/checkin/' + encodeURIComponent(bookingId), { method: 'POST' });
          await loadCheckinPage();
        } catch (ex) {
          showError($('ba-checkin-error'), ex && ex.message ? ex.message : String(ex));
        } finally {
          btn.disabled = false;
        }
      });
    });
  }

  async function loadCheckinPage() {
    bindSharedEventsOnce();
    var errEl = $('ba-checkin-error');
    var container = $('ba-checkin-list');
    if (container) container.innerHTML = '<div class="ba-loading">Loading…</div>';
    hideError(errEl);

    try {
      var data = await api('/admin/checkin', { method: 'GET' });
      if (!Array.isArray(data)) data = [];
      renderCheckinCards(data);
    } catch (ex) {
      showError(errEl, ex && ex.message ? ex.message : String(ex));
    }
  }

  function statusBlockClass(status) {
    var s = (status || '').toLowerCase();
    if (s === 'pending_deposit' || s === 'pending') return '';
    if (s === 'confirmed' || s === 'checked_in') return 'ba-schedule-block--green';
    return 'ba-schedule-block--gray';
  }

  async function loadSchedulePage() {
    bindSharedEventsOnce();
    var errEl = $('ba-schedule-error');
    hideError(errEl);

    var dateInput = $('ba-schedule-date');
    var dateVal = dateInput ? dateInput.value : '';
    if (!dateVal) dateVal = todayISO();
    if (dateInput && !dateInput.value) dateInput.value = dateVal;
    var prevBtn = $('ba-schedule-prev');
    var nextBtn = $('ba-schedule-next');

    var container = $('ba-schedule-container');
    if (container) container.innerHTML = '<div class="ba-loading">Loading…</div>';
    if (prevBtn) prevBtn.disabled = true;
    if (nextBtn) nextBtn.disabled = true;

    try {
      var data = await api('/admin/schedule?date=' + encodeURIComponent(dateVal), { method: 'GET' });
      if (!data) data = {};
      if (data.isClosed) {
        container.innerHTML =
          '<div style="padding:40px;text-align:center;color:#888;">' +
          '<div style="font-size:1.5rem;font-weight:900;color:#fff;margin-bottom:10px;">Closed</div>' +
          '<div>' + escapeHtml(data.closedReason || 'Closed.') + '</div>' +
          '</div>';
        return;
      }

      var timeSlots = Array.isArray(data.timeSlots) ? data.timeSlots : [];
      var displaySlots = Array.isArray(data.displayTimeSlots) ? data.displayTimeSlots : timeSlots.map(function (_, i) {
        return i;
      });
      var bookings = Array.isArray(data.bookings) ? data.bookings : [];
      var totalSimulators = data.totalSimulators != null ? Number(data.totalSimulators) : 8;

      // Build per simulator blocks
      var slotCount = timeSlots.length;
      var SLOT_W = 86;
      var ROW_H = 66;
      var LABEL_W = 72;
      var gridW = LABEL_W + slotCount * SLOT_W;

      // Header
      var html = '';
      html += '<div class="ba-schedule-scroll">';
      html += '<div style="min-width:' + gridW + 'px;">';
      html += '<div class="ba-schedule-header" style="grid-template-columns:' + LABEL_W + 'px repeat(' + slotCount + ',' + SLOT_W + 'px);">'; // includes label column
      html += '<div class="ba-schedule-head-cell" style="text-align:center;color:#fff;border-right:1px solid rgba(255,255,255,0.06);">SIM</div>';
      for (var i = 0; i < slotCount; i++) {
        html += '<div class="ba-schedule-head-cell">' + escapeHtml(displaySlots[i] || '') + '</div>';
      }
      html += '</div>'; // header

      // Rows
      for (var sim = 1; sim <= totalSimulators; sim++) {
        html += '<div class="ba-schedule-row" style="grid-template-columns:' + LABEL_W + 'px repeat(' + slotCount + ',' + SLOT_W + 'px);height:' + ROW_H + 'px;">';
        html += '<div class="ba-schedule-row-label">Sim ' + sim + '</div>';

        // timeline container spans all slot columns
        html += '<div class="ba-sim-timeline" style="grid-column:2 / span ' + slotCount + ';width:' + (slotCount * SLOT_W) + 'px;height:' + ROW_H + 'px;">';

        // slot grid (for subtle dividers)
        html += '<div style="position:absolute;inset:0;display:flex;">';
        for (var s = 0; s < slotCount; s++) {
          html += '<div class="ba-slot-cell" style="width:' + SLOT_W + 'px;height:' + ROW_H + 'px;"></div>';
        }
        html += '</div>';

        // blocks for bookings that include this sim
        bookings.forEach(function (b) {
          var sims = Array.isArray(b.simulatorNumbers) ? b.simulatorNumbers : [];
          if (sims.indexOf(sim) < 0) return;
          var startIdx = Number(b.startSlotIndex != null ? b.startSlotIndex : 0);
          var span = Number(b.durationSlots != null ? b.durationSlots : 1);
          var left = startIdx * SLOT_W + 2;
          var width = span * SLOT_W - 8;
          var cls = 'ba-schedule-block ' + statusBlockClass(b.status);
          var name = b.customerName || '';
          var dur = b.durationDisplay || (b.durationMinutes != null ? String(b.durationMinutes) + ' min' : '');
          var simsText = (Array.isArray(b.simulatorNumbers) && b.simulatorNumbers.length) ? ('Sims ' + b.simulatorNumbers.join(', ')) : '';
          var bId = String(b.id);

          html += '<div class="' + cls + '" style="left:' + left + 'px;width:' + width + 'px;" title="' + escapeHtml(name) + '">' +
                    '<div class="ba-block-name">' + escapeHtml(name) + '</div>' +
                    '<div class="ba-block-sub">' + escapeHtml(dur) + (simsText ? (' · ' + escapeHtml(simsText)) : '') + '</div>' +
                  '</div>';
        });

        // data attribute binding for clicks: use click delegation later
        // (We re-render each booking block; easiest is to add listeners after DOM insertion.)
        html += '</div>'; // timeline
        html += '</div>'; // row
      }

      html += '</div>'; // min-width wrapper
      html += '</div>'; // scroll

      container.innerHTML = html;

      // Click delegation: find clicked block and open booking detail
      // Since blocks were created without data-id, we map by matching title/name + time window isn’t safe.
      // We instead re-render with data-ba-booking-id for each block.
      // Quick fix: attach an onClick handler by scanning bookings again.
      var blocks = container.querySelectorAll('.ba-schedule-block');
      if (blocks && blocks.length) {
        // We need to associate blocks with their booking ids in render order.
        // Rebuild a deterministic list of blocks in the same order as DOM insertion.
        var blockList = [];
        for (var sim2 = 1; sim2 <= totalSimulators; sim2++) {
          bookings.forEach(function (b) {
            var sims2 = Array.isArray(b.simulatorNumbers) ? b.simulatorNumbers : [];
            if (sims2.indexOf(sim2) < 0) return;
            blockList.push(String(b.id));
          });
        }
        // Attach click handlers by index (matches insertion order)
      blocks.forEach(function (blk, idx) {
          blk.setAttribute('data-ba-booking-id', blockList[idx] || '');
          blk.addEventListener('click', function () {
            var id = blk.getAttribute('data-ba-booking-id');
          if (id) openBookingDetail(id, { dateHint: dateVal });
          });
        });
      }

      // Setup date prev/next if not already
      if (!window._baScheduleEventsBound) {
        window._baScheduleEventsBound = true;
        if (dateInput) dateInput.addEventListener('change', function () { loadSchedulePage(); });
        if (prevBtn) prevBtn.addEventListener('click', function () {
          var d = new Date((dateInput.value || dateVal) + 'T12:00:00');
          d.setDate(d.getDate() - 1);
          dateInput.value = d.toISOString().slice(0, 10);
          loadSchedulePage();
        });
        if (nextBtn) nextBtn.addEventListener('click', function () {
          var d = new Date((dateInput.value || dateVal) + 'T12:00:00');
          d.setDate(d.getDate() + 1);
          dateInput.value = d.toISOString().slice(0, 10);
          loadSchedulePage();
        });
      }
    } catch (ex) {
      showError(errEl, ex && ex.message ? ex.message : String(ex));
    } finally {
      if (prevBtn) prevBtn.disabled = false;
      if (nextBtn) nextBtn.disabled = false;
    }
  }

  function renderAnalytics(data) {
    var errEl = $('ba-analytics-error');
    hideError(errEl);
    var kpis = $('ba-analytics-kpis');
    var barsWrap = $('ba-analytics-bars');
    var statusWrap = $('ba-analytics-status');
    if (!kpis || !barsWrap || !statusWrap) return;

    if (!data) data = {};

    var totalBookings = Number(data.totalBookings || 0);
    var revenueDollars = '$' + centsToDollars(data.totalRevenueCents || 0);
    var avgRacers = data.avgRacersPerBooking != null ? Number(data.avgRacersPerBooking).toFixed(1) : '0.0';

    var avgDuration = 0;
    var durBreak = Array.isArray(data.durationBreakdown) ? data.durationBreakdown : [];
    if (durBreak.length && totalBookings > 0) {
      var sum = 0;
      durBreak.forEach(function (d) {
        sum += Number(d.durationMinutes || 0) * Number(d.count || 0);
      });
      avgDuration = Math.round((sum / totalBookings) * 10) / 10;
    }

    kpis.innerHTML =
      '<div class="ba-kpi-tile">' +
        '<div class="ba-kpi-label">Total Bookings</div>' +
        '<div class="ba-kpi-value">' + escapeHtml(String(totalBookings)) + '</div>' +
      '</div>' +
      '<div class="ba-kpi-tile">' +
        '<div class="ba-kpi-label">Revenue</div>' +
        '<div class="ba-kpi-value">' + escapeHtml(revenueDollars) + '</div>' +
      '</div>' +
      '<div class="ba-kpi-tile">' +
        '<div class="ba-kpi-label">Average Duration (min)</div>' +
        '<div class="ba-kpi-value">' + escapeHtml(String(avgDuration || 0)) + '</div>' +
      '</div>' +
      '<div class="ba-kpi-tile">' +
        '<div class="ba-kpi-label">Avg Racers</div>' +
        '<div class="ba-kpi-value">' + escapeHtml(avgRacers) + '</div>' +
      '</div>';

    // Revenue by day bars (CSS-only)
    var rbd = Array.isArray(data.revenueByDay) ? data.revenueByDay : [];
    var last = rbd.slice(Math.max(0, rbd.length - 12));
    var maxBookings = 0;
    last.forEach(function (d) { maxBookings = Math.max(maxBookings, Number(d.bookings || 0)); });
    barsWrap.innerHTML = '';

    if (!last.length) {
      barsWrap.innerHTML = '<div class="ba-loading">No analytics for this period.</div>';
    } else {
      var barHeights = last.map(function (d) {
        var b = Number(d.bookings || 0);
        var pct = maxBookings > 0 ? (b / maxBookings) : 0;
        return { date: d.date, bookings: b, h: Math.round(pct * 130) };
      });
      barsWrap.innerHTML =
        '<div class="ba-bar-chart">' +
          barHeights.map(function (x) {
            return '<div style="display:flex;flex-direction:column;align-items:center;gap:8px;width:28px;">' +
              '<div class="ba-bar" style="height:' + x.h + 'px;"></div>' +
            '</div>';
          }).join('') +
        '</div>' +
        '<div class="ba-bar-labels">' +
          barHeights.map(function (x) {
            return '<span style="min-width:28px;text-align:center;">' + escapeHtml((x.date || '').slice(5)) + '</span>';
          }).join('') +
        '</div>';
    }

    // Status breakdown
    var sb = Array.isArray(data.statusBreakdown) ? data.statusBreakdown : [];
    if (!sb.length) {
      statusWrap.innerHTML = '<div class="ba-loading">No status breakdown.</div>';
      return;
    }
    statusWrap.innerHTML =
      '<div class="ba-status-list">' +
        sb.map(function (x) {
          var b = statusBadge(x.status);
          return '<div class="ba-status-row">' +
            '<span class="ba-badge ' + b.cls + '">' + escapeHtml(x.label || b.label) + '</span>' +
            '<span style="color:#fff;font-weight:900;font-variant-numeric:tabular-nums;">' + escapeHtml(String(x.count || 0)) + '</span>' +
          '</div>';
        }).join('') +
      '</div>';
  }

  async function loadAnalyticsPage() {
    var errEl = $('ba-analytics-error');
    var wrap = $('ba-analytics-container');
    if (wrap) wrap.innerHTML = '<div class="ba-loading">Loading…</div>';
    hideError(errEl);
    try {
      var data = await api('/admin/analytics?period=' + encodeURIComponent(baState.cachePeriod), { method: 'GET' });
      renderAnalytics(data);
    } catch (ex) {
      showError(errEl, ex && ex.message ? ex.message : String(ex));
    }
  }

  // Page bootstraps (called by app.js showPage)
  window.loadBookingsListPage = async function () {
    bindBookingsPageEventsOnce();
    await loadBookingsListPage();
  };

  window.loadCheckinPage = async function () {
    await loadCheckinPage();
  };

  window.loadSchedulePage = async function () {
    await loadSchedulePage();
  };

  window.loadAnalyticsPage = async function () {
    await loadAnalyticsPage();
  };

  // Bind nav-driven events once in case user lands directly on a page and clicks.
  bindSharedEventsOnce();
})();

