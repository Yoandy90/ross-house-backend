# Resident store pilot

Staging implementation for owner inventory. No seed products, sales, provider calls,
or activation are performed by deployment. The store defaults to closed.

## Operations

- Admin `/admin/tienda`: bilingual product names/descriptions, HTTPS images, unit
  purchase cost and sale price, explicit product tax, available stock, visibility.
- Configure delivery ZIPs, pickup address, selectable delivery windows, minimum
  merchandise amount, delivery fee and delivery tax. Remove obsolete windows
  manually. Windows are offered labels, not a capacity-managed booking calendar.
- Open the store only after actual inventory and fulfillment settings are ready.
- Residents: catalog, search/category filter, in-memory cart, server quote, review,
  pay-on-receipt order, order history/receipt and cancellation before preparation.
- Admin workflow: received → preparing → ready → delivered. Payment must be
  recorded before delivery. The payment action records money already received;
  it does not debit a saved card or bank account.
- Unpaid orders can be cancelled; reserved inventory is returned once. Paid orders
  cannot be cancelled without a refund workflow (not part of this pilot).
- Collecting payment is audited and idempotent. Purchases never write rental
  invoices, rent payments or rental autopay records. Merchandise margin excludes
  delivery, preparation and other operating costs.

## Integrity and operating limits

`resident_store` contains one bounded versioned aggregate. Mongo compare-and-swap
replaces inventory, immutable order snapshot and audit in a single atomic write;
this works on standalone staging Mongo as well as replica sets. Conflicts retry
up to 12 times. Administrative inventory edits carry the original revision so
concurrent reservations cannot be overwritten. Quote hashes bind current prices,
tax, items and fulfillment; checkout recalculates from server data. Idempotency
keys are scoped to authenticated identity and bind the entire confirmed request.
No customer identity or price is accepted from the client.

Pilot limits: 300 products, 2,000 orders, 15,000 audit entries, 6 MB BSON. Capacity
checks fail closed without partial writes. Monitor aggregate size and migrate to
transaction-backed collections before reaching these limits; do not discard
orders or audit entries to make room. Admin product stock means units available
for NEW orders; already reserved units are excluded.

## Next stage

Online Helcim checkout needs a distinct merchandise payment purpose, provider
reconciliation, uncertain-result handling and refund flow. Existing rent charge
endpoints must not be reused. Push status updates/marketing opt-in, recurring
orders, delivery capacity and inventory procurement are future extensions.
Product/fulfillment tax rates are explicit configuration; no fiscal classification
is inferred. No products, tax rates or real delivery windows ship preconfigured.

## Validation

`PYTHONPATH=. python -m pytest -q tests/test_resident_store.py`

Covers concurrency/last unit, injected CAS conflict, duplicate retries, quote
changes, cancellation/restock, ownership and real unauthenticated requests,
strict validation, payment/delivery gates, accounting isolation and size limits.
Frontend PR carries actual React component interaction tests and active mobile
TypeScript checks. Local browser access was blocked; visual QA remains required
on the deployed staging web and new mobile preview.
