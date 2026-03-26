# PitBox release workflow (controller / bundle)

Short checklist for shipping a controller or bundle build. Adjust for your signing / CI setup.

## Before tagging

1. **Tests** — from repo root (or `dev/pitbox` as applicable):
   ```bash
   python -m unittest discover -s controller/tests -p "test_*.py"
   ```
2. **Docs** — if API auth or GET routes changed, update **`docs/API_GET_ROUTES.md`** in the same commit.
3. **Release notes** — add or extend a versioned file (e.g. **`RELEASE_NOTES_1.4.1.md`**) with user-visible fixes and features.

## Version identifiers

- **`BUILD_ID`** in `controller/api_routes.py` is set at import time (`time.strftime` UTC) for **`X-PitBox-Build`**-style tracing; it is not a semantic version.
- Use **git tags** or your installer’s **semantic version** as the customer-facing version; keep release notes aligned with that.

## After merge

- Build / sign artifacts per your pipeline.
- Smoke: start controller, open main UI, confirm **`GET /api/version`** and a logged-in **`GET /api/status`** (if `employee_password` is set) behave as expected.

## Related docs

- **[`PITBOX_UPDATER.md`](PITBOX_UPDATER.md)** — update channel behaviour  
- **[`UPDATE_TESTING.md`](UPDATE_TESTING.md)** — updater QA  
- **[`API_GET_ROUTES.md`](API_GET_ROUTES.md)** — GET route auth matrix  
