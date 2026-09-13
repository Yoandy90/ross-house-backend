"""Canonical signature-derived lease state.

An ``active`` label is never authoritative by itself: occupancy requires the
tenant signature plus either the landlord or Ross House administrator
signature. These helpers let read and authorization boundaries fail closed for
historical records activated before lifecycle guards existed.
"""


def lease_has_required_signatures(contract: dict) -> bool:
    return bool(contract.get("tenant_signature")) and bool(
        contract.get("landlord_signature") or contract.get("admin_signature")
    )


def effective_lease_status(contract: dict) -> str:
    """Downgrade only inconsistent active records to the truthful signing state."""
    status = str(contract.get("status") or "draft").strip().lower()
    if status != "active" or lease_has_required_signatures(contract):
        return status

    tenant_signed = bool(contract.get("tenant_signature"))
    counterparty_signed = bool(
        contract.get("landlord_signature") or contract.get("admin_signature")
    )
    if not tenant_signed and not counterparty_signed:
        return "pending_signatures"
    if not tenant_signed:
        return "pending_tenant"
    return "pending_landlord" if contract.get("landlord_id") else "pending_signatures"
