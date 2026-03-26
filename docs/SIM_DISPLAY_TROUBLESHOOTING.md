# Sim Display – Troubleshooting

If the sim page shows "Nothing" or errors, follow these steps in order.

---

## 1. Controller is running and reachable

- On the **Control PC**, start the PitBox Controller (from source or EXE).
- In a browser, open: **`http://<CONTROL_PC>:<PORT>/status`**  
  Example: `http://127.0.0.1:9630/status` if the Controller is on the same machine.
- You should see plain text: **`Controller is running.`**
- If you don’t:
  - Confirm the Controller process is running.
  - Check the port (default 9630) in `controller_config.json` → `ui_port`.
  - If opening from another PC, use the Control PC’s IP and ensure firewall allows that port.

---

## 2. Sim page loads

- Open: **`http://<CONTROL_PC>:<PORT>/sim?agent_id=Sim5`** (use your agent id, e.g. Sim1, Sim5).
- If you get a **404** or "sim.html not found":
  - You’re likely running a **packaged Controller (EXE)** that was built before `sim.html` was added. Rebuild the Controller and redeploy so the bundle includes `static/sim.html`.
  - Or run the Controller **from source** so it uses `controller/static/sim.html`.

---

## 3. Assign the sim to a server

The sim display shows track/cars only after that sim is **assigned** to a server preset. Assignments are stored in memory on the Controller.

**Option A – curl (from any machine that can reach the Controller)**

```bash
curl -X POST "http://<CONTROL_PC>:<PORT>/api/assignments/Sim5" -H "Content-Type: application/json" -d "{\"server_id\": \"SERVER_01\"}"
```

Use your agent id and a preset that exists (e.g. `SERVER_01`, `SERVER_02`). You should get: `{"ok":true,"agent_id":"Sim5","server_id":"SERVER_01"}`.

**Option B – PowerShell**

```powershell
Invoke-RestMethod -Method Post -Uri "http://192.168.1.200:9630/api/assignments/Sim5" -ContentType "application/json" -Body '{"server_id":"SERVER_01"}'
```

After this, reload the sim page. If the only problem was "no assignment", you should now see content or a different error (e.g. preset not found).

---

## 4. Server preset exists and is valid

The assigned `server_id` must be a **preset folder** that the Controller can read.

- Default path: **`C:\Program Files (x86)\Steam\steamapps\common\assettocorsa\server\presets\<SERVER_ID>\`**
- That folder must contain at least one of:
  - **`server_cfg.ini`**
  - **`entry_list.ini`**
- If the page shows **"Preset not found for server_id=SERVER_01"** (or similar):
  - Create that folder under the presets path and add a valid `server_cfg.ini` (and optionally `entry_list.ini`), or
  - Assign a `server_id` that already exists (e.g. copy from another preset folder).
- If your Controller uses a custom path, presets are under the path from **`ac_server_presets_root`** (or **`ac_server_root`**) in `controller_config.json`.

---

## 5. Summary

| What you see | What to do |
|--------------|------------|
| Page doesn’t load / connection error | Check step 1 (Controller running, port, firewall). |
| 404 on `/sim` or "sim.html not found" | Rebuild/redeploy Controller so `sim.html` is in the bundle, or run from source. |
| "No server assigned" | Assign via step 3 (POST `/api/assignments/<agent_id>`). |
| "Preset not found for server_id=..." | Create that preset folder with `server_cfg.ini` (and optionally `entry_list.ini`) under the presets path, or assign a different `server_id`. |
| "Connection lost" / "Cannot reach Controller" | Same as step 1; ensure the sim PC can reach the Controller URL you use in the browser. |

**Quick test**

- **From Admin PC** (192.168.1.200): open `http://127.0.0.1:9630/sim?agent_id=Sim5` and assign via `http://127.0.0.1:9630`.
- **From a Sim PC**: open `http://192.168.1.200:9630/sim?agent_id=Sim5`. Assign from Admin or:  
  `Invoke-RestMethod -Method Post -Uri "http://192.168.1.200:9630/api/assignments/Sim5" -ContentType "application/json" -Body '{"server_id":"SERVER_01"}'`

If the preset `SERVER_01` exists with a valid `server_cfg.ini`, the sim page should show the track and car selection.
