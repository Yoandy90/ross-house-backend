"""One-time revision of recognized seed content; preserves custom legal documents."""
from datetime import datetime, timezone
import hashlib

REVISION = "public_content_20260910"
LEGACY_HASHES = {'privacy_es': 'c16b0d9f49256faf6077314022cbab3acfad944cd91b12ba1157449cbd414aaf', 'privacy_en': '5ed48d7c898be7d85a83396354d65b506cb46ca34f91684a280e07396c1139ca', 'cookies_es': '5b0d246662950efcfd793ba63b78a2fcd5943e5b5f9b6459c32d837683d7fcb8', 'cookies_en': '720b003ed1b8033cfc543f3566be453b78f6766b0f58389854b313359b5435a7'}

LEGACY_HASHES.update({'terms_es': '6365709a38b781e6c10d6889413345b453e23fc36951c0742f580d6841bb3b37', 'terms_en': '5ff469dac64603d82ffee15f020d3cef1c0a9c646eb843b60b8dbd4e83f3738c', 'account_deletion_es': 'b3974b7da78fb212f63cbb30e46623996643fedb59186a3b64d2c9888fc9df2f', 'account_deletion_en': '70a461b91a12ac96031de7b0e7108692ecc63c4aa2c1c98afed8d26f9a1fd283'})

async def revise_known_seed_documents(collection, existing):
    from rental.legal_router import (
        DEFAULT_PRIVACY_ES, DEFAULT_PRIVACY_EN, DEFAULT_COOKIES_ES, DEFAULT_COOKIES_EN,
        DEFAULT_TERMS_ES, DEFAULT_TERMS_EN, DEFAULT_ACCOUNT_DELETION_ES, DEFAULT_ACCOUNT_DELETION_EN,
    )
    replacements = {
        "privacy_es": DEFAULT_PRIVACY_ES.strip(), "privacy_en": DEFAULT_PRIVACY_EN.strip(),
        "cookies_es": DEFAULT_COOKIES_ES.strip(), "cookies_en": DEFAULT_COOKIES_EN.strip(),
    }
    replacements.update({
        "terms_es": DEFAULT_TERMS_ES.strip(), "terms_en": DEFAULT_TERMS_EN.strip(),
        "account_deletion_es": DEFAULT_ACCOUNT_DELETION_ES.strip(),
        "account_deletion_en": DEFAULT_ACCOUNT_DELETION_EN.strip(),
    })
    revised = []
    for field, expected_hash in LEGACY_HASHES.items():
        value = existing.get(field)
        if not isinstance(value, str) or hashlib.sha256(value.encode()).hexdigest() != expected_hash:
            continue
        marker = f"content_revisions.{REVISION}.{field}"
        now = datetime.now(timezone.utc)
        result = await collection.update_one(
            {"_id": "legal_config", field: value, marker: {"$exists": False}},
            {"$set": {field: replacements[field], "updated_at": now,
                      marker: {"previous_content": value, "revised_at": now}}},
        )
        if result.modified_count:
            revised.append(field)
    return revised

