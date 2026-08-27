# Payment Reconciliation Operations

This document describes the safety contract for manual payment reconciliation in Ross House Rentals.

## Purpose

Automatic payment flows intentionally fail closed when the backend cannot prove the final financial state. The reconciliation workflow lets administrators investigate and record decisions without bypassing payment integrity controls.

## Workflow

1. **Investigate** the active exception using the read-only queue/detail endpoints.
2. **Admin A proposes** one immutable decision with reason and evidence reference.
3. **Admin B confirms** the exact proposal digest and outcome. Admin B must be a different identity.
4. **Admin C executes** the already-confirmed decision. Admin C must be different from both proposer and confirmer.
5. The backend writes an immutable `execution_claim` before any local accounting mutation.
6. If local accounting is allowed, the canonical invoice is updated only with a full financial snapshot guard.
7. An immutable `execution_result` records the outcome.

## What this phase can do

Only `provider_confirmed_paid` may produce a local accounting write.

That write is allowed only when all of the following are true:

- the reconciliation source is still the exact approved exception version;
- the system can prove an exact canonical invoice linkage from trusted local data;
- the invoice remains `pending`, `late`, or `partial`;
- current outstanding balance is positive;
- the original server-recorded charge amount is available;
- original charge amount equals current outstanding;
- Admin C echoes the exact invoice id and exact amount;
- the deterministic execution claim does not already exist;
- the invoice financial/version snapshot still matches at update time.

The resulting payment method is `manual_reconciliation_verified`, making the accounting origin explicit.

## What this phase cannot do

The reconciliation execution code does **not**:

- call Stripe, Square, Clover, Bank of America, or Helcim;
- create a PaymentIntent or hosted checkout;
- retry a payment;
- release a checkout/autopay claim;
- issue a refund;
- create a new charge;
- automatically apply a manual credit review;
- automatically execute a refund review.

`needs_refund_review` and `needs_manual_credit_review` remain review-only decisions.

## Amount authority

The admin request is never the source of truth for the amount.

For a paid reconciliation the backend requires:

`trusted original attempt amount == canonical current outstanding == Admin C echoed amount`

Hosted checkout uses the server-created checkout claim amount. Autopay uses the server-recorded `last_attempt_amount`. Hardened Stripe reconciliation logs intentionally do not retain raw amount metadata; if no trusted amount is available, paid execution remains blocked.

## Concurrency and crash behavior

Execution uses deterministic IDs:

- one `execution_claim` per confirmed decision;
- one `execution_result` per confirmed decision.

The claim is inserted before any financial write. If a process crashes after the claim, future automatic execution attempts are blocked.

A missing result is investigated with:

`GET /admin/payment-reconciliation/execution-claims/{claim_id}/recovery`

The recovery endpoint is read-only and always returns `automatic_retry_allowed=false`.

## Recovery classifications

- `result_recorded` — normal completed audit trail.
- `financial_write_applied_result_missing` — invoice itself proves the execution claim was applied; do not retry.
- `no_financial_write_detected` — invoice still matches the stored pre-write snapshot; investigate before any action.
- `record_only_result_missing` — the execution had no financial write authority.
- `ambiguous_state` — state cannot be proven; do not retry or change balances until investigated.

## Workflow dashboard

`GET /admin/payment-reconciliation/workflows`

States:

- `proposed`
- `confirmed`
- `execution_started`
- `executed`
- `requires_review`

An execution claim without a result is automatically triaged as `requires_review` after 5 minutes. This is read-only classification; it never releases the execution lock.

Filtered workflow scans are hard bounded to at most 1,000 proposals. Normal output remains capped to 200 items.

## Audit collection

All proposals, confirmations, execution claims, and execution results use:

`payment_reconciliation_actions`

The workflow is append-oriented. Payment source records are never edited by proposal/confirmation endpoints. The execution endpoint has exactly one allowed financial writer: a guarded update to the already-proven canonical `rental_payments` invoice for `provider_confirmed_paid`.

## Administrative identity rules

- proposal: authenticated admin required;
- confirmation: a second different admin required;
- execution: a third different admin required.

Identity comparison uses both admin id and normalized email. Session/auth tokens are never stored in reconciliation action records.

## Operational rule

When any evidence, amount, source version, invoice linkage, or concurrency state is ambiguous: **do not retry automatically and do not alter balances. Investigate first.**
