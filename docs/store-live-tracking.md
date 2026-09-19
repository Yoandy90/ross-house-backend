# Live delivery location (staging)

Endpoints are relative to /api:

- POST /store/driver/orders/{id}/tracking/start: active assigned courier,
  departed delivery, optional geocoded destination. Returns opaque session ID
  and expiry. Only one sharing session per driver; other orders stop sharing.
- POST .../tracking/position: authorized courier + matching live session,
  coordinate bounds, accuracy <=100 m, GPS timestamp no older than 30 seconds
  and no more than 10 seconds ahead. Latest-only monotonic updates, at most one
  persisted sample per 3 seconds. No offline sample replay or route history.
- POST .../tracking/stop: invalidates that specific session, idempotently.
- GET /store/orders/{id}/tracking/live: authenticated order owner only.
- GET /admin/store/orders/{id}/tracking/live: existing admin authorization.

All location reads/writes use private/no-store responses. Public order/receipt
serializers strip _live; driver lists expose neither session IDs nor positions.
Location coordinates are never written to order audits, notifications or receipts.

store_live_locations keeps only the latest point per session. A TTL index on
expires_at deletes points after five minutes (MongoDB cleanup may lag; API expiry
is enforced independently). Start/stop eagerly remove old session points. Handoff,
terminal status and reassignment invalidate aggregate consent. Racing GPS writes
cannot restore visibility because every read rechecks assignment and consent.
Session/driver revocation is rechecked at read time; old sid-less tokens are bounded
by their signed expiry. Sharing authorization expires after four hours.

Deploy this backend before the mobile companion. No production activation, no
payment processor calls, no historical-location migration. Existing delivery and
pay-on-receipt flows keep working if clients never start a tracking session.

Tests: pytest -q tests/test_store_live_tracking.py tests/test_store_delivery.py
