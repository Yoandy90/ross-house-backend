# Saved-card rent charge integrity

Saved Helcim charges resolve the canonical current-month invoice, charge only its outstanding balance, and mark cumulative total_paid only after an approved response with a transaction ID. Existing partial payments are preserved. Client-supplied amounts and tenant IDs do not select the charge.

A persistent `charge_attempt` on the canonical invoice is acquired by a conditional Mongo update comparing its financial snapshot. Saved cards, both autopay processor branches, and hosted checkout creation use this gate before contacting a processor. Existing hosted checkouts and this month's recorded automatic attempts also block new attempts. The saved-card completion write compares the original financial snapshot so concurrent accounting changes require reconciliation instead of being overwritten.

The claim deliberately has no automatic expiry or release, including after a decline or cancellation. A timeout, crash or failed confirmation write must not permit a second charge. Review the processor's authoritative transaction records and reconcile the invoice before deciding whether another attempt is safe; this PR adds no claim-reset API. Existing checkout URLs may remain payable and must be accounted for during reconciliation. Already-running pre-deployment workers are outside the new atomic gate.

Scope: saved Helcim cards, the autopay worker and hosted checkout creation. This does not establish a universal lock across manual accounting writes or the separate native Stripe payment-intent flow. It does not enable saved ACH. It does not add automatic refunds or operator reconciliation UI. A completed payment may still require review if local persistence fails. The public saved-card endpoint returns no success receipt until its invoice transition succeeds.

Validation uses fake database/provider responses: partial balances, tenant ownership, settled/no-contract rejection, concurrent requests, cross-entrypoint exclusion, legacy pending attempts, timeouts, missing transaction IDs, accounting conflicts and database write failure. No real payment or hosted database is used. Real-device/provider acceptance remains separate.
