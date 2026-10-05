# Property climate integration — staging rollout

This module targets Resideo First Alert API devices, including FocusPRO X2S RTH2CWF/U. Compatibility documentation: https://developer.honeywellhome.com/content/first-alert-app-integration-guide. This is implementation against published documentation, not a completed hardware certification.

Two independent connectors share the same property authorization, normalized readings and tenant controls:

- `first_alert`: existing Resideo First Alert OAuth adapter, future X2S. Legacy connection records without a provider remain First Alert.
- `tcc_us`: existing RTH9585WF1004GG1 through US Total Connect Comfort. Uses pinned community AIOSomecomfort 0.0.38, not a manufacturer-supported public API. Home Assistant lists RTH9585WF1004 as known working: https://www.home-assistant.io/integrations/honeywell/. Real-account and hardware validation are still pending. Do not migrate this account into First Alert.

The admin page exposes separate connect buttons. `POST /api/admin/climate/tcc/connect` accepts a username and password only from an authenticated administrator, verifies authenticated discovery, and encrypts credentials and cookies. Provider connection details returned to admins contain only IDs/types; no secrets or usernames. Account sessions use database locks, a 45-second overall timeout and a two-minute cooldown after transport/auth errors. Redirects may not leave the fixed HTTPS provider origin. Upstream logging is disabled because debug messages include cookies. Commands submit mode/setpoints together once and are never automatically retried. Missing native capabilities block unsafe commands. Discovery uses upstream's four-page limit; intended for this small portfolio.

## Server configuration

All values are server-side secrets/environment settings. Never put them in EXPO_PUBLIC or NEXT_PUBLIC variables.

- `CLIMATE_ENABLED=true`: enable connection and reads (default off).
- `CLIMATE_CONTROL_ENABLED=true`: permit thermostat commands (default off).
- For First Alert only: `CLIMATE_RESIDEO_CLIENT_ID`, `CLIMATE_RESIDEO_CLIENT_SECRET`: registered Resideo app credentials.
- `CLIMATE_TOKEN_KEY`: Fernet key, generated securely and retained with the deployment secrets. Losing or replacing it requires reconnecting all accounts.
- For First Alert only: `CLIMATE_REDIRECT_URI`: exact HTTPS callback registered with Resideo, e.g. the staging site's `/admin/climatizacion`. OAuth is initiated/completed by the authenticated administrator. State is single-use, expires after 10 minutes, and is bound to that administrator.

TCC requires only `CLIMATE_TOKEN_KEY` and `CLIMATE_ENABLED=true` to connect/read; keep `CLIMATE_CONTROL_ENABLED=false` until hardware validation is authorized. It does not require Resideo OAuth app credentials. Tokens and TCC credentials/cookies are encrypted at rest. The provider host is fixed; clients cannot supply endpoints. OAuth state gets a TTL index. Commands are journaled by actor/request ID before dispatch, and serialized with a per-device database lease. A timeout becomes `unknown`, never an automatic retry. Provider refresh uses a database lock.

## Authorization

Admin connection → discover device → choose property and optionally unit → link. Whole-home bindings use an empty unit ID. A tenant lease with a unit ID never inherits a whole-building thermostat. Each tenant request re-resolves canonical identity and one active lease and validates both lease dates. This deliberately denies malformed or expired leases until the lease record is corrected.

Only mode (Off/Heat/Cool/Auto as advertised) and heat/cool setpoints are exposed in v1. The server fetches fresh provider capabilities before every command, enforces native bounds and automatic-mode deadband, and preserves other provider changeableValues. Changing a setpoint requests PermanentHold. Fan, scheduling, emergency heat and usage reporting are not implemented.

## Validation and activation

1. Deploy backend and companion UI PRs to staging, keeping control disabled. Production merge needs owner approval per the application AGENTS.md.
2. Start with TCC US: configure the encryption key and enable reads, enter the existing account through the admin form, then discover/link the thermostat. When X2S is installed, register the First Alert callback and OAuth credentials and connect it separately.
3. Confirm discovery, device capabilities, Fahrenheit/Celsius display, offline status, property/unit binding, and tenant access denial for other properties and inactive/expired contracts.
4. Enable control in staging for that test thermostat only. Verify Heat/Cool/Off/Auto where supported, real bounds/deadband, PermanentHold behavior and read-after-write confirmation. Compare both physical screen and official app. Test timeout handling without repeated commands.
5. Record hardware/firmware/account region, results and API approval/quota details. Only then consider production activation.

Web routes: `/admin/climatizacion`, `/tenant/dashboard/climate`. Mobile route: `/climate` from tenant profile. UI has EN/ES, theme support, F/C, honest empty/offline/pending/unknown states. No simulated temperatures are shipped. Read refresh is manual (no background polling).

Operational limitations: no self-service removal/reassignment or connection revocation screen in this first increment; coordinate those changes with an administrator/developer after reviewing active leases. At present the admin list is capped at 200 bindings; discovery should remain a small portfolio operation. No provider quota, latency or physical-device test has been performed. Reauthorize once per account, avoiding duplicate connections. If an existing connection must be replaced, update its binding explicitly server-side after review; do not assign duplicate devices to tenants.

Tests: `python -m pytest tests/test_climate.py tests/test_climate_tcc.py tests/test_tenant_identity_integrity.py -q`.
