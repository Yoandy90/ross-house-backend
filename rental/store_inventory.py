"""Stable item identity, sale presentation and append-only stock movements."""
from decimal import Decimal, InvalidOperation
from fastapi import HTTPException
from rental import resident_store as s


def sku_for(pid, product):
    return product.get('sku') or ('RH-' + pid.replace('-', '').upper())


def validate_product(state, pid, product):
    product['sku'] = (product.get('sku') or sku_for(pid, product)).strip().upper()
    for other_id, other in state['products'].items():
        if other_id == pid:
            continue
        if sku_for(other_id, other).upper() == product['sku']:
            raise HTTPException(409, 'store_sku_exists')
        if product.get('barcode') and product['barcode'] == other.get('barcode'):
            raise HTTPException(409, 'store_barcode_exists')
    if product.get('tax_class') and not any(t['id'] == product['tax_class'] for t in state['settings'].get('tax_classes', [])):
        raise HTTPException(400, 'store_tax_class_missing')
    try:
        size = Decimal(product.get('pack_size') or '1')
        if not size.is_finite() or not Decimal('0.001') <= size <= Decimal('100000'):
            raise ValueError()
    except (ValueError, InvalidOperation):
        raise HTTPException(400, 'store_pack_size_invalid')
    # A sellable item is a fixed presentation. Price and stock count presentations.
    product['pack_size'] = format(size.normalize(), 'f')
    return product


def tax_rate(state, product):
    key = product.get('tax_class')
    if not key:
        return product['tax_bps']
    profile = next((t for t in state['settings'].get('tax_classes', []) if t['id'] == key), None)
    if profile is None:
        raise HTTPException(409, 'store_tax_class_missing')
    return profile['rate_bps']


def movement(state, actor, pid, delta, reason, order_id=''):
    if not delta:
        return
    product = state['products'][pid]
    s.audit(state, actor, 'inventory_movement', {
        'product_id': pid, 'sku': sku_for(pid, product), 'quantity_delta': delta,
        'available_after': product['stock'], 'reason': reason, 'order_id': order_id,
        'unit_cost_cents': product['cost_cents'],
    })
