"""
Rental Contracts Router
========================
"""
import logging
from datetime import datetime, timedelta
from typing import Optional, List
from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request
import io
import base64
import uuid
import os
from rental.shared import (
    get_db, auth_admin, auth_marketplace, auth_tenant,
    serialize, create_marketplace_token, create_tenant_token,
    send_rental_push_to_user, send_rental_push_to_admins,
    TENANT_JWT_SECRET,
)

router = APIRouter()

@router.post('/admin/leases')
async def admin_create_lease(request: Request):
    """Admin: Create a new lease contract for a property"""
    await auth_admin(request)
    data = await request.json()

    required = ["property_id", "tenant_name", "tenant_email", "start_date", "end_date", "rent_amount"]
    for f in required:
        if not data.get(f):
            raise HTTPException(status_code=400, detail=f"Campo requerido: {f}")

    lease = {
        "property_id": data.get("property_id", ""),
        "property_address": data.get("property_address", ""),
        "tenant_id": data.get("tenant_id", ""),
        "tenant_name": data.get("tenant_name", ""),
        "tenant_email": data.get("tenant_email", ""),
        "landlord_id": data.get("landlord_id", ""),
        "lease_type": data.get("lease_type", "residential"),
        "start_date": data.get("start_date", ""),
        "end_date": data.get("end_date", ""),
        "rent_amount": float(data.get("rent_amount", 0)),
        "deposit_amount": float(data.get("deposit_amount", 0)),
        "terms": data.get("terms", ""),
        "clauses": data.get("clauses", []),
        "tenant_signature": None,
        "tenant_signed_at": None,
        "landlord_signature": None,
        "landlord_signed_at": None,
        "admin_signature": data.get("admin_signature", None),
        "admin_signed_at": datetime.utcnow() if data.get("admin_signature") else None,
        "status": "pending_tenant",
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
    }

    result = await get_db().rental_contracts.insert_one(lease)
    return {
        "success": True,
        "lease_id": str(result.inserted_id),
        "message": "Contrato creado. Pendiente firma del inquilino."
    }


@router.get('/admin/leases')
async def admin_list_leases(request: Request):
    """Admin: List all lease contracts (now backed by rental_contracts)."""
    await auth_admin(request)
    status_filter = request.query_params.get("status", "all")

    query = {}
    if status_filter != "all":
        query["status"] = status_filter

    cursor = get_db().rental_contracts.find(query).sort("created_at", -1)
    leases = []
    async for l in cursor:
        doc = serialize(l)
        leases.append({
            "id": doc.get("_id"),
            "property_address": doc.get("property_address", ""),
            "tenant_name": doc.get("tenant_name", ""),
            "tenant_email": doc.get("tenant_email", ""),
            "lease_type": doc.get("lease_type", "residential"),
            "start_date": doc.get("start_date", ""),
            "end_date": doc.get("end_date", ""),
            "rent_amount": doc.get("rent_amount", doc.get("monthly_rent", 0)),
            "status": doc.get("status", "draft"),
            "has_tenant_signature": bool(doc.get("tenant_signature")),
            "has_landlord_signature": bool(doc.get("landlord_signature") or doc.get("admin_signature")),
            "has_admin_signature": bool(doc.get("admin_signature")),
            "created_at": doc.get("created_at", ""),
        })

    return {"success": True, "leases": leases, "count": len(leases)}


@router.get('/admin/leases/{lease_id}')
async def admin_get_lease(lease_id: str, request: Request):
    """Admin: Get full lease details (now backed by rental_contracts)."""
    await auth_admin(request)
    try:
        lease = await get_db().rental_contracts.find_one({"_id": ObjectId(lease_id)})
    except Exception:
        raise HTTPException(status_code=400, detail="ID de contrato inválido")

    if not lease:
        raise HTTPException(status_code=404, detail="Contrato no encontrado")

    doc = serialize(lease)
    return {"success": True, "lease": doc}


@router.post('/lease/{lease_id}/sign')
async def sign_lease(lease_id: str, request: Request):
    """Sign a lease contract (tenant, landlord, or admin) — uses rental_contracts."""
    data = await request.json()
    signature = data.get("signature", "")
    signer_role = data.get("role", "")
    signer_name = data.get("name", "")
    signer_email = data.get("email", "")

    if not signature or not signature.startswith("data:image/"):
        raise HTTPException(status_code=400, detail="Firma digital requerida")
    if signer_role not in ["tenant", "landlord", "admin"]:
        raise HTTPException(status_code=400, detail="Rol de firmante inválido")

    try:
        lease = await get_db().rental_contracts.find_one({"_id": ObjectId(lease_id)})
    except Exception:
        raise HTTPException(status_code=400, detail="ID de contrato inválido")

    if not lease:
        raise HTTPException(status_code=404, detail="Contrato no encontrado")

    update = {"updated_at": datetime.utcnow()}

    if signer_role == "tenant":
        if lease.get("status") not in ["pending_tenant", "pending_signatures"]:
            raise HTTPException(status_code=400, detail="Este contrato no está pendiente de firma del inquilino")
        update["tenant_signature"] = signature
        update["tenant_signed_at"] = datetime.utcnow()
        update["tenant_signer_name"] = signer_name
        # Determine next status
        if lease.get("landlord_id") and not lease.get("landlord_signature"):
            update["status"] = "pending_landlord"
        else:
            update["status"] = "active"

    elif signer_role == "landlord":
        if lease.get("status") not in ["pending_landlord", "pending_signatures"]:
            raise HTTPException(status_code=400, detail="Este contrato no está pendiente de firma del propietario")
        update["landlord_signature"] = signature
        update["landlord_signed_at"] = datetime.utcnow()
        update["landlord_signer_name"] = signer_name
        if lease.get("tenant_signature"):
            update["status"] = "active"
        else:
            update["status"] = "pending_tenant"

    elif signer_role == "admin":
        update["admin_signature"] = signature
        update["admin_signed_at"] = datetime.utcnow()
        update["admin_signer_name"] = signer_name

    await get_db().rental_contracts.update_one({"_id": ObjectId(lease_id)}, {"$set": update})

    return {
        "success": True,
        "new_status": update.get("status", lease.get("status")),
        "message": f"Firma de {signer_role} guardada exitosamente"
    }


@router.get('/my-leases')
async def get_my_leases(request: Request):
    """Get contracts for the authenticated user from `rental_contracts`.

    Resolves all tenant_ids that match the user (direct id, via tenants link,
    or via email/phone) and returns every matching contract.
    """
    import re as _re
    from bson import ObjectId

    user = await auth_marketplace(request)
    db = get_db()
    user_email = (user.get("email") or "").strip().lower()
    user_id = str(user.get("_id", ""))
    role = user.get("role", "tenant")

    # Resolve tenant_ids to try (direct + via tenants._id + email)
    tenant_ids = {user_id}
    if user_id:
        async for t in db.tenants.find({"app_user_id": user_id}):
            tenant_ids.add(str(t["_id"]))
    if user_email:
        async for t in db.tenants.find({
            "email": {"$regex": f"^{_re.escape(user_email)}$", "$options": "i"}
        }):
            tenant_ids.add(str(t["_id"]))
    tenant_ids_list = list(tenant_ids)

    leases: list = []
    seen_ids: set = set()

    contract_query = {
        "$or": [
            {"tenant_id": {"$in": tenant_ids_list}},
            {"tenant_email": user_email} if user_email else {"_id": None},
            {"landlord_id": user_id} if user_id else {"_id": None},
        ]
    }
    async for c in db.rental_contracts.find(contract_query).sort("created_at", -1):
        doc = serialize(c)
        cid = doc.get("_id")
        if cid in seen_ids:
            continue
        seen_ids.add(cid)

        # Fetch property address if not embedded
        prop_address = doc.get("property_address", "")
        if not prop_address and doc.get("property_id"):
            try:
                prop = await db.properties.find_one({"_id": ObjectId(doc["property_id"])})
                if prop:
                    prop_address = prop.get("address") or prop.get("name") or ""
            except Exception:
                pass

        leases.append({
            "id": cid,
            "property_address": prop_address,
            "lease_type": doc.get("lease_type", "residential"),
            "start_date": doc.get("start_date", ""),
            "end_date": doc.get("end_date", ""),
            "rent_amount": doc.get("monthly_rent") or doc.get("rent_amount", 0),
            "deposit_amount": doc.get("deposit_amount", 0) or doc.get("security_deposit", 0),
            "terms": doc.get("terms", ""),
            "clauses": doc.get("clauses", []),
            "status": doc.get("status", "draft"),
            "has_tenant_signature": bool(doc.get("tenant_signature")),
            "has_landlord_signature": bool(doc.get("landlord_signature") or doc.get("admin_signature")),
            "has_admin_signature": bool(doc.get("admin_signature")),
            "tenant_signature": doc.get("tenant_signature") if role == "tenant" else None,
            "landlord_signature": doc.get("landlord_signature") if role == "landlord" else None,
            "created_at": doc.get("created_at", ""),
            "source": "rental_contracts",
        })

    return {"success": True, "leases": leases, "count": len(leases)}


@router.get('/lease/{lease_id}')
async def get_lease_detail(lease_id: str, request: Request):
    """Get full lease details for signing"""
    import re as _re
    user = await auth_marketplace(request)
    user_email = (user.get("email") or "").strip().lower()
    user_id = str(user.get("_id", ""))
    db = get_db()

    try:
        oid = ObjectId(lease_id)
    except Exception:
        raise HTTPException(status_code=400, detail="ID inválido")

    # Lookup in rental_contracts (unified collection)
    lease = await db.rental_contracts.find_one({"_id": oid})
    source = "rental_contracts"

    if not lease:
        raise HTTPException(status_code=404, detail="Contrato no encontrado")

    doc = serialize(lease)
    doc["source"] = source

    # Resolve tenant_ids the user might match against (direct + via tenants doc)
    tenant_ids = {user_id}
    async for t in db.tenants.find({"app_user_id": user_id}):
        tenant_ids.add(str(t["_id"]))
    if user_email:
        async for t in db.tenants.find({
            "email": {"$regex": f"^{_re.escape(user_email)}$", "$options": "i"}
        }):
            tenant_ids.add(str(t["_id"]))

    is_tenant = (
        doc.get("tenant_email", "").lower() == user_email
        or doc.get("tenant_id") in tenant_ids
    )
    is_landlord = (doc.get("landlord_id") == user_id)
    if not is_tenant and not is_landlord:
        raise HTTPException(status_code=403, detail="No tienes acceso a este contrato")

    # Normalize fields for the frontend (rental_contracts uses monthly_rent)
    if source == "rental_contracts":
        doc.setdefault("rent_amount", doc.get("monthly_rent", 0))
        # Enrich property_address from properties collection
        if not doc.get("property_address") and doc.get("property_id"):
            try:
                prop = await db.properties.find_one({"_id": ObjectId(doc["property_id"])})
                if prop:
                    doc["property_address"] = prop.get("address") or prop.get("name") or ""
            except Exception:
                pass

    return {"success": True, "lease": doc}





@router.get('/admin/rental-contracts')
async def list_contracts(request: Request):
    """List all rental contracts"""
    user = await auth_admin(request)
    from urllib.parse import parse_qs
    params = parse_qs(str(request.url.query))
    status_filter = params.get('status', [None])[0]

    query = {}
    if status_filter:
        query['status'] = status_filter

    cursor = get_db().rental_contracts.find(query).sort("created_at", -1)
    contracts = []
    async for c in cursor:
        contracts.append(serialize(c))

    return {"success": True, "contracts": contracts, "count": len(contracts)}


@router.post('/admin/rental-contracts')
async def create_contract(request: Request):
    """Create a rental contract linking a tenant to a property"""
    user = await auth_admin(request)
    data = await request.json()
    now = datetime.utcnow()

    property_id = data.get('property_id')
    tenant_id = data.get('tenant_id')

    if not property_id or not tenant_id:
        raise HTTPException(status_code=400, detail="Se requiere property_id y tenant_id")

    prop = await get_db().properties.find_one({"_id": ObjectId(property_id)})
    if not prop:
        raise HTTPException(status_code=404, detail="Propiedad no encontrada")

    tenant = await get_db().tenants.find_one({"_id": ObjectId(tenant_id)})
    if not tenant:
        raise HTTPException(status_code=404, detail="Inquilino no encontrado")

    count = await get_db().rental_contracts.count_documents({})
    contract_number = f"CONT-{now.year}-{str(count + 1).zfill(3)}"

    rent_amount = float(data.get('rent_amount', prop.get('rent_amount', 0)))
    deposit_amount = float(data.get('deposit_amount', prop.get('deposit_amount', 0)))

    # Build addendums from request data or defaults from rental config
    addendums_data = data.get('addendums', {})
    if not addendums_data:
        # Try to load defaults from rental config
        try:
            rental_cfg = await get_db().rental_config.find_one({"type": "company"})
            if rental_cfg and rental_cfg.get('lease_clauses'):
                lc = rental_cfg['lease_clauses']
                addendums_data = {
                    'mold': lc.get('mold_addendum', True),
                    'bedbug': lc.get('bedbug_addendum', True),
                    'military': lc.get('military_scra', True),
                    'lead_paint': lc.get('lead_paint', False),
                    'pets': lc.get('pet_addendum', False),
                }
                if addendums_data.get('pets') and rental_cfg.get('pet_defaults'):
                    addendums_data['pet_details'] = rental_cfg['pet_defaults']
            else:
                addendums_data = {'mold': True, 'bedbug': True, 'military': True, 'lead_paint': False, 'pets': False}
        except Exception:
            addendums_data = {'mold': True, 'bedbug': True, 'military': True, 'lead_paint': False, 'pets': False}

    contract_doc = {
        "contract_number": contract_number,
        "property_id": property_id,
        "property_address": prop.get('address', ''),
        "property_number": prop.get('property_number', ''),
        "tenant_id": tenant_id,
        "tenant_name": tenant.get('name', ''),
        "tenant_phone": tenant.get('phone', ''),
        "tenant_email": tenant.get('email', ''),
        "start_date": data.get('start_date', now.strftime('%Y-%m-%d')),
        "end_date": data.get('end_date', ''),
        "rent_amount": rent_amount,
        "deposit_amount": deposit_amount,
        "payment_due_day": int(data.get('payment_due_day', 1)),
        "late_fee_amount": float(data.get('late_fee_amount', 50)),
        "late_fee_grace_days": int(data.get('late_fee_grace_days', 5)),
        "terms": data.get('terms', ''),
        "special_conditions": data.get('special_conditions', ''),
        "payment_method_type": data.get('payment_method_type', 'cash'),
        "customer_vault_id": data.get('customer_vault_id', ''),
        "vault_display": data.get('vault_display', ''),
        "vault_customer_name": data.get('vault_customer_name', ''),
        "addendums": addendums_data,
        "status": data.get('status', 'draft'),  # draft, active, expired, terminated
        "signature": None,
        "signature_status": "pending",
        "created_at": now,
        "updated_at": now,
        "created_by": user.get('email', 'admin'),
    }

    result = await get_db().rental_contracts.insert_one(contract_doc)
    contract_id = str(result.inserted_id)

    # If contract is active, update property and tenant
    if contract_doc['status'] == 'active':
        await get_db().properties.update_one(
            {"_id": ObjectId(property_id)},
            {"$set": {"status": "rented", "current_tenant_id": tenant_id, "current_contract_id": contract_id, "updated_at": now}}
        )
        await get_db().tenants.update_one(
            {"_id": ObjectId(tenant_id)},
            {"$set": {"current_property_id": property_id, "updated_at": now}}
        )

    return {
        "success": True,
        "message": f"Contrato {contract_number} creado",
        "contract_id": contract_id,
        "contract_number": contract_number,
    }


# ─── Get Single Contract Detail ───────────────────────────────────────────
@router.get('/admin/rental-contracts/{contract_id}')
async def get_contract_detail(contract_id: str, request: Request):
    """Get a single rental contract by ID"""
    user = await auth_admin(request)
    
    contract = await get_db().rental_contracts.find_one({"_id": ObjectId(contract_id)})
    if not contract:
        raise HTTPException(status_code=404, detail="Contrato no encontrado")
    
    return {"success": True, "contract": serialize(contract)}


@router.patch('/admin/rental-contracts/{contract_id}/status')
async def update_contract_status(contract_id: str, request: Request):
    """Update contract status (activate, terminate, expire)"""
    user = await auth_admin(request)
    data = await request.json()
    new_status = data.get('status')
    now = datetime.utcnow()

    contract = await get_db().rental_contracts.find_one({"_id": ObjectId(contract_id)})
    if not contract:
        raise HTTPException(status_code=404, detail="Contrato no encontrado")

    update = {"status": new_status, "updated_at": now}

    if new_status == 'active':
        # Mark property as rented
        await get_db().properties.update_one(
            {"_id": ObjectId(contract['property_id'])},
            {"$set": {"status": "rented", "current_tenant_id": contract['tenant_id'], "current_contract_id": contract_id, "updated_at": now}}
        )
        await get_db().tenants.update_one(
            {"_id": ObjectId(contract['tenant_id'])},
            {"$set": {"current_property_id": contract['property_id'], "updated_at": now}}
        )
    elif new_status in ('terminated', 'expired'):
        # Free the property (only if not manually overridden)
        prop = await get_db().properties.find_one({"_id": ObjectId(contract['property_id'])})
        if prop and not prop.get('status_manually_set'):
            await get_db().properties.update_one(
                {"_id": ObjectId(contract['property_id'])},
                {"$set": {"status": "available", "current_tenant_id": None, "current_contract_id": None, "updated_at": now}}
            )
        await get_db().tenants.update_one(
            {"_id": ObjectId(contract['tenant_id'])},
            {"$set": {"current_property_id": None, "updated_at": now},
             "$push": {"rental_history": {
                 "property_id": contract['property_id'],
                 "property_address": contract.get('property_address', ''),
                 "start_date": contract.get('start_date'),
                 "end_date": now.strftime('%Y-%m-%d'),
                 "rent_amount": contract.get('rent_amount', 0),
             }}}
        )
    elif new_status in ('draft', 'pending_signature', 'pending'):
        # Contract reverted to draft/pending — free the property if no other active contract uses it
        prop_id = contract.get('property_id')
        if prop_id:
            prop = await get_db().properties.find_one({"_id": ObjectId(prop_id)})
            if prop and not prop.get('status_manually_set'):
                # Check if there's any OTHER active contract on this property
                other_active = await get_db().rental_contracts.find_one({
                    "property_id": prop_id,
                    "status": "active",
                    "_id": {"$ne": ObjectId(contract_id)},
                })
                if not other_active:
                    await get_db().properties.update_one(
                        {"_id": ObjectId(prop_id)},
                        {"$set": {"status": "available", "current_tenant_id": None, "current_contract_id": None, "updated_at": now}}
                    )

    await get_db().rental_contracts.update_one({"_id": ObjectId(contract_id)}, {"$set": update})
    return {"success": True, "message": f"Contrato actualizado a: {new_status}"}


# ─── Reconcile properties ↔ contracts (admin tool) ─────────────────────────
@router.post('/admin/properties/sync-status')
async def sync_property_status(request: Request):
    """Reconcile property statuses based on actual active contracts.
    For each property:
      - If there's an active contract → status = rented (with current_contract_id, current_tenant_id)
      - Else if status_manually_set is False/missing → status = available
      - If status_manually_set is True → leave it alone (admin's manual override)
    """
    user = await auth_admin(request)
    db = get_db()
    now = datetime.utcnow()

    fixed = []
    skipped_manual = []
    unchanged = []

    async for prop in db.properties.find({}):
        pid = str(prop["_id"])
        active_contract = await db.rental_contracts.find_one({"property_id": pid, "status": "active"})

        if prop.get('status_manually_set'):
            skipped_manual.append({"id": pid, "address": prop.get('address', ''), "status": prop.get('status')})
            continue

        if active_contract:
            target = {
                "status": "rented",
                "current_tenant_id": str(active_contract.get('tenant_id', '')),
                "current_contract_id": str(active_contract.get('_id', '')),
            }
        else:
            target = {
                "status": "available",
                "current_tenant_id": None,
                "current_contract_id": None,
            }

        # Only update if different
        needs_update = (
            prop.get('status') != target['status'] or
            (str(prop.get('current_contract_id', '') or '') != (target.get('current_contract_id') or ''))
        )
        if needs_update:
            await db.properties.update_one({"_id": prop["_id"]}, {"$set": {**target, "updated_at": now}})
            fixed.append({
                "id": pid,
                "address": prop.get('address', ''),
                "old_status": prop.get('status'),
                "new_status": target['status'],
            })
        else:
            unchanged.append({"id": pid, "status": prop.get('status')})

    return {
        "success": True,
        "summary": f"{len(fixed)} corregidas, {len(skipped_manual)} ignoradas (manuales), {len(unchanged)} ya correctas",
        "fixed": fixed,
        "skipped_manual": skipped_manual,
        "unchanged_count": len(unchanged),
    }


@router.put('/admin/rental-contracts/{contract_id}')
async def update_contract(contract_id: str, request: Request):
    """Update a rental contract"""
    user = await auth_admin(request)
    data = await request.json()
    now = datetime.utcnow()

    contract = await get_db().rental_contracts.find_one({"_id": ObjectId(contract_id)})
    if not contract:
        raise HTTPException(status_code=404, detail="Contrato no encontrado")

    update_fields = {"updated_at": now}

    # Fields that can be updated
    editable_fields = [
        'start_date', 'end_date', 'rent_amount', 'deposit_amount',
        'payment_due_day', 'late_fee_amount', 'late_fee_grace_days',
        'terms', 'special_conditions', 'payment_method_type',
        'customer_vault_id', 'vault_display', 'vault_customer_name',
    ]

    for field in editable_fields:
        if field in data:
            val = data[field]
            if field in ('rent_amount', 'deposit_amount', 'late_fee_amount'):
                val = float(val) if val else 0
            elif field in ('payment_due_day', 'late_fee_grace_days'):
                val = int(val) if val else 1
            update_fields[field] = val

    # Allow changing property and tenant if contract is draft
    if contract.get('status') == 'draft':
        if 'property_id' in data and data['property_id'] != contract.get('property_id'):
            prop = await get_db().properties.find_one({"_id": ObjectId(data['property_id'])})
            if prop:
                update_fields['property_id'] = data['property_id']
                update_fields['property_address'] = prop.get('address', '')
                update_fields['property_number'] = prop.get('property_number', '')
        if 'tenant_id' in data and data['tenant_id'] != contract.get('tenant_id'):
            tenant = await get_db().tenants.find_one({"_id": ObjectId(data['tenant_id'])})
            if tenant:
                update_fields['tenant_id'] = data['tenant_id']
                update_fields['tenant_name'] = tenant.get('name', '')
                update_fields['tenant_phone'] = tenant.get('phone', '')
                update_fields['tenant_email'] = tenant.get('email', '')

    # Update addendums if provided
    if 'addendums' in data:
        update_fields['addendums'] = data['addendums']

    await get_db().rental_contracts.update_one({"_id": ObjectId(contract_id)}, {"$set": update_fields})

    return {
        "success": True,
        "message": f"Contrato {contract.get('contract_number', '')} actualizado exitosamente",
    }


@router.delete('/admin/rental-contracts/{contract_id}')
async def delete_contract(contract_id: str, request: Request):
    """Delete a contract (draft only unless forced). Also frees up the property if it was rented by this contract."""
    user = await auth_admin(request)
    contract = await get_db().rental_contracts.find_one({"_id": ObjectId(contract_id)})
    if not contract:
        raise HTTPException(status_code=404, detail="Contrato no encontrado")

    if contract['status'] == 'active':
        from urllib.parse import parse_qs
        params = parse_qs(str(request.url.query))
        force = params.get('force', ['false'])[0].lower() == 'true'
        if not force:
            raise HTTPException(status_code=400, detail="No se puede eliminar un contrato activo. Use ?force=true")

    # Free up the property if this contract was the one renting it
    prop_id = contract.get('property_id')
    if prop_id:
        try:
            prop = await get_db().properties.find_one({"_id": ObjectId(prop_id)})
        except Exception:
            prop = None
        if prop and not prop.get('status_manually_set'):
            # Only auto-free if admin hasn't manually overridden the status
            if str(prop.get('current_contract_id', '')) == contract_id:
                await get_db().properties.update_one(
                    {"_id": ObjectId(prop_id)},
                    {"$set": {
                        "status": "available",
                        "current_tenant_id": None,
                        "current_contract_id": None,
                        "updated_at": datetime.utcnow(),
                    }}
                )

    # Also clear the tenant's current_property_id if it pointed to this property
    tenant_id = contract.get('tenant_id')
    if tenant_id and prop_id:
        try:
            await get_db().tenants.update_one(
                {"_id": ObjectId(tenant_id), "current_property_id": prop_id},
                {"$set": {"current_property_id": None, "updated_at": datetime.utcnow()}}
            )
        except Exception:
            pass

    await get_db().rental_contracts.delete_one({"_id": ObjectId(contract_id)})
    return {"success": True, "message": "Contrato eliminado y propiedad liberada"}


# ─── Contract Signature ─────────────────────────────────────────────────────
@router.post('/admin/rental-contracts/{contract_id}/sign')
async def sign_contract(contract_id: str, request: Request):
    """Sign a rental contract"""
    import hashlib
    user = await auth_admin(request)
    data = await request.json()

    contract = await get_db().rental_contracts.find_one({"_id": ObjectId(contract_id)})
    if not contract:
        raise HTTPException(status_code=404, detail="Contrato no encontrado")

    sig_type = data.get('type', 'canvas')
    image_data = data.get('image_data', '')
    biometric_data = data.get('biometric_data', '')

    if not image_data and not biometric_data:
        raise HTTPException(status_code=400, detail="No se recibió firma")

    sig_payload = (image_data or biometric_data).encode('utf-8')
    sig_hash = hashlib.sha256(sig_payload).hexdigest()
    now = datetime.utcnow()
    client_ip = request.client.host if request.client else 'unknown'

    signature_record = {
        "type": sig_type,
        "image_data": image_data,
        "biometric_data": biometric_data if sig_type == 'topaz' else '',
        "pad_model": data.get('pad_model', ''),
        "hash": sig_hash,
        "signed_at": now,
        "signed_by_admin": user.get('email', 'admin'),
        "signer_name": contract.get('tenant_name', ''),
        "client_ip": client_ip,
    }

    update = {
        "signature": signature_record,
        "signature_status": "signed",
        "signed_at": now,
        "updated_at": now,
    }

    # Auto-activate if draft
    if contract.get('status') == 'draft':
        update['status'] = 'active'
        await get_db().properties.update_one(
            {"_id": ObjectId(contract['property_id'])},
            {"$set": {"status": "rented", "current_tenant_id": contract['tenant_id'], "current_contract_id": contract_id, "updated_at": now}}
        )
        await get_db().tenants.update_one(
            {"_id": ObjectId(contract['tenant_id'])},
            {"$set": {"current_property_id": contract['property_id'], "updated_at": now}}
        )

    await get_db().rental_contracts.update_one({"_id": ObjectId(contract_id)}, {"$set": update})
    return {"success": True, "message": "Contrato firmado exitosamente", "hash": sig_hash}


# ─── Office Signature (In-Person Signing) ─────────────────────────────────
@router.post('/admin/rental-contracts/{contract_id}/office-sign')
async def office_sign_contract(contract_id: str, request: Request):
    """
    Capture in-office signature for a rental contract.
    Supports both Canvas (touch) and Topaz Pad signatures.
    """
    import hashlib
    user = await auth_admin(request)
    data = await request.json()

    contract = await get_db().rental_contracts.find_one({"_id": ObjectId(contract_id)})
    if not contract:
        raise HTTPException(status_code=404, detail="Contrato no encontrado")

    sig_type = data.get('type', 'canvas')  # 'canvas' or 'topaz'
    signature_data = data.get('signature', '')  # base64 image
    signer_name = data.get('signer_name', '')
    signer_role = data.get('signer_role', 'tenant')  # 'tenant' or 'admin'

    if not signature_data:
        raise HTTPException(status_code=400, detail="No se recibió firma")

    if not signer_name:
        raise HTTPException(status_code=400, detail="Se requiere el nombre del firmante")

    sig_payload = signature_data.encode('utf-8')
    sig_hash = hashlib.sha256(sig_payload).hexdigest()
    now = datetime.utcnow()
    client_ip = request.client.host if request.client else 'unknown'

    signature_record = {
        "type": sig_type,
        "image_data": signature_data,
        "hash": sig_hash,
        "signed_at": now,
        "signed_by_admin": user.get('email', 'admin'),
        "signer_name": signer_name,
        "signer_role": signer_role,
        "client_ip": client_ip,
        "method": "office",  # Indicates in-person signing
    }

    # Determine which field to update based on signer role
    if signer_role == 'tenant':
        update = {
            "tenant_signature": signature_record,
            "tenant_signed_at": now,
            "updated_at": now,
        }
        # Check if admin also needs to sign or if contract is ready to activate
        if contract.get('admin_signature'):
            update['status'] = 'active'
        else:
            update['status'] = 'pending_signature'  # Waiting for admin
    else:  # admin signature
        update = {
            "admin_signature": signature_record,
            "admin_signed_at": now,
            "updated_at": now,
        }
        # Check if tenant also signed
        if contract.get('tenant_signature'):
            update['status'] = 'active'
        else:
            update['status'] = 'pending_tenant'

    # Also store in legacy signature field for backward compatibility
    update['signature'] = signature_record
    update['signature_status'] = 'signed'
    update['signed_at'] = now

    # Auto-activate if draft and both parties signed (or single signature mode)
    current_status = contract.get('status', 'draft')
    if current_status == 'draft':
        update['status'] = 'active'
        # Update property and tenant records
        await get_db().properties.update_one(
            {"_id": ObjectId(contract['property_id'])},
            {"$set": {"status": "rented", "current_tenant_id": contract.get('tenant_id'), "current_contract_id": contract_id, "updated_at": now}}
        )
        if contract.get('tenant_id'):
            await get_db().tenants.update_one(
                {"_id": ObjectId(contract['tenant_id'])},
                {"$set": {"current_property_id": contract['property_id'], "updated_at": now}}
            )

    await get_db().rental_contracts.update_one({"_id": ObjectId(contract_id)}, {"$set": update})
    
    return {
        "success": True, 
        "message": f"Firma de {signer_role} capturada exitosamente",
        "signer_name": signer_name,
        "signer_role": signer_role,
        "method": sig_type,
        "hash": sig_hash
    }


# ─── Contract PDF ─────────────────────────────────────────────────────────
@router.get('/admin/rental-contracts/{contract_id}/pdf')
async def generate_contract_pdf(contract_id: str, request: Request):
    """Generate rental contract PDF (Texas-compliant bilingual)"""
    user = await auth_admin(request)

    contract = await get_db().rental_contracts.find_one({"_id": ObjectId(contract_id)})
    if not contract:
        raise HTTPException(status_code=404, detail="Contrato no encontrado")

    # Load rental config for company details
    config = await get_db().rental_config.find_one({"type": "company"})
    if not config:
        config = {}

    # Fetch saved admin signature if not already in contract
    if not contract.get('admin_signature') or not contract.get('admin_signature', {}).get('image_data'):
        try:
            saved_admin_sig = await get_db().admin_signatures.find_one({"type": "landlord_default"})
            if saved_admin_sig and saved_admin_sig.get('image_data'):
                config['saved_admin_signature'] = saved_admin_sig
                logging.info("Including saved admin signature for PDF generation")
        except Exception as e:
            logging.warning(f"Could not fetch saved admin signature: {e}")

    # Look up tenant photo for inclusion in contract
    tenant_photo_url = None
    if contract.get('tenant_id'):
        try:
            tenant = await get_db().tenants.find_one({"_id": ObjectId(contract['tenant_id'])})
            if tenant:
                tenant_photo_url = tenant.get('photo_url', '')
        except Exception:
            pass

    from rental_pdf_service import generate_rental_contract_pdf
    pdf_b64 = generate_rental_contract_pdf(contract, config=config, tenant_photo_url=tenant_photo_url)
    filename = f"Lease_Agreement_{contract.get('contract_number', contract_id)}.pdf"

    return {"success": True, "pdf_base64": pdf_b64, "filename": filename}


# ─── 3-Day Notice to Vacate PDF ──────────────────────────────────────────
@router.get('/admin/rental-contracts/{contract_id}/notice-3day')
async def generate_3day_notice(contract_id: str, request: Request):
    """Generate Texas 3-Day Notice to Vacate PDF"""
    user = await auth_admin(request)

    contract = await get_db().rental_contracts.find_one({"_id": ObjectId(contract_id)})
    if not contract:
        raise HTTPException(status_code=404, detail="Contrato no encontrado")

    from urllib.parse import parse_qs
    params = parse_qs(str(request.url.query))
    reason = params.get('reason', ['nonpayment'])[0]
    amount_owed = float(params.get('amount', ['0'])[0])

    # If no amount specified, calculate from contract
    if amount_owed <= 0:
        amount_owed = contract.get('rent_amount', 0)

    config = await get_db().rental_config.find_one({"type": "company"})

    from rental_pdf_service import generate_3day_notice_pdf
    pdf_b64 = generate_3day_notice_pdf(contract, config=config, reason=reason, amount_owed=amount_owed)
    filename = f"3Day_Notice_{contract.get('contract_number', contract_id)}.pdf"

    return {"success": True, "pdf_base64": pdf_b64, "filename": filename}


# ═══════════════════════════════════════════════════════════════════════════════
# RENTAL PAYMENTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.post('/admin/rent-payments/auto-generate')
async def admin_rent_auto_generate(request: Request):
    """Force a single pass of the rent-payment auto-generation cron.
    Idempotent: re-running won't duplicate payments for the same period.
    Returns stats: created / already_exists / late_fee_applied / errors.
    """
    await auth_admin(request)
    from .rent_payment_cron import run_once
    stats = await run_once(get_db())
    return {"success": True, "stats": stats}


@router.post('/admin/rental-payments')
async def register_rental_payment(request: Request):
    """Register a rent payment OR a pending invoice (cash, card, ach, or pending-only)."""
    user = await auth_admin(request)
    data = await request.json()
    now = datetime.utcnow()

    contract_id = data.get('contract_id')
    contract = await get_db().rental_contracts.find_one({"_id": ObjectId(contract_id)}) if contract_id else None

    amount = float(data.get('amount', 0))
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Monto debe ser mayor a 0")

    # Admin can specify status (pending => generate invoice without charging)
    target_status = (data.get('status') or 'completed').lower()
    if target_status not in ('completed', 'paid', 'pending', 'late', 'partial', 'cancelled'):
        target_status = 'completed'
    is_paid = target_status in ('completed', 'paid')

    payment_method = data.get('payment_method', 'cash')
    vault_id = data.get('customer_vault_id', '')
    nmi_transaction = None
    late_fee = float(data.get('late_fee', 0) or 0)

    # NMI charge ONLY if status is completed AND a vault is supplied
    if is_paid and payment_method in ('card', 'ach') and vault_id:
        try:
            from merchant_one_enhanced import charge_vault_customer
            desc = f"Renta {contract.get('property_address', '')} - {contract.get('tenant_name', '')}" if contract else "Pago de renta"
            nmi_result = await charge_vault_customer(customer_vault_id=vault_id, amount=amount, order_description=desc)
            if not nmi_result.get('success'):
                raise HTTPException(status_code=400, detail=f"Cobro rechazado: {nmi_result.get('responseText', 'Error')}")
            nmi_transaction = {
                "transaction_id": nmi_result.get('transactionId', ''),
                "vault_id": vault_id,
                "response_text": nmi_result.get('responseText', ''),
                "method": payment_method,
            }
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error NMI: {str(e)}")

    # Generate receipt number ONLY for completed/paid records
    receipt_number = ""
    if is_paid:
        pay_count = await get_db().rental_payments.count_documents({
            "status": {"$in": ["completed", "paid"]}
        })
        receipt_number = f"REC-{now.year}-{str(pay_count + 1).zfill(4)}"

    # Optional explicit payment_date / due_date
    def _parse_date(v):
        if not v:
            return None
        if isinstance(v, str):
            try:
                if "T" in v:
                    return datetime.fromisoformat(v.replace("Z", "+00:00"))
                return datetime.strptime(v, "%Y-%m-%d")
            except Exception:
                return None
        return v

    period_year = int(data.get('period_year', now.year))
    period_month_num = int(data.get('period_month_num', now.month))
    period_month_name = data.get('period_month', now.strftime('%B'))
    period_iso = data.get('period') or f"{period_year}-{str(period_month_num).zfill(2)}"

    payment_date = _parse_date(data.get('payment_date')) or (now if is_paid else None)
    due_date = _parse_date(data.get('due_date')) or datetime(period_year, period_month_num, 1)

    payment_doc = {
        "receipt_number": receipt_number,
        "contract_id": contract_id or '',
        "property_id": str(data.get('property_id', contract.get('property_id', '') if contract else '')),
        "property_address": data.get('property_address', contract.get('property_address', '') if contract else ''),
        "tenant_id": str(data.get('tenant_id', contract.get('tenant_id', '') if contract else '')),
        "tenant_name": data.get('tenant_name', contract.get('tenant_name', '') if contract else ''),
        "amount": amount,
        "late_fee": late_fee,
        "total_due": amount + late_fee,
        "total_paid": (amount + late_fee) if is_paid else 0.0,
        "payment_method": payment_method if is_paid else "",
        "period": period_iso,
        "period_month": period_month_name,
        "period_month_num": period_month_num,
        "period_year": period_year,
        "due_date": due_date,
        "payment_date": payment_date,
        "status": target_status,
        "paid": is_paid,
        "notes": data.get('notes', ''),
        "auto_generated": False,
        "recorded_by": user.get('email', 'admin'),
        "created_at": now,
    }
    if nmi_transaction:
        payment_doc['nmi_transaction'] = nmi_transaction
        payment_doc['customer_vault_id'] = vault_id

    result = await get_db().rental_payments.insert_one(payment_doc)

    return {
        "success": True,
        "message": f"Factura {receipt_number or 'pendiente'} creada — ${amount:,.2f}",
        "id": str(result.inserted_id),
        "receipt_number": receipt_number,
        "amount": amount,
        "status": target_status,
    }


@router.get('/admin/rental-payments')
async def list_rental_payments(request: Request):
    """List rental payments"""
    user = await auth_admin(request)
    from urllib.parse import parse_qs
    params = parse_qs(str(request.url.query))
    contract_id = params.get('contract_id', [None])[0]
    property_id = params.get('property_id', [None])[0]

    query = {}
    if contract_id:
        query['contract_id'] = contract_id
    if property_id:
        query['property_id'] = property_id

    cursor = get_db().rental_payments.find(query).sort("payment_date", -1).limit(100)
    payments = []
    async for p in cursor:
        payments.append(serialize(p))

    return {"success": True, "payments": payments, "count": len(payments)}


@router.put('/admin/rental-payments/{payment_id}')
async def update_rental_payment(payment_id: str, request: Request):
    """Update a rental payment.

    Allows editing all key fields: amount, late_fee, status, period, due_date,
    payment_date, payment_method, notes. When status is set to 'completed' /
    'paid', automatically marks `paid=true` and stamps `payment_date` if empty.
    """
    user = await auth_admin(request)
    data = await request.json()

    existing = await get_db().rental_payments.find_one({"_id": ObjectId(payment_id)})
    if not existing:
        raise HTTPException(status_code=404, detail="Pago no encontrado")

    now = datetime.utcnow()
    update_fields = {"updated_at": now}

    float_fields = ["amount", "late_fee", "total_due", "total_paid"]
    str_fields = ["payment_method", "period_month", "notes", "status", "receipt_number", "tenant_name", "property_address"]
    int_fields = ["period_year", "period_month_num"]
    date_fields = ["payment_date", "due_date"]

    for f in float_fields:
        if f in data:
            try:
                update_fields[f] = float(data[f] or 0)
            except (TypeError, ValueError):
                pass
    for f in str_fields:
        if f in data:
            update_fields[f] = str(data[f] or "")
    for f in int_fields:
        if f in data:
            try:
                update_fields[f] = int(data[f] or 0)
            except (TypeError, ValueError):
                pass
    for f in date_fields:
        if f in data and data[f]:
            try:
                # Accept ISO strings or 'YYYY-MM-DD'
                v = data[f]
                if isinstance(v, str):
                    if "T" in v:
                        update_fields[f] = datetime.fromisoformat(v.replace("Z", "+00:00"))
                    else:
                        update_fields[f] = datetime.strptime(v, "%Y-%m-%d")
                else:
                    update_fields[f] = v
            except Exception:
                pass

    # Auto-set paid flag based on status
    new_status = update_fields.get("status", existing.get("status", "pending")).lower()
    if new_status in ("completed", "paid"):
        update_fields["paid"] = True
        if not update_fields.get("payment_date") and not existing.get("payment_date"):
            update_fields["payment_date"] = now
        # Auto-assign receipt number if missing
        if not (update_fields.get("receipt_number") or existing.get("receipt_number")):
            pay_count = await get_db().rental_payments.count_documents({
                "status": {"$in": ["completed", "paid"]}
            })
            update_fields["receipt_number"] = f"REC-{now.year}-{str(pay_count + 1).zfill(4)}"
    elif new_status in ("pending", "late", "cancelled"):
        update_fields["paid"] = False

    await get_db().rental_payments.update_one(
        {"_id": ObjectId(payment_id)},
        {"$set": update_fields}
    )

    updated = await get_db().rental_payments.find_one({"_id": ObjectId(payment_id)})
    return {"success": True, "payment": serialize(updated)}


@router.delete('/admin/rental-payments/{payment_id}')
async def delete_rental_payment(payment_id: str, request: Request):
    """Delete a rental payment"""
    user = await auth_admin(request)

    existing = await get_db().rental_payments.find_one({"_id": ObjectId(payment_id)})
    if not existing:
        raise HTTPException(status_code=404, detail="Pago no encontrado")

    await get_db().rental_payments.delete_one({"_id": ObjectId(payment_id)})
    return {"success": True, "message": "Pago eliminado exitosamente"}


@router.post('/admin/rental-payments/generate-monthly')
async def admin_trigger_monthly_rent_generation(request: Request):
    """Manually trigger the monthly rent payment cron job.

    Useful for:
      - Creating the first pending payment for new contracts
      - Forcing re-check of current period payments
      - Applying late fees outside of the 6h cron interval

    Returns stats: {created, already_exists, late_fee_applied, skip_no_rent, errors}
    """
    await auth_admin(request)
    try:
        from rental.rent_payment_cron import run_once
        stats = await run_once(get_db())
        return {
            "success": True,
            "message": "Generación mensual de rentas ejecutada",
            "stats": stats,
        }
    except Exception as e:
        logging.exception("Manual rent generation failed")
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


# ═══════════════════════════════════════════════════════════════════════════════
# DASHBOARD & STATS
# ═══════════════════════════════════════════════════════════════════════════════

@router.get('/admin/rental-dashboard')
async def rental_dashboard(request: Request):
    """Get rental management dashboard stats"""
    user = await auth_admin(request)
    now = datetime.utcnow()

    # Property stats
    total_properties = await get_db().properties.count_documents({})
    available = await get_db().properties.count_documents({"status": "available"})
    rented = await get_db().properties.count_documents({"status": "rented"})
    maintenance = await get_db().properties.count_documents({"status": "maintenance"})
    occupancy_rate = (rented / total_properties * 100) if total_properties > 0 else 0

    # Tenant stats
    total_tenants = await get_db().tenants.count_documents({})
    active_tenants = await get_db().tenants.count_documents({"status": "active"})

    # Contract stats
    active_contracts = await get_db().rental_contracts.count_documents({"status": "active"})
    draft_contracts = await get_db().rental_contracts.count_documents({"status": "draft"})

    # Revenue this month
    month_start = datetime(now.year, now.month, 1)
    pipeline_monthly = [
        {"$match": {"payment_date": {"$gte": month_start}, "status": "completed"}},
        {"$group": {"_id": None, "total": {"$sum": "$amount"}, "count": {"$sum": 1}}}
    ]
    monthly_result = await get_db().rental_payments.aggregate(pipeline_monthly).to_list(1)
    monthly_revenue = monthly_result[0]['total'] if monthly_result else 0
    monthly_payments = monthly_result[0]['count'] if monthly_result else 0

    # Revenue this year
    year_start = datetime(now.year, 1, 1)
    pipeline_yearly = [
        {"$match": {"payment_date": {"$gte": year_start}, "status": "completed"}},
        {"$group": {"_id": None, "total": {"$sum": "$amount"}}}
    ]
    yearly_result = await get_db().rental_payments.aggregate(pipeline_yearly).to_list(1)
    yearly_revenue = yearly_result[0]['total'] if yearly_result else 0

    # Expected monthly income (sum of all active contract rents)
    pipeline_expected = [
        {"$match": {"status": "active"}},
        {"$group": {"_id": None, "total": {"$sum": "$rent_amount"}}}
    ]
    expected_result = await get_db().rental_contracts.aggregate(pipeline_expected).to_list(1)
    expected_monthly = expected_result[0]['total'] if expected_result else 0

    # Monthly revenue trend (last 6 months)
    monthly_trend = []
    for i in range(5, -1, -1):
        m = now.month - i
        y = now.year
        if m <= 0:
            m += 12
            y -= 1
        m_start = datetime(y, m, 1)
        if m == 12:
            m_end = datetime(y + 1, 1, 1)
        else:
            m_end = datetime(y, m + 1, 1)
        pipe = [
            {"$match": {"payment_date": {"$gte": m_start, "$lt": m_end}, "status": "completed"}},
            {"$group": {"_id": None, "total": {"$sum": "$amount"}}}
        ]
        res = await get_db().rental_payments.aggregate(pipe).to_list(1)
        monthly_trend.append({
            "month": m_start.strftime('%b %Y'),
            "revenue": res[0]['total'] if res else 0,
        })

    # Collection rate (percentage of expected rent collected)
    collection_rate = (monthly_revenue / expected_monthly * 100) if expected_monthly > 0 else 0

    # Total deposits held
    deposit_pipeline = [
        {"$match": {"status": "active"}},
        {"$group": {"_id": None, "total": {"$sum": "$deposit_amount"}}}
    ]
    deposit_result = await get_db().rental_contracts.aggregate(deposit_pipeline).to_list(1)
    total_deposits = deposit_result[0]['total'] if deposit_result else 0

    # Total portfolio value (sum of property values or estimated from rent * 100)
    value_pipeline = [
        {"$group": {"_id": None, "total_rent": {"$sum": "$rent_amount"}, "total_deposit": {"$sum": "$deposit_amount"}}}
    ]
    value_result = await get_db().properties.aggregate(value_pipeline).to_list(1)
    total_monthly_rent = value_result[0]['total_rent'] if value_result else 0
    estimated_portfolio_value = total_monthly_rent * 12 * 10  # Rough Cap Rate estimate

    # Total expenses
    exp_pipeline = [
        {"$match": {"status": {"$ne": "cancelled"}}},
        {"$group": {"_id": None, "total": {"$sum": "$amount"}, "count": {"$sum": 1}}}
    ]
    exp_result = await get_db().property_expenses.aggregate(exp_pipeline).to_list(1)
    total_expenses = exp_result[0]['total'] if exp_result else 0
    total_expense_count = exp_result[0]['count'] if exp_result else 0

    # Monthly expenses
    exp_monthly_pipeline = [
        {"$match": {"expense_date": {"$gte": month_start.strftime('%Y-%m-%d')}, "status": {"$ne": "cancelled"}}},
        {"$group": {"_id": None, "total": {"$sum": "$amount"}}}
    ]
    exp_monthly_result = await get_db().property_expenses.aggregate(exp_monthly_pipeline).to_list(1)
    monthly_expenses = exp_monthly_result[0]['total'] if exp_monthly_result else 0

    # Yearly expenses
    exp_yearly_pipeline = [
        {"$match": {"expense_date": {"$gte": year_start.strftime('%Y-%m-%d')}, "status": {"$ne": "cancelled"}}},
        {"$group": {"_id": None, "total": {"$sum": "$amount"}}}
    ]
    exp_yearly_result = await get_db().property_expenses.aggregate(exp_yearly_pipeline).to_list(1)
    yearly_expenses = exp_yearly_result[0]['total'] if exp_yearly_result else 0

    # Contracts expiring in 30 days
    thirty_days = (now + timedelta(days=30)).strftime('%Y-%m-%d')
    expiring_count = await get_db().rental_contracts.count_documents({
        "status": "active",
        "end_date": {"$lte": thirty_days, "$gte": now.strftime('%Y-%m-%d')}
    })

    # Recent payments (last 5)
    recent_payments = []
    async for p in get_db().rental_payments.find({"status": "completed"}).sort("payment_date", -1).limit(5):
        p['_id'] = str(p['_id'])
        recent_payments.append(p)

    return {
        "success": True,
        "dashboard": {
            "properties": {"total": total_properties, "available": available, "rented": rented, "maintenance": maintenance, "occupancy_rate": round(occupancy_rate, 1)},
            "tenants": {"total": total_tenants, "active": active_tenants},
            "contracts": {"active": active_contracts, "draft": draft_contracts, "expiring_soon": expiring_count},
            "revenue": {
                "monthly": monthly_revenue,
                "monthly_payments": monthly_payments,
                "yearly": yearly_revenue,
                "expected_monthly": expected_monthly,
                "collection_rate": round(collection_rate, 1),
            },
            "expenses": {
                "monthly": monthly_expenses,
                "yearly": yearly_expenses,
                "total": total_expenses,
                "count": total_expense_count,
            },
            "financials": {
                "net_monthly": monthly_revenue - monthly_expenses,
                "net_yearly": yearly_revenue - yearly_expenses,
                "total_deposits_held": total_deposits,
                "estimated_portfolio_value": estimated_portfolio_value,
                "noi_annual": (expected_monthly * 12) - total_expenses,  # Net Operating Income
                "cap_rate": round(((expected_monthly * 12 - total_expenses) / estimated_portfolio_value * 100), 2) if estimated_portfolio_value > 0 else 0,
            },
            "monthly_trend": monthly_trend,
            "recent_payments": recent_payments,
        }
    }



# ═══════════════════════════════════════════════════════════════════════════════
# PAYMENT METHOD MANAGEMENT (NMI VAULT + PIN CARDS)
# ═══════════════════════════════════════════════════════════════════════════════

@router.post('/admin/rentals/payment-method')
async def create_rental_payment_method(request: Request):
    """
    Create a payment method (card or ACH) for a rental tenant.
    - Creates customer vault in Merchant One (NMI)
    - Saves to vault_customers collection
    - Optionally saves card with PIN encryption
    - Returns vault_id to link with tenant/contract
    """
    user = await auth_admin(request)
    data = await request.json()
    now = datetime.utcnow()

    payment_type = data.get('payment_type', 'card')  # 'card' or 'ach'
    first_name = data.get('first_name', '').strip()
    last_name = data.get('last_name', '').strip()
    email = data.get('email', '').strip()
    phone = data.get('phone', '').strip()
    address = data.get('address', '').strip()
    city = data.get('city', '').strip()
    state = data.get('state', 'TX').strip()
    zip_code = data.get('zip_code', '').strip()
    pin = data.get('pin', '').strip()  # For PIN-encrypted card storage

    if not first_name or not last_name:
        raise HTTPException(status_code=400, detail="Se requiere nombre y apellido")

    try:
        import uuid

        if payment_type == 'card':
            # ── Card Vault ──
            card_number = data.get('card_number', '').replace(' ', '').replace('-', '')
            exp_month = int(data.get('exp_month', 0))
            exp_year = int(data.get('exp_year', 0))
            cvv = data.get('cvv', '')

            if not card_number or not cvv or not exp_month or not exp_year:
                raise HTTPException(status_code=400, detail="Se requiere número de tarjeta, vencimiento y CVV")

            from merchant_one_service import build_card_vault_payload, detect_card_brand, MerchantOneService
            payload, vault_id = build_card_vault_payload(
                card_number=card_number, exp_month=exp_month, exp_year=exp_year, cvv=cvv,
                first_name=first_name, last_name=last_name, email=email, phone=phone,
                address=address, city=city, state=state, zip_code=zip_code,
            )

            # Send to NMI
            service = MerchantOneService()
            response = await service._make_request(payload)

            if not (response.success and response.responseCode == '1'):
                raise HTTPException(status_code=400, detail=f"Error de Merchant One: {response.responseText}")

            card_brand = detect_card_brand(card_number)
            masked = f"****{card_number[-4:]}"
            display = f"{card_brand} {masked}"

            # Save to vault_customers
            record = {
                'id': str(uuid.uuid4()),
                'firstName': first_name,
                'lastName': last_name,
                'email': email,
                'phone': phone,
                'address1': address,
                'city': city,
                'state': state,
                'postalCode': zip_code,
                'maskedAccount': masked,
                'customerVaultId': vault_id,
                'subscriptionId': None,
                'subscriptionStatus': 'none',
                'vaultStatus': 'active',
                'planName': None,
                'planAmount': None,
                'dayFrequency': None,
                'payment_type': 'card',
                'card_brand': card_brand,
                'source': 'rental_module',
                'createdAt': now,
                'updatedAt': now,
            }
            await get_db().vault_customers.insert_one(record)

            # Save to encrypted cards with PIN if provided
            if pin:
                try:
                    from cryptography.fernet import Fernet
                    import hashlib
                    key = hashlib.sha256(pin.encode()).digest()
                    import base64 as b64
                    fernet_key = b64.urlsafe_b64encode(key)
                    f = Fernet(fernet_key)
                    encrypted_card = f.encrypt(card_number.encode()).decode()
                    encrypted_cvv = f.encrypt(cvv.encode()).decode()
                    encrypted_exp = f.encrypt(f"{exp_month:02d}/{exp_year}".encode()).decode()

                    card_doc = {
                        'client_name': f"{first_name} {last_name}",
                        'client_email': email,
                        'client_phone': phone,
                        'card_brand': card_brand,
                        'last_four': card_number[-4:],
                        'encrypted_number': encrypted_card,
                        'encrypted_cvv': encrypted_cvv,
                        'encrypted_exp': encrypted_exp,
                        'vault_id': vault_id,
                        'source': 'rental_module',
                        'created_at': now,
                        'created_by': user.get('email', 'admin'),
                    }
                    await get_db().encrypted_cards.insert_one(card_doc)
                    logging.info(f"🔐 Card saved with PIN encryption for {first_name} {last_name}")
                except Exception as e:
                    logging.warning(f"⚠️ PIN encryption failed (vault still created): {e}")

            return {
                "success": True,
                "vault_id": vault_id,
                "display": display,
                "payment_type": "card",
                "card_brand": card_brand,
                "masked": masked,
                "name": f"{first_name} {last_name}",
                "message": f"✅ Tarjeta {display} de {first_name} {last_name} guardada en Merchant One" + (" y encriptada con PIN" if pin else ""),
            }

        elif payment_type == 'ach':
            # ── ACH Vault ──
            routing = data.get('routing', '').strip()
            account_number = data.get('account_number', '').strip()
            account_type = data.get('account_type', 'checking')

            if not routing or not account_number:
                raise HTTPException(status_code=400, detail="Se requiere número de ruta y número de cuenta")
            if len(routing) != 9:
                raise HTTPException(status_code=400, detail="El número de ruta debe tener 9 dígitos")

            from merchant_one_models import CustomerInfo, BankInfo
            from merchant_one_service import MerchantOneService

            customer_info = CustomerInfo(
                firstName=first_name, lastName=last_name, email=email or f"{first_name.lower()}@placeholder.com",
                phone=phone or '0000000000', address1=address or '305 Bruce Ave',
                city=city or 'Dumas', state=state, postalCode=zip_code or '79029',
            )
            bank_info = BankInfo(
                checkName=f"{first_name} {last_name}", routing=routing,
                accountNumber=account_number, accountType=account_type, secCode='PPD',
            )

            service = MerchantOneService()
            service.db = get_db()
            response, vault_id = await service.create_vault_customer(customer_info, bank_info)

            if not (response.success and response.responseCode == '1'):
                raise HTTPException(status_code=400, detail=f"Error de Merchant One: {response.responseText}")

            masked = f"****{account_number[-4:]}"
            display = f"{account_type.title()} {masked}"

            # Save to vault_customers
            record = {
                'id': str(uuid.uuid4()),
                'firstName': first_name,
                'lastName': last_name,
                'email': email,
                'phone': phone,
                'address1': address,
                'city': city,
                'state': state,
                'postalCode': zip_code,
                'maskedAccount': masked,
                'customerVaultId': vault_id,
                'subscriptionId': None,
                'subscriptionStatus': 'none',
                'vaultStatus': 'active',
                'planName': None,
                'planAmount': None,
                'dayFrequency': None,
                'payment_type': account_type,
                'source': 'rental_module',
                'createdAt': now,
                'updatedAt': now,
            }
            await get_db().vault_customers.insert_one(record)

            return {
                "success": True,
                "vault_id": vault_id,
                "display": display,
                "payment_type": "ach",
                "account_type": account_type,
                "masked": masked,
                "name": f"{first_name} {last_name}",
                "message": f"✅ Cuenta {display} de {first_name} {last_name} guardada en Merchant One",
            }

        else:
            raise HTTPException(status_code=400, detail="Tipo de pago inválido. Use 'card' o 'ach'")

    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"❌ Error creating rental payment method: {e}")
        raise HTTPException(status_code=500, detail=f"Error al crear método de pago: {str(e)}")


# ═══════════════════════════════════════════════════════════════════════════════
# RECURRING PAYMENTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.post('/admin/rental-contracts/{contract_id}/recurring')
async def setup_recurring_payment(contract_id: str, request: Request):
    """Set up or update a recurring payment for a rental contract"""
    user = await auth_admin(request)
    data = await request.json()
    now = datetime.utcnow()

    contract = await get_db().rental_contracts.find_one({"_id": ObjectId(contract_id)})
    if not contract:
        raise HTTPException(status_code=404, detail="Contrato no encontrado")

    if contract.get('status') != 'active':
        raise HTTPException(status_code=400, detail="Solo se pueden configurar pagos recurrentes en contratos activos")

    vault_id = data.get('customer_vault_id', '')
    payment_method = data.get('payment_method', 'card')  # card or ach

    if not vault_id:
        raise HTTPException(status_code=400, detail="Se requiere un método de pago (customer_vault_id)")

    charge_day = int(data.get('charge_day', contract.get('payment_due_day', 1)))
    if charge_day < 1 or charge_day > 28:
        raise HTTPException(status_code=400, detail="El día de cobro debe ser entre 1 y 28")

    # Calculate next charge date
    next_month = now.month + 1 if now.day >= charge_day else now.month
    next_year = now.year
    if next_month > 12:
        next_month = 1
        next_year += 1
    next_charge_date = f"{next_year}-{str(next_month).zfill(2)}-{str(charge_day).zfill(2)}"

    recurring_config = {
        "enabled": True,
        "customer_vault_id": vault_id,
        "payment_method": payment_method,
        "vault_customer_name": data.get('vault_customer_name', ''),
        "vault_display": data.get('vault_display', ''),
        "charge_day": charge_day,
        "amount": float(data.get('amount', contract.get('rent_amount', 0))),
        "auto_late_fee": data.get('auto_late_fee', True),
        "status": "active",
        "next_charge_date": next_charge_date,
        "last_charged_at": None,
        "total_charged": 0,
        "successful_charges": 0,
        "failed_charges": 0,
        "created_at": now,
        "created_by": user.get('email', 'admin'),
        "charge_history": [],
    }

    await get_db().rental_contracts.update_one(
        {"_id": ObjectId(contract_id)},
        {"$set": {"recurring_payment": recurring_config, "updated_at": now}}
    )

    return {
        "success": True,
        "message": f"Pago recurrente configurado — ${recurring_config['amount']:,.2f}/mes el día {charge_day}",
        "next_charge_date": next_charge_date,
    }


@router.get('/admin/rental-contracts/{contract_id}/recurring')
async def get_recurring_payment(contract_id: str, request: Request):
    """Get recurring payment configuration for a contract"""
    user = await auth_admin(request)

    contract = await get_db().rental_contracts.find_one({"_id": ObjectId(contract_id)})
    if not contract:
        raise HTTPException(status_code=404, detail="Contrato no encontrado")

    recurring = contract.get('recurring_payment')
    if not recurring:
        return {"success": True, "recurring": None, "message": "No hay pago recurrente configurado"}

    # Serialize dates
    if recurring.get('created_at') and isinstance(recurring['created_at'], datetime):
        recurring['created_at'] = recurring['created_at'].isoformat()
    if recurring.get('last_charged_at') and isinstance(recurring['last_charged_at'], datetime):
        recurring['last_charged_at'] = recurring['last_charged_at'].isoformat()
    for h in recurring.get('charge_history', []):
        if isinstance(h.get('date'), datetime):
            h['date'] = h['date'].isoformat()

    return {"success": True, "recurring": recurring}


@router.patch('/admin/rental-contracts/{contract_id}/recurring')
async def update_recurring_payment(contract_id: str, request: Request):
    """Update recurring payment status (pause, resume, cancel)"""
    user = await auth_admin(request)
    data = await request.json()
    now = datetime.utcnow()

    contract = await get_db().rental_contracts.find_one({"_id": ObjectId(contract_id)})
    if not contract:
        raise HTTPException(status_code=404, detail="Contrato no encontrado")

    recurring = contract.get('recurring_payment')
    if not recurring:
        raise HTTPException(status_code=404, detail="No hay pago recurrente configurado")

    action = data.get('action', '')
    update_fields = {"updated_at": now}

    if action == 'pause':
        update_fields['recurring_payment.status'] = 'paused'
        msg = "Pago recurrente pausado"
    elif action == 'resume':
        update_fields['recurring_payment.status'] = 'active'
        # Recalculate next charge date
        charge_day = recurring.get('charge_day', 1)
        next_month = now.month + 1 if now.day >= charge_day else now.month
        next_year = now.year
        if next_month > 12:
            next_month = 1
            next_year += 1
        update_fields['recurring_payment.next_charge_date'] = f"{next_year}-{str(next_month).zfill(2)}-{str(charge_day).zfill(2)}"
        msg = "Pago recurrente reanudado"
    elif action == 'cancel':
        update_fields['recurring_payment.status'] = 'cancelled'
        update_fields['recurring_payment.enabled'] = False
        msg = "Pago recurrente cancelado"
    elif action == 'update':
        # Update configuration
        if 'charge_day' in data:
            new_day = int(data['charge_day'])
            if 1 <= new_day <= 28:
                update_fields['recurring_payment.charge_day'] = new_day
        if 'amount' in data:
            update_fields['recurring_payment.amount'] = float(data['amount'])
        if 'customer_vault_id' in data:
            update_fields['recurring_payment.customer_vault_id'] = data['customer_vault_id']
            update_fields['recurring_payment.payment_method'] = data.get('payment_method', recurring.get('payment_method', 'card'))
            update_fields['recurring_payment.vault_customer_name'] = data.get('vault_customer_name', '')
            update_fields['recurring_payment.vault_display'] = data.get('vault_display', '')
        if 'auto_late_fee' in data:
            update_fields['recurring_payment.auto_late_fee'] = bool(data['auto_late_fee'])
        msg = "Pago recurrente actualizado"
    else:
        raise HTTPException(status_code=400, detail="Acción inválida. Use: pause, resume, cancel, update")

    await get_db().rental_contracts.update_one({"_id": ObjectId(contract_id)}, {"$set": update_fields})
    return {"success": True, "message": msg}


@router.get('/admin/recurring-rental-payments')
async def list_recurring_payments(request: Request):
    """List all contracts with recurring payments configured"""
    user = await auth_admin(request)

    cursor = get_db().rental_contracts.find({"recurring_payment.enabled": True}).sort("created_at", -1)
    results = []
    async for c in cursor:
        item = {
            "_id": str(c['_id']),
            "contract_number": c.get('contract_number', ''),
            "property_address": c.get('property_address', ''),
            "tenant_name": c.get('tenant_name', ''),
            "rent_amount": c.get('rent_amount', 0),
            "contract_status": c.get('status', ''),
            "recurring": {},
        }
        rp = c.get('recurring_payment', {})
        item['recurring'] = {
            "status": rp.get('status', 'unknown'),
            "charge_day": rp.get('charge_day', 1),
            "amount": rp.get('amount', 0),
            "payment_method": rp.get('payment_method', ''),
            "vault_display": rp.get('vault_display', ''),
            "next_charge_date": rp.get('next_charge_date', ''),
            "last_charged_at": rp.get('last_charged_at', '').isoformat() if isinstance(rp.get('last_charged_at'), datetime) else rp.get('last_charged_at', ''),
            "successful_charges": rp.get('successful_charges', 0),
            "failed_charges": rp.get('failed_charges', 0),
            "total_charged": rp.get('total_charged', 0),
        }
        results.append(item)

    return {"success": True, "recurring_payments": results, "count": len(results)}


# ─── Recurring Payment Processor (called by scheduler) ─────────────────────
async def process_recurring_rental_payments():
    """
    Process all due recurring rental payments.
    Should be called daily by the scheduler (e.g., at 8:00 AM).
    """
    if not get_db():
        logging.warning("⚠️ Rental DB not initialized, skipping recurring payments")
        return {"processed": 0, "success": 0, "failed": 0}

    now = datetime.utcnow()
    today_str = now.strftime('%Y-%m-%d')
    today_day = now.day

    logging.info(f"🔄 Processing recurring rental payments for {today_str}...")

    # Find all active contracts with active recurring payments due today
    query = {
        "status": "active",
        "recurring_payment.enabled": True,
        "recurring_payment.status": "active",
        "recurring_payment.charge_day": today_day,
    }

    cursor = get_db().rental_contracts.find(query)
    processed = 0
    success = 0
    failed = 0

    async for contract in cursor:
        contract_id = str(contract['_id'])
        recurring = contract.get('recurring_payment', {})
        vault_id = recurring.get('customer_vault_id', '')
        amount = recurring.get('amount', contract.get('rent_amount', 0))
        payment_method = recurring.get('payment_method', 'card')

        # Check if already charged this month (avoid double charges)
        last_charged = recurring.get('last_charged_at')
        if last_charged and isinstance(last_charged, datetime):
            if last_charged.month == now.month and last_charged.year == now.year:
                logging.info(f"⏭️ Contract {contract.get('contract_number', '')} already charged this month, skipping")
                continue

        processed += 1
        charge_result = {
            "date": now,
            "amount": amount,
            "contract_number": contract.get('contract_number', ''),
            "tenant_name": contract.get('tenant_name', ''),
            "property_address": contract.get('property_address', ''),
        }

        try:
            # Charge via NMI vault
            from merchant_one_enhanced import charge_vault_customer
            desc = f"Renta Recurrente {contract.get('property_address', '')} - {contract.get('tenant_name', '')} - {now.strftime('%B %Y')}"
            nmi_result = await charge_vault_customer(
                customer_vault_id=vault_id,
                amount=amount,
                order_description=desc
            )

            if nmi_result.get('success'):
                # Create payment record
                pay_count = await get_db().rental_payments.count_documents({})
                receipt_number = f"REC-{now.year}-{str(pay_count + 1).zfill(4)}"

                payment_doc = {
                    "receipt_number": receipt_number,
                    "contract_id": contract_id,
                    "property_id": contract.get('property_id', ''),
                    "property_address": contract.get('property_address', ''),
                    "tenant_id": contract.get('tenant_id', ''),
                    "tenant_name": contract.get('tenant_name', ''),
                    "amount": amount,
                    "late_fee": 0,
                    "total_paid": amount,
                    "payment_method": payment_method,
                    "period_month": now.strftime('%B'),
                    "period_year": now.year,
                    "payment_date": now,
                    "status": "completed",
                    "notes": "Pago recurrente automático",
                    "recorded_by": "sistema_recurrente",
                    "is_recurring": True,
                    "created_at": now,
                    "nmi_transaction": {
                        "transaction_id": nmi_result.get('transactionId', ''),
                        "vault_id": vault_id,
                        "response_text": nmi_result.get('responseText', ''),
                        "method": payment_method,
                    },
                    "customer_vault_id": vault_id,
                }
                await get_db().rental_payments.insert_one(payment_doc)

                charge_result['status'] = 'success'
                charge_result['transaction_id'] = nmi_result.get('transactionId', '')
                charge_result['receipt_number'] = receipt_number

                # Update contract recurring stats
                next_month = now.month + 1
                next_year = now.year
                if next_month > 12:
                    next_month = 1
                    next_year += 1
                charge_day = recurring.get('charge_day', 1)
                next_charge = f"{next_year}-{str(next_month).zfill(2)}-{str(charge_day).zfill(2)}"

                await get_db().rental_contracts.update_one(
                    {"_id": ObjectId(contract_id)},
                    {
                        "$set": {
                            "recurring_payment.last_charged_at": now,
                            "recurring_payment.next_charge_date": next_charge,
                            "updated_at": now,
                        },
                        "$inc": {
                            "recurring_payment.successful_charges": 1,
                            "recurring_payment.total_charged": amount,
                        },
                        "$push": {
                            "recurring_payment.charge_history": {
                                "$each": [charge_result],
                                "$slice": -24,  # Keep last 24 entries
                            }
                        }
                    }
                )

                success += 1
                logging.info(f"✅ Recurring payment {receipt_number}: ${amount:,.2f} charged for {contract.get('tenant_name', '')} — {contract.get('property_address', '')}")

            else:
                raise Exception(nmi_result.get('responseText', 'Cobro rechazado'))

        except Exception as e:
            charge_result['status'] = 'failed'
            charge_result['error'] = str(e)

            await get_db().rental_contracts.update_one(
                {"_id": ObjectId(contract_id)},
                {
                    "$inc": {"recurring_payment.failed_charges": 1},
                    "$push": {
                        "recurring_payment.charge_history": {
                            "$each": [charge_result],
                            "$slice": -24,
                        }
                    },
                    "$set": {"updated_at": now},
                }
            )

            failed += 1
            logging.error(f"❌ Recurring payment failed for {contract.get('tenant_name', '')}: {e}")

    result = {"processed": processed, "success": success, "failed": failed, "date": today_str}
    logging.info(f"🔄 Recurring rental payments complete: {processed} processed, {success} success, {failed} failed")
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# RENTAL CONFIGURATION / SETTINGS
# ═══════════════════════════════════════════════════════════════════════════════

@router.get('/admin/rental-config')
async def get_rental_config(request: Request):
    """Get rental company configuration"""
    user = await auth_admin(request)

    config = await get_db().rental_config.find_one({"type": "company"})
    if not config:
        # Return defaults
        config = {
            "type": "company",
            "name": "Ross House Rentals LLC",
            "address": "305 Bruce Ave, Dumas, TX 79029",
            "phone": "(806) 934-2018",
            "email": "info@rosshouserentals.com",
            "website": "www.rosshouserentals.com",
            "state": "Texas",
            "county": "Moore",
            "late_fee_default": 50,
            "grace_days_default": 5,
            "lease_clauses": {
                "acceleration": True,
                "mold_addendum": True,
                "bedbug_addendum": True,
                "military_scra": True,
                "lead_paint": False,
                "pet_addendum": False,
            },
            "pet_defaults": {
                "max_pets": 2,
                "max_weight": 50,
                "deposit": 250,
                "monthly_rent": 25,
            },
            "notices": {
                "entry_notice_hours": 24,
                "termination_notice_days": 30,
                "eviction_notice_days": 3,
            },
        }
    else:
        config = serialize(config)

    return {"success": True, "config": config}


@router.put('/admin/rental-config')
async def update_rental_config(request: Request):
    """Update rental company configuration"""
    user = await auth_admin(request)
    data = await request.json()
    now = datetime.utcnow()

    allowed_fields = [
        'name', 'address', 'phone', 'email', 'website', 'state', 'county',
        'late_fee_default', 'grace_days_default',
        'lease_clauses', 'pet_defaults', 'notices',
        'stripe_secret_key', 'stripe_publishable_key', 'stripe_enabled',
        'payment_methods',
        'commission_rate', 'connect_enabled',
    ]

    update_data = {"type": "company", "updated_at": now, "updated_by": user.get('email', 'admin')}
    for field in allowed_fields:
        if field in data:
            if field in ('late_fee_default', 'grace_days_default'):
                update_data[field] = float(data[field])
            else:
                update_data[field] = data[field]

    # Upsert (insert if not exists, update if exists)
    await get_db().rental_config.update_one(
        {"type": "company"},
        {"$set": update_data},
        upsert=True
    )

    return {"success": True, "message": "Configuración de renta actualizada exitosamente"}


# ─── Admin Signature (Reusable landlord signature) ─────────────────────────
@router.get('/admin/admin-signature')
async def get_admin_signature(request: Request):
    """Get the saved admin/landlord signature"""
    user = await auth_admin(request)
    
    signature_doc = await get_db().admin_signatures.find_one({"type": "landlord_default"})
    if signature_doc:
        return {"success": True, "signature": signature_doc.get("image_data")}
    return {"success": True, "signature": None}


@router.put('/admin/admin-signature')
async def save_admin_signature(request: Request):
    """Save or update the admin/landlord signature"""
    user = await auth_admin(request)
    data = await request.json()
    now = datetime.utcnow()

    signature_data = data.get('signature', '')
    if not signature_data:
        raise HTTPException(status_code=400, detail="No se recibió firma")

    await get_db().admin_signatures.update_one(
        {"type": "landlord_default"},
        {"$set": {
            "type": "landlord_default",
            "image_data": signature_data,
            "updated_at": now,
            "updated_by": user.get('email', 'admin')
        }},
        upsert=True
    )

    return {"success": True, "message": "Firma guardada exitosamente"}


@router.delete('/admin/admin-signature')
async def delete_admin_signature(request: Request):
    """Delete the admin/landlord signature"""
    user = await auth_admin(request)
    
    await get_db().admin_signatures.delete_one({"type": "landlord_default"})
    
    return {"success": True, "message": "Firma eliminada exitosamente"}


@router.put('/admin/rental-contracts/{contract_id}/addendums')
async def update_contract_addendums(contract_id: str, request: Request):
    """Update contract addendums (pets, mold, bedbug, military, lead paint)"""
    user = await auth_admin(request)
    data = await request.json()
    now = datetime.utcnow()

    contract = await get_db().rental_contracts.find_one({"_id": ObjectId(contract_id)})
    if not contract:
        raise HTTPException(status_code=404, detail="Contrato no encontrado")

    addendums = contract.get('addendums', {})
    # Merge new data
    for key in ['pets', 'mold', 'bedbug', 'military', 'lead_paint', 'pet_details']:
        if key in data:
            addendums[key] = data[key]

    await get_db().rental_contracts.update_one(
        {"_id": ObjectId(contract_id)},
        {"$set": {"addendums": addendums, "updated_at": now}}
    )

    return {"success": True, "message": "Addendums del contrato actualizados"}


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 2: PROPERTY PHOTOS & MOVE-IN/OUT CHECKLISTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.post('/admin/properties/{property_id}/photos')
async def upload_property_photo(property_id: str, request: Request):
    """Upload a photo for a property via base64"""
    user = await auth_admin(request)
    data = await request.json()

    prop = await get_db().properties.find_one({"_id": ObjectId(property_id)})
    if not prop:
        raise HTTPException(status_code=404, detail="Propiedad no encontrada")

    image_data = data.get('image_data', '')
    filename = data.get('filename', 'photo.jpg')
    content_type = data.get('content_type', 'image/jpeg')
    caption = data.get('caption', '')
    category = data.get('category', 'other')  # exterior, kitchen, bathroom, bedroom, living_room, patio, garage, other

    if not image_data:
        raise HTTPException(status_code=400, detail="No se proporcionaron datos de imagen")

    # Decode base64
    if ',' in image_data:
        image_data = image_data.split(',', 1)[1]

    try:
        file_bytes = base64.b64decode(image_data)
    except Exception:
        raise HTTPException(status_code=400, detail="Datos de imagen inválidos")

    if len(file_bytes) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="La imagen excede 10MB")

    # Try to load storage key from DB config if not in env
    from rental_storage_service import set_emergent_key, upload_property_photo as _upload_photo
    config = await get_db().api_config.find_one({"_id": "main"})
    if config and config.get("EMERGENT_LLM_KEY"):
        set_emergent_key(config["EMERGENT_LLM_KEY"])

    try:
        photo_info = _upload_photo(property_id, file_bytes, filename, content_type)
    except Exception as e:
        logger.error(f"Photo upload error: {e}")
        raise HTTPException(status_code=500, detail=f"Error subiendo foto: {str(e)}")

    photo_info['caption'] = caption
    photo_info['category'] = category
    photo_info['is_deleted'] = False

    # Save reference in DB
    await get_db().property_photos.insert_one({
        **photo_info,
        "property_id": property_id,
        "uploaded_by": user.get('email', 'admin'),
    })

    # Also add to property's photos array
    storage_path = photo_info.get('storage_path', photo_info.get('base64_data', ''))
    await get_db().properties.update_one(
        {"_id": ObjectId(property_id)},
        {"$push": {"photos": storage_path}}
    )

    return {"success": True, "message": "Foto subida exitosamente", "photo": photo_info}


@router.get('/admin/properties/{property_id}/photos')
async def list_property_photos(property_id: str, request: Request):
    """List all photos for a property"""
    user = await auth_admin(request)
    photos = await get_db().property_photos.find(
        {"property_id": property_id, "is_deleted": {"$ne": True}}
    ).sort("uploaded_at", -1).to_list(100)
    
    result_photos = [serialize(p) for p in photos]
    
    # Fallback: If property_photos collection is empty, build list from property.photos array
    if not result_photos:
        prop = await get_db().properties.find_one({"_id": ObjectId(property_id)})
        if prop and prop.get("photos"):
            for i, path_or_url in enumerate(prop["photos"]):
                if isinstance(path_or_url, str) and path_or_url:
                    storage_path = path_or_url
                    # Build a clean URL path
                    clean_path = path_or_url
                    if clean_path.startswith("ross-rentals/"):
                        clean_path = clean_path[len("ross-rentals/"):]
                    result_photos.append({
                        "_id": f"legacy_{i}",
                        "file_id": f"legacy_{i}",
                        "property_id": property_id,
                        "storage_path": storage_path,
                        "url": f"/api/public/property-file/{clean_path}",
                        "filename": path_or_url.split("/")[-1] if "/" in path_or_url else path_or_url,
                        "caption": "",
                        "is_legacy": True,
                    })
    
    return {"success": True, "photos": result_photos}


@router.delete('/admin/properties/{property_id}/photos/{file_id}')
async def delete_property_photo(property_id: str, file_id: str, request: Request):
    """Soft-delete a property photo"""
    user = await auth_admin(request)
    await get_db().property_photos.update_one(
        {"file_id": file_id, "property_id": property_id},
        {"$set": {"is_deleted": True}}
    )
    return {"success": True, "message": "Foto eliminada"}


@router.put('/admin/properties/{property_id}/photos/{file_id}')
async def update_property_photo(property_id: str, file_id: str, request: Request):
    """Update a property photo's category and/or caption"""
    user = await auth_admin(request)
    data = await request.json()
    update_fields = {}
    if 'category' in data:
        update_fields['category'] = data['category']
    if 'caption' in data:
        update_fields['caption'] = data['caption']
    if not update_fields:
        raise HTTPException(status_code=400, detail="No fields to update")
    result = await get_db().property_photos.update_one(
        {"file_id": file_id, "property_id": property_id},
        {"$set": update_fields}
    )
    return {"success": True, "message": "Foto actualizada", "modified": result.modified_count}


@router.get('/admin/rental-files/{path:path}')
async def serve_rental_file(path: str, request: Request):
    """Serve a file from object storage (admin auth required)"""
    user = await auth_admin(request)
    from rental_storage_service import get_object, set_emergent_key
    from fastapi.responses import Response
    try:
        # Ensure storage key is loaded
        config = await get_db().api_config.find_one({"_id": "main"})
        if config and config.get("EMERGENT_LLM_KEY"):
            set_emergent_key(config["EMERGENT_LLM_KEY"])
        data, content_type = get_object(path)
        return Response(content=data, media_type=content_type,
                        headers={"Cache-Control": "public, max-age=86400"})
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Archivo no encontrado: {str(e)}")


@router.get('/public/property-photos/{path:path}')
async def serve_property_photo_public(path: str):
    """Serve property photos publicly (no auth - for <img> tags)"""
    from rental_storage_service import get_object, set_emergent_key
    from fastapi.responses import Response
    try:
        config = await get_db().api_config.find_one({"_id": "main"})
        if config and config.get("EMERGENT_LLM_KEY"):
            set_emergent_key(config["EMERGENT_LLM_KEY"])
        full_path = f"ross-rentals/{path}"
        data, content_type = get_object(full_path)
        return Response(content=data, media_type=content_type,
                        headers={"Cache-Control": "public, max-age=86400"})
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Foto no encontrada")


# ─── MOVE-IN / MOVE-OUT CHECKLISTS ───────────────────────────────────────────

CHECKLIST_ROOMS = [
    "living_room", "kitchen", "bedroom_1", "bedroom_2", "bedroom_3",
    "bathroom_1", "bathroom_2", "dining_room", "garage", "backyard",
    "front_yard", "laundry", "hallway", "closets", "exterior"
]

CHECKLIST_ITEMS = [
    "walls", "ceiling", "floor", "doors", "windows", "blinds",
    "light_fixtures", "outlets", "paint", "cabinets", "countertops",
    "appliances", "plumbing", "smoke_detector", "locks", "general_cleanliness"
]


@router.post('/admin/rental-contracts/{contract_id}/checklist')
async def create_or_update_checklist(contract_id: str, request: Request):
    """Create or update a move-in/move-out checklist"""
    user = await auth_admin(request)
    data = await request.json()
    now = datetime.utcnow()

    contract = await get_db().rental_contracts.find_one({"_id": ObjectId(contract_id)})
    if not contract:
        raise HTTPException(status_code=404, detail="Contrato no encontrado")

    checklist_type = data.get('type', 'move_in')  # move_in or move_out
    rooms = data.get('rooms', {})
    notes = data.get('notes', '')

    checklist_doc = {
        "contract_id": contract_id,
        "property_id": contract.get('property_id', ''),
        "tenant_id": contract.get('tenant_id', ''),
        "tenant_name": contract.get('tenant_name', ''),
        "property_address": contract.get('property_address', ''),
        "type": checklist_type,
        "rooms": rooms,
        "notes": notes,
        "photos": [],
        "status": data.get('status', 'in_progress'),  # in_progress, completed, signed
        "inspected_by": user.get('email', 'admin'),
        "inspection_date": data.get('inspection_date', now.strftime('%Y-%m-%d')),
        "tenant_signature": None,
        "landlord_signature": None,
        "created_at": now,
        "updated_at": now,
    }

    # Upsert by contract_id + type
    result = await get_db().rental_checklists.update_one(
        {"contract_id": contract_id, "type": checklist_type},
        {"$set": checklist_doc},
        upsert=True
    )

    return {
        "success": True,
        "message": f"Checklist de {'entrada' if checklist_type == 'move_in' else 'salida'} guardado",
        "checklist_id": str(result.upserted_id) if result.upserted_id else contract_id,
    }


@router.get('/admin/rental-contracts/{contract_id}/checklist')
async def get_checklists(contract_id: str, request: Request):
    """Get checklists for a contract"""
    user = await auth_admin(request)
    checklists = await get_db().rental_checklists.find(
        {"contract_id": contract_id}
    ).to_list(10)
    return {"success": True, "checklists": [serialize(c) for c in checklists]}


@router.post('/admin/rental-contracts/{contract_id}/checklist-photo')
async def upload_checklist_photo(contract_id: str, request: Request):
    """Upload a photo for a checklist room"""
    user = await auth_admin(request)
    data = await request.json()

    checklist_type = data.get('type', 'move_in')
    room = data.get('room', 'general')
    image_data = data.get('image_data', '')
    filename = data.get('filename', 'photo.jpg')
    content_type = data.get('content_type', 'image/jpeg')
    caption = data.get('caption', '')
    item = data.get('item', '')  # Which checklist item this photo is for

    if not image_data:
        raise HTTPException(status_code=400, detail="No se proporcionaron datos de imagen")

    if ',' in image_data:
        image_data = image_data.split(',', 1)[1]

    try:
        file_bytes = base64.b64decode(image_data)
    except Exception:
        raise HTTPException(status_code=400, detail="Datos de imagen inválidos")

    from rental_storage_service import upload_checklist_photo as _upload_check
    photo_info = _upload_check(contract_id, checklist_type, room, file_bytes, filename, content_type)
    photo_info['caption'] = caption
    photo_info['item'] = item
    photo_info['is_deleted'] = False

    # Save in DB
    await get_db().checklist_photos.insert_one({
        **photo_info,
        "contract_id": contract_id,
        "uploaded_by": user.get('email', 'admin'),
    })

    # Also add to checklist's photos array
    await get_db().rental_checklists.update_one(
        {"contract_id": contract_id, "type": checklist_type},
        {"$push": {"photos": {
            "file_id": photo_info['file_id'],
            "storage_path": photo_info['storage_path'],
            "room": room,
            "item": item,
            "caption": caption,
        }}}
    )

    return {"success": True, "message": "Foto del checklist subida", "photo": photo_info}


@router.get('/admin/rental-contracts/{contract_id}/checklist-photos')
async def list_checklist_photos(contract_id: str, request: Request):
    """List all checklist photos for a contract"""
    user = await auth_admin(request)
    from urllib.parse import parse_qs
    params = parse_qs(str(request.url.query))
    checklist_type = params.get('type', [None])[0]

    query = {"contract_id": contract_id, "is_deleted": {"$ne": True}}
    if checklist_type:
        query["checklist_type"] = checklist_type

    photos = await get_db().checklist_photos.find(query).sort("uploaded_at", -1).to_list(200)
    return {"success": True, "photos": [serialize(p) for p in photos]}


@router.post('/admin/rental-contracts/{contract_id}/checklist-sign')
async def sign_checklist(contract_id: str, request: Request):
    """Sign a checklist (tenant or landlord)"""
    user = await auth_admin(request)
    data = await request.json()
    now = datetime.utcnow()

    checklist_type = data.get('type', 'move_in')
    signer = data.get('signer', 'landlord')  # landlord or tenant
    signature_data = data.get('signature', '')

    sig_field = f"{signer}_signature"
    update = {
        sig_field: {
            "image_data": signature_data,
            "signed_at": now.isoformat(),
            "signer": signer,
        },
        "updated_at": now,
    }

    # If both signatures exist, mark as completed
    checklist = await get_db().rental_checklists.find_one(
        {"contract_id": contract_id, "type": checklist_type}
    )
    if checklist:
        other = "tenant_signature" if signer == "landlord" else "landlord_signature"
        if checklist.get(other):
            update["status"] = "signed"

    await get_db().rental_checklists.update_one(
        {"contract_id": contract_id, "type": checklist_type},
        {"$set": update}
    )

    return {"success": True, "message": f"Checklist firmado por {signer}"}


# ─── PROPERTY GEOLOCATION UPDATE ─────────────────────────────────────────────

@router.put('/admin/properties/{property_id}/location')
async def update_property_location(property_id: str, request: Request):
    """Update property geolocation (lat/lng)"""
    user = await auth_admin(request)
    data = await request.json()

    prop = await get_db().properties.find_one({"_id": ObjectId(property_id)})
    if not prop:
        raise HTTPException(status_code=404, detail="Propiedad no encontrada")

    await get_db().properties.update_one(
        {"_id": ObjectId(property_id)},
        {"$set": {
            "latitude": float(data.get('latitude', 0)),
            "longitude": float(data.get('longitude', 0)),
            "updated_at": datetime.utcnow(),
        }}
    )

    return {"success": True, "message": "Ubicación actualizada"}



