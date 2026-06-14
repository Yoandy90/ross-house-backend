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
    """Public endpoint: Receive rental applications from the website"""
    data = await request.json()
    application = {
        "name": data.get("name", ""),
        "email": data.get("email", ""),
        "phone": data.get("phone", ""),
        "property_interest": data.get("property_interest", ""),
        "employment": data.get("employment", ""),
        "monthly_income": data.get("monthly_income", ""),
        "message": data.get("message", ""),
        "status": "new",
        "source": "website",
        "created_at": datetime.utcnow(),
    }
    result = await get_db().rental_applications.insert_one(application)
    return {"success": True, "application_id": str(result.inserted_id), "message": "Application received successfully"}




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

    # Update landlord's property count
    await get_db().app_users.update_one(
        {"_id": ObjectId(user.get("_id"))},
        {"$inc": {"properties_count": 1}}
    )

    return {
        "success": True,
        "listing_id": str(result.inserted_id),
        "message": "Propiedad enviada para aprobación"
    }


@router.get('/landlord/my-listings')
async def get_landlord_listings(request: Request):
    """Landlord: Get my property listings"""
    user = await auth_marketplace(request)
    if user.get("role") != "landlord":
        raise HTTPException(status_code=403, detail="Solo propietarios")

    cursor = get_db().marketplace_listings.find(
        {"owner_id": user.get("_id")}
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
    """Landlord: Update own listing"""
    user = await auth_marketplace(request)
    if user.get("role") != "landlord":
        raise HTTPException(status_code=403, detail="Solo propietarios")

    listing = await get_db().marketplace_listings.find_one({"_id": ObjectId(listing_id)})
    if not listing or listing.get("owner_id") != user.get("_id"):
        raise HTTPException(status_code=404, detail="Propiedad no encontrada")

    data = await request.json()
    update_fields = {}
    allowed = ["address", "city", "state", "zip_code", "property_type", "listing_type",
               "bedrooms", "bathrooms", "square_feet", "rent_amount", "deposit_amount",
               "sale_price", "description", "features", "photos"]
    for f in allowed:
        if f in data:
            update_fields[f] = data[f]
    update_fields["updated_at"] = datetime.utcnow()

    # If listing was rejected, re-submit for approval
    if listing.get("status") == "rejected" and update_fields:
        update_fields["status"] = "pending"

    await get_db().marketplace_listings.update_one(
        {"_id": ObjectId(listing_id)},
        {"$set": update_fields}
    )

    return {"success": True, "message": "Propiedad actualizada"}


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

    return {"success": True, "message": f"Listado {new_status}", "status": new_status}


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
        "created_at": now,
        "updated_at": now,
        "created_by": user.get('email', 'admin'),
    }

    result = await get_db().properties.insert_one(property_doc)
    return {
        "success": True,
        "message": f"Propiedad {prop_number} creada exitosamente",
        "property_id": str(result.inserted_id),
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
               'square_feet', 'rent_amount', 'deposit_amount', 'features', 'status', 'notes', 'description']
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


