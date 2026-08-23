"""Verificación en vivo Fase A (flag ON) — pasos 4-13. Sesión de testing tenant."""
import asyncio, os, json, base64, time
import httpx
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
BASE = "https://ross-house-backend-production.up.railway.app"
EMAIL, PASSWORD = "yosbelgarrido26@gmail.com", "sRUUSvEB4O"


def jwt_payload(tok):
    p = tok.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4)))


async def login(c):
    r = await c.post(f"{BASE}/api/public/marketplace-login",
                     json={"email": EMAIL, "password": PASSWORD})
    assert r.status_code == 200, r.text[:150]
    return r.json()["token"]


async def refresh(c, refresh_token=None, bearer=None):
    h = {"Authorization": f"Bearer {bearer}"} if bearer else {}
    return await c.post(f"{BASE}/api/auth/refresh",
                        json={"refresh_token": refresh_token}, headers=h)


async def main():
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ.get("DB_NAME", "taxportal")]
    async with httpx.AsyncClient(timeout=30) as c:
        # 4. TEST SESSION
        access = await login(c)
        p = jwt_payload(access)
        sid = p["sid"]
        print("4) login OK — claims:", {k: bool(p.get(k)) for k in ("sid", "jti", "iat", "exp")})
        ses = await db.auth_sessions.find_one({"sid": sid})
        print("   auth_sessions existe:", bool(ses), "| expires_at:", ses["expires_at"])
        exp_before = ses["expires_at"]

        # 5. BOOTSTRAP
        r = await c.post(f"{BASE}/api/auth/refresh", json={},
                         headers={"Authorization": f"Bearer {access}"})
        print("5) bootstrap:", r.status_code)
        d = r.json(); r1, a1 = d["refresh_token"], d["access_token"]
        p1 = jwt_payload(a1)
        ses = await db.auth_sessions.find_one({"sid": sid})
        print("   mismo sid:", p1["sid"] == sid, "| gen:", ses.get("refresh_generation"),
              "| expiry sin cambio:", ses["expires_at"] == exp_before,
              "| raw NO en DB:", r1 not in json.dumps(ses, default=str),
              "| hash presente:", bool(ses.get("refresh_token_hash")))

        # 6. ROTATION R1 -> R2
        r = await refresh(c, r1)
        print("6) rotation:", r.status_code)
        d2 = r.json(); r2, a2 = d2["refresh_token"], d2["access_token"]
        ses = await db.auth_sessions.find_one({"sid": sid})
        import hashlib
        h1 = hashlib.sha256(r1.encode()).hexdigest()
        consumed = await db.consumed_refresh_hashes.find_one({"hash": h1})
        print("   mismo sid:", jwt_payload(a2)["sid"] == sid, "| gen:", ses.get("refresh_generation"),
              "| R2 es hash actual:", ses["refresh_token_hash"] == hashlib.sha256(r2.encode()).hexdigest(),
              "| R1 en consumed:", bool(consumed), "| expiry sin cambio:", ses["expires_at"] == exp_before)

        # 7. GRACE RETRY (R1 de nuevo, inmediato)
        r = await refresh(c, r1)
        d3 = r.json()
        ses = await db.auth_sessions.find_one({"sid": sid})
        print("7) grace retry:", r.status_code, "| R2 idéntico (determinista):",
              d3["refresh_token"] == r2, "| gen sin cambio:", ses.get("refresh_generation") == 2)

        # 8. REUSE fuera de grace (una sola petición tras 61s)
        print("8) esperando 61s para reuse fuera de grace...")
        await asyncio.sleep(61)
        r = await refresh(c, r1)
        ses = await db.auth_sessions.find_one({"sid": sid})
        audit = await db.admin_audit_logs.find_one({"action": "refresh_reuse_detected",
                                                    "resource_id": sid})
        r_acc = await c.get(f"{BASE}/api/marketplace/notifications",
                            headers={"Authorization": f"Bearer {a2}"})
        r_r2 = await refresh(c, r2)
        print("   reuse:", r.status_code, "(401 genérico)",
              "| familia revocada:", ses.get("revoked_reason"),
              "| audit:", bool(audit),
              "| access muerto:", r_acc.status_code, "| R2 muerto:", r_r2.status_code)

        # 9. RE-LOGIN
        access_b = await login(c)
        sid_b = jwt_payload(access_b)["sid"]
        r = await c.post(f"{BASE}/api/auth/refresh", json={},
                         headers={"Authorization": f"Bearer {access_b}"})
        rb1 = r.json()["refresh_token"]
        print("9) re-login nuevo sid:", sid_b != sid, "| bootstrap nuevo:", r.status_code)

        # 10. LOGOUT mata refresh
        r = await c.post(f"{BASE}/api/auth/logout",
                         headers={"Authorization": f"Bearer {access_b}"})
        r_acc = await c.get(f"{BASE}/api/marketplace/notifications",
                            headers={"Authorization": f"Bearer {access_b}"})
        r_rf = await refresh(c, rb1)
        print("10) logout:", r.status_code, "| access viejo:", r_acc.status_code,
              "| refresh tras logout:", r_rf.status_code)

        # 11-12. LOGOUT-ALL + REMOTE REVOKE con 2 sesiones
        acc_a = await login(c); acc_b2 = await login(c)
        sid_a, sid_b2 = jwt_payload(acc_a)["sid"], jwt_payload(acc_b2)["sid"]
        ra = (await c.post(f"{BASE}/api/auth/refresh", json={},
                           headers={"Authorization": f"Bearer {acc_a}"})).json()["refresh_token"]
        # remote revoke B desde A
        r = await c.request("DELETE", f"{BASE}/api/auth/sessions/{sid_b2}",
                            headers={"Authorization": f"Bearer {acc_a}"})
        r_b = await c.get(f"{BASE}/api/marketplace/notifications",
                          headers={"Authorization": f"Bearer {acc_b2}"})
        r_boot_b = await c.post(f"{BASE}/api/auth/refresh", json={},
                                headers={"Authorization": f"Bearer {acc_b2}"})
        r_a_ok = await refresh(c, ra)
        print("12) remote revoke:", r.status_code, "| access B:", r_b.status_code,
              "| refresh B:", r_boot_b.status_code, "| A sigue refrescando:", r_a_ok.status_code)
        ra2 = r_a_ok.json().get("refresh_token")
        # logout-all desde A
        r = await c.post(f"{BASE}/api/auth/logout-all",
                         headers={"Authorization": f"Bearer {acc_a}"})
        body = r.text[:120]
        r_rf = await refresh(c, ra2)
        print("11) logout-all:", r.status_code, body, "| refresh A tras logout-all:", r_rf.status_code)

        # 13. LEGACY ISOLATION
        legacy = await db.user_sessions.find_one({}, sort=[("expires_at", -1)])
        r = await c.post(f"{BASE}/api/auth/refresh", json={},
                         headers={"Authorization": f"Bearer {legacy['session_token']}"})
        print("13) legacy user_sessions raw en bootstrap:", r.status_code, "(esperado 401)")

        # limpieza: sesiones tenant test — revocar residuales
        res = await db.auth_sessions.update_many(
            {"user_id": {"$in": [str(p.get("user_id")), p.get("user_id")]}, "revoked_at": None},
            {"$set": {"revoked_at": __import__("datetime").datetime.utcnow(),
                      "revoked_reason": "test_cleanup_fase_a"}})
        print("cleanup: sesiones test revocadas adicionales:", res.modified_count)

asyncio.run(main())
