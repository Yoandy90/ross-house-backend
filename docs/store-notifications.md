# Store order notifications

New purchases create an owner notice and an administrator notice. Preparing,
ready, delivered and cancelled transitions notify the owner. ES/EN copy uses no
names, addresses, item descriptions, totals or payment references. Admin header
counts include received/preparing/ready orders and link to the store's Orders tab.

## Persistence and delivery

The existing atomic inventory/order/audit write includes a compact `store_notice`
intent. Rolled-back CAS attempts cannot emit a notice. Legacy unmarked audit
entries are skipped, so deployment does not notify historical orders.

`store_notification_events` imports at most 50 events per pass. Its monotonic
source cursor advances only after event persistence. Do not truncate/reorder the
store audit. Deterministic IDs make event replay and concurrent inbox writes safe;
`$setOnInsert` preserves read state. Notices persist in `rental_notifications`.

Request handlers only synchronize inbox/outbox records, with a two-second budget.
A sync failure leaves the committed purchase successful. Subsequent store reads,
notification reads or the worker recover it. The worker runs through the existing
notification scheduler, every 30 seconds plus processing time.

Outbound attempts use `push_deliveries` and the existing Expo transport/receipt
worker. A compare-and-set claims each pending attempt once. A lost provider result
or a claimed attempt interrupted for one hour becomes `uncertain`, never an
automatic resend. Provider acceptance is not proof the phone displayed the alert.
No device and inactive recipients remain recorded separately. Superseded states
and events older than one day are not pushed; their inbox history remains.

## Staging boundary

`should_disable_background_jobs()` is enforced when creating and sending attempts.
Staging still creates in-app notices, but records push attempts as
`suppressed_environment`; these are not sent later when a switch changes. The
global background-job gate stays unchanged. No real push or email is used by tests.

The mobile customer deep link and store icons need a subsequent app build. Build
153 predates this change. Admin mobile notices open their text; actual order
management remains in the web admin panel.

## Validation

`tests/test_store_notifications.py` covers durable recovery, replay/read-state
preservation, concurrent workers, legacy exclusion, staging/kill-switch behavior,
uncertain sends, missing/disabled devices, superseded updates, rejected purchases,
owner/admin audience boundaries, status transitions and pending counts.
Receipt tracking reuses the Notification Center's tests. Physical-device delivery
and visual QA are not established by these isolated tests.
