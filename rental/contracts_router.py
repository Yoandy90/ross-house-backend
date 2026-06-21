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

    # ── Auto-send PDF by email when contract becomes ACTIVE ──
    final_status = update.get("status", lease.get("status"))
    if final_status == "active" and lease.get("status") != "active":
        try:
            # Reload the full updated contract (with all signatures) for the PDF
            fresh = await get_db().rental_contracts.find_one({"_id": ObjectId(lease_id)})
            if fresh:
                await _email_signed_lease_pdf(fresh)
        except Exception as e:
            logging.warning(f"⚠️ Auto-email signed lease PDF failed: {e}")

    return {
        "success": True,
        "new_status": final_status,
        "message": f"Firma de {signer_role} guardada exitosamente"
    }


async def _email_signed_lease_pdf(contract: dict):
    """Generate the signed lease PDF and email it (as attachment) to tenant + admins.
    Best-effort: any failure is logged but does not interrupt the signing flow.
    """
    import os as _os
    import base64 as _base64
    db = get_db()

    # ── 1) Resolve recipients ──
    tenant_email = (contract.get("tenant_email") or "").strip().lower()
    if not tenant_email and contract.get("tenant_id"):
        try:
            t = await db.tenants.find_one({"_id": ObjectId(contract["tenant_id"])})
            if t:
                tenant_email = (t.get("email") or "").strip().lower()
        except Exception:
            pass

    admin_emails_raw = _os.getenv("RENTAL_ADMIN_EMAILS") or "yoandyross@gmail.com"
    admin_list = [e.strip().lower() for e in admin_emails_raw.split(",") if e.strip()]
    # Support override via _force_recipients (used by admin manual send endpoint)
    forced = contract.get("_force_recipients") if isinstance(contract, dict) else None
    if forced:
        recipients = [r.strip().lower() for r in forced if r.strip()]
    else:
        recipients = list({*(admin_list or []), *([tenant_email] if tenant_email else [])})
    if not recipients:
        logging.info("ℹ️ No recipients for signed lease email")
        return

    # ── 2) Generate the PDF ──
    config = await db.rental_config.find_one({"type": "company"}) or {}
    if not contract.get("admin_signature") or not contract.get("admin_signature", {}).get("image_data"):
        try:
            saved_admin_sig = await db.admin_signatures.find_one({"type": "landlord_default"})
            if saved_admin_sig and saved_admin_sig.get("image_data"):
                config["saved_admin_signature"] = saved_admin_sig
        except Exception:
            pass
    tenant_photo_url = None
    if contract.get("tenant_id"):
        try:
            t = await db.tenants.find_one({"_id": ObjectId(contract["tenant_id"])})
            if t:
                tenant_photo_url = t.get("photo_url", "")
        except Exception:
            pass

    from rental_pdf_service import generate_rental_contract_pdf
    pdf_b64 = generate_rental_contract_pdf(contract, config=config, tenant_photo_url=tenant_photo_url)
    if not pdf_b64:
        logging.warning("PDF generation returned empty")
        return

    # ── 3) Send via SendGrid with attachment ──
    sendgrid_key = _os.getenv("SENDGRID_API_KEY")
    from_email = _os.getenv("SENDGRID_FROM_EMAIL", "info@rosshouserentals.com")
    if not sendgrid_key:
        cfg = await db.api_config.find_one({"_id": "main"})
        if cfg:
            sendgrid_key = cfg.get("sendgrid_api_key") or cfg.get("SENDGRID_API_KEY")
            from_email = cfg.get("sendgrid_from_email", from_email)
    if not sendgrid_key:
        logging.info("ℹ️ SENDGRID_API_KEY missing — signed lease email skipped")
        return

    try:
        import sendgrid
        from sendgrid.helpers.mail import Mail, Email, To, Content, Attachment, FileContent, FileName, FileType, Disposition
        sg = sendgrid.SendGridAPIClient(api_key=sendgrid_key)

        contract_number = contract.get("contract_number", str(contract.get("_id", ""))[:8])
        property_address = contract.get("property_address", "su propiedad")
        rent = contract.get("rent_amount", 0)
        start_date = contract.get("start_date", "")
        if hasattr(start_date, "strftime"):
            start_date = start_date.strftime("%Y-%m-%d")
        filename = f"Lease_Agreement_{contract_number}.pdf"

        for recipient in recipients:
            is_tenant = recipient == tenant_email
            subject = (
                f"✅ Tu contrato firmado — {property_address}"
                if is_tenant
                else f"✅ Contrato firmado: {contract_number} ({property_address})"
            )

            if is_tenant:
                html = f"""
                <div style="font-family:Helvetica,Arial,sans-serif;max-width:560px;margin:auto;background:#0b1220;color:#fff;border-radius:14px;padding:24px;">
                  <div style="background:linear-gradient(135deg,#10b981,#06b6d4);padding:14px;border-radius:10px;text-align:center;">
                    <h2 style="margin:0;color:#fff;">✅ Contrato firmado y activo</h2>
                  </div>
                  <p style="color:#cbd5e1;margin-top:18px;">¡Felicidades! Tu contrato de arrendamiento ha sido firmado por todas las partes y está oficialmente activo.</p>
                  <div style="margin:14px 0;padding:14px;background:#111827;border-radius:10px;color:#e2e8f0;font-size:14px;">
                    <div><strong style="color:#94a3b8;">Contrato:</strong> {contract_number}</div>
                    <div><strong style="color:#94a3b8;">Propiedad:</strong> {property_address}</div>
                    <div><strong style="color:#94a3b8;">Renta mensual:</strong> ${rent:,.2f}</div>
                    <div><strong style="color:#94a3b8;">Inicio:</strong> {start_date}</div>
                  </div>
                  <p style="color:#cbd5e1;">📎 Encontrarás el <strong>PDF completo del contrato firmado adjunto</strong> a este email. Guárdalo para tus registros.</p>
                  <p style="color:#cbd5e1;font-size:13px;margin-top:14px;">También puedes acceder a tu portal en cualquier momento:</p>
                  <a href="https://www.rosshouserentals.com/tenant" style="display:inline-block;background:#10b981;color:#fff;padding:12px 20px;border-radius:10px;text-decoration:none;font-weight:bold;">Ir a mi portal →</a>
                  <p style="color:#64748b;font-size:11px;margin-top:18px;border-top:1px solid #1e293b;padding-top:12px;">— Ross House Rentals · Dumas, TX · (806) 934-2018</p>
                </div>
                """
                plain = f"Contrato firmado y activo.\nContrato: {contract_number}\nPropiedad: {property_address}\nRenta: ${rent:,.2f}\nInicio: {start_date}\n\nEl PDF está adjunto a este email."
            else:
                html = f"""
                <div style="font-family:Helvetica,Arial,sans-serif;max-width:560px;margin:auto;background:#0b1220;color:#fff;border-radius:14px;padding:24px;">
                  <div style="background:linear-gradient(135deg,#3b82f6,#06b6d4);padding:14px;border-radius:10px;text-align:center;">
                    <h2 style="margin:0;color:#fff;">📋 Contrato firmado por todas las partes</h2>
                  </div>
                  <div style="margin:14px 0;padding:14px;background:#111827;border-radius:10px;color:#e2e8f0;font-size:14px;">
                    <div><strong style="color:#94a3b8;">Contrato:</strong> {contract_number}</div>
                    <div><strong style="color:#94a3b8;">Propiedad:</strong> {property_address}</div>
                    <div><strong style="color:#94a3b8;">Inquilino:</strong> {contract.get('tenant_name','')} ({tenant_email or 'sin email'})</div>
                    <div><strong style="color:#94a3b8;">Renta:</strong> ${rent:,.2f}/mes</div>
                    <div><strong style="color:#94a3b8;">Inicio:</strong> {start_date}</div>
                  </div>
                  <p style="color:#cbd5e1;font-size:13px;">📎 PDF firmado adjunto.</p>
                  <a href="https://www.rosshouserentals.com/admin/contratos" style="display:inline-block;background:#3b82f6;color:#fff;padding:12px 20px;border-radius:10px;text-decoration:none;font-weight:bold;">Ver en panel admin →</a>
                </div>
                """
                plain = f"Contrato firmado: {contract_number} en {property_address}. Inquilino: {contract.get('tenant_name','')}. PDF adjunto."

            mail = Mail(
                from_email=Email(from_email, "Ross House Rentals"),
                to_emails=To(recipient),
                subject=subject,
                plain_text_content=Content("text/plain", plain),
            )
            mail.add_content(Content("text/html", html))
            attachment = Attachment(
                FileContent(pdf_b64),
                FileName(filename),
                FileType("application/pdf"),
                Disposition("attachment"),
            )
            mail.attachment = attachment
            try:
                sg.client.mail.send.post(request_body=mail.get())
                logging.info(f"📧 Signed lease PDF emailed to {recipient}")
            except Exception as se:
                logging.warning(f"SendGrid send failed for {recipient}: {se}")
    except Exception as e:
        logging.warning(f"⚠️ Signed lease email block failed: {e}")


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


@router.get('/admin/properties/{property_id}/contract-defaults')
async def get_contract_defaults_from_property(property_id: str, request: Request):
    """Returns suggested defaults for a new rental contract based on the property's
    saved rent/deposit. Frontend should call this when selecting a property
    in the contract form to prefill rent_amount and deposit_amount."""
    await auth_admin(request)
    try:
        prop = await get_db().properties.find_one({"_id": ObjectId(property_id)})
    except Exception:
        raise HTTPException(status_code=400, detail="ID de propiedad inválido")
    if not prop:
        raise HTTPException(status_code=404, detail="Propiedad no encontrada")

    # Try multiple field names used historically
    rent = float(prop.get('rent_amount') or prop.get('monthly_rent') or prop.get('rent') or 0)
    deposit = float(prop.get('deposit_amount') or prop.get('security_deposit') or prop.get('deposit') or 0)

    return {
        "success": True,
        "property_id": str(prop["_id"]),
        "property_address": prop.get('address', ''),
        "rent_amount": rent,
        "deposit_amount": deposit,
        "suggested_late_fee_amount": 50.0,
        "suggested_late_fee_grace_days": 5,
        "suggested_payment_due_day": 1,
    }


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
        # ── Validate ALL required signatures before activating ─────────
        force = bool(data.get('force_activate', False))  # admin override
        missing = []
        if not contract.get('tenant_signature'):
            missing.append('Inquilino')
        # Either landlord or admin signature is acceptable as "office" signer
        if not (contract.get('landlord_signature') or contract.get('admin_signature')):
            missing.append('Propietario/Admin')
        if missing and not force:
            raise HTTPException(
                status_code=400,
                detail=f"No se puede activar el contrato. Firmas faltantes: {', '.join(missing)}. "
                       f"Para forzar la activación envía force_activate=true."
            )

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

    # Accept legacy `late_fee` (from Next.js admin) as alias of `late_fee_amount`
    if 'late_fee' in data and 'late_fee_amount' not in data:
        data['late_fee_amount'] = data['late_fee']
    # Accept legacy `grace_period_days` alias
    if 'grace_period_days' in data and 'late_fee_grace_days' not in data:
        data['late_fee_grace_days'] = data['grace_period_days']
    # Accept legacy `payment_day` alias
    if 'payment_day' in data and 'payment_due_day' not in data:
        data['payment_due_day'] = data['payment_day']

    # Fields that can be updated
    editable_fields = [
        'start_date', 'end_date', 'rent_amount', 'deposit_amount',
        'payment_due_day', 'late_fee_amount', 'late_fee_grace_days',
        'terms', 'special_conditions', 'payment_method_type',
        'customer_vault_id', 'vault_display', 'vault_customer_name',
        'status',
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

    # ---- Sync late_fee_amount changes to pending invoices ----
    synced_invoices = 0
    if 'late_fee_amount' in data:
        try:
            new_late_fee = float(data['late_fee_amount']) if data['late_fee_amount'] else 0.0
            old_late_fee = float(contract.get('late_fee_amount') or contract.get('late_fee') or 0)
            if abs(new_late_fee - old_late_fee) > 0.001:
                # Update pending invoices that ALREADY have a late fee applied.
                # We don't touch pending invoices with late_fee=0 (not late yet)
                # because the cron will apply the new amount when they go past due.
                pending_cursor = get_db().rental_payments.find({
                    "contract_id": contract_id,
                    "status": "pending",
                    "late_fee": {"$gt": 0},
                })
                async for inv in pending_cursor:
                    base_amount = float(inv.get('amount') or 0)
                    new_total = base_amount + new_late_fee
                    new_paid = new_total if (inv.get('total_paid') or 0) >= (inv.get('total_due') or 0) else float(inv.get('total_paid') or 0)
                    await get_db().rental_payments.update_one(
                        {"_id": inv["_id"]},
                        {"$set": {
                            "late_fee": new_late_fee,
                            "total_due": new_total,
                            "total_paid": new_paid,
                            "updated_at": now,
                            "late_fee_synced_from_contract": True,
                        }},
                    )
                    synced_invoices += 1
        except Exception as e:
            # Don't fail the contract update if sync fails — log and continue.
            print(f"[contract update] late_fee sync error: {e}")

    msg = f"Contrato {contract.get('contract_number', '')} actualizado exitosamente"
    if synced_invoices:
        msg += f". {synced_invoices} factura(s) pendiente(s) recalculada(s) con el nuevo cargo por mora."

    return {
        "success": True,
        "message": msg,
        "synced_pending_invoices": synced_invoices,
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


@router.post('/admin/rental-contracts/{contract_id}/email-pdf')
async def admin_email_lease_pdf(contract_id: str, request: Request):
    """Admin: manually send the lease PDF by email to tenant + admins.
    Useful when admin wants to re-send the signed/draft contract.
    Optional body: { "recipients": ["custom@email.com", ...] } to override defaults.
    """
    await auth_admin(request)
    try:
        oid = ObjectId(contract_id)
    except Exception:
        raise HTTPException(status_code=400, detail="ID inválido")
    db = get_db()
    contract = await db.rental_contracts.find_one({"_id": oid})
    if not contract:
        raise HTTPException(status_code=404, detail="Contrato no encontrado")

    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    custom_recipients = body.get("recipients") if isinstance(body, dict) else None

    # Optionally override tenant_email so _email_signed_lease_pdf only emails specific recipients
    if custom_recipients and isinstance(custom_recipients, list):
        # Temporarily inject recipients via tenant_email + admin env
        # Simpler: replicate the email function inline with custom recipients
        contract_with_emails = dict(contract)
        contract_with_emails["_force_recipients"] = [r for r in custom_recipients if r]
        await _email_signed_lease_pdf(contract_with_emails)
    else:
        await _email_signed_lease_pdf(contract)

    return {"success": True, "message": "Email enviado (revisa spam si no aparece en bandeja)"}


# ─── Tenant-facing Lease PDF (signed contract download) ──────────────────
@router.get('/lease/{lease_id}/pdf')
async def tenant_download_lease_pdf(lease_id: str, request: Request):
    """Tenant: download the PDF of their own signed lease.
    Returns base64-encoded PDF. Tenant must own the lease.
    """
    import re as _re
    user = await auth_marketplace(request)
    user_email = (user.get("email") or "").strip().lower()
    user_id = str(user.get("_id", ""))
    db = get_db()

    try:
        oid = ObjectId(lease_id)
    except Exception:
        raise HTTPException(status_code=400, detail="ID inválido")

    contract = await db.rental_contracts.find_one({"_id": oid})
    if not contract:
        raise HTTPException(status_code=404, detail="Contrato no encontrado")

    # Authorization: tenant of this lease or landlord/admin
    tenant_ids = {user_id}
    async for t in db.tenants.find({"app_user_id": user_id}):
        tenant_ids.add(str(t["_id"]))
    if user_email:
        async for t in db.tenants.find({
            "email": {"$regex": f"^{_re.escape(user_email)}$", "$options": "i"}
        }):
            tenant_ids.add(str(t["_id"]))

    is_tenant = (
        (contract.get("tenant_email") or "").lower() == user_email
        or str(contract.get("tenant_id", "")) in tenant_ids
    )
    is_landlord = (str(contract.get("landlord_id", "")) == user_id)
    is_admin = user.get("role") == "admin"
    if not (is_tenant or is_landlord or is_admin):
        raise HTTPException(status_code=403, detail="No tienes acceso a este contrato")

    # Load company config + landlord signature so the PDF is identical to admin export
    config = await db.rental_config.find_one({"type": "company"}) or {}
    if not contract.get("admin_signature") or not contract.get("admin_signature", {}).get("image_data"):
        try:
            saved_admin_sig = await db.admin_signatures.find_one({"type": "landlord_default"})
            if saved_admin_sig and saved_admin_sig.get("image_data"):
                config["saved_admin_signature"] = saved_admin_sig
        except Exception:
            pass

    # Pull tenant photo if available
    tenant_photo_url = None
    if contract.get("tenant_id"):
        try:
            tenant = await db.tenants.find_one({"_id": ObjectId(contract["tenant_id"])})
            if tenant:
                tenant_photo_url = tenant.get("photo_url", "")
        except Exception:
            pass

    from rental_pdf_service import generate_rental_contract_pdf
    pdf_b64 = generate_rental_contract_pdf(contract, config=config, tenant_photo_url=tenant_photo_url)
    filename = f"Lease_Agreement_{contract.get('contract_number', lease_id)}.pdf"
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
    """List rental payments with server-side pagination and filtering.

    Query params:
      - contract_id, property_id (optional filters)
      - status: 'all' | 'paid' | 'pending'  (groups completed/paid vs pending/late/partial)
      - year: '2025' (matches period_year, falls back to payment_date.year)
      - search: substring matched against tenant_name | property_address | receipt_number
      - page: 1-based page (default 1)
      - page_size: default 50, max 200

    Returns:
      {
        success, payments[], count, total,
        page, page_size, total_pages,
        stats: { paid_count, pending_count, total_completed, total_pending }
      }

    NOTE: keeps backward compatibility — old callers without `page` still receive
    up to `page_size` (default 50) most-recent docs.
    """
    user = await auth_admin(request)
    qp = request.query_params

    contract_id = qp.get('contract_id') or None
    property_id = qp.get('property_id') or None
    status_filter = (qp.get('status') or 'all').lower()
    year_filter = qp.get('year') or 'all'
    search = (qp.get('search') or '').strip()

    try:
        page = max(1, int(qp.get('page') or 1))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = int(qp.get('page_size') or 50)
    except (TypeError, ValueError):
        page_size = 50
    page_size = max(1, min(page_size, 200))

    query: dict = {}
    if contract_id:
        query['contract_id'] = contract_id
    if property_id:
        query['property_id'] = property_id

    if status_filter == 'paid':
        query['status'] = {'$in': ['completed', 'paid']}
    elif status_filter == 'pending':
        query['status'] = {'$in': ['pending', 'late', 'partial']}

    if year_filter and year_filter != 'all':
        try:
            y = int(year_filter)
            query['$or'] = [
                {'period_year': y},
                {'payment_date': {
                    '$gte': datetime(y, 1, 1),
                    '$lt': datetime(y + 1, 1, 1),
                }},
            ]
        except ValueError:
            pass

    if search:
        # Case-insensitive partial match across common text fields
        import re as _re
        escaped = _re.escape(search)
        regex = {'$regex': escaped, '$options': 'i'}
        text_or = [
            {'tenant_name': regex},
            {'property_address': regex},
            {'receipt_number': regex},
        ]
        if '$or' in query:
            # Combine with year filter using $and
            query = {'$and': [{'$or': query.pop('$or')}, {'$or': text_or}], **query}
        else:
            query['$or'] = text_or

    db = get_db()
    total = await db.rental_payments.count_documents(query)
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = min(page, total_pages) if total > 0 else 1
    skip = (page - 1) * page_size

    cursor = (db.rental_payments
              .find(query)
              .sort("payment_date", -1)
              .skip(skip)
              .limit(page_size))
    payments = []
    async for p in cursor:
        payments.append(serialize(p))

    # Aggregate stats (across all filtered results, not just current page)
    stats_pipeline = [
        {'$match': query},
        {'$group': {
            '_id': None,
            'paid_count': {'$sum': {'$cond': [
                {'$in': [{'$ifNull': ['$status', '']}, ['completed', 'paid']]}, 1, 0
            ]}},
            'pending_count': {'$sum': {'$cond': [
                {'$in': [{'$ifNull': ['$status', '']}, ['pending', 'late']]}, 1, 0
            ]}},
            'total_completed': {'$sum': {'$cond': [
                {'$in': [{'$ifNull': ['$status', '']}, ['completed', 'paid']]},
                {'$add': [{'$ifNull': ['$amount', 0]}, {'$ifNull': ['$late_fee', 0]}]},
                0,
            ]}},
            'total_pending': {'$sum': {'$cond': [
                {'$in': [{'$ifNull': ['$status', '']}, ['pending', 'late', 'partial']]},
                {'$add': [{'$ifNull': ['$amount', 0]}, {'$ifNull': ['$late_fee', 0]}]},
                0,
            ]}},
        }}
    ]
    stats = {'paid_count': 0, 'pending_count': 0, 'total_completed': 0.0, 'total_pending': 0.0}
    try:
        async for row in db.rental_payments.aggregate(stats_pipeline):
            stats.update({k: v for k, v in row.items() if k != '_id'})
    except Exception:
        pass

    # Available years (lightweight distinct, capped)
    try:
        years_set = set()
        async for d in db.rental_payments.aggregate([
            {'$group': {'_id': '$period_year'}},
            {'$match': {'_id': {'$ne': None}}},
            {'$limit': 30},
        ]):
            if d.get('_id') is not None:
                years_set.add(str(d['_id']))
        available_years = sorted(years_set, reverse=True)
    except Exception:
        available_years = []

    return {
        "success": True,
        "payments": payments,
        "count": len(payments),
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "stats": stats,
        "available_years": available_years,
    }


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
# BULK OPERATIONS (Sprint 2 — scalability for 142+ units)
# ═══════════════════════════════════════════════════════════════════════════════

@router.post('/admin/rental-payments/bulk-reminders')
async def admin_send_bulk_payment_reminders(request: Request):
    """Send bulk payment reminders for multiple invoices at once.

    Body:
      - payment_ids: list[str]  → invoice IDs to remind
      - channel: 'email' | 'sms' | 'both'   (default 'email')
      - custom_message: str (optional) → overrides default reminder text

    For each payment:
      1. Resolves tenant email / phone via the linked rental_contract
      2. Builds a personalized reminder (period, amount, due date, late fee)
      3. Sends via SendGrid (email) and/or Twilio (SMS)

    Returns per-payment status + aggregate stats.
    """
    await auth_admin(request)
    db = get_db()
    data = await request.json()

    payment_ids = data.get('payment_ids', []) or []
    channel = (data.get('channel') or 'email').lower()
    custom_message = (data.get('custom_message') or '').strip()

    if not payment_ids:
        raise HTTPException(status_code=400, detail="Selecciona al menos una factura")
    if channel not in ('email', 'sms', 'both'):
        channel = 'email'

    # ── Resolve API credentials (env first, fallback api_config) ──
    sendgrid_key = os.getenv('SENDGRID_API_KEY')
    from_email = os.getenv('SENDGRID_FROM_EMAIL', 'info@rosshouserentals.com')
    twilio_sid = os.getenv('TWILIO_ACCOUNT_SID')
    twilio_token = os.getenv('TWILIO_AUTH_TOKEN')
    twilio_phone = os.getenv('TWILIO_PHONE_NUMBER')

    if (not sendgrid_key) or (not twilio_sid):
        try:
            cfg = await db.api_config.find_one({'_id': 'main'})
            if cfg:
                if not sendgrid_key:
                    sendgrid_key = cfg.get('sendgrid_api_key') or cfg.get('SENDGRID_API_KEY')
                    from_email = cfg.get('sendgrid_from_email', from_email)
                if not twilio_sid:
                    twilio_sid = cfg.get('twilio_account_sid') or cfg.get('TWILIO_ACCOUNT_SID')
                    twilio_token = cfg.get('twilio_auth_token') or cfg.get('TWILIO_AUTH_TOKEN')
                    twilio_phone = cfg.get('twilio_phone_number') or cfg.get('TWILIO_PHONE_NUMBER')
        except Exception:
            pass

    sg_client = None
    if channel in ('email', 'both') and sendgrid_key:
        try:
            import sendgrid
            sg_client = sendgrid.SendGridAPIClient(api_key=sendgrid_key)
        except Exception as e:
            logging.warning(f"SendGrid init failed: {e}")

    twilio_client = None
    if channel in ('sms', 'both') and twilio_sid and twilio_token and twilio_phone:
        try:
            from twilio.rest import Client
            twilio_client = Client(twilio_sid, twilio_token)
        except Exception as e:
            logging.warning(f"Twilio init failed: {e}")

    def _fmt_money(n: float) -> str:
        try:
            return f"${float(n or 0):,.2f}"
        except Exception:
            return f"${n}"

    def _month_label(period_year, period_month_num, period_month):
        names = ['Enero','Febrero','Marzo','Abril','Mayo','Junio',
                 'Julio','Agosto','Septiembre','Octubre','Noviembre','Diciembre']
        try:
            if period_month_num and 1 <= int(period_month_num) <= 12:
                return f"{names[int(period_month_num) - 1]} {period_year}"
        except Exception:
            pass
        return f"{period_month or ''} {period_year or ''}".strip()

    def _normalize_phone(raw: str) -> Optional[str]:
        if not raw:
            return None
        digits = ''.join(filter(str.isdigit, str(raw)))
        if not digits:
            return None
        if len(digits) == 10:
            return f'+1{digits}'
        if len(digits) == 11 and digits.startswith('1'):
            return f'+{digits}'
        if str(raw).startswith('+'):
            return str(raw)
        return f'+{digits}'

    results = {
        "total": len(payment_ids),
        "email_sent": 0,
        "sms_sent": 0,
        "skipped_no_contact": 0,
        "failed": 0,
        "details": [],
    }

    for pid in payment_ids:
        item = {"payment_id": pid, "email": False, "sms": False, "errors": []}
        try:
            try:
                pay = await db.rental_payments.find_one({"_id": ObjectId(pid)})
            except Exception:
                pay = None
            if not pay:
                item["errors"].append("Factura no encontrada")
                results["failed"] += 1
                results["details"].append(item)
                continue

            # Skip already paid invoices defensively
            status = (pay.get('status') or '').lower()
            if status in ('completed', 'paid', 'cancelled'):
                item["errors"].append(f"Ignorada ({status})")
                results["details"].append(item)
                continue

            tenant_email = pay.get('tenant_email', '') or ''
            tenant_phone = pay.get('tenant_phone', '') or ''
            tenant_name = pay.get('tenant_name', '') or 'Inquilino'
            property_address = pay.get('property_address', '') or ''

            # Fallback to contract for contact info
            if (not tenant_email or not tenant_phone) and pay.get('contract_id'):
                try:
                    contract = await db.rental_contracts.find_one({"_id": ObjectId(pay['contract_id'])})
                except Exception:
                    contract = None
                if contract:
                    if not tenant_email:
                        tenant_email = contract.get('tenant_email', '') or ''
                    if not tenant_phone:
                        tenant_phone = contract.get('tenant_phone', '') or ''
                    if not tenant_name:
                        tenant_name = contract.get('tenant_name', tenant_name)
                    if not property_address:
                        property_address = contract.get('property_address', property_address)

            if (channel == 'email' and not tenant_email) or \
               (channel == 'sms' and not tenant_phone) or \
               (channel == 'both' and not tenant_email and not tenant_phone):
                results["skipped_no_contact"] += 1
                item["errors"].append("Sin contacto")
                results["details"].append(item)
                continue

            # ── Build personalized reminder content ──
            period_label = _month_label(
                pay.get('period_year'),
                pay.get('period_month_num'),
                pay.get('period_month'),
            )
            amount = float(pay.get('amount', 0) or 0)
            late_fee = float(pay.get('late_fee', 0) or 0)
            total = amount + late_fee
            due_date = pay.get('due_date')
            if isinstance(due_date, datetime):
                due_str = due_date.strftime('%d/%m/%Y')
            else:
                due_str = ''

            if custom_message:
                msg_text = custom_message
            else:
                msg_lines = [
                    f"Hola {tenant_name},",
                    "",
                    f"Te recordamos que tu pago de renta correspondiente a {period_label} sigue pendiente.",
                    f"• Monto: {_fmt_money(amount)}",
                ]
                if late_fee > 0:
                    msg_lines.append(f"• Recargo por atraso: {_fmt_money(late_fee)}")
                    msg_lines.append(f"• Total a pagar: {_fmt_money(total)}")
                if due_str:
                    msg_lines.append(f"• Vencimiento: {due_str}")
                if property_address:
                    msg_lines.append(f"• Propiedad: {property_address}")
                msg_lines += [
                    "",
                    "Puedes pagar desde la app Ross House Rentals o contactarnos para coordinar.",
                    "",
                    "Gracias,",
                    "Ross House Rentals",
                ]
                msg_text = "\n".join(msg_lines)

            subject = f"Recordatorio de pago — {period_label}"

            # ── Email ──
            if channel in ('email', 'both') and tenant_email:
                if sg_client:
                    try:
                        from sendgrid.helpers.mail import Mail, Email, To, Content
                        html_msg = msg_text.replace('\n', '<br>')
                        mail = Mail(
                            from_email=Email(from_email, "Ross House Rentals"),
                            to_emails=To(tenant_email),
                            subject=subject,
                            plain_text_content=Content("text/plain", msg_text),
                        )
                        try:
                            mail.add_content(Content("text/html",
                                f"<div style='font-family:Arial,sans-serif;font-size:14px;color:#222;line-height:1.5'>{html_msg}</div>"))
                        except Exception:
                            pass
                        sg_client.client.mail.send.post(request_body=mail.get())
                        item["email"] = True
                        results["email_sent"] += 1
                    except Exception as e:
                        item["errors"].append(f"Email: {str(e)[:120]}")
                        results["failed"] += 1
                else:
                    item["errors"].append("SendGrid no configurado")

            # ── SMS ──
            if channel in ('sms', 'both') and tenant_phone:
                if twilio_client:
                    try:
                        to_phone = _normalize_phone(tenant_phone)
                        sms_body = msg_text
                        # SMS keep it concise if too long
                        if len(sms_body) > 480:
                            sms_body = (
                                f"Ross House Rentals: Hola {tenant_name}, tienes pendiente la renta de "
                                f"{period_label} por {_fmt_money(total)}"
                                + (f" (vence {due_str})" if due_str else "")
                                + ". Paga desde la app o contáctanos."
                            )
                        twilio_client.messages.create(
                            body=sms_body, from_=twilio_phone, to=to_phone
                        )
                        item["sms"] = True
                        results["sms_sent"] += 1
                    except Exception as e:
                        item["errors"].append(f"SMS: {str(e)[:120]}")
                        results["failed"] += 1
                else:
                    item["errors"].append("Twilio no configurado")

            # Stamp on the invoice
            try:
                now = datetime.utcnow()
                history_entry = {
                    "at": now,
                    "channel": channel,
                    "email_sent": item["email"],
                    "sms_sent": item["sms"],
                }
                await db.rental_payments.update_one(
                    {"_id": ObjectId(pid)},
                    {
                        "$set": {"last_reminder_at": now, "updated_at": now},
                        "$inc": {"reminder_count": 1},
                        "$push": {"reminder_history": history_entry},
                    },
                )
            except Exception:
                pass

            results["details"].append(item)
        except Exception as e:
            logging.exception(f"Bulk reminder failed for {pid}")
            item["errors"].append(str(e)[:200])
            results["failed"] += 1
            results["details"].append(item)

    # Log aggregate to message history
    try:
        await db.message_history.insert_one({
            "channel": channel,
            "subject": "Bulk payment reminders",
            "message": custom_message or "(auto-generated reminder)",
            "recipient_count": len(payment_ids),
            "sent": results["email_sent"] + results["sms_sent"],
            "failed": results["failed"],
            "kind": "bulk_payment_reminder",
            "created_at": datetime.utcnow(),
        })
    except Exception:
        pass

    return {"success": True, **results}


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

    # ── Maintenance pending (counts: pending/open/in_progress) ──
    maintenance_pending = await get_db().maintenance_requests.count_documents({
        "status": {"$in": ["pending", "open", "in_progress", "scheduled"]}
    })

    # ── Pending payments (due or overdue, not yet completed) ──
    # Counts both rental_payments with status='pending' and rental_invoices with status='pending'/'overdue'
    pending_payments_count = await get_db().rental_payments.count_documents({
        "status": {"$in": ["pending", "overdue", "scheduled"]}
    })
    # Also count unpaid invoices
    pending_invoices_count = 0
    try:
        pending_invoices_count = await get_db().rental_invoices.count_documents({
            "status": {"$in": ["pending", "overdue", "unpaid"]}
        })
    except Exception:
        pass
    pending_payments_total = pending_payments_count + pending_invoices_count

    # Pending dollar amount (sum of expected_monthly minus monthly_revenue actually paid)
    pending_amount = max(0, float(expected_monthly) - float(monthly_revenue))

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
                "pending_amount": round(pending_amount, 2),
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
            "maintenance_pending": maintenance_pending,
            "pending_payments": pending_payments_total,
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



