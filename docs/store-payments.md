# Store Helcim payments — staging integration

The store reuses the existing Helcim vault, never the rent invoice/charge endpoint. A customer's store order, stock reservation, payment attempt and authorization version are committed with the store revision CAS before any external charge. Only the creator of that committed attempt can submit to Helcim. Every provider mutation uses the durable UUID as its idempotency key. Retry/restart returns the existing order; it never submits a second purchase.

## Checkout and accounting

- Payment options expose masked, owned, ready methods only. Ownership is the authenticated app user plus its unique explicit tenant link, without email-based guessing.
- The reviewed quote binds the chosen method; the final Pay and confirm action authorizes `store-purchase-v1`. Prices, stock, delivery and tax are recalculated by the server.
- Card approval requires an identified transaction with the exact USD amount. ACH acceptance stays pending until a GET of the same transaction confirms APPROVED and CLEARED.
- Confirmed payment creates one existing premium receipt and its durable inbox/email event. Rental invoices, rent autopay and rent ledger collections are untouched.
- Fulfillment is blocked for unsettled online payments, refunds and post-settlement accounting reviews, including courier endpoints.
- Full refunds require admin confirmation and a reason. A refund has a separate durable attempt; unknown responses are never resubmitted. A distinct refund transaction must be confirmed. Refund is blocked during delivery transit.
- Refund does not itself restock. Cancellation before delivery restores stock once; delivered returns require the existing reasoned inventory adjustment. The original paid receipt remains downloadable after refund. This release does not issue a separate credit-note PDF.

## Failure handling

Only a safe result summary is stored in `store_payment_results` before applying it to the store aggregate. A saved successful response can be replayed after an aggregate write failure without calling the charge endpoint. Known pending transactions can be refreshed by owner/admin; the background worker reconciles up to ten pending attempts per cycle with the existing environment kill switch.

A timeout without a transaction ID remains `review_required`; this release deliberately has no automatic retry/reset/manual-paid override. An operator must inspect Helcim using the attempt ID and resolve through a reviewed follow-up repair. An unknown response schema, mismatched amount/currency, changed merchant credentials or an original charge returned as a refund cannot silently complete payment. A later detected ACH return flags accounting review and blocks fulfillment without rewriting the original receipt. Post-settlement ACH returns require operational reconciliation; the pending worker is not a complete chargeback/returns service.

## Environment and activation gate

Every store provider call refuses production credentials unless ENVIRONMENT is exactly production. Nonproduction requires an active sandbox Helcim account. Existing background jobs and external notifications remain disabled in staging. No real provider charges or customer messages were used for automated testing.

The staging processor was inspected read-only: Helcim is configured as production and has no sandbox API token. Online checkout therefore reports unavailable and pay upon receipt remains usable. Configure actual test-account credentials through the existing admin payment processor page (never in source/chat), switch its active environment to sandbox, and save test methods before provider acceptance testing.

Before any production rollout, verify the actual merchant's purchase/decline response, ACH acceptance/clearing, lost response, full refund and refund clearing against test Helcim. Unit/interaction tests simulate these contracts; they do not establish live-provider compatibility. Confirm ownership links and vault environment provenance for existing methods. Legacy vault rows without environment metadata are accepted only through the active account; new provider-specific provenance should be added as part of vault hardening.

Official API references consulted:
- https://devdocs.helcim.com/reference/purchase
- https://devdocs.helcim.com/reference/getcardtransaction
- https://devdocs.helcim.com/reference/refund
- https://devdocs.helcim.com/reference/achwithdraw
- https://devdocs.helcim.com/reference/getachtransactionbyid
- https://devdocs.helcim.com/reference/achrefund
