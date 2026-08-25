"""PREPARADO — NO EJECUTAR sin orden explícita del usuario.

Remote Revoke Test (sección 3 del checkpoint Build 138):
Revoca UNA sesión específica (Device A = iPhone Build 138) para validar que la app
detecta session_revoked, limpia tokens locales y vuelve a login sin loop.

Uso (solo cuando el usuario lo autorice):
  cd /app/ross-house-backend
  # 1) listar candidatas (read-only):
  python scripts/revoke_session_test.py --list
  # 2) revocar por sid exacto:
  python scripts/revoke_session_test.py --revoke <sid>

Escritura mínima: $set revoked_at + revoked_reason="remote_revoke_test" en 1 doc.
NUNCA borra nada. NUNCA toca user_sessions (legacy compartida con Ross Tax).
"""
import os
import sys
import asyncio
from datetime import datetime, timezone

from motor.motor_asyncio import AsyncIOMotorClient


def load_env():
    here = os.path.dirname(__file__)
    with open(os.path.join(here, "..", ".env")) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


async def main():
    load_env()
    client = AsyncIOMotorClient(os.environ["MONGO_URL"], serverSelectionTimeoutMS=15000)
    db = client[os.environ.get("DB_NAME", "taxportal")]

    if "--list" in sys.argv:
        cur = db.auth_sessions.find({"revoked_at": None}).sort("created_at", -1).limit(15)
        async for s in cur:
            print(f"sid={s['sid']} role={s.get('role')} platform={s.get('platform')} "
                  f"ua='{(s.get('user_agent') or '')[:45]}' created={s.get('created_at')} "
                  f"last_seen={s.get('last_seen_at')}")
        client.close()
        return

    if "--revoke" in sys.argv:
        sid = sys.argv[sys.argv.index("--revoke") + 1]
        confirm = input(f"⚠️  Revocar sesión {sid[:8]}…? Escribe REVOCAR para confirmar: ")
        if confirm.strip() != "REVOCAR":
            print("Cancelado.")
            client.close()
            return
        r = await db.auth_sessions.update_one(
            {"sid": sid, "revoked_at": None},
            {"$set": {"revoked_at": datetime.now(timezone.utc),
                      "revoked_reason": "remote_revoke_test"}})
        print(f"modified={r.modified_count} (1 = revocada; 0 = no encontrada/ya revocada)")
        client.close()
        return

    print(__doc__)
    client.close()


asyncio.run(main())
