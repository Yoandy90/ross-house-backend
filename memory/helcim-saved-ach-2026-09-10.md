# Saved ACH verification — no debit activation

## Implemented

- Explicit `payment_method: ach` creates a $0 `verify` Helcim session; omitted method remains `cc` for existing apps. Session creation remains available without an active lease and binds identity from authentication, not request fields.
- The public payment adapter routes only verification responses to a dedicated signature/hash, currency and zero-amount validator. Purchase callbacks continue through the existing core handler.
- Store bank token, customer code and last four digits. Do not store the complete provider response, full bank/card numbers, routing number or CVV. A pending ACH verification may save a token; it is not proof of active PAD authorization, readiness or settlement.
- A deterministic Mongo `_id` makes repeated callbacks and interrupted session-finalization retries reuse one method per verification session. Separate sessions may still save the same underlying account; no claim of cross-session token deduplication.
- Verification expires after 30 minutes. An authenticated, tenant-scoped session-status endpoint returns only pending, expired or verified, enabling a future verified native completion flow.
- Old app list requests exclude bank methods. New clients opt in with `include_bank=true`; bank records explicitly return `can_pay: false` and `can_autopay: false`. Neither card charge nor card autopay endpoints accept a bank record, even if called directly.

## Mobile pairing

The paired mobile change requests the versioned list and only offers the ACH save button when the backend advertises `capabilities.saved_ach`. Bank entry remains in the existing native Helcim modal. A bank row explains that saved-bank payments are not enabled and is excluded from Pay and autopay selectors. This supports either deployment order; an older server continues to expose only cards.

## Remaining before stored ACH payments

Resolve customer/account IDs against Helcim, verify live bank readiness and an accepted active PAD (including earliest debit/revocation), persist explicit recurring consent and the selected method, and implement debit submission with canonical-invoice claims plus asynchronous settlement/reconciliation. Pending ACH cannot complete a rent invoice. This PR does not activate that functionality, alter rent calculations, generate charges or modify a tenant's autopay configuration by itself.

Provider sandbox/device acceptance for actual bank verification and the returned payload is pending. Automated coverage uses documented response shapes and synthetic data only. No TestFlight build or provider/database production write was used in validation.

## Evidence

- https://devdocs.helcim.com/docs/available-payment-types-and-methods-through-helcimpayjs
- https://devdocs.helcim.com/docs/validate-helcimpayjs
- https://devdocs.helcim.com/docs/pad-agreements
- New executable tests cover card/ACH responses, Unicode signatures, tampering, expiry, direct-charge exclusion, tenancy, callback concurrency/recovery and public route dispatch. Core CI includes these alongside settlement adapter/policy tests.
