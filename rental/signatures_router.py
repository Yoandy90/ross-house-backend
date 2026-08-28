"""Signature Management Router — authenticated, contract-bound signatures."""
import re
from fastapi import APIRouter, HTTPException, Request
from datetime import datetime, timezone
from bson import ObjectId
from .shared import get_db, auth_admin, auth_marketplace, serialize

router = APIRouter()
_ALLOWED_CONTRACT_SIGNER_ROLES = {"tenant", "landlord", "admin"}
_ALLOWED_DOCUMENT_TYPES = {"contract", "document"}


async def _my_ids(user, db) -> list:
    ids = [str(user['_id'])]
    email = (user.get('email') or '').strip().lower()
    if email:
        tenant_doc = await db.tenants.find_one({"email": {"$regex": f"^{re.escape(email)}$", "$options": "i"}})
        if tenant_doc:
            ids.append(str(tenant_doc['_id']))
    return list(dict.fromkeys(ids))


def _effective_role(user) -> str:
    role = str(user.get('role', 'tenant') or 'tenant').strip().lower()
    return 'tenant' if role in ('tenant', 'client') else role


def _actor_name(user: dict, role: str) -> str:
    name = str(user.get('name') or user.get('full_name') or '').strip()
    if name:
        return name
    joined = f"{str(user.get('first_name') or '').strip()} {str(user.get('last_name') or '').strip()}".strip()
    return joined or str(user.get('email') or '').strip().lower() or role


def _norm_id(value) -> str:
    return str(value or '').strip()


async def _authorize_contract_signer(user: dict, contract: dict, db) -> tuple[str, list[str]]:
    role = _effective_role(user)
    if role not in _ALLOWED_CONTRACT_SIGNER_ROLES:
        raise HTTPException(403, "Rol no autorizado para firmar contratos")
    user_id = _norm_id(user.get('_id'))
    my_ids = await _my_ids(user, db)
    if role == 'tenant':
        if _norm_id(contract.get('tenant_id')) not in my_ids:
            raise HTTPException(403, "No autorizado para firmar este contrato")
    elif role == 'landlord':
        landlord_id = _norm_id(contract.get('landlord_id'))
        if not landlord_id or landlord_id != user_id:
            raise HTTPException(403, "No autorizado para firmar este contrato")
    return role, my_ids


async def _authorize_standalone_document(user: dict, document: dict, db) -> None:
    role = _effective_role(user)
    if role == 'admin':
        return
    user_id = _norm_id(user.get('_id'))
    my_ids = await _my_ids(user, db)
    recipient_id = document.get('recipient_id')
    if isinstance(recipient_id, list):
        is_recipient = bool({_norm_id(x) for x in recipient_id}.intersection(my_ids))
    else:
        is_recipient = _norm_id(recipient_id) in my_ids
    if not is_recipient and _norm_id(document.get('created_by')) != user_id:
        raise HTTPException(403, "No autorizado para firmar este documento")


@router.get('/signatures/pending')
async def get_pending_signatures(request: Request):
    user = await auth_marketplace(request)
    db = get_db()
    user_id = str(user['_id'])
    role = _effective_role(user)
    my_ids = await _my_ids(user, db)
    pending = []
    contracts = await db.rental_contracts.find({
        '$or': [{'tenant_id': {'$in': my_ids}}, {'landlord_id': user_id}, {'admin_id': user_id}],
        'status': {'$in': ['pending_signatures', 'pending_tenant', 'pending_landlord',
                           'pending_activation', 'active', 'draft']}
    }).sort('created_at', -1).to_list(20)
    for contract in contracts:
        c = serialize(contract)
        needs_sig = False
        signed_by_me = False
        if role == 'tenant' and c.get('tenant_id') in my_ids:
            signed_by_me = bool(c.get('tenant_signature'))
            needs_sig = not signed_by_me and c.get('status') in ['pending_signatures', 'pending_tenant']
        elif role == 'landlord' and c.get('landlord_id') == user_id:
            signed_by_me = bool(c.get('landlord_signature'))
            needs_sig = not signed_by_me and c.get('status') in ['pending_signatures', 'pending_landlord']
        elif role == 'admin':
            signed_by_me = bool(c.get('admin_signature'))
            needs_sig = not signed_by_me and c.get('status') != 'active'
        pending.append({
            'id': c['id'], 'type': 'contract',
            'title': f"Contrato - {c.get('property_address', 'Propiedad')}",
            'description': f"Renta: ${c.get('rent_amount', 0)}/mes",
            'status': 'pending' if needs_sig else ('signed' if signed_by_me else 'waiting'),
            'needs_my_signature': needs_sig, 'signed_by_me': signed_by_me,
            'property_address': c.get('property_address', ''), 'created_at': c.get('created_at', ''),
            'parties': {'tenant': c.get('tenant_name', ''), 'landlord': c.get('landlord_name', '')},
            'signatures': {'admin': bool(c.get('admin_signature')), 'tenant': bool(c.get('tenant_signature')),
                           'landlord': bool(c.get('landlord_signature'))},
        })
    docs = await db.signature_documents.find({
        '$or': [{'recipient_id': {'$in': my_ids}}, {'created_by': user_id}]
    }).sort('created_at', -1).to_list(20)
    for doc in docs:
        d = serialize(doc)
        my_sig = next((s for s in d.get('signatures', []) if s.get('signer_id') == user_id), None)
        pending.append({'id': d['id'], 'type': 'document', 'title': d.get('title', 'Documento'),
                        'description': d.get('description', ''), 'status': 'signed' if my_sig else 'pending',
                        'needs_my_signature': not my_sig, 'signed_by_me': bool(my_sig),
                        'created_at': d.get('created_at', ''), 'parties': {}, 'signatures': {}})
    pending.sort(key=lambda x: (0 if x['needs_my_signature'] else 1, str(x.get('created_at', ''))))
    return {'success': True, 'documents': pending, 'total': len(pending),
            'pending_count': sum(1 for p in pending if p['needs_my_signature'])}


@router.post('/signatures/sign')
async def submit_signature(request: Request):
    user = await auth_marketplace(request)
    db = get_db()
    body = await request.json()
    document_id = str(body.get('document_id') or '').strip()
    document_type = str(body.get('document_type', 'contract') or '').strip().lower()
    signature_data = body.get('signature_data')
    signature_method = body.get('method', 'touch')
    if not document_id or not signature_data:
        raise HTTPException(400, "document_id y signature_data son requeridos")
    if not isinstance(signature_data, str) or not signature_data.startswith('data:image/'):
        raise HTTPException(400, "signature_data inválida")
    if document_type not in _ALLOWED_DOCUMENT_TYPES:
        raise HTTPException(400, "document_type inválido")
    try:
        object_id = ObjectId(document_id)
    except Exception:
        raise HTTPException(400, "document_id inválido")

    user_id = str(user['_id'])
    role = _effective_role(user)
    now = datetime.now(timezone.utc)
    contract = None
    if document_type == 'contract':
        contract = await db.rental_contracts.find_one({'_id': object_id})
        if not contract:
            raise HTTPException(404, "Contrato no encontrado")
        role, _ = await _authorize_contract_signer(user, contract, db)
    else:
        source_document = await db.signature_documents.find_one({'_id': object_id})
        if not source_document:
            raise HTTPException(404, "Documento no encontrado")
        await _authorize_standalone_document(user, source_document, db)

    sig_record = {
        'document_id': document_id, 'document_type': document_type, 'signer_id': user_id,
        'signer_name': _actor_name(user, role), 'signer_role': role, 'signer_email': user.get('email', ''),
        'signature_data': signature_data, 'method': signature_method,
        'device_info': body.get('device_info', ''), 'ip_address': request.client.host if request.client else '',
        'signed_at': now, 'created_at': now,
    }
    result = await db.signatures.insert_one(sig_record)
    sig_id = str(result.inserted_id)

    if document_type == 'contract':
        update_field = f"{role}_signature"
        update_data = {update_field: signature_data, f"{update_field}_date": now,
                       f"{update_field}_method": signature_method, 'updated_at': now}
        sigs_after = {
            'admin': contract.get('admin_signature') or (signature_data if role == 'admin' else None),
            'tenant': contract.get('tenant_signature') or (signature_data if role == 'tenant' else None),
        }
        if contract.get('landlord_id'):
            sigs_after['landlord'] = contract.get('landlord_signature') or (signature_data if role == 'landlord' else None)
        all_signed = all(sigs_after.values())
        if all_signed:
            # Signing is evidence, not occupancy authority. The lifecycle route
            # performs the separate CAS-protected activation/occupancy claim.
            update_data['status'] = 'pending_activation'
            update_data['signed_at'] = now
        elif role == 'tenant':
            update_data['status'] = 'pending_signatures'
        write_filter = {'_id': object_id, 'updated_at': contract.get('updated_at')}
        if contract.get('updated_at') is None:
            write_filter = {'_id': object_id, 'updated_at': {'$exists': False}}
        write_result = await db.rental_contracts.update_one(write_filter, {'$set': update_data})
        if getattr(write_result, 'matched_count', 0) != 1:
            await db.signatures.update_one({'_id': result.inserted_id},
                {'$set': {'source_write_status': 'conflict', 'source_write_conflict_at': now}})
            raise HTTPException(409, "signature_source_state_changed")
    else:
        write_result = await db.signature_documents.update_one(
            {'_id': object_id},
            {'$push': {'signatures': {**sig_record, '_id': ObjectId(sig_id)}}, '$set': {'updated_at': now}})
        if getattr(write_result, 'matched_count', 0) != 1:
            await db.signatures.update_one({'_id': result.inserted_id},
                {'$set': {'source_write_status': 'missing', 'source_write_conflict_at': now}})
            raise HTTPException(409, "signature_source_state_changed")
    return {'success': True, 'signature_id': sig_id, 'message': 'Firma registrada exitosamente',
            'method': signature_method}


@router.get('/signatures/history')
async def get_signature_history(request: Request):
    user = await auth_marketplace(request)
    db = get_db()
    user_id = str(user['_id'])
    sigs = await db.signatures.find({'signer_id': user_id}).sort('signed_at', -1).to_list(50)
    history = []
    for sig in sigs:
        s = serialize(sig)
        history.append({'id': s['id'], 'document_id': s.get('document_id'),
                        'document_type': s.get('document_type'), 'method': s.get('method', 'touch'),
                        'signed_at': s.get('signed_at'), 'signer_name': s.get('signer_name')})
    return {'success': True, 'signatures': history, 'total': len(history)}


@router.get('/admin/signatures/overview')
async def admin_signatures_overview(request: Request):
    await auth_admin(request)
    db = get_db()
    pending_contracts = await db.rental_contracts.count_documents({
        'status': {'$in': ['pending_signatures', 'pending_tenant', 'pending_landlord', 'pending_activation']}})
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    monthly_sigs = await db.signatures.count_documents({'signed_at': {'$gte': month_start}})
    touch_count = await db.signatures.count_documents({'method': 'touch'})
    topaz_count = await db.signatures.count_documents({'method': 'topaz'})
    recent = await db.signatures.find().sort('signed_at', -1).to_list(10)
    recent_list = []
    for sig in recent:
        s = serialize(sig)
        recent_list.append({'id': s['id'], 'signer_name': s.get('signer_name', ''),
                            'signer_role': s.get('signer_role', ''), 'method': s.get('method', 'touch'),
                            'document_type': s.get('document_type', ''), 'signed_at': s.get('signed_at', '')})
    return {'success': True, 'pending_contracts': pending_contracts, 'monthly_signatures': monthly_sigs,
            'total_signatures': {'touch': touch_count, 'topaz': topaz_count,
                                 'total': touch_count + topaz_count}, 'recent': recent_list}
