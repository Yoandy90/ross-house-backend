"""
Rental Properties Router
=========================
"""
import logging
import base64
from datetime import datetime, timedelta
from typing import Optional, List
from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from rental.shared import (
    get_db, auth_admin, auth_marketplace, auth_tenant,
    serialize, create_marketplace_token, create_tenant_token,
    send_rental_push_to_user, send_rental_push_to_admins,
    TENANT_JWT_SECRET,
)
from rental.tx_fmr_data import get_fmr, compute_s8_impact

router = APIRouter()


# ─── Public Photo Serving ─────────────────────────────────────────────────────

@router.get('/public/property-photos/{property_id}')
async def public_list_property_photos(property_id: str):
    """Public: List all photos for a property with full URLs"""
    db = get_db()
    photos = await db.property_photos.find(
        {"property_id": property_id, "is_deleted": {"$ne": True}}
    ).sort("uploaded_at", -1).to_list(50)

    result = []
    for p in photos:
        sp = p.get('storage_path', '')
        # Strip the app name prefix so the public URL is clean
        if sp.startswith("ross-rentals/"):
            sp = sp[len("ross-rentals/"):]
        result.append({
            "id": str(p.get("_id", "")),
            "file_id": p.get("file_id", ""),
            "url": f"/api/public/property-file/{sp}",
            "caption": p.get("caption", ""),
            "filename": p.get("filename", ""),
        })
    return {"success": True, "photos": result}


@router.get('/public/property-file/{path:path}')
async def public_serve_property_file(path: str):
    """Public: Serve a property photo from object storage (no auth required)"""
    # Only allow serving from properties/ or checklists/ paths for security
    if not path.startswith("properties/") and not path.startswith("checklists/") and not path.startswith("tenants/"):
        raise HTTPException(status_code=403, detail="Acceso denegado")
    try:
        from rental_storage_service import get_object, set_emergent_key, APP_NAME
        # Load key from DB if not in env
        try:
            config = await get_db().api_config.find_one({"_id": "main"})
            if config and config.get("EMERGENT_LLM_KEY"):
                set_emergent_key(config["EMERGENT_LLM_KEY"])
        except Exception:
            pass
        # Prepend the app name prefix to get the full storage path
        full_path = f"{APP_NAME}/{path}"
        data, content_type = get_object(full_path)
        return Response(
            content=data,
            media_type=content_type,
            headers={"Cache-Control": "public, max-age=86400"}
        )
    except Exception as e:
        logging.error(f"Error serving property file {path}: {e}")
        raise HTTPException(status_code=404, detail=f"Foto no encontrada")


@router.get('/public/section8-welcome')
async def public_section8_welcome():
    """Public endpoint: Returns Section 8 program info + properties accepting vouchers.

    Used by the mobile app Welcome screen for Section 8 voucher holders.
    Lists only properties where ``accepts_section_8`` is true.
    """
    db = get_db()
    cursor = db.properties.find({"accepts_section_8": True}).sort("created_at", -1)
    items = []
    async for p in cursor:
        prop = serialize(p)
        items.append({
            "id": prop.get("_id"),
            "address": prop.get("address", ""),
            "city": prop.get("city", ""),
            "state": prop.get("state", "TX"),
            "zip": prop.get("zip", ""),
            "bedrooms": prop.get("bedrooms", 0),
            "bathrooms": prop.get("bathrooms", 0),
            "rent": prop.get("rent_amount", prop.get("rent", 0)),
            "accepts_section_8": True,
            "s8_voucher_number": prop.get("s8_voucher_number", ""),
            "s8_pha_name": prop.get("s8_pha_name", ""),
            "photos": prop.get("photos", [])[:3] if isinstance(prop.get("photos"), list) else [],
        })
    return {
        "success": True,
        "count": len(items),
        "properties": items,
        "info": {
            "title": "Programa de Vivienda Sección 8 (HUD)",
            "description": "Aceptamos vouchers HCV de la Public Housing Authority (PHA).",
            "benefits": [
                "Renta cubierta por HUD según tu voucher",
                "Casas inspeccionadas anualmente bajo estándar HQS",
                "Sin pagos atrasados — el gobierno paga directo al landlord",
                "Apoyo bilingüe al inquilino",
            ],
            "next_step": "Comparte tu voucher con nosotros para validar elegibilidad.",
        },
    }


@router.get('/public/properties')
async def public_list_properties(request: Request):
    """Public endpoint: List all properties (own + approved marketplace)"""
    # Determine base URL for absolute photo URLs
    base_url = str(request.base_url).rstrip('/')
    # In production, use the forwarded host if behind a proxy
    forwarded_host = request.headers.get('x-forwarded-host') or request.headers.get('host')
    forwarded_proto = request.headers.get('x-forwarded-proto', 'https')
    if forwarded_host:
        base_url = f"{forwarded_proto}://{forwarded_host}"

    def resolve_photo(p: str) -> str:
        """Convert storage path to a full absolute URL"""
        if p.startswith("http"):
            return p
        elif p.startswith("ross-rentals/"):
            return f"{base_url}/api/public/property-file/{p[len('ross-rentals/'):]}"
        elif p.startswith("properties/"):
            return f"{base_url}/api/public/property-file/{p}"
        elif p.startswith("/api/"):
            return f"{base_url}{p}"
        return p

    # Own properties — show all (available, rented, maintenance)
    own_cursor = get_db().properties.find({}).sort("created_at", -1)
    properties = []
    async for p in own_cursor:
        prop = serialize(p)
        all_photos = prop.get("photos", [])
        resolved_photos = [resolve_photo(ph) for ph in all_photos if isinstance(ph, str)]
        safe_prop = {
            "id": prop.get("_id"),
            "address": prop.get("address", ""),
            "city": prop.get("city", ""),
            "state": prop.get("state", ""),
            "zip_code": prop.get("zip_code", ""),
            "property_type": prop.get("type", "house"),
            "bedrooms": prop.get("bedrooms", 0),
            "bathrooms": prop.get("bathrooms", 0),
            "square_feet": prop.get("square_feet", 0),
            "rent_amount": prop.get("rent_amount", 0),
            "deposit_amount": prop.get("deposit_amount", 0),
            "sale_price": prop.get("sale_price", 0),
            "listing_type": prop.get("listing_type", "rent"),
            "description": prop.get("notes", ""),
            "features": prop.get("features", []),
            "photos": [resolved_photos[0]] if resolved_photos else [],
            "photo_count": len(all_photos),
            "status": prop.get("status", "available"),
            "owner_type": "ross_house",
            "owner_name": "Ross House Rentals LLC",
            "section8_accepted": bool(prop.get("section8_accepted", False)),
        }
        properties.append(safe_prop)

    # Marketplace (approved third-party listings)
    mp_cursor = get_db().marketplace_listings.find({"status": "approved"}).sort("created_at", -1)
    async for m in mp_cursor:
        ml = serialize(m)
        all_photos = ml.get("photos", [])
        safe_mp = {
            "id": ml.get("_id"),
            "address": ml.get("address", ""),
            "city": ml.get("city", ""),
            "state": ml.get("state", ""),
            "zip_code": ml.get("zip_code", ""),
            "property_type": ml.get("property_type", "house"),
            "bedrooms": ml.get("bedrooms", 0),
            "bathrooms": ml.get("bathrooms", 0),
            "square_feet": ml.get("square_feet", 0),
            "rent_amount": ml.get("rent_amount", 0),
            "deposit_amount": ml.get("deposit_amount", 0),
            "sale_price": ml.get("sale_price", 0),
            "listing_type": ml.get("listing_type", "rent"),
            "description": ml.get("description", ""),
            "features": ml.get("features", []),
            "photos": [all_photos[0]] if all_photos else [],
            "photo_count": len(all_photos),
            "status": "available",
            "owner_type": "ross_house",
            "owner_name": "Ross House Rentals LLC",
        }
        properties.append(safe_mp)

    return {"success": True, "properties": properties, "count": len(properties)}


@router.get('/public/properties/{property_id}')
async def public_get_property(property_id: str, request: Request):
    """Public endpoint: Get a single property with all photos (with resolved URLs)"""
    # Determine base URL for absolute photo URLs
    base_url = str(request.base_url).rstrip('/')
    forwarded_host = request.headers.get('x-forwarded-host') or request.headers.get('host')
    forwarded_proto = request.headers.get('x-forwarded-proto', 'https')
    if forwarded_host:
        base_url = f"{forwarded_proto}://{forwarded_host}"

    def resolve_url(path: str) -> str:
        if path.startswith("http"):
            return path
        elif path.startswith("/api/"):
            return f"{base_url}{path}"
        elif path.startswith("ross-rentals/"):
            return f"{base_url}/api/public/property-file/{path[len('ross-rentals/'):]}"
        elif path.startswith("properties/"):
            return f"{base_url}/api/public/property-file/{path}"
        return path

    db = get_db()
    # Try own properties first (show all statuses since this is a detail view)
    try:
        prop = await db.properties.find_one({"_id": ObjectId(property_id)})
    except:
        prop = None

    if prop:
        prop = serialize(prop)
        prop_id_str = str(prop.get("_id", property_id))

        # Fetch categorized photos from property_photos collection
        photo_docs = await db.property_photos.find(
            {"property_id": prop_id_str, "is_deleted": {"$ne": True}}
        ).sort("uploaded_at", -1).to_list(50)

        photo_urls = []
        categories_map = {}  # {category: [photo_objs]}
        for p in photo_docs:
            sp = p.get("storage_path", "")
            if sp:
                cat = p.get("category", "other")
                photo_obj = {
                    "url": resolve_url(sp),
                    "caption": p.get("caption", ""),
                    "category": cat,
                }
                photo_urls.append(photo_obj)
                if cat not in categories_map:
                    categories_map[cat] = []
                categories_map[cat].append(photo_obj)

        # Fallback: if no photos in property_photos, try the photos array
        if not photo_urls and prop.get("photos"):
            for path_or_url in prop["photos"]:
                if isinstance(path_or_url, str):
                    photo_obj = {"url": resolve_url(path_or_url), "caption": "", "category": "other"}
                    photo_urls.append(photo_obj)
                    if "other" not in categories_map:
                        categories_map["other"] = []
                    categories_map["other"].append(photo_obj)

        # Build categories array for the app
        categories_list = []
        for cat, photos_in_cat in categories_map.items():
            categories_list.append({
                "key": cat,
                "count": len(photos_in_cat),
                "thumbnail": photos_in_cat[0]["url"] if photos_in_cat else "",
            })

        return {
            "success": True,
            "property": {
                "id": prop.get("_id"),
                "name": prop.get("name", ""),
                "address": prop.get("address", ""),
                "city": prop.get("city", ""),
                "state": prop.get("state", ""),
                "zip_code": prop.get("zip_code", ""),
                "property_type": prop.get("type", "house"),
                "bedrooms": prop.get("bedrooms", 0),
                "bathrooms": prop.get("bathrooms", 0),
                "square_feet": prop.get("square_feet", 0),
                "rent_amount": prop.get("rent_amount", 0),
                "deposit_amount": prop.get("deposit_amount", 0),
                "sale_price": prop.get("sale_price", 0),
                "listing_type": prop.get("listing_type", "rent"),
                "description": prop.get("description", prop.get("notes", "")),
                "features": prop.get("features", []),
                "photos": [p["url"] for p in photo_urls],
                "photos_categorized": photo_urls,
                "photo_categories": categories_list,
                "status": prop.get("status", "available"),
                "owner_type": "ross_house",
                "owner_name": "Ross House Rentals LLC",
            }
        }

    # Try marketplace listings
    try:
        ml = await get_db().marketplace_listings.find_one({"_id": ObjectId(property_id), "status": "approved"})
    except:
        ml = None

    if ml:
        ml = serialize(ml)
        return {
            "success": True,
            "property": {
                "id": ml.get("_id"),
                "address": ml.get("address", ""),
                "city": ml.get("city", ""),
                "state": ml.get("state", ""),
                "zip_code": ml.get("zip_code", ""),
                "property_type": ml.get("property_type", "house"),
                "bedrooms": ml.get("bedrooms", 0),
                "bathrooms": ml.get("bathrooms", 0),
                "square_feet": ml.get("square_feet", 0),
                "rent_amount": ml.get("rent_amount", 0),
                "deposit_amount": ml.get("deposit_amount", 0),
                "sale_price": ml.get("sale_price", 0),
                "listing_type": ml.get("listing_type", "rent"),
                "description": ml.get("description", ""),
                "features": ml.get("features", []),
                "photos": ml.get("photos", []),
                "status": "available",
                "owner_type": "ross_house",
                "owner_name": "Ross House Rentals LLC",
            }
        }

    raise HTTPException(status_code=404, detail="Propiedad no encontrada")


@router.post('/public/rental-application')
async def submit_rental_application(request: Request):
    """Public endpoint: Receive rental applications from the website.
    
    Stores application in MongoDB and sends notification emails to admin
    + acknowledgement email to applicant (via SendGrid if configured).
    Also pushes a notification to admin devices.
    """
    data = await request.json()
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip()
    phone = (data.get("phone") or "").strip()
    property_interest = (data.get("property_interest") or "").strip()
    employment = (data.get("employment") or "").strip()
    monthly_income = (data.get("monthly_income") or "").strip()
    message = (data.get("message") or "").strip()

    if not name or not (email or phone):
        raise HTTPException(status_code=400, detail="Nombre y al menos un contacto (email o teléfono) son requeridos")

    application = {
        "name": name,
        "email": email,
        "phone": phone,
        "property_interest": property_interest,
        "employment": employment,
        "monthly_income": monthly_income,
        "message": message,
        "status": "new",
        "source": "website",
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
    }
    db = get_db()
    result = await db.rental_applications.insert_one(application)
    application_id = str(result.inserted_id)

    # ── Notify admins via push notification ──
    try:
        await send_rental_push_to_admins(
            title="📋 Nueva Aplicación de Renta",
            body=f"{name}" + (f" · {property_interest}" if property_interest else ""),
            data={"type": "rental_application_new", "application_id": application_id},
        )
    except Exception as e:
        logging.warning(f"⚠️ Push notification failed (rental application): {e}")

    # ── Notify admin via email (best-effort) ──
    try:
        import os as _os
        sendgrid_key = _os.getenv("SENDGRID_API_KEY")
        from_email = _os.getenv("SENDGRID_FROM_EMAIL", "info@rosshouserentals.com")
        admin_emails_raw = _os.getenv("RENTAL_ADMIN_EMAILS") or "yoandyross@gmail.com"
        if not sendgrid_key:
            cfg = await db.api_config.find_one({"_id": "main"})
            if cfg:
                sendgrid_key = cfg.get("sendgrid_api_key") or cfg.get("SENDGRID_API_KEY")
                from_email = cfg.get("sendgrid_from_email", from_email)
                admin_emails_raw = cfg.get("rental_admin_emails", admin_emails_raw)
        if sendgrid_key:
            import sendgrid
            from sendgrid.helpers.mail import Mail, Email, To, Content
            sg = sendgrid.SendGridAPIClient(api_key=sendgrid_key)

            # 1) Notify admins
            admin_list = [e.strip() for e in (admin_emails_raw or "").split(",") if e.strip()]
            _msg_html = (message or '<em style="color:#64748b;">(sin mensaje)</em>')
            _msg_html = _msg_html.replace("\n", "<br/>") if message else _msg_html
            html_body = f"""
            <div style="font-family:Helvetica,Arial,sans-serif;max-width:560px;margin:auto;background:#0b1220;color:#fff;border-radius:14px;padding:24px;">
              <div style="background:linear-gradient(135deg,#3b82f6,#06b6d4);padding:14px;border-radius:10px;text-align:center;">
                <h2 style="margin:0;color:#fff;">📋 Nueva Aplicación de Renta</h2>
              </div>
              <table style="width:100%;margin-top:18px;border-collapse:collapse;color:#cbd5e1;font-size:14px;">
                <tr><td style="padding:6px 0;color:#94a3b8;">Nombre:</td><td><strong style="color:#fff;">{name}</strong></td></tr>
                <tr><td style="padding:6px 0;color:#94a3b8;">Email:</td><td>{email or '—'}</td></tr>
                <tr><td style="padding:6px 0;color:#94a3b8;">Teléfono:</td><td>{phone or '—'}</td></tr>
                <tr><td style="padding:6px 0;color:#94a3b8;">Interés:</td><td>{property_interest or '—'}</td></tr>
                <tr><td style="padding:6px 0;color:#94a3b8;">Empleo:</td><td>{employment or '—'}</td></tr>
                <tr><td style="padding:6px 0;color:#94a3b8;">Ingreso mensual:</td><td>{monthly_income or '—'}</td></tr>
              </table>
              <div style="margin-top:14px;padding:12px;background:#111827;border-radius:10px;color:#e2e8f0;font-size:14px;">
                <strong style="color:#94a3b8;">Mensaje:</strong><br/>{_msg_html}
              </div>
              <a href="https://www.rosshouserentals.com/admin/aplicaciones" 
                 style="display:inline-block;margin-top:18px;background:#3b82f6;color:#fff;padding:12px 20px;border-radius:10px;text-decoration:none;font-weight:bold;">
                🔎 Revisar en el panel admin
              </a>
              <p style="color:#64748b;font-size:11px;margin-top:18px;">Ross House Rentals · Notificación automática</p>
            </div>
            """
            for admin_email in admin_list:
                try:
                    mail = Mail(
                        from_email=Email(from_email, "Ross House Rentals"),
                        to_emails=To(admin_email),
                        subject=f"📋 Nueva aplicación: {name}",
                        plain_text_content=Content("text/plain",
                            f"Nueva aplicación recibida:\nNombre: {name}\nEmail: {email}\nTeléfono: {phone}\nInterés: {property_interest}\nEmpleo: {employment}\nIngreso: {monthly_income}\nMensaje: {message}\n\nRevisa: https://www.rosshouserentals.com/admin/aplicaciones"),
                    )
                    mail.add_content(Content("text/html", html_body))
                    sg.client.mail.send.post(request_body=mail.get())
                    logging.info(f"📧 Admin notified about new application ({admin_email})")
                except Exception as ae:
                    logging.warning(f"⚠️ Admin email failed ({admin_email}): {ae}")

            # 2) Confirmation to applicant
            if email:
                try:
                    confirm_html = f"""
                    <div style="font-family:Helvetica,Arial,sans-serif;max-width:560px;margin:auto;background:#0b1220;color:#fff;border-radius:14px;padding:24px;">
                      <div style="background:linear-gradient(135deg,#10b981,#06b6d4);padding:14px;border-radius:10px;text-align:center;">
                        <h2 style="margin:0;color:#fff;">✅ Recibimos tu aplicación</h2>
                      </div>
                      <p style="color:#cbd5e1;margin-top:18px;">Hola <strong style="color:#fff;">{name}</strong>,</p>
                      <p style="color:#cbd5e1;">Gracias por aplicar con <strong>Ross House Rentals</strong>. Tu aplicación fue recibida correctamente y será revisada por nuestro equipo en las próximas 24-72 horas.</p>
                      <div style="margin:14px 0;padding:12px;background:#111827;border-radius:10px;color:#e2e8f0;font-size:13px;">
                        <div style="color:#94a3b8;margin-bottom:6px;">Resumen:</div>
                        <div><strong>Propiedad de interés:</strong> {property_interest or '—'}</div>
                        <div><strong>Email de contacto:</strong> {email}</div>
                        <div><strong>Teléfono:</strong> {phone or '—'}</div>
                      </div>
                      <p style="color:#cbd5e1;">Si tienes alguna pregunta o necesitas adjuntar documentación adicional, simplemente responde a este email.</p>
                      <p style="color:#64748b;font-size:12px;margin-top:18px;">— Equipo Ross House Rentals · Dumas, TX</p>
                    </div>
                    """
                    mail2 = Mail(
                        from_email=Email(from_email, "Ross House Rentals"),
                        to_emails=To(email),
                        subject="✅ Recibimos tu aplicación de renta — Ross House Rentals",
                        plain_text_content=Content("text/plain", f"Hola {name},\n\nRecibimos tu aplicación correctamente. Nuestro equipo la revisará en las próximas 24-72 horas y te contactaremos.\n\n— Equipo Ross House Rentals · Dumas, TX"),
                    )
                    mail2.add_content(Content("text/html", confirm_html))
                    sg.client.mail.send.post(request_body=mail2.get())
                    logging.info(f"📧 Applicant confirmation sent ({email})")
                except Exception as ce:
                    logging.warning(f"⚠️ Applicant confirmation failed: {ce}")
        else:
            logging.info("ℹ️ SENDGRID_API_KEY no configurado — emails de aplicación omitidos")
    except Exception as e:
        logging.warning(f"⚠️ Email notification block failed (rental application): {e}")

    return {"success": True, "application_id": application_id, "message": "Application received successfully"}




@router.post('/landlord/listings')
async def create_landlord_listing(request: Request):
    """Landlord: Create a new property listing for approval"""
    user = await auth_marketplace(request)
    if user.get("role") != "landlord":
        raise HTTPException(status_code=403, detail="Solo propietarios pueden publicar propiedades")

    data = await request.json()
    listing = {
        "owner_id": user.get("_id"),
        "owner_name": user.get("name", ""),
        "owner_email": user.get("email", ""),
        "address": data.get("address", ""),
        "city": data.get("city", ""),
        "state": data.get("state", "TX"),
        "zip_code": data.get("zip_code", ""),
        "property_type": data.get("property_type", "house"),
        "listing_type": data.get("listing_type", "rent"),  # rent or sale
        "bedrooms": int(data.get("bedrooms", 0)),
        "bathrooms": float(data.get("bathrooms", 0)),
        "square_feet": int(data.get("square_feet", 0)),
        "rent_amount": float(data.get("rent_amount", 0)),
        "deposit_amount": float(data.get("deposit_amount", 0)),
        "sale_price": float(data.get("sale_price", 0)),
        "description": data.get("description", ""),
        "features": data.get("features", []),
        "photos": data.get("photos", []),
        "commission_rate": user.get("commission_rate", 10),
        "status": "pending",  # pending -> approved/rejected by admin
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
    }

    result = await get_db().marketplace_listings.insert_one(listing)
    inserted_id = str(result.inserted_id)

    # Update landlord's property count
    await get_db().app_users.update_one(
        {"_id": ObjectId(user.get("_id"))},
        {"$inc": {"properties_count": 1}}
    )

    # ── Notify admins (push + email) ──
    try:
        await _notify_admins_new_listing(listing, inserted_id)
    except Exception as e:
        logging.warning(f"⚠️ Admin notification on new listing failed: {e}")

    return {
        "success": True,
        "listing_id": inserted_id,
        "message": "Propiedad enviada para aprobación"
    }


async def _notify_admins_new_listing(listing: dict, listing_id: str):
    """Send email + push to all admins when a landlord creates a new marketplace listing."""
    import os as _os
    address = listing.get("address", "Sin dirección")
    city = listing.get("city", "")
    state = listing.get("state", "")
    rent = listing.get("rent_amount", 0) or listing.get("sale_price", 0)
    beds = listing.get("bedrooms", 0)
    baths = listing.get("bathrooms", 0)
    owner_name = listing.get("owner_name", "Propietario")
    owner_email = listing.get("owner_email", "")
    listing_type = listing.get("listing_type", "rent")
    type_label = "venta" if listing_type == "sale" else "renta"

    # Push
    try:
        await send_rental_push_to_admins(
            title=f"🏠 Nueva propiedad pendiente",
            body=f"{owner_name}: {address}, {city} (${rent:,.0f}/{type_label})",
            data={"type": "marketplace_new_listing", "listing_id": listing_id},
        )
    except Exception as pe:
        logging.warning(f"Admin push failed: {pe}")

    # Email
    sendgrid_key = _os.getenv("SENDGRID_API_KEY")
    from_email = _os.getenv("SENDGRID_FROM_EMAIL", "info@rosshouserentals.com")
    if not sendgrid_key:
        cfg = await get_db().api_config.find_one({"_id": "main"})
        if cfg:
            sendgrid_key = cfg.get("sendgrid_api_key") or cfg.get("SENDGRID_API_KEY")
            from_email = cfg.get("sendgrid_from_email", from_email)
    if not sendgrid_key:
        return

    # Gather all admin emails
    admins = await get_db().app_users.find({"role": "admin"}).to_list(20)
    admin_emails = [a.get("email") for a in admins if a.get("email")]
    if not admin_emails:
        return

    try:
        import sendgrid
        from sendgrid.helpers.mail import Mail, Email, To, Content
        sg = sendgrid.SendGridAPIClient(api_key=sendgrid_key)

        subject = f"🏠 Nueva propiedad pendiente de revisión — {address}"
        html = f"""
        <div style="font-family:Helvetica,Arial,sans-serif;max-width:560px;margin:auto;background:#0b1220;color:#fff;border-radius:14px;padding:24px;">
          <div style="background:linear-gradient(135deg,#3b82f6,#8b5cf6);padding:14px;border-radius:10px;text-align:center;">
            <h2 style="margin:0;color:#fff;">🏠 Nueva propiedad pendiente</h2>
          </div>
          <p style="color:#cbd5e1;margin-top:18px;">Un propietario externo publicó una nueva propiedad y espera tu revisión.</p>
          <div style="margin:14px 0;padding:14px;background:#111827;border-radius:10px;color:#e2e8f0;font-size:14px;border-left:4px solid #3b82f6;">
            <div><strong style="color:#94a3b8;">Dirección:</strong> {address}</div>
            <div><strong style="color:#94a3b8;">Ciudad:</strong> {city}, {state}</div>
            <div><strong style="color:#94a3b8;">Tipo:</strong> {type_label.capitalize()}</div>
            <div><strong style="color:#94a3b8;">{('Renta' if listing_type=='rent' else 'Precio')}:</strong> ${rent:,.0f}{'/mes' if listing_type=='rent' else ''}</div>
            <div><strong style="color:#94a3b8;">Camas / Baños:</strong> {beds} / {baths}</div>
            <div style="margin-top:10px;padding-top:10px;border-top:1px solid #1e293b;">
              <strong style="color:#94a3b8;">Propietario:</strong> {owner_name}<br/>
              <strong style="color:#94a3b8;">Email:</strong> {owner_email}
            </div>
          </div>
          <a href="https://www.rosshouserentals.com/admin/marketplace" style="display:inline-block;background:#3b82f6;color:#fff;padding:12px 20px;border-radius:10px;text-decoration:none;font-weight:bold;">Revisar propiedad →</a>
          <p style="color:#64748b;font-size:11px;margin-top:18px;border-top:1px solid #1e293b;padding-top:12px;">— Ross House Rentals · Dumas, TX</p>
        </div>
        """
        plain = f"Nueva propiedad pendiente:\n{address}, {city}, {state}\n{owner_name} ({owner_email})\n${rent:,.0f}\n\nRevisar en https://www.rosshouserentals.com/admin/marketplace"

        for admin_email in admin_emails:
            try:
                mail = Mail(
                    from_email=Email(from_email, "Ross House Rentals"),
                    to_emails=To(admin_email),
                    subject=subject,
                    plain_text_content=Content("text/plain", plain),
                )
                mail.add_content(Content("text/html", html))
                sg.client.mail.send.post(request_body=mail.get())
            except Exception as me:
                logging.warning(f"Admin email failed for {admin_email}: {me}")
        logging.info(f"📧 Admin notified about new listing: {address}")
    except Exception as e:
        logging.warning(f"⚠️ Admin email batch failed: {e}")


@router.get('/landlord/my-listings')
async def get_landlord_listings(request: Request):
    """Landlord: Get my property listings"""
    user = await auth_marketplace(request)
    if user.get("role") != "landlord":
        raise HTTPException(status_code=403, detail="Solo propietarios")

    cursor = get_db().marketplace_listings.find(
        {"owner_id": user.get("_id"), "status": {"$ne": "deleted"}}
    ).sort("created_at", -1)

    listings = []
    async for l in cursor:
        ml = serialize(l)
        listings.append({
            "id": ml.get("_id"),
            "address": ml.get("address", ""),
            "city": ml.get("city", ""),
            "state": ml.get("state", ""),
            "property_type": ml.get("property_type", "house"),
            "listing_type": ml.get("listing_type", "rent"),
            "bedrooms": ml.get("bedrooms", 0),
            "bathrooms": ml.get("bathrooms", 0),
            "rent_amount": ml.get("rent_amount", 0),
            "sale_price": ml.get("sale_price", 0),
            "status": ml.get("status", "pending"),
            "photos": ml.get("photos", []),
            "created_at": ml.get("created_at", ""),
        })

    return {"success": True, "listings": listings, "count": len(listings)}


@router.post('/landlord/listings/{listing_id}/photos')
async def upload_listing_photos(listing_id: str, request: Request):
    """Landlord: Upload photos for a listing (accepts base64 images)"""
    user = await auth_marketplace(request)
    if user.get("role") != "landlord":
        raise HTTPException(status_code=403, detail="Solo propietarios pueden subir fotos")

    listing = await get_db().marketplace_listings.find_one({"_id": ObjectId(listing_id)})
    if not listing or str(listing.get("owner_id")) != str(user.get("_id")):
        raise HTTPException(status_code=404, detail="Propiedad no encontrada")

    data = await request.json()
    new_photos = data.get("photos", [])

    if not new_photos or not isinstance(new_photos, list):
        raise HTTPException(status_code=400, detail="Se requiere al menos una foto")

    # Validate and process photos (base64 data URIs)
    processed = []
    MAX_PHOTOS = 10
    existing = listing.get("photos", [])

    for photo in new_photos:
        if len(existing) + len(processed) >= MAX_PHOTOS:
            break
        if isinstance(photo, str) and (photo.startswith("data:image/") or photo.startswith("http")):
            # Compress large base64 images if needed (limit ~500KB per image)
            if photo.startswith("data:image/") and len(photo) > 700000:
                # Too large, try to accept but warn
                logging.warning(f"Large photo uploaded for listing {listing_id}: {len(photo)} chars")
            processed.append(photo)

    if not processed:
        raise HTTPException(status_code=400, detail="No se procesaron fotos válidas")

    # Append new photos to existing
    await get_db().marketplace_listings.update_one(
        {"_id": ObjectId(listing_id)},
        {
            "$push": {"photos": {"$each": processed}},
            "$set": {"updated_at": datetime.utcnow()}
        }
    )

    total = len(existing) + len(processed)
    return {
        "success": True,
        "added": len(processed),
        "total": total,
        "message": f"{len(processed)} foto(s) agregada(s)"
    }


@router.delete('/landlord/listings/{listing_id}/photos/{photo_index}')
async def delete_listing_photo(listing_id: str, photo_index: int, request: Request):
    """Landlord: Remove a specific photo from a listing"""
    user = await auth_marketplace(request)
    if user.get("role") != "landlord":
        raise HTTPException(status_code=403, detail="Solo propietarios")

    listing = await get_db().marketplace_listings.find_one({"_id": ObjectId(listing_id)})
    if not listing or str(listing.get("owner_id")) != str(user.get("_id")):
        raise HTTPException(status_code=404, detail="Propiedad no encontrada")

    photos = listing.get("photos", [])
    if photo_index < 0 or photo_index >= len(photos):
        raise HTTPException(status_code=400, detail="Índice de foto inválido")

    photos.pop(photo_index)
    await get_db().marketplace_listings.update_one(
        {"_id": ObjectId(listing_id)},
        {"$set": {"photos": photos, "updated_at": datetime.utcnow()}}
    )

    return {"success": True, "total": len(photos), "message": "Foto eliminada"}


@router.put('/landlord/listings/{listing_id}')
async def update_landlord_listing(listing_id: str, request: Request):
    """Landlord: Update own listing.
    Material changes (price, address, beds, baths, sqft, type) reset status to 'pending' for re-approval.
    """
    user = await auth_marketplace(request)
    if user.get("role") != "landlord":
        raise HTTPException(status_code=403, detail="Solo propietarios")

    listing = await get_db().marketplace_listings.find_one({"_id": ObjectId(listing_id)})
    if not listing or str(listing.get("owner_id")) != str(user.get("_id")):
        raise HTTPException(status_code=404, detail="Propiedad no encontrada")

    data = await request.json()
    update_fields = {}
    allowed = ["address", "city", "state", "zip_code", "property_type", "listing_type",
               "bedrooms", "bathrooms", "square_feet", "rent_amount", "deposit_amount",
               "sale_price", "description", "features", "photos"]
    # Material fields that trigger re-approval if changed on an already-approved listing
    material_fields = {"address", "city", "state", "zip_code", "property_type", "listing_type",
                       "bedrooms", "bathrooms", "square_feet", "rent_amount", "sale_price"}
    material_changed = False
    for f in allowed:
        if f in data:
            new_val = data[f]
            old_val = listing.get(f)
            update_fields[f] = new_val
            if f in material_fields:
                try:
                    if str(new_val) != str(old_val):
                        material_changed = True
                except Exception:
                    material_changed = True
    update_fields["updated_at"] = datetime.utcnow()

    # Re-approval logic
    current_status = listing.get("status", "pending")
    requires_review = False
    if current_status == "rejected" and update_fields:
        update_fields["status"] = "pending"
        requires_review = True
    elif current_status == "approved" and material_changed:
        update_fields["status"] = "pending"
        update_fields["previous_status"] = "approved"
        update_fields["resubmitted_at"] = datetime.utcnow()
        requires_review = True

    await get_db().marketplace_listings.update_one(
        {"_id": ObjectId(listing_id)},
        {"$set": update_fields}
    )

    # Notify admins if listing is now pending again
    if requires_review:
        try:
            merged = {**listing, **update_fields}
            await _notify_admins_new_listing(merged, listing_id)
        except Exception as e:
            logging.warning(f"⚠️ Admin re-review notification failed: {e}")

    return {
        "success": True,
        "message": "Propiedad actualizada" + (" — pendiente de re-aprobación" if requires_review else ""),
        "requires_review": requires_review,
        "status": update_fields.get("status", current_status),
    }


@router.delete('/landlord/listings/{listing_id}')
async def delete_landlord_listing(listing_id: str, request: Request):
    """Landlord: Soft-delete own listing (sets status='deleted'). Admin can still see in audit log."""
    user = await auth_marketplace(request)
    if user.get("role") != "landlord":
        raise HTTPException(status_code=403, detail="Solo propietarios")

    if not ObjectId.is_valid(listing_id):
        raise HTTPException(status_code=400, detail="ID inválido")

    listing = await get_db().marketplace_listings.find_one({"_id": ObjectId(listing_id)})
    if not listing or str(listing.get("owner_id")) != str(user.get("_id")):
        raise HTTPException(status_code=404, detail="Propiedad no encontrada")

    # Soft-delete: set status to 'deleted' so it disappears from public + landlord views
    await get_db().marketplace_listings.update_one(
        {"_id": ObjectId(listing_id)},
        {"$set": {
            "status": "deleted",
            "deleted_at": datetime.utcnow(),
            "deleted_by": str(user.get("_id")),
        }}
    )

    # Decrement landlord's property count
    await get_db().app_users.update_one(
        {"_id": ObjectId(user.get("_id"))},
        {"$inc": {"properties_count": -1}}
    )

    # Notify admins via push (audit trail)
    try:
        await send_rental_push_to_admins(
            title="🗑️ Propiedad eliminada por propietario",
            body=f"{user.get('name', 'Propietario')}: {listing.get('address', '')} — {listing.get('city', '')}",
            data={"type": "marketplace_deleted", "listing_id": listing_id},
        )
    except Exception:
        pass

    return {"success": True, "message": "Propiedad eliminada"}


@router.get('/public/marketplace/listings')
async def public_marketplace_listings(
    request: Request,
):
    """Public: List all APPROVED marketplace listings published by external landlords.
    No auth required. Supports filters: state, city, min_rent, max_rent, beds, baths, property_type, listing_type.
    """
    qp = request.query_params
    query = {"status": "approved"}

    if qp.get("state"):
        query["state"] = qp.get("state", "").upper()
    if qp.get("city"):
        # Case-insensitive city match
        query["city"] = {"$regex": f"^{qp.get('city')}$", "$options": "i"}
    if qp.get("property_type"):
        query["property_type"] = qp.get("property_type")
    if qp.get("listing_type"):
        query["listing_type"] = qp.get("listing_type")
    if qp.get("beds"):
        try:
            query["bedrooms"] = {"$gte": int(qp.get("beds"))}
        except ValueError:
            pass
    if qp.get("baths"):
        try:
            query["bathrooms"] = {"$gte": float(qp.get("baths"))}
        except ValueError:
            pass

    rent_filter = {}
    if qp.get("min_rent"):
        try:
            rent_filter["$gte"] = float(qp.get("min_rent"))
        except ValueError:
            pass
    if qp.get("max_rent"):
        try:
            rent_filter["$lte"] = float(qp.get("max_rent"))
        except ValueError:
            pass
    if rent_filter:
        query["rent_amount"] = rent_filter

    try:
        page = max(1, int(qp.get("page", "1")))
        page_limit = min(50, max(1, int(qp.get("page_limit", "24"))))
    except ValueError:
        page, page_limit = 1, 24

    total = await get_db().marketplace_listings.count_documents(query)
    cursor = (
        get_db().marketplace_listings.find(query)
        .sort("created_at", -1)
        .skip((page - 1) * page_limit)
        .limit(page_limit)
    )

    formatted = []
    async for l in cursor:
        ml = serialize(l)
        formatted.append({
            "id": ml.get("_id"),
            "address": ml.get("address", ""),
            "city": ml.get("city", ""),
            "state": ml.get("state", ""),
            "zip_code": ml.get("zip_code", ""),
            "property_type": ml.get("property_type", "house"),
            "listing_type": ml.get("listing_type", "rent"),
            "bedrooms": ml.get("bedrooms", 0),
            "bathrooms": ml.get("bathrooms", 0),
            "square_feet": ml.get("square_feet", 0),
            "rent_amount": ml.get("rent_amount", 0),
            "sale_price": ml.get("sale_price", 0),
            "deposit_amount": ml.get("deposit_amount", 0),
            "description": ml.get("description", ""),
            "features": ml.get("features", []),
            "photos": ml.get("photos", []),
            "image_url": (ml.get("photos") or [None])[0],
            "owner_name": ml.get("owner_name", ""),
            "source": "marketplace",
        })

    return {
        "success": True,
        "status": "success",
        "listings": formatted,
        "total": total,
        "page": page,
        "page_limit": page_limit,
    }


@router.post('/public/property-inquiry')
async def property_inquiry(request: Request):
    """Public: Send an inquiry about a property (apply/contact)"""
    data = await request.json()
    inquiry = {
        "property_id": data.get("property_id", ""),
        "property_type": data.get("property_type", ""),  # ross_house or marketplace
        "name": data.get("name", ""),
        "email": data.get("email", ""),
        "phone": data.get("phone", ""),
        "message": data.get("message", ""),
        "inquiry_type": data.get("inquiry_type", "contact"),  # contact, apply, visit
        "status": "new",
        "created_at": datetime.utcnow(),
    }
    result = await get_db().property_inquiries.insert_one(inquiry)
    return {"success": True, "inquiry_id": str(result.inserted_id), "message": "Consulta enviada exitosamente"}




# ── Admin: Marketplace Management ──

@router.get('/admin/marketplace-listings')
async def admin_list_marketplace(request: Request):
    """Admin: List all marketplace listings with filters"""
    await auth_admin(request)
    status_filter = request.query_params.get("status", "all")

    query = {}
    if status_filter != "all":
        query["status"] = status_filter

    cursor = get_db().marketplace_listings.find(query).sort("created_at", -1)
    listings = []
    async for l in cursor:
        ml = serialize(l)
        listings.append(ml)

    return {"success": True, "listings": listings, "count": len(listings)}


@router.put('/admin/marketplace-listings/{listing_id}')
async def admin_update_listing(listing_id: str, request: Request):
    """Admin: Approve or reject a marketplace listing"""
    await auth_admin(request)
    data = await request.json()
    action = data.get("action", "")  # approve, reject

    if action not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail="Acción debe ser 'approve' o 'reject'")

    listing = await get_db().marketplace_listings.find_one({"_id": ObjectId(listing_id)})
    if not listing:
        raise HTTPException(status_code=404, detail="Listado no encontrado")

    new_status = "approved" if action == "approve" else "rejected"
    update = {
        "status": new_status,
        "reviewed_at": datetime.utcnow(),
        "review_notes": data.get("notes", ""),
    }

    # If approving, set commission rate from admin
    if action == "approve" and "commission_rate" in data:
        update["commission_rate"] = float(data["commission_rate"])

    await get_db().marketplace_listings.update_one(
        {"_id": ObjectId(listing_id)},
        {"$set": update}
    )

    # ── Notify landlord via email + push (best-effort) ──
    try:
        await _notify_landlord_listing_review(listing, new_status, data.get("notes", ""))
    except Exception as e:
        logging.warning(f"⚠️ Landlord notification failed: {e}")

    return {"success": True, "message": f"Listado {new_status}", "status": new_status}


@router.post('/admin/marketplace-listings')
async def admin_create_listing(request: Request):
    """Admin: Create a new marketplace listing on behalf of a landlord.
    Body: {owner_id*, address*, city*, state*, zip_code, listing_type, property_type, bedrooms, bathrooms, square_feet, rent_amount, sale_price, deposit_amount, description, photos, status?}
    """
    await auth_admin(request)
    data = await request.json()

    owner_id_raw = data.get("owner_id", "").strip() if isinstance(data.get("owner_id"), str) else data.get("owner_id")
    if not owner_id_raw or not ObjectId.is_valid(str(owner_id_raw)):
        raise HTTPException(status_code=400, detail="owner_id requerido y válido")
    address = (data.get("address") or "").strip()
    city = (data.get("city") or "").strip()
    if not address or not city:
        raise HTTPException(status_code=400, detail="Dirección y ciudad son requeridas")

    owner = await get_db().app_users.find_one({"_id": ObjectId(str(owner_id_raw))})
    if not owner:
        raise HTTPException(status_code=404, detail="Propietario no encontrado")

    now = datetime.utcnow()
    listing = {
        "owner_id": ObjectId(str(owner_id_raw)),
        "owner_name": owner.get("name", ""),
        "owner_email": owner.get("email", ""),
        "owner_phone": owner.get("phone", ""),
        "address": address,
        "city": city,
        "state": (data.get("state") or "TX").upper()[:2],
        "zip_code": data.get("zip_code", ""),
        "listing_type": data.get("listing_type", "rent"),
        "property_type": data.get("property_type", "house"),
        "bedrooms": int(data.get("bedrooms", 0)),
        "bathrooms": float(data.get("bathrooms", 0)),
        "square_feet": int(data.get("square_feet", 0)),
        "rent_amount": float(data.get("rent_amount", 0)),
        "sale_price": float(data.get("sale_price", 0)),
        "deposit_amount": float(data.get("deposit_amount", 0)),
        "description": data.get("description", ""),
        "features": data.get("features", []),
        "photos": data.get("photos", []),
        "status": data.get("status", "approved"),  # Admin-created defaults to approved
        "commission_rate": float(data.get("commission_rate", 10)),
        "created_by_admin": True,
        "created_at": now,
        "updated_at": now,
    }
    if listing["status"] == "approved":
        listing["reviewed_at"] = now

    result = await get_db().marketplace_listings.insert_one(listing)
    new_id = str(result.inserted_id)

    # Increment owner's property count
    await get_db().app_users.update_one(
        {"_id": ObjectId(str(owner_id_raw))},
        {"$inc": {"properties_count": 1}}
    )

    return {
        "success": True,
        "listing_id": new_id,
        "status": listing["status"],
        "message": f"Listing creado para {owner.get('name', '')}",
    }


@router.patch('/admin/marketplace-listings/{listing_id}')
async def admin_edit_listing(listing_id: str, request: Request):
    """Admin: Edit any field of a marketplace listing (full editorial control).
    Does NOT trigger re-approval — admin override.
    """
    await auth_admin(request)
    if not ObjectId.is_valid(listing_id):
        raise HTTPException(status_code=400, detail="ID inválido")
    listing = await get_db().marketplace_listings.find_one({"_id": ObjectId(listing_id)})
    if not listing:
        raise HTTPException(status_code=404, detail="Listing no encontrado")

    data = await request.json()
    allowed = ["address", "city", "state", "zip_code", "property_type", "listing_type",
               "bedrooms", "bathrooms", "square_feet", "rent_amount", "deposit_amount",
               "sale_price", "description", "features", "photos", "status", "commission_rate",
               "admin_notes"]
    update = {k: v for k, v in data.items() if k in allowed}

    # If admin changes owner, reassign
    new_owner_id = data.get("owner_id")
    if new_owner_id and ObjectId.is_valid(str(new_owner_id)) and str(new_owner_id) != str(listing.get("owner_id", "")):
        new_owner = await get_db().app_users.find_one({"_id": ObjectId(str(new_owner_id))})
        if new_owner:
            update["owner_id"] = ObjectId(str(new_owner_id))
            update["owner_name"] = new_owner.get("name", "")
            update["owner_email"] = new_owner.get("email", "")
            update["owner_phone"] = new_owner.get("phone", "")

    update["updated_at"] = datetime.utcnow()
    update["edited_by_admin"] = True

    await get_db().marketplace_listings.update_one(
        {"_id": ObjectId(listing_id)},
        {"$set": update}
    )
    return {"success": True, "message": "Listing actualizado"}


@router.delete('/admin/marketplace-listings/{listing_id}')
async def admin_delete_listing(listing_id: str, request: Request):
    """Admin: Soft-delete a marketplace listing. Notifies the landlord via push + email."""
    await auth_admin(request)
    if not ObjectId.is_valid(listing_id):
        raise HTTPException(status_code=400, detail="ID inválido")
    listing = await get_db().marketplace_listings.find_one({"_id": ObjectId(listing_id)})
    if not listing:
        raise HTTPException(status_code=404, detail="Listing no encontrado")

    await get_db().marketplace_listings.update_one(
        {"_id": ObjectId(listing_id)},
        {"$set": {
            "status": "deleted",
            "deleted_at": datetime.utcnow(),
            "deleted_by_admin": True,
        }}
    )
    # Decrement owner's property count
    owner_id = listing.get("owner_id")
    if owner_id:
        try:
            await get_db().app_users.update_one(
                {"_id": owner_id if isinstance(owner_id, ObjectId) else ObjectId(str(owner_id))},
                {"$inc": {"properties_count": -1}}
            )
        except Exception:
            pass

    # Notify landlord
    try:
        owner_email = listing.get("owner_email", "")
        owner_id_str = str(listing.get("owner_id", ""))
        from rental.shared import send_rental_push_to_user
        if owner_id_str:
            await send_rental_push_to_user(
                user_id=owner_id_str,
                title="📋 Propiedad retirada",
                body=f"Tu propiedad '{listing.get('address', '')}' fue retirada por el administrador.",
                data={"type": "marketplace_admin_delete", "listing_id": listing_id},
            )
    except Exception as e:
        logging.warning(f"Admin-delete landlord notification failed: {e}")

    return {"success": True, "message": "Listing eliminado"}


async def _notify_landlord_listing_review(listing: dict, new_status: str, notes: str = ""):
    """Send email + push to the landlord when admin approves/rejects their listing"""
    import os as _os
    owner_email = (listing.get("owner_email") or "").strip()
    owner_name = listing.get("owner_name") or "Propietario"
    address = listing.get("address", "tu propiedad")
    city = listing.get("city", "")
    listing_id = str(listing.get("_id", ""))

    # Push to landlord (if owner_id exists)
    try:
        owner_id = listing.get("owner_id")
        if owner_id:
            from rental.shared import send_rental_push_to_user
            emoji = "🎉" if new_status == "approved" else "📋"
            await send_rental_push_to_user(
                user_id=str(owner_id),
                title=f"{emoji} Listing {'aprobado' if new_status == 'approved' else 'rechazado'}",
                body=f"{address}{', ' + city if city else ''}",
                data={"type": "marketplace_review", "listing_id": listing_id, "status": new_status},
            )
    except Exception as pe:
        logging.warning(f"Push to landlord failed: {pe}")

    if not owner_email:
        return

    # Email
    sendgrid_key = _os.getenv("SENDGRID_API_KEY")
    from_email = _os.getenv("SENDGRID_FROM_EMAIL", "info@rosshouserentals.com")
    if not sendgrid_key:
        cfg = await get_db().api_config.find_one({"_id": "main"})
        if cfg:
            sendgrid_key = cfg.get("sendgrid_api_key") or cfg.get("SENDGRID_API_KEY")
            from_email = cfg.get("sendgrid_from_email", from_email)
    if not sendgrid_key:
        logging.info("ℹ️ SENDGRID_API_KEY missing — landlord notify skipped")
        return

    try:
        import sendgrid
        from sendgrid.helpers.mail import Mail, Email, To, Content
        sg = sendgrid.SendGridAPIClient(api_key=sendgrid_key)

        if new_status == "approved":
            subject = f"🎉 Tu propiedad fue aprobada — {address}"
            html = f"""
            <div style="font-family:Helvetica,Arial,sans-serif;max-width:560px;margin:auto;background:#0b1220;color:#fff;border-radius:14px;padding:24px;">
              <div style="background:linear-gradient(135deg,#10b981,#06b6d4);padding:14px;border-radius:10px;text-align:center;">
                <h2 style="margin:0;color:#fff;">🎉 ¡Tu listing fue aprobado!</h2>
              </div>
              <p style="color:#cbd5e1;margin-top:18px;">Hola <strong style="color:#fff;">{owner_name}</strong>,</p>
              <p style="color:#cbd5e1;">¡Buenas noticias! Tu propiedad ya está visible públicamente en Ross House Rentals.</p>
              <div style="margin:14px 0;padding:14px;background:#111827;border-radius:10px;color:#e2e8f0;font-size:14px;border-left:4px solid #10b981;">
                <div><strong style="color:#94a3b8;">Propiedad:</strong> {address}</div>
                {f'<div><strong style="color:#94a3b8;">Ciudad:</strong> {city}</div>' if city else ''}
                <div><strong style="color:#94a3b8;">Comisión:</strong> {listing.get('commission_rate', 10)}%</div>
                {f'<div style="margin-top:8px;padding-top:8px;border-top:1px solid #1e293b;"><strong style="color:#94a3b8;">Nota del admin:</strong><br/>{notes}</div>' if notes else ''}
              </div>
              <p style="color:#cbd5e1;">Empezarás a recibir consultas (inquiries) de potenciales inquilinos/compradores en tu dashboard.</p>
              <a href="https://www.rosshouserentals.com" style="display:inline-block;background:#10b981;color:#fff;padding:12px 20px;border-radius:10px;text-decoration:none;font-weight:bold;">Ver mi listing →</a>
              <p style="color:#64748b;font-size:11px;margin-top:18px;border-top:1px solid #1e293b;padding-top:12px;">— Ross House Rentals · Dumas, TX</p>
            </div>
            """
            plain = f"¡Tu listing fue aprobado!\nPropiedad: {address}\nComisión: {listing.get('commission_rate', 10)}%\n\nYa está visible al público."
        else:
            subject = f"📋 Actualización sobre tu propiedad — {address}"
            html = f"""
            <div style="font-family:Helvetica,Arial,sans-serif;max-width:560px;margin:auto;background:#0b1220;color:#fff;border-radius:14px;padding:24px;">
              <div style="background:linear-gradient(135deg,#f59e0b,#dc2626);padding:14px;border-radius:10px;text-align:center;">
                <h2 style="margin:0;color:#fff;">📋 Revisión de tu listing</h2>
              </div>
              <p style="color:#cbd5e1;margin-top:18px;">Hola <strong style="color:#fff;">{owner_name}</strong>,</p>
              <p style="color:#cbd5e1;">Tu propiedad necesita ajustes antes de que podamos publicarla en Ross House Rentals.</p>
              <div style="margin:14px 0;padding:14px;background:#111827;border-radius:10px;color:#e2e8f0;font-size:14px;border-left:4px solid #f59e0b;">
                <div><strong style="color:#94a3b8;">Propiedad:</strong> {address}</div>
                {f'<div><strong style="color:#94a3b8;">Ciudad:</strong> {city}</div>' if city else ''}
                {f'<div style="margin-top:10px;padding-top:10px;border-top:1px solid #1e293b;"><strong style="color:#94a3b8;">Motivo / Observaciones:</strong><br/>{notes}</div>' if notes else '<div style="margin-top:10px;color:#94a3b8;">Razones comunes: fotos faltantes o de baja calidad, descripción incompleta, precio fuera de mercado, o información de contacto inválida.</div>'}
              </div>
              <p style="color:#cbd5e1;">Puedes editar tu listing y volverá a entrar a revisión automáticamente. Si tienes preguntas, responde directamente a este email.</p>
              <a href="https://www.rosshouserentals.com" style="display:inline-block;background:#f59e0b;color:#fff;padding:12px 20px;border-radius:10px;text-decoration:none;font-weight:bold;">Editar mi listing →</a>
              <p style="color:#64748b;font-size:11px;margin-top:18px;border-top:1px solid #1e293b;padding-top:12px;">— Ross House Rentals · Dumas, TX</p>
            </div>
            """
            plain = f"Tu listing necesita ajustes.\nPropiedad: {address}\n{'Notas: ' + notes if notes else 'Edita tu listing en tu portal para corregir y volverá a revisión.'}"

        mail = Mail(
            from_email=Email(from_email, "Ross House Rentals"),
            to_emails=To(owner_email),
            subject=subject,
            plain_text_content=Content("text/plain", plain),
        )
        mail.add_content(Content("text/html", html))
        sg.client.mail.send.post(request_body=mail.get())
        logging.info(f"📧 Landlord notified ({owner_email}): listing {new_status}")
    except Exception as e:
        logging.warning(f"⚠️ Landlord email failed for {owner_email}: {e}")


@router.get('/admin/marketplace-commissions')
async def admin_marketplace_commissions(request: Request):
    """Admin: aggregated commissions earned per landlord.
    Computes total commission based on commission_rate × rent_amount × signed_contracts.
    """
    await auth_admin(request)
    db = get_db()

    # Optional ?include_test=1 query to show test users (default: hide)
    qp = request.query_params
    include_test = qp.get("include_test", "0") in ("1", "true", "yes")
    test_email_patterns = ["qa.", "ui.test.", "demo.", "test@", "@test.com", "@example.com", "@rosshouse.test"]

    # 1) Fetch all landlords
    landlords_cursor = db.app_users.find({"role": "landlord"})
    landlords = [l async for l in landlords_cursor]
    if not include_test:
        def _is_test(em: str) -> bool:
            em = (em or "").lower()
            return any(p in em for p in test_email_patterns)
        landlords = [l for l in landlords if not _is_test(l.get("email", ""))]

    rows = []
    grand_total_revenue = 0
    grand_total_commission = 0

    for landlord in landlords:
        owner_id = str(landlord["_id"])
        # Listings stats
        all_listings = await db.marketplace_listings.count_documents({"owner_id": owner_id})
        approved_listings = await db.marketplace_listings.count_documents({"owner_id": owner_id, "status": "approved"})
        pending_listings = await db.marketplace_listings.count_documents({"owner_id": owner_id, "status": "pending"})

        # Inquiries received (interest)
        inquiries = await db.property_inquiries.count_documents({"landlord_owner_id": owner_id})

        # Signed contracts for landlord properties (revenue side)
        # We match contracts where the landlord_id is this landlord OR property comes from
        # a marketplace listing owned by this landlord
        property_ids = []
        async for listing in db.marketplace_listings.find({"owner_id": owner_id, "status": "approved"}):
            property_ids.append(str(listing["_id"]))

        contract_q = {"$or": [{"landlord_id": owner_id}]}
        if property_ids:
            contract_q["$or"].append({"property_id": {"$in": property_ids}})

        signed_contracts = 0
        total_monthly_rent = 0
        total_annualized = 0
        async for c in db.rental_contracts.find({**contract_q, "status": {"$in": ["active", "completed"]}}):
            signed_contracts += 1
            rent = float(c.get("rent_amount", 0) or 0)
            total_monthly_rent += rent
            total_annualized += rent * 12

        # Commission: commission_rate% of first month's rent (typical real-estate convention)
        commission_rate = float(landlord.get("commission_rate", 10))
        commission_earned = (total_monthly_rent * commission_rate) / 100.0

        grand_total_revenue += total_monthly_rent
        grand_total_commission += commission_earned

        rows.append({
            "landlord_id": owner_id,
            "name": landlord.get("name", ""),
            "email": landlord.get("email", ""),
            "phone": landlord.get("phone", ""),
            "commission_rate": commission_rate,
            "total_listings": all_listings,
            "approved_listings": approved_listings,
            "pending_listings": pending_listings,
            "inquiries_received": inquiries,
            "signed_contracts": signed_contracts,
            "total_monthly_rent": round(total_monthly_rent, 2),
            "total_annualized_rent": round(total_annualized, 2),
            "commission_earned": round(commission_earned, 2),
            "joined_at": landlord.get("created_at", "").isoformat() if landlord.get("created_at") else "",
        })

    rows.sort(key=lambda r: r["commission_earned"], reverse=True)
    return {
        "success": True,
        "landlords": rows,
        "totals": {
            "total_landlords": len(rows),
            "total_monthly_revenue": round(grand_total_revenue, 2),
            "total_annualized_revenue": round(grand_total_revenue * 12, 2),
            "total_commission_earned": round(grand_total_commission, 2),
        },
    }


# ═══════════════════════════════════════════════════════════════════════════════
# MONTHLY COMMISSION REPORT — PDF + AUTO-EMAIL
# ═══════════════════════════════════════════════════════════════════════════════

@router.post('/admin/marketplace-commissions/{landlord_id}/report-pdf')
async def email_commission_report(landlord_id: str, request: Request):
    """Admin: generate + email a monthly commission report PDF to a specific landlord.
    Optional body: { "period": "2026-01" } to override the period (default = current month).
    """
    import os as _os
    import io as _io
    import base64 as _b64
    from datetime import datetime as _dt
    await auth_admin(request)
    if not ObjectId.is_valid(landlord_id):
        raise HTTPException(status_code=400, detail="ID inválido")
    db = get_db()
    landlord = await db.app_users.find_one({"_id": ObjectId(landlord_id)})
    if not landlord:
        raise HTTPException(status_code=404, detail="Landlord no encontrado")

    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    period = (body.get("period") if isinstance(body, dict) else None) or _dt.utcnow().strftime("%Y-%m")

    # Compute landlord's stats
    owner_id = str(landlord["_id"])
    property_ids = [str(l["_id"]) async for l in db.marketplace_listings.find({"owner_id": owner_id, "status": "approved"})]
    contract_q = {"$or": [{"landlord_id": owner_id}]}
    if property_ids:
        contract_q["$or"].append({"property_id": {"$in": property_ids}})
    contracts = []
    async for c in db.rental_contracts.find({**contract_q, "status": {"$in": ["active", "completed"]}}):
        contracts.append(c)
    total_rent = sum(float(c.get("rent_amount", 0) or 0) for c in contracts)
    commission_rate = float(landlord.get("commission_rate", 10))
    commission = (total_rent * commission_rate) / 100.0
    net_payout = total_rent - commission

    # Build PDF
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import LETTER
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    buf = _io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=LETTER, topMargin=0.5*inch, bottomMargin=0.5*inch, leftMargin=0.6*inch, rightMargin=0.6*inch)
    styles = getSampleStyleSheet()
    h_style = ParagraphStyle('h', parent=styles['Heading1'], fontSize=18, textColor=colors.HexColor("#0b1220"), spaceAfter=4)
    sub = ParagraphStyle('s', parent=styles['Normal'], fontSize=10, textColor=colors.HexColor("#475569"), spaceAfter=14)
    sec = ParagraphStyle('sec', parent=styles['Heading2'], fontSize=12, textColor=colors.HexColor("#10b981"), spaceAfter=8, spaceBefore=14)
    story = [
        Paragraph(f"<b>Ross House Rentals</b> — Reporte de Comisiones", h_style),
        Paragraph(f"Landlord: <b>{landlord.get('name','')}</b> ({landlord.get('email','')})<br/>Período: <b>{period}</b> · Generado: {_dt.utcnow().strftime('%Y-%m-%d %H:%M UTC')}", sub),
        Paragraph("Resumen del Período", sec),
    ]
    summary_data = [
        ["Contratos activos", str(len(contracts))],
        ["Renta total mensual (gross)", f"${total_rent:,.2f}"],
        ["Tasa de comisión", f"{commission_rate}%"],
        ["Comisión Ross House", f"${commission:,.2f}"],
        ["Pago neto al landlord", f"${net_payout:,.2f}"],
    ]
    t = Table(summary_data, colWidths=[2.5*inch, 2.5*inch])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (0,-1), colors.HexColor("#F1F5F9")),
        ("FONTNAME", (0,0), (0,-1), "Helvetica-Bold"),
        ("FONTSIZE", (0,0), (-1,-1), 10),
        ("GRID", (0,0), (-1,-1), 0.3, colors.HexColor("#E2E8F0")),
        ("BACKGROUND", (0,-1), (-1,-1), colors.HexColor("#ECFDF5")),
        ("FONTNAME", (1,-1), (1,-1), "Helvetica-Bold"),
        ("TEXTCOLOR", (1,-1), (1,-1), colors.HexColor("#059669")),
        ("LEFTPADDING", (0,0), (-1,-1), 8),
        ("TOPPADDING", (0,0), (-1,-1), 6),
        ("BOTTOMPADDING", (0,0), (-1,-1), 6),
    ]))
    story.append(t)
    if contracts:
        story.append(Paragraph(f"Contratos ({len(contracts)})", sec))
        rows = [["Propiedad", "Inquilino", "Renta", "Comisión", "Neto"]]
        for c in contracts:
            r = float(c.get("rent_amount", 0) or 0)
            cm = r * commission_rate / 100
            rows.append([c.get("property_address","")[:32], c.get("tenant_name","")[:22], f"${r:,.0f}", f"${cm:,.0f}", f"${r-cm:,.0f}"])
        rows.append(["", "TOTAL", f"${total_rent:,.0f}", f"${commission:,.0f}", f"${net_payout:,.0f}"])
        tt = Table(rows, colWidths=[2.4*inch, 1.8*inch, 0.9*inch, 0.9*inch, 0.9*inch])
        tt.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#10b981")),
            ("TEXTCOLOR", (0,0), (-1,0), colors.white),
            ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
            ("FONTSIZE", (0,0), (-1,-1), 9),
            ("GRID", (0,0), (-1,-1), 0.3, colors.HexColor("#E2E8F0")),
            ("BACKGROUND", (0,-1), (-1,-1), colors.HexColor("#F1F5F9")),
            ("FONTNAME", (0,-1), (-1,-1), "Helvetica-Bold"),
            ("ALIGN", (2,1), (4,-1), "RIGHT"),
        ]))
        story.append(tt)
    story.append(Spacer(1, 14))
    story.append(Paragraph("<i>Documento informativo · Confidencial · Ross House Rentals · Dumas, TX</i>", sub))
    doc.build(story)
    buf.seek(0)
    pdf_b64 = _b64.b64encode(buf.read()).decode("utf-8")
    filename = f"Comisiones_{landlord.get('name','landlord').replace(' ','_')}_{period}.pdf"

    # Email via SendGrid (best-effort)
    landlord_email = (landlord.get("email") or "").strip()
    sendgrid_key = _os.getenv("SENDGRID_API_KEY")
    from_email = _os.getenv("SENDGRID_FROM_EMAIL", "info@rosshouserentals.com")
    sent_to = []
    if sendgrid_key and landlord_email:
        try:
            import sendgrid
            from sendgrid.helpers.mail import Mail, Email, To, Content, Attachment, FileContent, FileName, FileType, Disposition
            sg = sendgrid.SendGridAPIClient(api_key=sendgrid_key)
            html = f"""
            <div style="font-family:Helvetica,Arial,sans-serif;max-width:560px;margin:auto;background:#0b1220;color:#fff;border-radius:14px;padding:24px;">
              <div style="background:linear-gradient(135deg,#10b981,#06b6d4);padding:14px;border-radius:10px;text-align:center;">
                <h2 style="margin:0;color:#fff;">📊 Reporte de Comisiones — {period}</h2>
              </div>
              <p style="color:#cbd5e1;margin-top:18px;">Hola <strong style="color:#fff;">{landlord.get('name','')}</strong>,</p>
              <p style="color:#cbd5e1;">Adjunto encontrarás el reporte de comisiones de tus propiedades para el período <strong>{period}</strong>.</p>
              <div style="margin:14px 0;padding:14px;background:#111827;border-radius:10px;color:#e2e8f0;font-size:14px;">
                <div><strong style="color:#94a3b8;">Renta total:</strong> ${total_rent:,.2f}</div>
                <div><strong style="color:#94a3b8;">Comisión ({commission_rate}%):</strong> ${commission:,.2f}</div>
                <div style="margin-top:8px;padding-top:8px;border-top:1px solid #1e293b;"><strong style="color:#94a3b8;">Pago neto:</strong> <strong style="color:#10b981;font-size:18px;">${net_payout:,.2f}</strong></div>
              </div>
              <p style="color:#64748b;font-size:11px;margin-top:18px;border-top:1px solid #1e293b;padding-top:12px;">— Ross House Rentals · Dumas, TX</p>
            </div>
            """
            mail = Mail(
                from_email=Email(from_email, "Ross House Rentals"),
                to_emails=To(landlord_email),
                subject=f"📊 Reporte de Comisiones — {period}",
                plain_text_content=Content("text/plain", f"Tu reporte de comisiones de {period} está adjunto. Renta: ${total_rent:,.2f} · Comisión: ${commission:,.2f} · Neto: ${net_payout:,.2f}"),
            )
            mail.add_content(Content("text/html", html))
            mail.attachment = Attachment(FileContent(pdf_b64), FileName(filename), FileType("application/pdf"), Disposition("attachment"))
            sg.client.mail.send.post(request_body=mail.get())
            sent_to.append(landlord_email)
            logging.info(f"📊 Commission report sent to {landlord_email}")
        except Exception as e:
            logging.warning(f"Commission report email failed: {e}")

    return {"success": True, "pdf_base64": pdf_b64, "filename": filename, "emailed_to": sent_to,
            "summary": {"total_rent": round(total_rent,2), "commission_rate": commission_rate, "commission": round(commission,2), "net_payout": round(net_payout,2), "contracts": len(contracts)}}


# ═══════════════════════════════════════════════════════════════════════════════
# LANDLORD ONBOARDING (registration + KYC)
# ═══════════════════════════════════════════════════════════════════════════════

@router.post('/public/landlord-register')
async def landlord_register(request: Request):
    """Public: landlord signup with KYC. Creates app_users (role=landlord, status=pending_kyc).
    Body: { name, email, phone, password, business_name?, tax_id?, address?, bank_info?, id_doc_base64? }
    """
    import os as _os
    import bcrypt as _bcrypt
    from datetime import datetime as _dt
    data = await request.json()
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip().lower()
    phone = (data.get("phone") or "").strip()
    password = data.get("password") or ""
    if not name or not email or not password:
        raise HTTPException(status_code=400, detail="Nombre, email y password requeridos")
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="Password mínimo 6 caracteres")

    db = get_db()
    existing = await db.app_users.find_one({"email": email})
    if existing:
        raise HTTPException(status_code=409, detail="Ya existe una cuenta con ese email")

    user = {
        "email": email,
        "name": name,
        "phone": phone,
        "role": "landlord",
        "password_hash": _bcrypt.hashpw(password.encode(), _bcrypt.gensalt()).decode(),
        "status": "pending_kyc",
        "kyc": {
            "business_name": (data.get("business_name") or "").strip(),
            "tax_id": (data.get("tax_id") or "").strip(),
            "address": (data.get("address") or "").strip(),
            "city": (data.get("city") or "").strip(),
            "state": (data.get("state") or "").strip(),
            "zip_code": (data.get("zip_code") or "").strip(),
            "bank_info_encrypted": data.get("bank_info") or "",  # frontend can encrypt before sending
            "id_doc_base64": data.get("id_doc_base64") or "",  # ID front/back as data URL
            "submitted_at": _dt.utcnow(),
        },
        "commission_rate": float(data.get("commission_rate") or 10),
        "created_at": _dt.utcnow(),
    }
    result = await db.app_users.insert_one(user)

    # Notify admins of new landlord application
    try:
        from rental.shared import send_rental_push_to_admins
        await send_rental_push_to_admins(
            title="🏠 Nuevo Landlord registrado",
            body=f"{name} · {email} · pendiente KYC",
            data={"type": "landlord_register", "user_id": str(result.inserted_id)},
        )
    except Exception:
        pass

    # Email admins
    try:
        sendgrid_key = _os.getenv("SENDGRID_API_KEY")
        from_email = _os.getenv("SENDGRID_FROM_EMAIL", "info@rosshouserentals.com")
        admin_emails = (_os.getenv("RENTAL_ADMIN_EMAILS") or "yoandyross@gmail.com").split(",")
        if sendgrid_key:
            import sendgrid
            from sendgrid.helpers.mail import Mail, Email, To, Content
            sg = sendgrid.SendGridAPIClient(api_key=sendgrid_key)
            for ae in [e.strip() for e in admin_emails if e.strip()]:
                mail = Mail(
                    from_email=Email(from_email, "Ross House Rentals"),
                    to_emails=To(ae),
                    subject=f"🏠 Nuevo landlord pendiente KYC: {name}",
                    plain_text_content=Content("text/plain", f"Un nuevo landlord se registró:\n\nNombre: {name}\nEmail: {email}\nTeléfono: {phone}\nNegocio: {user['kyc']['business_name']}\n\nRevisar en: https://www.rosshouserentals.com/admin/marketplace")
                )
                sg.client.mail.send.post(request_body=mail.get())
    except Exception as e:
        logging.warning(f"Landlord register admin email failed: {e}")

    return {"success": True, "user_id": str(result.inserted_id), "status": "pending_kyc",
            "message": "Tu cuenta fue creada. Te avisaremos cuando se complete la verificación KYC (24-72h)."}


@router.post('/admin/landlords/{landlord_id}/kyc-approve')
async def admin_approve_landlord_kyc(landlord_id: str, request: Request):
    """Admin: approve a landlord's KYC and activate the account"""
    import os as _os
    from datetime import datetime as _dt
    await auth_admin(request)
    if not ObjectId.is_valid(landlord_id):
        raise HTTPException(status_code=400, detail="ID inválido")
    db = get_db()
    result = await db.app_users.update_one(
        {"_id": ObjectId(landlord_id), "role": "landlord"},
        {"$set": {"status": "active", "kyc.approved_at": _dt.utcnow()}},
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Landlord no encontrado")
    landlord = await db.app_users.find_one({"_id": ObjectId(landlord_id)})

    # Notify landlord
    try:
        sendgrid_key = _os.getenv("SENDGRID_API_KEY")
        from_email = _os.getenv("SENDGRID_FROM_EMAIL", "info@rosshouserentals.com")
        if sendgrid_key and landlord.get("email"):
            import sendgrid
            from sendgrid.helpers.mail import Mail, Email, To, Content
            sg = sendgrid.SendGridAPIClient(api_key=sendgrid_key)
            mail = Mail(
                from_email=Email(from_email, "Ross House Rentals"),
                to_emails=To(landlord["email"]),
                subject="✅ Tu cuenta de Landlord fue aprobada",
                plain_text_content=Content("text/plain", f"¡Hola {landlord.get('name','')}!\n\nTu cuenta de landlord fue aprobada. Ya puedes publicar propiedades en Ross House Rentals.\n\nIniciar sesión: https://www.rosshouserentals.com"),
            )
            mail.add_content(Content("text/html", f"""
            <div style="font-family:Helvetica,Arial,sans-serif;max-width:520px;margin:auto;background:#0b1220;color:#fff;border-radius:14px;padding:24px;">
              <div style="background:linear-gradient(135deg,#10b981,#06b6d4);padding:14px;border-radius:10px;text-align:center;"><h2 style="margin:0;color:#fff;">✅ KYC Aprobado</h2></div>
              <p style="color:#cbd5e1;margin-top:18px;">Hola <strong style="color:#fff;">{landlord.get('name','')}</strong>,</p>
              <p style="color:#cbd5e1;">¡Buenas noticias! Tu cuenta de landlord fue aprobada. Ya puedes publicar propiedades.</p>
              <a href="https://www.rosshouserentals.com" style="display:inline-block;margin-top:8px;background:#10b981;color:#fff;padding:12px 20px;border-radius:10px;text-decoration:none;font-weight:bold;">Acceder a mi portal →</a>
              <p style="color:#64748b;font-size:11px;margin-top:18px;">— Ross House Rentals · Dumas, TX</p>
            </div>
            """))
            sg.client.mail.send.post(request_body=mail.get())
    except Exception as e:
        logging.warning(f"KYC approval email failed: {e}")

    return {"success": True, "status": "active"}


@router.get('/admin/property-inquiries')
async def admin_list_inquiries(request: Request):
    """Admin: List all property inquiries"""
    await auth_admin(request)
    cursor = get_db().property_inquiries.find().sort("created_at", -1).limit(100)
    inquiries = []
    async for i in cursor:
        inquiries.append(serialize(i))
    return {"success": True, "inquiries": inquiries, "count": len(inquiries)}


@router.get('/admin/marketplace-stats')
async def admin_marketplace_stats(request: Request):
    """Admin: Get marketplace statistics"""
    await auth_admin(request)

    total_users = await get_db().app_users.count_documents({})
    landlords = await get_db().app_users.count_documents({"role": "landlord"})
    tenants_mp = await get_db().app_users.count_documents({"role": "tenant"})
    buyers = await get_db().app_users.count_documents({"role": "buyer"})

    total_listings = await get_db().marketplace_listings.count_documents({})
    pending = await get_db().marketplace_listings.count_documents({"status": "pending"})
    approved = await get_db().marketplace_listings.count_documents({"status": "approved"})
    rejected = await get_db().marketplace_listings.count_documents({"status": "rejected"})

    inquiries = await get_db().property_inquiries.count_documents({})

    return {
        "success": True,
        "users": {"total": total_users, "landlords": landlords, "tenants": tenants_mp, "buyers": buyers},
        "listings": {"total": total_listings, "pending": pending, "approved": approved, "rejected": rejected},
        "inquiries": inquiries,
    }





@router.get('/admin/properties')
async def list_properties(request: Request):
    """List all properties"""
    user = await auth_admin(request)
    from urllib.parse import parse_qs
    params = parse_qs(str(request.url.query))
    status_filter = params.get('status', [None])[0]
    search = params.get('search', [''])[0]

    query = {}
    if status_filter:
        query['status'] = status_filter
    if search:
        query['$or'] = [
            {"address": {"$regex": search, "$options": "i"}},
            {"city": {"$regex": search, "$options": "i"}},
            {"property_number": {"$regex": search, "$options": "i"}},
        ]

    cursor = get_db().properties.find(query).sort("created_at", -1)
    properties = []
    async for p in cursor:
        properties.append(serialize(p))

    return {"success": True, "properties": properties, "count": len(properties)}


@router.post('/admin/properties')
async def create_property(request: Request):
    """Create a new property"""
    user = await auth_admin(request)
    data = await request.json()
    now = datetime.utcnow()

    # Generate property number
    count = await get_db().properties.count_documents({})
    prop_number = f"PROP-{now.year}-{str(count + 1).zfill(3)}"

    property_doc = {
        "property_number": prop_number,
        "name": data.get('name', ''),
        "address": data.get('address', ''),
        "city": data.get('city', ''),
        "state": data.get('state', 'TX'),
        "zip_code": data.get('zip_code', data.get('zip', '')),
        "type": data.get('type', 'house'),  # house, apartment, condo, townhouse
        "bedrooms": int(data.get('bedrooms', 0)),
        "bathrooms": float(data.get('bathrooms', 0)),
        "square_feet": int(data.get('square_feet', data.get('sqft', 0))),
        "rent_amount": float(data.get('rent_amount', 0)),
        "deposit_amount": float(data.get('deposit_amount', 0)),
        "description": data.get('description', ''),
        "features": data.get('features', []),
        "status": data.get('status', 'available'),  # available, rented, maintenance
        "current_tenant_id": None,
        "current_contract_id": None,
        "notes": data.get('notes', ''),
        "latitude": float(data['latitude']) if data.get('latitude') else None,
        "longitude": float(data['longitude']) if data.get('longitude') else None,
        "photos": [],  # List of photo storage paths
        # ─── Owner assignment ───
        "owner_id": None,
        "owner_name": "",
        "owner_email": "",
        "owner_phone": "",
        # ─── Section 8 / Housing Choice Voucher fields ───
        "section8_accepted": bool(data.get('section8_accepted', False)),
        "section8_pha": data.get('section8_pha', ''),  # e.g., "Amarillo Housing Authority"
        "section8_pha_contact": data.get('section8_pha_contact', ''),  # liaison name/phone
        "section8_last_inspection": data.get('section8_last_inspection'),  # ISO date or None
        "section8_next_inspection": data.get('section8_next_inspection'),  # ISO date or None
        "section8_notes": data.get('section8_notes', ''),
        "created_at": now,
        "updated_at": now,
        "created_by": user.get('email', 'admin'),
    }

    result = await get_db().properties.insert_one(property_doc)
    new_id = str(result.inserted_id)

    # Handle initial owner assignment if provided
    owner_id_raw = data.get("owner_id")
    if owner_id_raw and ObjectId.is_valid(owner_id_raw):
        owner_user = await get_db().app_users.find_one({"_id": ObjectId(owner_id_raw)})
        if owner_user:
            await get_db().properties.update_one(
                {"_id": result.inserted_id},
                {"$set": {
                    "owner_id": str(owner_user.get("_id")),
                    "owner_name": owner_user.get("name", ""),
                    "owner_email": owner_user.get("email", ""),
                    "owner_phone": owner_user.get("phone", ""),
                }}
            )

    return {
        "success": True,
        "message": f"Propiedad {prop_number} creada exitosamente",
        "property_id": new_id,
        "property_number": prop_number,
    }


@router.get('/admin/properties/{property_id}')
async def get_property(property_id: str, request: Request):
    """Get property detail"""
    user = await auth_admin(request)
    prop = await get_db().properties.find_one({"_id": ObjectId(property_id)})
    if not prop:
        raise HTTPException(status_code=404, detail="Propiedad no encontrada")

    # Get current tenant info
    tenant_info = None
    if prop.get('current_tenant_id'):
        tenant = await get_db().tenants.find_one({"_id": ObjectId(prop['current_tenant_id'])})
        if tenant:
            tenant_info = {"name": tenant.get('name', ''), "phone": tenant.get('phone', ''), "email": tenant.get('email', '')}

    # Get payment history for this property
    payments = []
    async for pay in get_db().rental_payments.find({"property_id": property_id}).sort("payment_date", -1).limit(12):
        payments.append(serialize(pay))

    # Get contracts
    contracts = []
    async for c in get_db().rental_contracts.find({"property_id": property_id}).sort("created_at", -1).limit(5):
        contracts.append(serialize(c))

    result = serialize(prop)
    result['tenant_info'] = tenant_info
    result['recent_payments'] = payments
    result['contracts'] = contracts
    return {"success": True, "property": result}


@router.put('/admin/properties/{property_id}')
async def update_property(property_id: str, request: Request):
    """Update a property"""
    user = await auth_admin(request)
    data = await request.json()
    now = datetime.utcnow()

    prop = await get_db().properties.find_one({"_id": ObjectId(property_id)})
    if not prop:
        raise HTTPException(status_code=404, detail="Propiedad no encontrada")

    update_fields = {}
    allowed = ['name', 'address', 'city', 'state', 'zip_code', 'type', 'bedrooms', 'bathrooms',
               'square_feet', 'rent_amount', 'deposit_amount', 'features', 'status', 'notes', 'description',
               # Section 8 fields
               'section8_accepted', 'section8_pha', 'section8_pha_contact',
               'section8_last_inspection', 'section8_next_inspection', 'section8_notes']
    # Also accept frontend field aliases
    alias_map = {'zip': 'zip_code', 'sqft': 'square_feet'}
    for alias, real in alias_map.items():
        if alias in data and real not in data:
            data[real] = data[alias]
    for field in allowed:
        if field in data:
            if field in ('bedrooms', 'square_feet'):
                update_fields[field] = int(data[field])
            elif field in ('bathrooms', 'rent_amount', 'deposit_amount'):
                update_fields[field] = float(data[field])
            elif field == 'section8_accepted':
                update_fields[field] = bool(data[field])
            else:
                update_fields[field] = data[field]

    # If admin is manually changing the status, mark it as manual override
    # so auto-sync logic (from contracts) won't override the choice.
    if 'status' in data:
        new_status = data['status']
        old_status = prop.get('status')
        if new_status != old_status:
            update_fields['status_manually_set'] = True
            update_fields['status_manually_set_at'] = now
            update_fields['status_manually_set_by'] = user.get('email', 'admin')
        # Allow admin to "release" the lock by explicitly setting auto-sync
        if data.get('clear_manual_override'):
            update_fields['status_manually_set'] = False

    update_fields['updated_at'] = now

    await get_db().properties.update_one({"_id": ObjectId(property_id)}, {"$set": update_fields})
    return {"success": True, "message": "Propiedad actualizada"}


@router.delete('/admin/properties/{property_id}')
async def delete_property(property_id: str, request: Request):
    """Delete a property"""
    user = await auth_admin(request)
    prop = await get_db().properties.find_one({"_id": ObjectId(property_id)})
    if not prop:
        raise HTTPException(status_code=404, detail="Propiedad no encontrada")
    if prop.get('status') == 'rented':
        from urllib.parse import parse_qs
        params = parse_qs(str(request.url.query))
        force = params.get('force', ['false'])[0].lower() == 'true'
        if not force:
            raise HTTPException(status_code=400, detail="No se puede eliminar una propiedad alquilada. Use ?force=true")

    await get_db().properties.delete_one({"_id": ObjectId(property_id)})
    return {"success": True, "message": f"Propiedad {prop.get('property_number', '')} eliminada"}


# ═══════════════════════════════════════════════════════════════════════
# SECTION 8 — NOI Impact Calculator
# ═══════════════════════════════════════════════════════════════════════

@router.get('/admin/properties/{property_id}/s8-impact')
async def admin_property_s8_impact(property_id: str, request: Request):
    """Calculate the Section 8 NOI impact for a single property.

    Compares the property's current rent vs. the HUD Fair Market Rent (FMR)
    for the matching Texas MSA and bedroom count. Returns the monthly/annual
    uplift potential plus a recommendation.
    """
    await auth_admin(request)
    db = get_db()
    try:
        prop = await db.properties.find_one({"_id": ObjectId(property_id)})
    except Exception:
        raise HTTPException(status_code=400, detail="ID inválido")
    if not prop:
        raise HTTPException(status_code=404, detail="Propiedad no encontrada")

    city = prop.get("city", "")
    bedrooms = int(prop.get("bedrooms", 0) or 0)
    current_rent = float(prop.get("rent_amount", 0) or 0)

    fmr_info = get_fmr(city, bedrooms)
    impact = compute_s8_impact(current_rent, fmr_info["fmr_amount"])

    return {
        "success": True,
        "property_id": property_id,
        "property_name": prop.get("name") or prop.get("address"),
        "city": city,
        "bedrooms": bedrooms,
        "section8_accepted": bool(prop.get("section8_accepted", False)),
        "fmr": fmr_info,
        "impact": impact,
    }


@router.get('/admin/properties/s8-impact-summary')
async def admin_properties_s8_impact_summary(request: Request):
    """Aggregate S8 NOI impact across ALL properties.

    Returns per-property impact + portfolio totals:
      - total potential uplift if all non-S8 properties activated S8
      - count of properties with positive uplift opportunity
      - top 3 properties by absolute annual uplift
    """
    await auth_admin(request)
    db = get_db()

    items = []
    total_potential_monthly = 0.0
    total_potential_annual = 0.0
    eligible_count = 0
    already_s8_count = 0
    no_data_count = 0

    async for prop in db.properties.find({}):
        city = prop.get("city", "")
        bedrooms = int(prop.get("bedrooms", 0) or 0)
        current_rent = float(prop.get("rent_amount", 0) or 0)
        is_s8 = bool(prop.get("section8_accepted", False))
        fmr_info = get_fmr(city, bedrooms)
        impact = compute_s8_impact(current_rent, fmr_info["fmr_amount"])

        items.append({
            "property_id": str(prop["_id"]),
            "name": prop.get("name") or prop.get("address", ""),
            "address": prop.get("address", ""),
            "city": city,
            "bedrooms": bedrooms,
            "section8_accepted": is_s8,
            "current_rent": impact["current_rent"],
            "fmr_amount": impact["fmr_amount"],
            "monthly_uplift": impact["monthly_uplift"],
            "annual_uplift": impact["annual_uplift"],
            "pct_uplift": impact["pct_uplift"],
            "recommendation": impact["recommendation"],
            "msa": fmr_info["msa_display"],
        })

        if is_s8:
            already_s8_count += 1
        elif impact["recommendation"] == "no_data":
            no_data_count += 1
        elif impact["monthly_uplift"] > 0:
            eligible_count += 1
            total_potential_monthly += impact["monthly_uplift"]
            total_potential_annual += impact["annual_uplift"]

    # Top 3 opportunities (only non-S8 with positive uplift)
    top_3 = sorted(
        [i for i in items if not i["section8_accepted"] and i["monthly_uplift"] > 0],
        key=lambda x: x["annual_uplift"],
        reverse=True,
    )[:3]

    return {
        "success": True,
        "properties": items,
        "totals": {
            "property_count": len(items),
            "already_s8": already_s8_count,
            "eligible_for_uplift": eligible_count,
            "no_fmr_data": no_data_count,
            "total_potential_monthly": round(total_potential_monthly, 2),
            "total_potential_annual": round(total_potential_annual, 2),
        },
        "top_opportunities": top_3,
    }


@router.get('/admin/section8/inspections')
async def admin_section8_inspections(request: Request):
    """List all Section 8 properties + their next-inspection status.

    Each entry classifies urgency based on days-until-inspection:
      - overdue   (negative days)
      - urgent    (0-7 days)
      - soon      (8-15 days)
      - upcoming  (16-30 days)
      - scheduled (>30 days)
      - none      (S8 enabled but no next_inspection date set)

    Returns: {inspections[], counts{overdue,urgent,soon,upcoming,scheduled,none}}
    """
    await auth_admin(request)
    db = get_db()
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

    items = []
    counts = {"overdue": 0, "urgent": 0, "soon": 0, "upcoming": 0, "scheduled": 0, "none": 0}

    async for prop in db.properties.find({"section8_accepted": True}):
        next_str = prop.get("section8_next_inspection")
        last_str = prop.get("section8_last_inspection")

        next_dt = None
        if next_str:
            try:
                if isinstance(next_str, datetime):
                    next_dt = next_str
                else:
                    s = str(next_str)[:10]
                    next_dt = datetime.strptime(s, "%Y-%m-%d")
            except Exception:
                next_dt = None

        if next_dt is None:
            urgency = "none"
            days_until = None
        else:
            days_until = (next_dt - today).days
            if days_until < 0:
                urgency = "overdue"
            elif days_until <= 7:
                urgency = "urgent"
            elif days_until <= 15:
                urgency = "soon"
            elif days_until <= 30:
                urgency = "upcoming"
            else:
                urgency = "scheduled"

        counts[urgency] = counts.get(urgency, 0) + 1

        items.append({
            "property_id": str(prop["_id"]),
            "name": prop.get("name") or prop.get("address", ""),
            "address": prop.get("address", ""),
            "city": prop.get("city", ""),
            "section8_pha": prop.get("section8_pha", ""),
            "section8_pha_contact": prop.get("section8_pha_contact", ""),
            "section8_last_inspection": str(last_str)[:10] if last_str else None,
            "section8_next_inspection": next_dt.strftime("%Y-%m-%d") if next_dt else None,
            "days_until": days_until,
            "urgency": urgency,
            "notes": prop.get("section8_notes", ""),
        })

    # Sort: overdue first, then urgent, then by days
    URGENCY_ORDER = {"overdue": 0, "urgent": 1, "soon": 2, "upcoming": 3, "scheduled": 4, "none": 5}
    items.sort(key=lambda x: (URGENCY_ORDER[x["urgency"]], x["days_until"] if x["days_until"] is not None else 99999))

    return {
        "success": True,
        "inspections": items,
        "counts": counts,
        "total": len(items),
        "needs_attention": counts["overdue"] + counts["urgent"] + counts["soon"] + counts["none"],
    }


