"""
Rental Storage Service — Property Photos & Checklist Images
============================================================
Uses Emergent Object Storage for file uploads.
Falls back to MongoDB base64 storage if Object Storage is unavailable.
"""
import os
import uuid
import logging
import base64
import requests
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

STORAGE_URL = "https://integrations.emergentagent.com/objstore/api/v1/storage"
EMERGENT_KEY = os.environ.get("EMERGENT_LLM_KEY")
APP_NAME = "ross-rentals"

storage_key = None
_use_mongo_fallback = False


def set_emergent_key(key: str):
    """Set the Emergent key at runtime (from DB config)."""
    global EMERGENT_KEY, storage_key, _use_mongo_fallback
    if key and key != EMERGENT_KEY:
        EMERGENT_KEY = key
        # Reset cached storage state so init_storage() re-initializes with the new key
        storage_key = None
        _use_mongo_fallback = False
        logger.info("✅ Storage service: Emergent key set from DB config (cache cleared)")


def init_storage():
    """Initialize storage — call once at startup."""
    global storage_key, _use_mongo_fallback
    if storage_key:
        return storage_key
    if not EMERGENT_KEY:
        logger.warning("⚠️ No EMERGENT_LLM_KEY - using MongoDB fallback for photos")
        _use_mongo_fallback = True
        return None
    try:
        resp = requests.post(
            f"{STORAGE_URL}/init",
            json={"emergent_key": EMERGENT_KEY},
            timeout=30
        )
        resp.raise_for_status()
        storage_key = resp.json()["storage_key"]
        logger.info("✅ Rental Object Storage initialized")
        return storage_key
    except Exception as e:
        logger.warning(f"⚠️ Object Storage init failed: {e} — using MongoDB fallback")
        _use_mongo_fallback = True
        return None


def put_object(path: str, data: bytes, content_type: str) -> dict:
    """Upload a file to object storage or return MongoDB-ready dict."""
    if _use_mongo_fallback:
        # Store as base64 in MongoDB
        b64 = base64.b64encode(data).decode('utf-8')
        return {
            "path": path,
            "size": len(data),
            "storage_type": "mongodb",
            "base64_data": f"data:{content_type};base64,{b64}",
        }

    key = init_storage()
    if not key:
        # Fallback to MongoDB
        b64 = base64.b64encode(data).decode('utf-8')
        return {
            "path": path,
            "size": len(data),
            "storage_type": "mongodb",
            "base64_data": f"data:{content_type};base64,{b64}",
        }

    resp = requests.put(
        f"{STORAGE_URL}/objects/{path}",
        headers={"X-Storage-Key": key, "Content-Type": content_type},
        data=data,
        timeout=120
    )
    resp.raise_for_status()
    result = resp.json()
    result["storage_type"] = "object_storage"
    return result


def get_object(path: str):
    """Download a file from object storage. Returns (bytes, content_type)."""
    key = init_storage()
    if not key:
        raise Exception("Storage not initialized")
    resp = requests.get(
        f"{STORAGE_URL}/objects/{path}",
        headers={"X-Storage-Key": key},
        timeout=60
    )
    resp.raise_for_status()
    return resp.content, resp.headers.get("Content-Type", "application/octet-stream")


def upload_property_photo(property_id: str, file_data: bytes, filename: str, content_type: str) -> dict:
    """Upload a property photo. Returns storage info."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "jpg"
    file_id = str(uuid.uuid4())
    path = f"{APP_NAME}/properties/{property_id}/{file_id}.{ext}"
    result = put_object(path, file_data, content_type)
    return {
        "file_id": file_id,
        "storage_path": result.get("path", path),
        "original_filename": filename,
        "content_type": content_type,
        "size": result.get("size", len(file_data)),
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
    }


def upload_checklist_photo(contract_id: str, checklist_type: str, room: str,
                           file_data: bytes, filename: str, content_type: str) -> dict:
    """Upload a move-in/move-out checklist photo. Returns storage info."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "jpg"
    file_id = str(uuid.uuid4())
    path = f"{APP_NAME}/checklists/{contract_id}/{checklist_type}/{room}/{file_id}.{ext}"
    result = put_object(path, file_data, content_type)
    return {
        "file_id": file_id,
        "storage_path": result.get("path", path),
        "original_filename": filename,
        "content_type": content_type,
        "size": result.get("size", len(file_data)),
        "room": room,
        "checklist_type": checklist_type,
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
    }
