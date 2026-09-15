"""Amounts and audit data for an administrator's full-payment confirmation."""
from decimal import Decimal, InvalidOperation

from fastapi import HTTPException


def payment_money(value, field):
    try:
        value = Decimal(str(value))
        if not value.is_finite() or value < 0 or value != value.quantize(Decimal('0.01')):
            raise ValueError
        return float(value)
    except (InvalidOperation, ValueError, TypeError):
        raise HTTPException(400, f"Importe inválido: {field}")


def recorded_paid_amount(payment):
    # Legacy receipts without a total used the base amount. Never infer receipt
    # of a late fee from an invoice's amount due, nor from a processing attempt.
    value = payment.get('total_paid')
    if value is None:
        value = payment.get('amount', 0)
    return payment_money(value, 'total_paid')


def manual_confirmation(existing, updates, payload, admin, now):
    """Normalize an explicit full-payment action; return its atomic audit event.

    Marking an invoice paid is an administrator's attestation of full receipt.
    Explicitly supplied received amounts must agree with the invoice breakdown.
    Provider attempts are retained verbatim for independent reconciliation.
    """
    status = updates.get('status', existing.get('status', 'pending')).lower()
    if status not in ('completed', 'paid'):
        return None
    financial_fields = {'amount', 'late_fee', 'total_due', 'total_paid'}
    if 'status' not in payload and not financial_fields.intersection(payload):
        return None
    if existing.get('record_type') == 'checkout_attempt' or existing.get('invoice_id'):
        raise HTTPException(409, 'Confirma el pago en la factura de renta, no en el intento de cobro.')

    merged = {**existing, **updates}
    amount = payment_money(merged.get('amount', 0), 'amount')
    fee = payment_money(merged.get('late_fee', 0), 'late_fee')
    due = round(amount + fee, 2)
    if due <= 0:
        raise HTTPException(400, 'El total de la renta debe ser mayor a cero.')
    if 'total_due' in payload and payment_money(payload['total_due'], 'total_due') != due:
        raise HTTPException(409, 'El total debe coincidir con la renta más el recargo.')

    was_paid = existing.get('status') in ('completed', 'paid') or existing.get('paid') is True
    if 'total_paid' in payload:
        received = payment_money(payload['total_paid'], 'total_paid')
    elif was_paid and existing.get('total_paid') is not None:
        received = payment_money(existing['total_paid'], 'total_paid')
    else:
        received = due
    if received != due:
        raise HTTPException(409, 'El importe recibido debe cubrir exactamente la renta y el recargo para marcarla pagada.')
    if not str(merged.get('payment_method') or '').strip():
        raise HTTPException(400, 'Selecciona el método del pago confirmado.')

    updates.update(total_due=due, total_paid=received, paid=True)
    changed = (not was_paid or existing.get('total_paid') != received
               or existing.get('amount') != amount or existing.get('late_fee', 0) != fee
               or existing.get('payment_method') != merged.get('payment_method'))
    if not changed:
        return None
    actor = str(admin.get('email') or admin.get('_id') or 'admin')
    updates.update(confirmation_source='admin_manual', confirmed_by=actor, confirmed_at=now)
    attempt = existing.get('charge_attempt') or {}
    if attempt.get('status') in ('processing', 'pending', 'unknown', 'review_required'):
        updates['provider_reconciliation_required'] = True
    return {
        'source': 'admin_manual', 'confirmed_by': actor, 'confirmed_at': now,
        'amount': received, 'payment_method': merged['payment_method'],
        'previous_status': existing.get('status'),
        'previous_total_paid': existing.get('total_paid'),
        'previous_total_due': existing.get('total_due'),
    }
