# Property climate integration — staging rollout

This module targets Resideo First Alert API devices, including FocusPRO X2S RTH2CWF/U. Compatibility documentation: https://developer.honeywellhome.com/content/first-alert-app-integration-guide. This is implementation against published documentation, not a completed hardware certification.

The existing RTH9585WF1004GG1 uses Total Connect Comfort US. It is **not covered by this adapter**. Do not migrate its account or claim it will appear through the First Alert authorization flow. A separate TCC adapter would need investigation and approval.

## Server configuration

All values are server-side secrets/environment settings. Never put them in EXPO_PUBLIC or NEXT_PUBLIC variables.

- `CLIMATE_ENABLED=true`: enable connection and reads (default off).
- `CLIMATE_CONTROL_ENABLED=true`: permit thermostat commands (default off).
- `CLIMATE_RESIDEO_CLIENT_ID`, `CLIMATE_RESIDEO_CLIENT_SECRET`: registered Resideo app credentials.
- `CLIMATE_TOKEN_KEY`: Fernet key, generated securely and retained with the deployment secrets. Losing or replacing it requires reconnecting all accounts.
- `CLIMATE_REDIRECT_URI`: exact HTTPS callback registered with Resideo, e.g. the staging site's `/admin/climatizacion`. OAuth is initiated/completed by the authenticated administrator. State is single-use, expires after 10 minutes, and is bound to that administrator.

Tokens are encrypted at rest. The provider host is fixed; clients cannot supply endpoints. OAuth state gets a TTL index. Commands are journaled by actor/request ID before dispatch, and serialized with a per-device database lease. A timeout becomes `unknown`, never an automatic retry. Provider refresh uses a database lock.

## Authorization

Admin connection → discover device → choose property and optionally unit → link. Whole-home bindings use an empty unit ID. A tenant lease with a unit ID never inherits a whole-building thermostat. Each tenant request re-resolves canonical identity and one active lease and validates both lease dates. This deliberately denies malformed or expired leases until the lease record is corrected.

Only mode (Off/Heat/Cool/Auto as advertised) and heat/cool setpoints are exposed in v1. The server fetches fresh provider capabilities before every command, enforces native bounds and automatic-mode deadband, and preserves other provider changeableValues. Changing a setpoint requests PermanentHold. Fan, scheduling, emergency heat and usage reporting are not implemented.

## Validation and activation

1. Deploy backend and companion UI PRs to staging, keeping control disabled. Production merge needs owner approval per the application AGENTS.md.
2. Register the callback, configure secrets and enable reads; connect a test X2S on a test First Alert account.
3. Confirm discovery, device capabilities, Fahrenheit/Celsius display, offline status, property/unit binding, and tenant access denial for other properties and inactive/expired contracts.
4. Enable control in staging for that test thermostat only. Verify Heat/Cool/Off/Auto where supported, real bounds/deadband, PermanentHold behavior and read-after-write confirmation. Compare both physical screen and official app. Test timeout handling without repeated commands.
5. Record hardware/firmware/account region, results and API approval/quota details. Only then consider production activation.

Web routes: `/admin/climatizacion`, `/tenant/dashboard/climate`. Mobile route: `/climate` from tenant profile. UI has EN/ES, theme support, F/C, honest empty/offline/pending/unknown states. No simulated temperatures are shipped. Read refresh is manual (no background polling).

Operational limitations: no self-service removal/reassignment or connection revocation screen in this first increment; coordinate those changes with an administrator/developer after reviewing active leases. At present the admin list is capped at 200 bindings; discovery should remain a small portfolio operation. No provider quota, latency or physical-device test has been performed. Reauthorize once per account, avoiding duplicate connections. If an existing connection must be replaced, update its binding explicitly server-side after review; do not assign duplicate devices to tenants.

Tests: `python -m pytest tests/test_climate.py tests/test_tenant_identity_integrity.py -q`.
