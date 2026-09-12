"""Public emergency contacts for the mobile and web apps.

The endpoint prefers active, admin-managed records from MongoDB. If the
collection is empty or temporarily unavailable, it returns a conservative set
of verified contacts so emergency access never depends on database health.
"""

from copy import deepcopy
import logging

from fastapi import APIRouter

logger = logging.getLogger("emergency_contacts")

router = APIRouter(
    prefix="/public/emergency-contacts",
    tags=["Public Emergency Contacts"],
)


VERIFIED_DEFAULT_CONTACTS = [
    {
        "_id": "default-911",
        "name_es": "Emergencias 911",
        "name_en": "Emergency 911",
        "phone": "911",
        "icon": "alert-triangle",
        "color": "#ff4545",
        "available": "24/7",
        "order": 10,
        "is_active": True,
    },
    {
        "_id": "default-dumas-police",
        "name_es": "Policía de Dumas",
        "name_en": "Dumas Police",
        "phone": "8069353998",
        "icon": "shield",
        "color": "#3b82f6",
        "available": "No-emergencia",
        "order": 20,
        "is_active": True,
    },
    {
        "_id": "default-dumas-fire",
        "name_es": "Bomberos de Dumas",
        "name_en": "Dumas Fire Department",
        "phone": "8069356434",
        "icon": "flame",
        "color": "#f97316",
        "available": "No-emergencia",
        "order": 30,
        "is_active": True,
    },
    {
        "_id": "default-moore-sheriff",
        "name_es": "Sheriff del Condado de Moore",
        "name_en": "Moore County Sheriff",
        "phone": "8069354145",
        "icon": "shield",
        "color": "#6366f1",
        "available": "24/7",
        "order": 40,
        "is_active": True,
    },
    {
        "_id": "default-moore-hospital",
        "name_es": "Hospital del Condado de Moore",
        "name_en": "Moore County Hospital",
        "phone": "8069357171",
        "icon": "medical-bag",
        "color": "#10b981",
        "available": "24/7 ER",
        "order": 50,
        "is_active": True,
    },
    {
        "_id": "default-poison-control",
        "name_es": "Control de Envenenamiento",
        "name_en": "Poison Control",
        "phone": "18002221222",
        "icon": "phone",
        "color": "#a855f7",
        "available": "24/7 nacional",
        "order": 60,
        "is_active": True,
    },
    {
        "_id": "default-xcel-outage",
        "name_es": "Xcel Energy (apagón)",
        "name_en": "Xcel Energy (outage)",
        "phone": "18008951999",
        "icon": "zap",
        "color": "#fbbf24",
        "available": "24/7",
        "order": 70,
        "is_active": True,
    },
    {
        "_id": "default-atmos-gas",
        "name_es": "Atmos Energy (fuga de gas)",
        "name_en": "Atmos Energy (gas leak)",
        "phone": "18663228667",
        "icon": "alert-triangle",
        "color": "#ef4444",
        "available": "24/7 EMERGENCIA",
        "order": 80,
        "is_active": True,
    },
    {
        "_id": "default-ross-house",
        "name_es": "Ross House Rentals",
        "name_en": "Ross House Rentals",
        "phone": "8069342018",
        "icon": "home",
        "color": "#e11d48",
        "available": "Administración",
        "order": 90,
        "is_active": True,
    },
]


def _serialize_contact(doc):
    return {
        "_id": str(doc.get("_id", "")),
        "name_es": doc.get("name_es", ""),
        "name_en": doc.get("name_en", doc.get("name_es", "")),
        "phone": str(doc.get("phone", "")),
        "icon": doc.get("icon", "phone"),
        "color": doc.get("color", "#e11d48"),
        "available": doc.get("available", ""),
        "order": doc.get("order", 0),
        "is_active": doc.get("is_active", True),
    }


@router.get("")
async def get_public_emergency_contacts():
    """Return active contacts in display order, with verified safe defaults."""
    contacts = []

    try:
        from rental.shared import get_db

        db = get_db()
        cursor = db.emergency_contacts.find({"is_active": True}).sort("order", 1)
        async for doc in cursor:
            contact = _serialize_contact(doc)
            if contact["name_es"] and contact["phone"]:
                contacts.append(contact)
    except Exception:
        logger.warning(
            "Emergency contacts collection unavailable; using verified defaults",
            exc_info=True,
        )

    if not contacts:
        contacts = deepcopy(VERIFIED_DEFAULT_CONTACTS)

    return {
        "success": True,
        "contacts": contacts,
        "total": len(contacts),
    }
