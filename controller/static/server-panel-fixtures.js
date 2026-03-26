/**
 * Mock status objects for Server Panel (CONNECTED / DISCONNECTED / UNAVAILABLE).
 * Use in console or temporarily in app.js to verify renderServerPanel().
 *
 * Usage (in browser console after load):
 *   var mock = window.__SERVER_PANEL_FIXTURES__;
 *   document.querySelector('.sim-card-thumb').innerHTML = renderServerPanel(mock.CONNECTED);
 */

(function (global) {
  global.__SERVER_PANEL_FIXTURES__ = {
    CONNECTED: {
      online: true,
      error: null,
      ac_running: true,
      server: {
        state: "CONNECTED",
        source: "PITBOX",
        server_id: "SERVER_03",
        name: "PitBox Drift",
        endpoint: { ip: "10.0.0.50", port: 9601 },
        track: { id: "magione", name: "Magione" },
        session: { type: "RACE", time_left_sec: 1200 },
        players: { current: 14, max: 18 },
        net: { ping_ms: 32 },
        role: "CLIENT",
      },
    },
    CONNECTED_MINIMAL: {
      online: true,
      error: null,
      ac_running: true,
      server: {
        state: "CONNECTED",
        source: "EXTERNAL",
        name: "Community Server",
        endpoint: { ip: "192.168.1.100", port: 9600 },
        track: null,
        players: null,
        net: null,
        role: null,
      },
    },
    DISCONNECTED: {
      online: true,
      error: null,
      ac_running: true,
      server: {
        state: "DISCONNECTED",
        source: "UNKNOWN",
        last: {
          server_id: "SERVER_01",
          name: "PitBox Race",
          endpoint: { ip: "10.0.0.50", port: 9601 },
          ended_ts: "2026-02-16T12:00:00Z",
        },
      },
    },
    UNAVAILABLE_OFFLINE: {
      online: false,
      error: "TIMEOUT",
      ac_running: false,
      server: { state: "UNAVAILABLE", source: "UNKNOWN" },
    },
    UNAVAILABLE_AC_NOT_RUNNING: {
      online: true,
      error: null,
      ac_running: false,
      server: { state: "UNAVAILABLE", source: "UNKNOWN" },
    },
  };
})(typeof window !== "undefined" ? window : this);
