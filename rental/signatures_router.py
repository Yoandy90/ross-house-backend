"""
Signature Management Router
Handles digital signatures (touch + Topaz pad) for contracts and documents.
"""
from fastapi import APIRouter, HTTPException, Request
from datetime import datetime, timezone
from bson import ObjectId
from .shared import get_db, auth_admin, auth_marketplace, serialize

router = APIRouter()


# ─── Helper ────────────────────────────────────────────
def _serialize_sig(doc):
    """Serialize a signature document for API response."""
    if not doc:
        return None
    d = serialize(doc)
    # Don't send full base64 in list views (too heavy)
    if 'signature_data' in d:
        d['has_signature'] = bool(d['signature_data'])
        d['signature_preview'] = d['signature_data'][:80] + '...' if d.get('signature_data') else None
    return d


# ─── List pending documents that need signatures ──────
@router.get('/signatures/pending')
async def get_pending_signatures(request: Request):
    """Get documents pending the current user's signature."""
    user = await auth_marketplace(request)
    db = get_db()
    user_id = str(user['_id'])
    role = user.get('role', 'tenant')

    pending = []

    # Check contracts/leases that need signatures
    contracts = db.rental_contracts.find({
        '$or': [
            {'tenant_id': user_id},
            {'landlord_id': user_id},
            {'admin_id': user_id},
        ],
        'status': {'$in': ['pending_signatures', 'pending_tenant', 'pending_landlord', 'active', 'draft']}
    }).sort('created_at', -1).limit(20)

    for contract in contracts:
        c = serialize(contract)
        needs_sig = False
        signed_by_me = False

        if role == 'tenant' and c.get('tenant_id') == user_id:
            signed_by_me = bool(c.get('tenant_signature'))
            needs_sig = not signed_by_me and c.get('status') in ['pending_signatures', 'pending_tenant']
        elif role == 'landlord' and c.get('landlord_id') == user_id:
            signed_by_me = bool(c.get('landlord_signature'))
            needs_sig = not signed_by_me and c.get('status') in ['pending_signatures', 'pending_landlord']
        elif role == 'admin':
            signed_by_me = bool(c.get('admin_signature'))
            needs_sig = not signed_by_me

        pending.append({
            'id': c['id'],
            'type': 'contract',
            'title': f"Contrato - {c.get('property_address', 'Propiedad')}",
            'description': f"Renta: ${c.get('rent_amount', 0)}/mes",
            'status': 'pending' if needs_sig else ('signed' if signed_by_me else 'waiting'),
            'needs_my_signature': needs_sig,
            'signed_by_me': signed_by_me,
            'property_address': c.get('property_address', ''),
            'created_at': c.get('created_at', ''),
            'parties': {
                'tenant': c.get('tenant_name', ''),
                'landlord': c.get('landlord_name', ''),
            },
            'signatures': {
                'admin': bool(c.get('admin_signature')),
                'tenant': bool(c.get('tenant_signature')),
                'landlord': bool(c.get('landlord_signature')),
            }
        })

    # Check standalone documents (legal docs, addendums, etc.)
    docs = db.signature_documents.find({
        '$or': [
            {'recipient_id': user_id},
            {'created_by': user_id},
        ]
    }).sort('created_at', -1).limit(20)

    for doc in docs:
        d = serialize(doc)
        sigs = d.get('signatures', [])
        my_sig = next((s for s in sigs if s.get('signer_id') == user_id), None)

        pending.append({
            'id': d['id'],
            'type': 'document',
            'title': d.get('title', 'Documento'),
            'description': d.get('description', ''),
            'status': 'signed' if my_sig else 'pending',
            'needs_my_signature': not my_sig,
            'signed_by_me': bool(my_sig),
            'created_at': d.get('created_at', ''),
            'parties': {},
            'signatures': {},
        })

    # Sort: pending first, then by date
    pending.sort(key=lambda x: (0 if x['needs_my_signature'] else 1, x.get('created_at', '')), reverse=False)

    return {
        'success': True,
        'documents': pending,
        'total': len(pending),
        'pending_count': sum(1 for p in pending if p['needs_my_signature']),
    }


# ─── Submit a signature ──────────────────────────────
@router.post('/signatures/sign')
async def submit_signature(request: Request):
    """Submit a signature for a document or contract."""
    user = await auth_marketplace(request)
    db = get_db()
    body = await request.json()

    document_id = body.get('document_id')
    document_type = body.get('document_type', 'contract')
    signature_data = body.get('signature_data')  # base64 image
    signature_method = body.get('method', 'touch')  # 'touch' or 'topaz'
    signer_name = body.get('signer_name', user.get('name', ''))

    if not document_id or not signature_data:
        raise HTTPException(400, "document_id y signature_data son requeridos")

    user_id = str(user['_id'])
    role = user.get('role', 'tenant')
    now = datetime.now(timezone.utc)

    # Create signature record
    sig_record = {
        'document_id': document_id,
        'document_type': document_type,
        'signer_id': user_id,
        'signer_name': signer_name,
        'signer_role': role,
        'signer_email': user.get('email', ''),
        'signature_data': signature_data,
        'method': signature_method,  # 'touch' | 'topaz'
        'device_info': body.get('device_info', ''),
        'ip_address': request.client.host if request.client else '',
        'signed_at': now,
        'created_at': now,
    }

    # Save the signature record
    result = db.signatures.insert_one(sig_record)
    sig_id = str(result.inserted_id)

    # Update the source document
    if document_type == 'contract':
        update_field = f"{role}_signature"
        update_data = {
            update_field: signature_data,
            f"{update_field}_date": now,
            f"{update_field}_method": signature_method,
            'updated_at': now,
        }

        # Determine new status
        contract = db.rental_contracts.find_one({'_id': ObjectId(document_id)})
        if contract:
            sigs_after = {
                'admin': contract.get('admin_signature') or (signature_data if role == 'admin' else None),
                'tenant': contract.get('tenant_signature') or (signature_data if role == 'tenant' else None),
                'landlord': contract.get('landlord_signature') or (signature_data if role == 'landlord' else None),
            }
            all_signed = all(sigs_after.values())
            if all_signed:
                update_data['status'] = 'active'
            elif role == 'tenant':
                update_data['status'] = 'pending_landlord'
            elif role == 'landlord':
                update_data['status'] = 'pending_tenant'

            db.rental_contracts.update_one(
                {'_id': ObjectId(document_id)},
                {'$set': update_data}
            )

    elif document_type == 'document':
        db.signature_documents.update_one(
            {'_id': ObjectId(document_id)},
            {
                '$push': {'signatures': {**sig_record, '_id': ObjectId(sig_id)}},
                '$set': {'updated_at': now}
            }
        )

    return {
        'success': True,
        'signature_id': sig_id,
        'message': 'Firma registrada exitosamente',
        'method': signature_method,
    }


# ─── Get signature history ──────────────────────────
@router.get('/signatures/history')
async def get_signature_history(request: Request):
    """Get the user's signature history."""
    user = await auth_marketplace(request)
    db = get_db()
    user_id = str(user['_id'])

    sigs = list(db.signatures.find(
        {'signer_id': user_id}
    ).sort('signed_at', -1).limit(50))

    history = []
    for sig in sigs:
        s = serialize(sig)
        history.append({
            'id': s['id'],
            'document_id': s.get('document_id'),
            'document_type': s.get('document_type'),
            'method': s.get('method', 'touch'),
            'signed_at': s.get('signed_at'),
            'signer_name': s.get('signer_name'),
        })

    return {
        'success': True,
        'signatures': history,
        'total': len(history),
    }


# ─── Admin: Get all pending signatures across all users ──
@router.get('/admin/signatures/overview')
async def admin_signatures_overview(request: Request):
    """Admin view: overview of all signature activity."""
    await auth_admin(request)
    db = get_db()

    # Count pending contracts
    pending_contracts = db.rental_contracts.count_documents({
        'status': {'$in': ['pending_signatures', 'pending_tenant', 'pending_landlord']}
    })

    # Count total signatures this month
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    monthly_sigs = db.signatures.count_documents({
        'signed_at': {'$gte': month_start}
    })

    # Count by method
    touch_count = db.signatures.count_documents({'method': 'touch'})
    topaz_count = db.signatures.count_documents({'method': 'topaz'})

    # Recent signatures
    recent = list(db.signatures.find().sort('signed_at', -1).limit(10))
    recent_list = []
    for sig in recent:
        s = serialize(sig)
        recent_list.append({
            'id': s['id'],
            'signer_name': s.get('signer_name', ''),
            'signer_role': s.get('signer_role', ''),
            'method': s.get('method', 'touch'),
            'document_type': s.get('document_type', ''),
            'signed_at': s.get('signed_at', ''),
        })

    return {
        'success': True,
        'pending_contracts': pending_contracts,
        'monthly_signatures': monthly_sigs,
        'total_signatures': {
            'touch': touch_count,
            'topaz': topaz_count,
            'total': touch_count + topaz_count,
        },
        'recent': recent_list,
    }
