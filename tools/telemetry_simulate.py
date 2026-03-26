"""
Simulate telemetry_tick POSTs to Controller and verify GET /api/timing/snapshot returns expected cars.
Usage:
  python -m tools.telemetry_simulate [--controller http://127.0.0.1:9630] [--agent Sim5] [--token YOUR_TOKEN]
Requires an enrolled agent (agent_id + token) so Controller accepts X-Agent-Id / X-Agent-Token.
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request

from pitbox_common.ports import CONTROLLER_HTTP_PORT


def main() -> int:
    parser = argparse.ArgumentParser(description="Simulate telemetry_tick and verify timing snapshot")
    parser.add_argument("--controller", default=f"http://127.0.0.1:{CONTROLLER_HTTP_PORT}", help="Controller base URL")
    parser.add_argument("--agent", default="Sim5", help="Agent ID (must be enrolled)")
    parser.add_argument("--token", default="", help="Agent token (required for POST)")
    args = parser.parse_args()
    base = args.controller.rstrip("/")
    agent_id = args.agent.strip()
    token = (args.token or "").strip()
    if not token:
        print("ERROR: --token required so Controller accepts POST /api/agents/telemetry")
        return 1

    device_id = agent_id
    ts_ms = int(time.time() * 1000)
    tick = {
        "type": "telemetry_tick",
        "v": 1,
        "agent_id": agent_id,
        "device_id": device_id,
        "ts_ms": ts_ms,
        "seq": 1001,
        "session_key": "online|192.168.1.218:9616|tatuusfa1|Jaiden",
        "car": {"car_id": 0, "driver_name": "Jaiden", "car_model": "tatuusfa1"},
        "timing": {"lap": 3, "lap_time_ms": 74231, "best_lap_ms": 73510, "last_lap_ms": 74880, "sector": 2, "sector_time_ms": 25110},
        "track": {"track_id": "ks_red_bull_ring", "layout": "layout_national", "normalized_pos": 0.6342, "world": {"x": 123.4, "y": 2.1, "z": -88.7}, "speed_kmh": 167.2},
        "car_state": {"gear": 4, "rpm": 8120, "throttle": 0.91, "brake": 0.0, "in_pit": False},
    }
    headers = {
        "Content-Type": "application/json",
        "X-Agent-Id": device_id,
        "X-Agent-Token": token,
    }
    # POST telemetry
    url = f"{base}/api/agents/telemetry"
    req = urllib.request.Request(url, data=json.dumps(tick).encode("utf-8"), method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode()
            print("POST /api/agents/telemetry:", resp.status, body)
    except urllib.error.HTTPError as e:
        print("POST failed:", e.code, e.read().decode())
        return 1
    except Exception as e:
        print("POST error:", e)
        return 1

    # GET snapshot
    snap_url = f"{base}/api/timing/snapshot"
    req = urllib.request.Request(snap_url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        print("GET snapshot error:", e)
        return 1

    if data.get("type") != "timing_snapshot":
        print("ERROR: expected type=timing_snapshot, got", data.get("type"))
        return 1
    cars = data.get("cars") or []
    if not cars:
        print("ERROR: expected at least one car in snapshot")
        return 1
    first = cars[0]
    if first.get("driver") != "Jaiden" or first.get("car_model") != "tatuusfa1":
        print("ERROR: expected first car driver=Jaiden car_model=tatuusfa1, got", first)
        return 1
    print("OK: timing_snapshot has cars list, first car driver=Jaiden car_model=tatuusfa1")
    return 0


if __name__ == "__main__":
    sys.exit(main())
