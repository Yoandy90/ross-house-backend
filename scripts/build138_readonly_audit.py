"""Build 138 / Phase C readiness — auditoría READ-ONLY de producción.
- SOLO find/count. CERO escrituras. CERO tokens/hashes impresos (solo booleanos).
Run: cd /app/ross-house-backend && python scripts/build138_readonly_audit.py
"""
import os
import re
import asyncio
from datetime import datetime, timezone, timedelta

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


BOT_UA = re.compile(r"python|curl|node|axios|httpx|bot|postman|insomnia|go-http", re.I)


def is_human(ses: dict) -> bool:
    ua = (ses.get("user_agent") or "").lower()
    if BOT_UA.search(ua):
        return False
    return True


def fmt_dt(v):
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M UTC")
    return str(v)


async def main():
    load_env()
    client = AsyncIOMotorClient(os.environ["MONGO_URL"], serverSelectionTimeoutMS=15000)
    db = client[os.environ.get("DB_NAME", "taxportal")]
    now = datetime.now(timezone.utc)

    def aware(d):
        if isinstance(d, datetime) and d.tzinfo is None:
            return d.replace(tzinfo=timezone.utc)
        return d

    print("=" * 70)
    print("1) SESIONES iOS RECIENTES (candidatas Build 138) — read-only")
    print("=" * 70)
    since = now - timedelta(days=14)
    cur = db.auth_sessions.find({"created_at": {"$gte": since}}).sort("created_at", -1)
    sessions = [s async for s in cur]
    ios_recent = [s for s in sessions if (s.get("platform") or "").lower() == "ios"]
    for s in ios_recent[:10]:
        exp = aware(s.get("expires_at"))
        r_exp = aware(s.get("refresh_expires_at"))
        print(f"- sid_len={len(s.get('sid',''))} role={s.get('role')} "
              f"device='{s.get('device_name','')}' app_version='{s.get('app_version','')}'")
        print(f"    created={fmt_dt(s.get('created_at'))} last_seen={fmt_dt(s.get('last_seen_at'))}")
        print(f"    revoked={s.get('revoked_at') is not None} "
              f"expires={fmt_dt(exp)} (abs OK={bool(exp and exp > now)})")
        print(f"    refresh_bootstrap={bool(s.get('refresh_token_hash'))} "
              f"hash_only={isinstance(s.get('refresh_token_hash'), str) and len(s.get('refresh_token_hash') or '') == 64} "
              f"family_present={bool(s.get('refresh_family_id'))} "
              f"family==sid={s.get('refresh_family_id') == s.get('sid')} "
              f"refresh_abs_exp={fmt_dt(r_exp)} rotated_count={len(s.get('rotated_hashes') or [])}")
        # Confirmar que NO existe token raw en el doc (solo hashes)
        raw_fields = [k for k in s.keys() if "raw" in k.lower() or k == "refresh_token"]
        print(f"    raw_token_fields_present={raw_fields or 'NONE'} ua='{(s.get('user_agent') or '')[:50]}'")

    print()
    print("=" * 70)
    print("2) MÉTRICAS PHASE C (activas = no revocadas, no expiradas)")
    print("=" * 70)
    active = [s for s in [x async for x in db.auth_sessions.find({})]
              if s.get("revoked_at") is None and (aware(s.get("expires_at")) or now) > now]
    human = [s for s in active if is_human(s)]
    bots = [s for s in active if not is_human(s)]
    human_refresh = [s for s in human if s.get("refresh_token_hash")]
    sid_ok = [s for s in active if isinstance(s.get("sid"), str) and len(s["sid"]) == 32]
    print(f"auth_sessions totales={await db.auth_sessions.count_documents({})} "
          f"activas={len(active)} humanas={len(human)} bots/QA excluidas={len(bots)}")
    print(f"sid presente y válido: {len(sid_ok)}/{len(active)} activas "
          f"({100 * len(sid_ok) / max(len(active), 1):.0f}%)")
    print(f"humanas activas con refresh bootstrap: {len(human_refresh)}/{len(human)} "
          f"({100 * len(human_refresh) / max(len(human), 1):.0f}%)")
    by_platform = {}
    for s in human:
        by_platform.setdefault((s.get("platform") or "?", s.get("role") or "?"), [0, 0])
        by_platform[(s.get("platform") or "?", s.get("role") or "?")][0] += 1
        if s.get("refresh_token_hash"):
            by_platform[(s.get("platform") or "?", s.get("role") or "?")][1] += 1
    for (plat, role), (tot, ref) in sorted(by_platform.items()):
        print(f"  humanas {plat}/{role}: {ref}/{tot} con refresh")

    print()
    print("=" * 70)
    print("3) LEGACY user_sessions (compartida con Ross Tax — NO tocar)")
    print("=" * 70)
    total_legacy = await db.user_sessions.count_documents({})
    live_legacy = await db.user_sessions.count_documents({"expires_at": {"$gt": now}})
    recent_legacy = await db.user_sessions.count_documents(
        {"created_at": {"$gte": now - timedelta(days=7)}})
    print(f"user_sessions total={total_legacy} vivas={live_legacy} creadas últimos 7d={recent_legacy}")
    print("NOTA: colección compartida con Ross Tax backend; 'hits' de fallback RHR no")
    print("      están instrumentados (gap de observabilidad → C1).")

    print()
    print("=" * 70)
    print("4) consumed_refresh_hashes (reuse antiguo) + revocaciones recientes")
    print("=" * 70)
    print(f"consumed_refresh_hashes docs={await db.consumed_refresh_hashes.count_documents({})}")
    rev7 = await db.auth_sessions.count_documents(
        {"revoked_at": {"$gte": now - timedelta(days=7)}})
    reuse = [s async for s in db.auth_sessions.find(
        {"revoked_reason": {"$regex": "reuse", "$options": "i"}})]
    print(f"sesiones revocadas últimos 7d={rev7} · revocadas por reuse (histórico)={len(reuse)}")

    client.close()


asyncio.run(main())
