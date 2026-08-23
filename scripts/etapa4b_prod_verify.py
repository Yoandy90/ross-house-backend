"""Etapa 4B — Verificación READ-ONLY del endpoint /analysis en producción.
- NO escribe en la DB (solo find_one lecturas).
- Genera un JWT admin efímero (legacy, exp 1h) firmado con TENANT_JWT_SECRET
  para autenticarse contra Railway (REQUIRE_SESSION_SID aún no activo).
"""
import asyncio, os, json
from datetime import datetime, timedelta

import jwt as pyjwt
import httpx
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
BASE = "https://ross-house-backend-production.up.railway.app"
SECRET = os.environ["TENANT_JWT_SECRET"]


async def main():
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ.get("DB_NAME", "taxportal")]
    admin = await db.app_users.find_one({"email": "yoandyross@gmail.com", "role": "admin"}, {"email": 1})
    invs = [i async for i in db.investments.find({}, {"address": 1, "property_address": 1, "name": 1})]
    print("ADMIN:", admin["_id"])
    for i in invs:
        print("INV:", i["_id"], "|", i.get("address") or i.get("property_address") or i.get("name"))

    token = pyjwt.encode({
        "user_id": str(admin["_id"]), "email": admin["email"], "role": "admin",
        "exp": datetime.utcnow() + timedelta(hours=1), "type": "marketplace",
    }, SECRET, algorithm="HS256")

    async with httpx.AsyncClient(timeout=30) as c:
        for i in invs:
            r = await c.get(f"{BASE}/api/admin/investments/{i['_id']}/analysis",
                            headers={"Authorization": f"Bearer {token}"})
            label = i.get("address") or i.get("property_address") or i.get("name")
            print(f"\n===== {label} — HTTP {r.status_code} =====")
            print(json.dumps(r.json(), indent=2, default=str))
        # security: sin auth y con token tenant falso
        r401 = await c.get(f"{BASE}/api/admin/investments/{invs[0]['_id']}/analysis")
        bad = pyjwt.encode({"user_id": str(admin["_id"]), "email": "x@y.com", "role": "tenant",
                            "exp": datetime.utcnow() + timedelta(hours=1), "type": "marketplace"},
                           SECRET, algorithm="HS256")
        rten = await c.get(f"{BASE}/api/admin/investments/{invs[0]['_id']}/analysis",
                           headers={"Authorization": f"Bearer {bad}"})
        print(f"\nSECURITY: no-auth={r401.status_code}, tenant-role-token={rten.status_code}")

asyncio.run(main())
