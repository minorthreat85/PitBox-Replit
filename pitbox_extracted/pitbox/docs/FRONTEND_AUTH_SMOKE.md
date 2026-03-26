# Frontend operator auth — manual smoke checks

Automated coverage: `controller/static/app.js` uses `pitboxFetch()` which sets **`credentials: 'same-origin'`** on all `/api` requests so the **`pitbox_employee`** cookie is sent after `/employee/login`.

## Quick checks (browser)

1. **Credentials on API calls**  
   - Open DevTools → Network.  
   - After signing in at `/employee/login`, trigger an action (e.g. refresh rigs).  
   - Select a `fetch`/`xhr` to `/api/...` and confirm **Request headers** include `Cookie: pitbox_employee=1` (or inspect Application → Cookies).

2. **Banner**  
   - Set `employee_password` in controller config and restart.  
   - Open main UI **without** visiting `/employee/login`.  
   - Expect the **connection-banner** style strip: *Operator sign-in required* with link to `/employee/login`.

3. **401/403 detail**  
   - From DevTools console (while logged out):  
     `fetch('/api/stop', { method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin', body: '{"all":true}' }).then(r => r.json()).then(console.log)`  
   - Expect JSON `detail` with a clear operator message (not a generic 500).

4. **`postCommand` path**  
   - Batch stop/start uses `postCommand`; on 401/403 the toast should show the server `detail` string.

## Repo verification (no browser)

```bash
rg "credentials: 'same-origin'" controller/static/app.js
rg "function pitboxFetch" controller/static/app.js
rg "refreshOperatorLoginBanner" controller/static/app.js
```
