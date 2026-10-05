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


## Capability-driven product rules

The UI must expose controls from the device's reported capabilities instead of assuming every thermostat supports the same features.

Temperature flow:
- Heat mode: show and edit the heat target only. Preserve the cooling target internally.
- Cool mode: show and edit the cool target only. Preserve the heating target internally.
- Auto mode: show both targets and enforce the native deadband.
- Off mode: hide temperature steppers; only a mode change can re-enable conditioning.
- If the user changes mode before applying, discard any now-hidden local setpoint edits so no invisible command is sent.
- TCC setpoint writes preserve both setpoints atomically and move the opposite target only when necessary to satisfy the thermostat's native deadband.
- The display unit toggle converts setpoints as absolute temperatures and deadband as a temperature difference.

Current capability matrix:

| Capability | TCC US / RTH9585WF path | First Alert / Honeywell Home API |
| --- | --- | --- |
| Indoor temperature | Supported | Supported |
| Indoor humidity | Supported when sensor reports it | Supported when device reports it |
| Heat/Cool/Off/Auto | Supported when advertised | Supported via allowedModes |
| Heat/cool setpoints | Supported | Supported |
| Native bounds/deadband | Supported | Supported |
| Equipment activity | Available on some TCC payloads; otherwise may be unknown/stale | operationStatus is documented |
| Fan mode | AIOSomecomfort supports Auto/On/Circulate where fanData allows it | Official fan GET/POST API |
| Fan running state | Available when TCC fanData reports it | Official fan/operation status |
| Permanent hold | Supported | Supported |
| Temporary hold / next schedule period | Supported by TCC client | Supported |
| Hold until a time | Supported by TCC client in 15-minute increments | Supported with nextPeriodTime |
| Resume schedule / NoHold | Supported by TCC client | Supported |
| Outdoor temperature/humidity | Supported when TCC reports weather fields | Documented |
| Weekly schedule editor | Not enabled until real-device validation of this community connector | Official GET/POST schedule APIs |
| Pause/resume schedule | Not enabled until real-device validation | Official APIs |
| Adaptive recovery | Not exposed by current TCC bridge | Official API where supported |
| Emergency heat | Do not expose unless the device explicitly advertises it | Supported only when advertised |
| Room/sensor priority | Not available through current TCC bridge | T9/T10-specific official APIs; do not assume X2S supports it |
| Thermostat system configuration / firmware | Limited through current TCC bridge | Official thermostatconfiguration GET |
| Hardware brightness | Not implemented | Can be reported in thermostat settings; write support must be verified before exposing |
| Real-time event subscription | Not supported by current TCC bridge | First Alert/Honeywell event APIs are available; First Alert event IDs use the newer UUID/subsystem formats |

### Product roadmap

P1 — validated current-device controls:
1. Correct mode-specific temperature UX and TCC deadband-safe writes.
2. Prefer provider-reported equipment activity, fall back to an explicitly labeled estimate only when necessary.
3. Add fan mode/status when the connected device advertises it.
4. Add hold controls: Follow schedule, Until next period, Permanent hold; keep Hold-until-time behind capability validation.
5. Surface outdoor conditions and current hold/schedule state as read-only context when available.

P2 — First Alert / X2S:
1. Fan control from official capabilities.
2. Schedule summary and pause/resume.
3. Full 7-day schedule editor only when scheduleCapabilities advertise a timed schedule.
4. Adaptive recovery toggle only when supported.
5. Thermostat configuration/firmware diagnostics for admin.
6. Event-driven refresh to reduce manual polling when Resideo event subscriptions are enabled.

P3 — model-specific enhancements:
- Room/sensor priority only for models whose API reports that capability (documented for T9/T10).
- Emergency heat only when allowedModes/settings explicitly advertise it.
- Vacation/geofencing/special modes only when the device response exposes the matching capability.
- Never render unsupported controls merely because another thermostat model supports them.

Public capability references reviewed on 2026-10-05: Resideo Honeywell Home thermostat GET/change-setting, fan, schedule, thermostat-configuration, room-priority and First Alert integration documentation; AIOSomecomfort's current TCC client capabilities. Keep provider-specific writes behind hardware validation before production.
