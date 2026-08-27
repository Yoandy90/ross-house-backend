"""Pure settlement policy for payment-processor webhooks.

Financial-integrity rule: a webhook is never proof of rent payment merely because
it contains an external transaction id. Settlement requires both authenticated
origin and an explicit final-success state for the corresponding processor.

This module is intentionally side-effect free so policy can be audited/tested
without MongoDB, live processor credentials, or network access.
"""


def clover_webhook_can_settle(*, verified: bool, status: str | None) -> bool:
    """Clover rent settlement requires a verified webhook and final APPROVED state."""
    return bool(verified) and (status or "").upper() == "APPROVED"


def bofa_webhook_can_settle(*, verified: bool, decision: str | None) -> bool:
    """BofA Secure Acceptance settlement requires verified signature + ACCEPT."""
    return bool(verified) and (decision or "").upper() == "ACCEPT"


def helcim_webhook_may_lookup(*, verified: bool, event_type: str | None,
                              transaction_id: str | None) -> bool:
    """A Helcim webhook may trigger server-side transaction lookup only if verified.

    The webhook itself is NOT payment proof; callers must retrieve the transaction
    from Helcim and separately verify approved status, amount and currency before
    settlement.
    """
    return (
        bool(verified)
        and (event_type or "") == "cardTransaction"
        and bool(transaction_id)
    )


def helcim_transaction_can_settle(*, status: str | None,
                                  amount_cents: int | None,
                                  expected_amount_cents: int | None,
                                  currency: str | None) -> bool:
    """Authoritative Helcim transaction requirements for rent settlement."""
    if (status or "").upper() != "APPROVED":
        return False
    if amount_cents is None or expected_amount_cents is None:
        return False
    if int(amount_cents) != int(expected_amount_cents):
        return False
    return (currency or "").upper() == "USD"
