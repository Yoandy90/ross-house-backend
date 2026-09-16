# Resident store: delivery and stock identity

Staging rollout, separate from rental accounting. The mobile client now offers home delivery only. Enable `settings.home_delivery_only`, clear `pickup_address`, and configure delivery slots. Existing pickup order snapshots stay historical; they are not relabeled as deliveries.

## Purchase and address

The authenticated tenant's signed active lease determines the property and unit. Missing or ambiguous linkage cannot authorize delivery. Address and ZIP are compared on quote and placement; arbitrary client addresses are rejected. Home delivery does not require a manually duplicated ZIP allowlist. Confirmation preserves a residence snapshot privately, along with immutable quoted amounts, SKU, variant, presentation and tax rate. Idempotent recovery precedes current-address validation so an accepted purchase can still be recovered after moving.

## Courier operations

Admin enrolls an existing active app account by unique email. This grants a store-specific permission, not an admin role or access to rent, leases or receipts. There is no public courier signup. Admin assigns a courier and timezone-qualified future ETA; stale reassignment is rejected. Courier dashboard under Profile > My deliveries lists only pending assigned orders and rechecks enrollment/account state on every request. No persistent courier cache.

Courier can depart after preparation, report an issue, and confirm handoff. Handoff is idempotent and does not mark money received. For unpaid handoffs, admin records the actual payment before final accounting completion. Already-paid handoffs complete delivery immediately. Reassignments are disallowed after handoff. Disable a courier only after reassigning their pending orders. Dashboard refreshes on focus/resume and every 20 seconds while visible. Maps link opens navigation; there is no simulated live GPS or invented ETA.

Customer sees the delivery timeline, assigned courier first name, estimated arrival and recorded handoff. Operational issue notes, driver account IDs and private property/lease links are not exposed through customer order endpoints.

## Product and inventory

Each sellable variant has a stable product ID, unique normalized SKU and optional unique barcode. Existing UUIDs are preserved. Missing legacy SKU is represented deterministically as `RH-` plus product ID without hyphens. Size, color, brand and family ID describe variants; every variant maintains its own stock and price. Catalogue accepts general merchandise categories.

Sale units describe fixed presentations: e.g. one bag containing 0.5 kg. Cart quantity, stock, cost and price count complete presentations. Variable weight measured during fulfillment and price adjustments are not implemented. Do not advertise variable-weight checkout until customer approval and settlement of adjustments exist.

Admin-created tax profiles provide named basis-point rates, with per-product explicit rates retained for compatibility. Profiles cannot be deleted while assigned. Checkout resolves server-side rates and retains tax cents/rate in order snapshots. No jurisdictional tax rate is guessed automatically. Configure rates according to the actual operation before selling.

Inventory movement audit records opening stock, reasoned adjustments, reservations and one-time cancellation returns atomically with stock changes. Historical movement records are not fabricated. Admin reports display low-stock thresholds and provide inventory, product sales and movement CSVs. Existing aggregate capacity safeguards still apply (300 products, 2,000 orders, 15,000 audit entries, 6 MB); this is not an unbounded enterprise warehouse/ERP. Multiple warehouses, purchase orders, supplier payable ledgers, automated refunds and variable-weight settlement need separate future work.

## Notifications and receipts

See `store-receipts.md`. Payment issues one receipt number and private ES/EN PDFs in `store_receipt_files`, separate from rental invoices. Outbox provides inbox/push events for payment, departure and handoff; staging outbound workers remain disabled. ETA updates are shown by refresh; no unsupported live-location claims. Delivery issue reporting is visible in admin orders and does not send an external message.

## Validation

API tests cover canonical home/unit linkage, address tampering, pickup rejection, idempotent recovery after address changes, assigned-only driver reads, revoked/inactive accounts, handoff/payment separation, single handoff event, SKU/barcode uniqueness, stock adjustment reasons, one-time inventory restoration, tax profiles and historical quote retention. Receipt and existing stock/order/notification suites remain part of the staging gate.
