"""Procesadores de Pago — Stripe / Square / Clover.

Sistema multi-procesador: el admin guarda las credenciales de cada procesador
y activa el que quiera usar en cualquier momento (aplica a la app móvil y a la web).

- Stripe  : procesador actual (sincroniza con rental_config type=company).
- Square  : Hosted Checkout via Payment Links API (connect.squareup[sandbox].com).
- Clover  : Hosted Checkout via invoicingcheckoutservice.

Colección: rental_config { type: "payment_processors" }
{
  active_processor: "stripe" | "square" | "clover",
  processors: {
    stripe: { publishable_key, secret_key, webhook_secret },
    square: { environment, application_id, access_token, location_id,
              webhook_signature_key, webhook_url },
    clover: { environment, merchant_id, private_key, webhook_signing_secret,
              page_config_uuid, webhook_url }
  }
}
Los webhooks Stripe ya existen (/api/stripe/webhook). Aquí se agregan:
  POST /api/webhooks/square   POST /api/webhooks/clover
"""
import base64
import hashlib
import hmac
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone, timedelta

import httpx
from fastapi import APIRouter, HTTPException, Request

from .shared import get_db, auth_admin, auth_tenant_flex

router = APIRouter()
logger = logging.getLogger(__name__)

PROCESSORS = ("stripe", "square", "clover", "bofa", "helcim")
SQUARE_VERSION = "2025-10-16"
SQUARE_BASE = {
    "sandbox": "https://connect.squareupsandbox.com/v2",
    "production": "https://connect.squareup.com/v2",
}
CLOVER_BASE = {
    "sandbox": "https://apisandbox.dev.clover.com",
    "production": "https://api.clover.com",
}
# Bank of America Gateway (plataforma CyberSource white-label)
BOFA_REST_HOST = {
    "sandbox": "apitest.merchant-services.bankofamerica.com",
    "production": "api.merchant-services.bankofamerica.com",
}
BOFA_SA_ENDPOINT = {
    "sandbox": "https://testsecureacceptance.merchant-services.bankofamerica.com/pay",
    "production": "https://secureacceptance.merchant-services.bankofamerica.com/pay",
}
# Helcim (un solo endpoint; el testing se hace con cuenta developer separada)
HELCIM_BASE = "https://api.helcim.com/v2"

# Campos secretos por procesador (se enmascaran en GET y nunca se sobreescriben
# con valores enmascarados enviados de vuelta por el frontend)
SECRET_FIELDS = {
    "stripe": {"secret_key", "webhook_secret"},
    "square": {"access_token", "webhook_signature_key"},
    "clover": {"private_key", "webhook_signing_secret"},
    "bofa": {"p12_base64", "p12_password", "sa_secret_key"},
    "helcim": {"api_token", "webhook_verifier_token"},
}
# Campos de credenciales POR ENTORNO (sandbox / production)
ENV_FIELDS = {
    "stripe": {"publishable_key", "secret_key", "webhook_secret"},
    "square": {"application_id", "access_token", "location_id",
               "webhook_signature_key", "webhook_url"},
    "clover": {"merchant_id", "private_key", "webhook_signing_secret",
               "page_config_uuid", "webhook_url"},
    "bofa": {"merchant_id", "p12_base64", "p12_password",
             "sa_profile_id", "sa_access_key", "sa_secret_key"},
    "helcim": {"api_token", "webhook_verifier_token"},
}
ALLOWED_FIELDS = ENV_FIELDS  # compat
ENVS = ("sandbox", "production")
# Credenciales mínimas para poder ACTIVAR cada procesador (en su entorno activo)
REQUIRED_TO_ACTIVATE = {
    "stripe": ["secret_key", "publishable_key"],
    "square": ["access_token", "location_id"],
    "clover": ["private_key", "merchant_id"],
    "bofa": ["merchant_id", "p12_base64", "p12_password",
             "sa_profile_id", "sa_access_key", "sa_secret_key"],
    "helcim": ["api_token"],
}
# 3D Secure por defecto ACTIVO (obligatorio) — responsabilidad de fraude al banco emisor
DEFAULT_3DS = {"stripe": True, "square": True}


def _mask(value: str) -> str:
    value = value or ""
    if not value:
        return ""
    if len(value) <= 8:
        return "••••"
    return f"{value[:4]}••••{value[-4:]}"


def _public_base_url() -> str:
    return (os.environ.get("PUBLIC_API_URL")
            or "https://ross-house-backend-production.up.railway.app").rstrip("/")


def _bofa_jwt_headers(cfg: dict, env: str, method: str, path: str,
                      body: bytes = b"") -> tuple[dict, str]:
    """Headers JWT (certificado P12, RS256) para el REST API del gateway de BofA.
    El `kid` es el atributo serialNumber del subject DN del certificado."""
    from cryptography.hazmat.primitives.serialization import (
        pkcs12, Encoding, PrivateFormat, NoEncryption)
    from cryptography.x509.oid import NameOID
    import jwt as pyjwt
    p12_bytes = base64.b64decode(cfg.get("p12_base64", ""))
    key, cert, _chain = pkcs12.load_key_and_certificates(
        p12_bytes, (cfg.get("p12_password") or "").encode())
    pem = key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    kid = next(a.value for a in cert.subject if a.oid == NameOID.SERIAL_NUMBER)
    host = BOFA_REST_HOST.get(env, BOFA_REST_HOST["sandbox"])
    mid = cfg.get("merchant_id", "")
    now_ts = int(time.time())
    claims = {"iat": now_ts, "exp": now_ts + 120, "iss": mid, "jti": str(uuid.uuid4()),
              "request-host": host, "request-method": method.upper(),
              "request-resource-path": path, "v-c-jwt-version": "2",
              "v-c-merchant-id": mid}
    if body:
        claims["digest"] = base64.b64encode(hashlib.sha256(body).digest()).decode()
        claims["digestAlgorithm"] = "SHA-256"
    token = pyjwt.encode(claims, pem, algorithm="RS256",
                         headers={"kid": kid, "alg": "RS256", "typ": "JWT"})
    return ({"Authorization": f"Bearer {token}", "Content-Type": "application/json",
             "Accept": "application/json"}, host)


def _sa_sign(fields: dict, secret_key: str) -> str:
    """Firma HMAC-SHA256 de Secure Acceptance (orden exacto de signed_field_names)."""
    names = str(fields.get("signed_field_names", "")).split(",")
    data = ",".join(f"{n}={fields.get(n, '')}" for n in names if n)
    return base64.b64encode(
        hmac.new(secret_key.encode(), data.encode(), hashlib.sha256).digest()).decode()


async def _get_doc() -> dict:
    doc = await get_db().rental_config.find_one({"type": "payment_processors"}) or {}
    doc.setdefault("active_processor", "stripe")
    doc.setdefault("processors", {})
    doc.setdefault("three_ds", dict(DEFAULT_3DS))
    for k, v in DEFAULT_3DS.items():
        doc["three_ds"].setdefault(k, v)
    for p in PROCESSORS:
        cfg = doc["processors"].setdefault(p, {})
        cfg.setdefault("credentials", {"sandbox": {}, "production": {}})
        cfg["credentials"].setdefault("sandbox", {})
        cfg["credentials"].setdefault("production", {})
        # Migración desde el esquema plano anterior → entorno correspondiente
        flat = {f: cfg.pop(f) for f in list(cfg.keys())
                if f in ENV_FIELDS[p] and not isinstance(cfg.get(f), dict)}
        if flat:
            env = cfg.get("environment") or ("production" if p == "stripe" else "sandbox")
            for f, val in flat.items():
                cfg["credentials"][env].setdefault(f, val)
        cfg.setdefault("environment", "production" if p == "stripe" else "sandbox")
    # Stripe production: hereda claves del config legacy (type=company) si faltan
    sp = doc["processors"]["stripe"]["credentials"]["production"]
    if not sp.get("secret_key"):
        company = await get_db().rental_config.find_one({"type": "company"}) or {}
        if company.get("stripe_secret_key"):
            sp["secret_key"] = company["stripe_secret_key"]
        if company.get("stripe_publishable_key"):
            sp.setdefault("publishable_key", company["stripe_publishable_key"])
        if not sp.get("webhook_secret") and os.environ.get("STRIPE_WEBHOOK_SECRET"):
            sp["webhook_secret"] = os.environ["STRIPE_WEBHOOK_SECRET"]
    return doc


def _active_creds(cfg: dict) -> dict:
    """Credenciales del entorno activo del procesador."""
    env = cfg.get("environment", "sandbox")
    return cfg.get("credentials", {}).get(env, {})


async def get_active_processor() -> tuple[str, dict]:
    """Helper para otros routers: (nombre, credenciales del entorno activo + environment)."""
    doc = await _get_doc()
    name = doc.get("active_processor", "stripe")
    cfg = doc["processors"].get(name, {})
    creds = dict(_active_creds(cfg))
    creds["environment"] = cfg.get("environment", "sandbox")
    return name, creds


async def get_three_ds_settings() -> dict:
    doc = await _get_doc()
    return doc.get("three_ds", dict(DEFAULT_3DS))


def _masked_view(doc: dict) -> dict:
    out = {"active_processor": doc.get("active_processor", "stripe"),
           "three_ds": doc.get("three_ds", dict(DEFAULT_3DS)), "processors": {}}
    base = _public_base_url()
    for p in PROCESSORS:
        cfg = doc["processors"].get(p, {})
        env = cfg.get("environment", "sandbox")
        view: dict = {"environment": env, "credentials": {}}
        for e in ENVS:
            creds = cfg.get("credentials", {}).get(e, {})
            cv = {}
            for field in ENV_FIELDS[p]:
                val = creds.get(field, "")
                if field in SECRET_FIELDS[p]:
                    cv[field + "_masked"] = _mask(val)
                    cv["has_" + field] = bool(val)
                else:
                    cv[field] = val
            cv["configured"] = all(bool(creds.get(f)) for f in REQUIRED_TO_ACTIVATE[p])
            view["credentials"][e] = cv
        view["configured"] = view["credentials"][env]["configured"]
        if p == "stripe":
            view["webhook_endpoint"] = f"{base}/api/stripe/webhook"
        else:
            view["webhook_endpoint"] = f"{base}/api/webhooks/{p}"
        out["processors"][p] = view
    return out


# ────────────────────────────── ADMIN CRUD ──────────────────────────────

@router.get("/admin/payment-processors")
async def list_processors(request: Request):
    """Config de los 3 procesadores con secretos enmascarados + cuál está activo."""
    await auth_admin(request)
    doc = await _get_doc()
    return {"success": True, **_masked_view(doc)}


@router.get("/admin/payment-processors/fee-comparison")
async def fee_comparison(request: Request):
    """Compara comisiones estimadas de Stripe/Square/Clover con el volumen REAL de rentas (últimos 12 meses)."""
    await auth_admin(request)

    # Tarifas publicadas estándar para pagos online (card-not-present)
    RATES = {
        "stripe": {"label": "Stripe", "pct": 0.029, "fixed": 0.30, "rate_label": "2.9% + $0.30"},
        "square": {"label": "Square", "pct": 0.029, "fixed": 0.30, "rate_label": "2.9% + $0.30"},
        "clover": {"label": "Clover", "pct": 0.035, "fixed": 0.10, "rate_label": "3.5% + $0.10"},
        "bofa": {"label": "Bank of America", "pct": 0.0299, "fixed": 0.30, "rate_label": "≈2.99% + $0.30"},
        "helcim": {"label": "Helcim", "pct": 0.024, "fixed": 0.25,
                   "rate_label": "tarjeta ≈2.4% + 25¢ · ACH 0.5% + 25¢ (tope $6)"},
    }

    now = datetime.now(timezone.utc)
    start = (now - timedelta(days=365)).strftime('%Y-%m-%d')

    monthly: dict = {}
    tx_count = 0
    volume = 0.0
    async for p in get_db().rental_payments.find({"status": {"$in": ["completed", "paid"]}}):
        amt = float(p.get("total_paid") or p.get("amount") or 0)
        if amt <= 0:
            continue
        d = p.get("payment_date") or p.get("created_at")
        if isinstance(d, datetime):
            dstr = d.strftime('%Y-%m-%d')
        else:
            dstr = str(d or "")[:10]
        if not dstr or dstr < start:
            continue
        month = dstr[:7]
        m = monthly.setdefault(month, {"volume": 0.0, "count": 0})
        m["volume"] += amt
        m["count"] += 1
        volume += amt
        tx_count += 1

    months_sorted = sorted(monthly.keys())
    months_with_data = max(len(months_sorted), 1)
    avg_ticket = volume / tx_count if tx_count else 0

    active, _ = await get_active_processor()

    comparison = []
    for name, r in RATES.items():
        fee_annual = volume * r["pct"] + tx_count * r["fixed"]
        comparison.append({
            "processor": name,
            "label": r["label"],
            "rate_label": r["rate_label"],
            "fee_annual": round(fee_annual, 2),
            "fee_monthly_avg": round(fee_annual / months_with_data, 2),
            "effective_pct": round(fee_annual / volume * 100, 2) if volume else 0,
            "is_active": name == active,
        })
    comparison.sort(key=lambda c: c["fee_annual"])
    cheapest = comparison[0]["processor"] if comparison else None
    active_fee = next((c["fee_annual"] for c in comparison if c["is_active"]), 0)
    savings = round(active_fee - comparison[0]["fee_annual"], 2) if comparison else 0

    return {
        "success": True,
        "volume_12m": round(volume, 2),
        "tx_count_12m": tx_count,
        "monthly_avg_volume": round(volume / months_with_data, 2),
        "avg_ticket": round(avg_ticket, 2),
        "months_with_data": len(months_sorted),
        "monthly": [{"month": m, **{k: round(v, 2) if k == 'volume' else v for k, v in monthly[m].items()}} for m in months_sorted],
        "active_processor": active,
        "comparison": comparison,
        "cheapest": cheapest,
        "savings_annual_vs_active": savings,
        "note": "Tarifas publicadas estándar para pagos online. Tus tarifas negociadas pueden variar.",
    }


@router.put("/admin/payment-processors/{name}")
async def save_processor(name: str, request: Request):
    """Guardar credenciales de un procesador en un entorno (body.environment =
    sandbox|production; default: entorno activo). Ignora valores enmascarados."""
    await auth_admin(request)
    if name not in PROCESSORS:
        raise HTTPException(status_code=404, detail="Procesador desconocido")
    data = await request.json()

    doc = await _get_doc()
    cfg = doc["processors"].get(name, {})
    target_env = str(data.get("environment") or cfg.get("environment", "sandbox"))
    if target_env not in ENVS:
        target_env = "sandbox"
    creds = dict(cfg.get("credentials", {}).get(target_env, {}))

    for field in ENV_FIELDS[name]:
        if field not in data:
            continue
        val = str(data.get(field) or "").strip()
        if field in SECRET_FIELDS[name]:
            # No sobreescribir con el valor enmascarado que devuelve el GET
            if not val or "••••" in val:
                continue
            creds[field] = val
        else:
            creds[field] = val

    await get_db().rental_config.update_one(
        {"type": "payment_processors"},
        {"$set": {f"processors.{name}.credentials.{target_env}": creds,
                  f"processors.{name}.environment": cfg.get("environment", target_env),
                  "updated_at": datetime.now(timezone.utc)}},
        upsert=True,
    )

    # Stripe production: mantener sincronizado el config legacy de los flujos existentes
    if name == "stripe" and target_env == "production":
        sync = {}
        if creds.get("secret_key"):
            sync["stripe_secret_key"] = creds["secret_key"]
        if creds.get("publishable_key"):
            sync["stripe_publishable_key"] = creds["publishable_key"]
        if sync:
            await get_db().rental_config.update_one(
                {"type": "company"}, {"$set": sync}, upsert=True)

    fresh = await _get_doc()
    return {"success": True,
            "message": f"Credenciales de {name.title()} ({target_env}) guardadas",
            **_masked_view(fresh)}


@router.post("/admin/payment-processors/{name}/environment")
async def switch_environment(name: str, request: Request):
    """Cambiar el entorno activo (sandbox ↔ production) de un procesador."""
    await auth_admin(request)
    if name not in PROCESSORS:
        raise HTTPException(status_code=404, detail="Procesador desconocido")
    data = await request.json()
    env = str(data.get("environment", "")).strip()
    if env not in ENVS:
        raise HTTPException(status_code=400, detail="environment debe ser sandbox o production")

    doc = await _get_doc()
    creds = doc["processors"][name].get("credentials", {}).get(env, {})
    missing = [f for f in REQUIRED_TO_ACTIVATE[name] if not creds.get(f)]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Faltan credenciales de {env} para {name.title()}: {', '.join(missing)}")

    await get_db().rental_config.update_one(
        {"type": "payment_processors"},
        {"$set": {f"processors.{name}.environment": env,
                  "updated_at": datetime.now(timezone.utc)}},
        upsert=True)
    # Stripe: sincronizar claves del entorno elegido al config legacy
    if name == "stripe":
        sync = {}
        if creds.get("secret_key"):
            sync["stripe_secret_key"] = creds["secret_key"]
        if creds.get("publishable_key"):
            sync["stripe_publishable_key"] = creds["publishable_key"]
        if sync:
            await get_db().rental_config.update_one(
                {"type": "company"}, {"$set": sync}, upsert=True)

    fresh = await _get_doc()
    return {"success": True,
            "message": f"{name.title()} ahora usa el entorno {env}",
            **_masked_view(fresh)}


@router.put("/admin/payment-processors-3ds")
async def set_three_ds(request: Request):
    """Activar/desactivar 3D Secure obligatorio por procesador (stripe/square).
    Con 3DS activo la verificación es OBLIGATORIA en web y links de pago, y el
    resultado (liability shift) queda registrado como evidencia en cada pago."""
    admin = await auth_admin(request)
    data = await request.json()
    proc = str(data.get("processor", "")).strip()
    if proc not in ("stripe", "square"):
        raise HTTPException(status_code=400, detail="processor debe ser stripe o square")
    enabled = bool(data.get("enabled", True))
    await get_db().rental_config.update_one(
        {"type": "payment_processors"},
        {"$set": {f"three_ds.{proc}": enabled,
                  "three_ds_updated_by": admin.get("email", ""),
                  "updated_at": datetime.now(timezone.utc)}},
        upsert=True)
    if proc == "stripe":
        # El flujo web de pagos de inquilinos lee este flag legacy
        await get_db().rental_config.update_one(
            {"type": "company"}, {"$set": {"stripe_3ds_enabled": enabled}}, upsert=True)
    fresh = await _get_doc()
    return {"success": True,
            "message": f"3D Secure {'ACTIVADO (obligatorio)' if enabled else 'desactivado'} para {proc.title()}",
            **_masked_view(fresh)}


@router.post("/admin/payment-processors/{name}/activate")
async def activate_processor(name: str, request: Request):
    """Activar un procesador como el método de cobro global (app + web)."""
    admin = await auth_admin(request)
    if name not in PROCESSORS:
        raise HTTPException(status_code=404, detail="Procesador desconocido")

    doc = await _get_doc()
    cfg = doc["processors"].get(name, {})
    creds = _active_creds(cfg)
    env = cfg.get("environment", "sandbox")
    missing = [f for f in REQUIRED_TO_ACTIVATE[name] if not creds.get(f)]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Faltan credenciales ({env}) para activar {name.title()}: {', '.join(missing)}")

    await get_db().rental_config.update_one(
        {"type": "payment_processors"},
        {"$set": {"active_processor": name,
                  "activated_at": datetime.now(timezone.utc),
                  "activated_by": admin.get("email", "")}},
        upsert=True,
    )
    # Toggle legacy stripe_enabled para que la app inicialice el SDK solo con Stripe
    await get_db().rental_config.update_one(
        {"type": "company"}, {"$set": {"stripe_enabled": name == "stripe"}}, upsert=True)

    fresh = await _get_doc()
    return {"success": True,
            "message": f"{name.title()} es ahora el procesador de pagos activo",
            **_masked_view(fresh)}


@router.post("/admin/payment-processors/{name}/test")
async def test_processor(name: str, request: Request):
    """Probar conexión con las credenciales guardadas."""
    await auth_admin(request)
    if name not in PROCESSORS:
        raise HTTPException(status_code=404, detail="Procesador desconocido")
    doc = await _get_doc()
    cfg = doc["processors"].get(name, {})
    env = cfg.get("environment", "sandbox")
    cfg = {**_active_creds(cfg), "environment": env}

    try:
        if name == "stripe":
            sk = cfg.get("secret_key", "")
            if not sk:
                return {"success": False, "error": "No hay clave secreta de Stripe configurada"}
            import stripe as stripe_lib
            stripe_lib.api_key = sk
            account = stripe_lib.Account.retrieve()
            return {"success": True,
                    "detail": f"Cuenta {account.id} — cargos: "
                              f"{'✓' if account.charges_enabled else '✗'}"}

        if name == "square":
            token = cfg.get("access_token", "")
            if not token:
                return {"success": False, "error": "No hay Access Token de Square configurado"}
            base = SQUARE_BASE.get(cfg.get("environment", "sandbox"), SQUARE_BASE["sandbox"])
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.get(f"{base}/locations", headers={
                    "Authorization": f"Bearer {token}",
                    "Square-Version": SQUARE_VERSION,
                    "Accept": "application/json",
                })
            if r.status_code != 200:
                return {"success": False,
                        "error": f"Square HTTP {r.status_code}: token inválido o entorno incorrecto"}
            locations = r.json().get("locations", [])
            return {"success": True,
                    "detail": f"{len(locations)} location(s) encontradas",
                    "locations": [{"id": l.get("id"), "name": l.get("name"),
                                   "status": l.get("status")} for l in locations]}

        if name == "clover":
            token = cfg.get("private_key", "")
            mid = cfg.get("merchant_id", "")
            if not token or not mid:
                return {"success": False, "error": "Faltan Private Key o Merchant ID de Clover"}
            base = CLOVER_BASE.get(cfg.get("environment", "sandbox"), CLOVER_BASE["sandbox"])
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.get(f"{base}/v3/merchants/{mid}", headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                })
            if r.status_code != 200:
                return {"success": False,
                        "error": f"Clover HTTP {r.status_code}: token/merchant inválidos o entorno incorrecto"}
            data = r.json()
            return {"success": True,
                    "detail": f"Merchant {data.get('name', mid)} verificado"}

        if name == "bofa":
            if not (cfg.get("p12_base64") and cfg.get("p12_password") and cfg.get("merchant_id")):
                return {"success": False,
                        "error": "Faltan Merchant ID, certificado P12 (Base64) o contraseña del P12"}
            path = "/pts/v2/payments/0000000000000000000000000"
            headers, host = _bofa_jwt_headers(cfg, cfg.get("environment", "sandbox"), "GET", path)
            async with httpx.AsyncClient(timeout=20.0) as client:
                r = await client.get(f"https://{host}{path}", headers=headers)
            if r.status_code == 401:
                return {"success": False,
                        "error": "BofA 401 UNAUTHORIZED: la credencial aún no está activa o el "
                                 "acceso REST API no está habilitado en la cuenta. Verifica el "
                                 "estado de la clave en Key Management o contacta a BofA Merchant Support."}
            if r.status_code in (200, 404):
                sa_ok = all(cfg.get(f) for f in ("sa_profile_id", "sa_access_key", "sa_secret_key"))
                return {"success": True,
                        "detail": "Certificado P12 válido — REST API autenticada correctamente"
                                  + ("" if sa_ok else ". ⚠️ Falta el perfil Secure Acceptance "
                                     "(Profile ID, Access Key, Secret Key) para el checkout hospedado")}
            return {"success": False, "error": f"BofA HTTP {r.status_code}: {r.text[:150]}"}

        if name == "helcim":
            if not cfg.get("api_token"):
                return {"success": False, "error": "Falta el API Token de Helcim"}
            async with httpx.AsyncClient(timeout=20.0) as client:
                r = await client.get(f"{HELCIM_BASE}/connection-test",
                                     headers={"api-token": cfg["api_token"],
                                              "accept": "application/json"})
            if r.status_code == 200:
                return {"success": True, "detail": "Helcim conectado correctamente (tarjeta + ACH)"}
            return {"success": False, "error": f"Helcim HTTP {r.status_code}: {r.text[:150]}"}
    except Exception as e:  # noqa: BLE001
        logger.exception("Processor test failed: %s", name)
        return {"success": False, "error": str(e)[:200]}


# ────────────────────────────── PÚBLICO ──────────────────────────────

@router.get("/public/payment-processor")
async def public_payment_processor():
    """La app móvil / web consultan qué procesador está activo (solo campos públicos)."""
    doc = await _get_doc()
    name = doc.get("active_processor", "stripe")
    pcfg = doc["processors"].get(name, {})
    cfg = _active_creds(pcfg)
    env = pcfg.get("environment", "sandbox")
    three_ds = doc.get("three_ds", dict(DEFAULT_3DS))
    public: dict = {"success": True, "active_processor": name, "environment": env,
                    "three_ds_required": bool(three_ds.get(name if name in three_ds else "stripe", True))}
    if name == "stripe":
        public["publishable_key"] = cfg.get("publishable_key", "")
    elif name == "square":
        public["application_id"] = cfg.get("application_id", "")
        public["location_id"] = cfg.get("location_id", "")
    return public


# ─────────────────────── CHECKOUT HOSPEDADO (helper) ───────────────────────

async def create_hosted_checkout(*, amount_cents: int, reference: str,
                                 customer_email: str = "",
                                 redirect_url: str = "") -> dict:
    """Crea un checkout hospedado con el procesador ACTIVO (square/clover).

    Devuelve {processor, url, external_id}. Para stripe, los flujos existentes
    (Payment Links / PaymentIntents) siguen usándose directamente.
    """
    name, cfg = await get_active_processor()

    if name == "square":
        base = SQUARE_BASE.get(cfg.get("environment", "sandbox"), SQUARE_BASE["sandbox"])
        body = {
            "idempotency_key": str(uuid.uuid4()),
            "quick_pay": {
                "name": reference[:255] or "Pago",
                "price_money": {"amount": amount_cents, "currency": "USD"},
                "location_id": cfg.get("location_id", ""),
            },
        }
        if redirect_url:
            body["checkout_options"] = {"redirect_url": redirect_url}
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.post(f"{base}/online-checkout/payment-links",
                                  headers={
                                      "Authorization": f"Bearer {cfg.get('access_token', '')}",
                                      "Square-Version": SQUARE_VERSION,
                                      "Content-Type": "application/json",
                                  }, json=body)
        if r.status_code >= 400:
            raise HTTPException(status_code=502,
                                detail=f"Square error HTTP {r.status_code}")
        link = r.json().get("payment_link", {})
        return {"processor": "square", "url": link.get("url") or link.get("long_url", ""),
                "external_id": link.get("id", ""), "order_id": link.get("order_id", "")}

    if name == "clover":
        base = CLOVER_BASE.get(cfg.get("environment", "sandbox"), CLOVER_BASE["sandbox"])
        body = {
            "customer": {"email": customer_email or "cliente@rosshouserentals.com"},
            "shoppingCart": {"lineItems": [
                {"name": reference[:200] or "Pago", "price": amount_cents, "unitQty": 1}
            ]},
        }
        if cfg.get("page_config_uuid"):
            body["pageConfigUuid"] = cfg["page_config_uuid"]
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.post(f"{base}/invoicingcheckoutservice/v1/checkouts",
                                  headers={
                                      "Authorization": f"Bearer {cfg.get('private_key', '')}",
                                      "X-Clover-Merchant-Id": cfg.get("merchant_id", ""),
                                      "Content-Type": "application/json",
                                  }, json=body)
        if r.status_code >= 400:
            raise HTTPException(status_code=502,
                                detail=f"Clover error HTTP {r.status_code}")
        data = r.json()
        return {"processor": "clover", "url": data.get("href", ""),
                "external_id": data.get("checkoutSessionId", "")}

    if name == "bofa":
        env = cfg.get("environment", "sandbox")
        if not all(cfg.get(f) for f in ("sa_profile_id", "sa_access_key", "sa_secret_key")):
            raise HTTPException(status_code=400,
                                detail="Bank of America: faltan credenciales de Secure Acceptance")
        txn_uuid = uuid.uuid4().hex
        ref_num = f"RHR{int(time.time())}{txn_uuid[:6].upper()}"
        fields = {
            "access_key": cfg["sa_access_key"],
            "profile_id": cfg["sa_profile_id"],
            "transaction_uuid": txn_uuid,
            "signed_date_time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "locale": "en-us",
            "transaction_type": "sale",
            "reference_number": ref_num,
            "amount": f"{amount_cents / 100:.2f}",
            "currency": "USD",
            "merchant_defined_data1": (reference or "Pago")[:100],
        }
        if customer_email:
            fields["bill_to_email"] = customer_email
        if redirect_url:
            fields["override_custom_receipt_page"] = redirect_url
            fields["override_custom_cancel_page"] = redirect_url
        signed_names = list(fields.keys()) + ["signed_field_names", "unsigned_field_names"]
        fields["unsigned_field_names"] = ""
        fields["signed_field_names"] = ",".join(signed_names)
        fields["signature"] = _sa_sign(fields, cfg["sa_secret_key"])
        sid = uuid.uuid4().hex
        await get_db().bofa_checkout_sessions.insert_one({
            "_id": sid,
            "action": BOFA_SA_ENDPOINT.get(env, BOFA_SA_ENDPOINT["sandbox"]),
            "fields": fields, "reference": reference,
            "created_at": datetime.now(timezone.utc)})
        return {"processor": "bofa",
                "url": f"{_public_base_url()}/api/public/bofa-checkout/{sid}",
                "external_id": txn_uuid, "order_id": ref_num}

    if name == "helcim":
        if not cfg.get("api_token"):
            raise HTTPException(status_code=400, detail="Helcim: falta el API Token")
        init_body = {
            "paymentType": "purchase",
            "amount": round(amount_cents / 100, 2),
            "currency": "USD",
            "paymentMethod": "cc-ach",
            "confirmationScreen": True,
        }
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.post(f"{HELCIM_BASE}/helcim-pay/initialize",
                                  headers={"api-token": cfg["api_token"],
                                           "accept": "application/json"},
                                  json=init_body)
        if r.status_code >= 400:
            raise HTTPException(status_code=502, detail=f"Helcim: {r.text[:200]}")
        data = r.json()
        checkout_token = data.get("checkoutToken")
        secret_token = data.get("secretToken")
        if not checkout_token or not secret_token:
            raise HTTPException(status_code=502, detail="Helcim no devolvió tokens de checkout")
        sid = uuid.uuid4().hex
        await get_db().helcim_checkout_sessions.insert_one({
            "_id": sid, "checkout_token": checkout_token, "secret_token": secret_token,
            "amount_cents": amount_cents, "reference": reference,
            "redirect_url": redirect_url or "", "status": "pending",
            "created_at": datetime.now(timezone.utc)})
        return {"processor": "helcim",
                "url": f"{_public_base_url()}/api/public/helcim-checkout/{sid}",
                "external_id": checkout_token, "order_id": sid}

    raise HTTPException(status_code=400,
                        detail="El procesador activo es Stripe — usa el flujo Stripe existente")


# ─────────────── PAGO DE RENTA MULTI-PROCESADOR (tenants) ───────────────

def _receipt_prefix(processor: str) -> str:
    return {"square": "SQR", "clover": "CLV", "stripe": "STR", "bofa": "BOA",
            "helcim": "HLC"}.get(processor, "PAY")


async def _mark_checkout_completed(payment: dict) -> dict:
    """Marca un rental_payment de checkout como completado y genera recibo."""
    now = datetime.now(timezone.utc)
    prefix = _receipt_prefix(payment.get("checkout_processor", ""))
    receipt_number = f"{prefix}-{now.strftime('%Y%m%d')}-{str(payment.get('tenant_id', ''))[-4:]}"
    await get_db().rental_payments.update_one(
        {"_id": payment["_id"], "status": {"$ne": "completed"}},
        {"$set": {
            "status": "completed",
            "receipt_number": receipt_number,
            "payment_date": now.isoformat(),
            "updated_at": now,
        }})
    logger.info("✅ Checkout %s completado: %s — $%s", payment.get("checkout_processor"),
                receipt_number, payment.get("total_paid"))
    return {"receipt_number": receipt_number}


@router.post("/tenant/create-checkout-payment")
async def tenant_create_checkout_payment(request: Request):
    """Inquilino: inicia el pago de renta con el procesador ACTIVO.

    - stripe → {"processor": "stripe"}: el cliente sigue el flujo Stripe existente.
    - square/clover → crea Hosted Checkout y devuelve {"processor", "url", "payment_id"}.
    """
    tenant = await auth_tenant_flex(request)
    data = await request.json()
    hosted = bool(data.get("hosted"))  # web: checkout hospedado también para Stripe

    name, _ = await get_active_processor()
    if name == "stripe" and not hosted:
        return {"success": True, "processor": "stripe"}

    from bson import ObjectId  # noqa: F401 (parity with stripe flow)
    contract = await get_db().rental_contracts.find_one({
        "tenant_id": tenant["_id"], "status": "active"})
    if not contract:
        raise HTTPException(status_code=404, detail="No se encontró contrato activo")

    now = datetime.utcnow()
    current_month = now.strftime('%B').lower()
    existing = await get_db().rental_payments.find_one({
        "contract_id": str(contract["_id"]),
        "period_month": {"$regex": f"^{current_month[:3]}", "$options": "i"},
        "period_year": now.year,
        "status": {"$in": ["completed", "paid", "pending_verification"]},
    })
    if existing:
        raise HTTPException(status_code=400, detail="Ya existe un pago registrado para este mes")

    # Monto SIEMPRE del contrato (server-side) — igual que el flujo Stripe
    contract_rent = float(contract.get("rent_amount") or 0)
    late_fee = float(data.get("late_fee") or 0)
    amount = contract_rent if contract_rent > 0 else float(data.get("rent_amount") or data.get("amount") or 0)
    total = amount + late_fee
    if total <= 0:
        raise HTTPException(status_code=400, detail="Monto inválido")

    reference = f"Renta {current_month.title()} {now.year} - {tenant.get('name', '')}"
    if name == "stripe":
        # Stripe Checkout Session (hosted) — para pagos con tarjeta desde la web
        company = await get_db().rental_config.find_one({"type": "company"}) or {}
        sk = company.get("stripe_secret_key", "")
        if not sk:
            raise HTTPException(status_code=400, detail="Stripe no está configurado")
        import stripe as stripe_lib
        stripe_lib.api_key = sk
        session = stripe_lib.checkout.Session.create(
            mode="payment",
            line_items=[{"price_data": {"currency": "usd",
                                        "product_data": {"name": reference},
                                        "unit_amount": int(round(total * 100))},
                         "quantity": 1}],
            customer_email=tenant.get("email") or None,
            success_url="https://www.rosshouserentals.com/pago-exitoso",
            cancel_url="https://www.rosshouserentals.com/tenant/dashboard",
            metadata={"tenant_id": str(tenant["_id"]), "contract_id": str(contract["_id"])},
        )
        checkout = {"processor": "stripe", "url": session.url, "external_id": session.id, "order_id": ""}
    else:
        checkout = await create_hosted_checkout(
            amount_cents=int(round(total * 100)),
            reference=reference,
            customer_email=tenant.get("email", ""),
            redirect_url="https://www.rosshouserentals.com/pago-exitoso",
        )

    payment_doc = {
        "contract_id": str(contract["_id"]),
        "property_id": str(contract.get("property_id", "")),
        "tenant_id": str(tenant["_id"]),
        "tenant_name": tenant.get("name", ""),
        "amount": amount,
        "late_fee": late_fee,
        "total_paid": total,
        "payment_method": name,
        "checkout_processor": name,
        "checkout_external_id": checkout.get("external_id", ""),
        "checkout_order_id": checkout.get("order_id", ""),
        "reference_number": checkout.get("external_id", ""),
        "period_month": current_month,
        "period_year": now.year,
        "status": "pending_checkout",
        "submitted_by": f"tenant_{name}",
        "submitted_at": now,
        "created_at": now,
        "updated_at": now,
    }
    r = await get_db().rental_payments.insert_one(payment_doc)

    return {"success": True, "processor": name, "url": checkout.get("url", ""),
            "payment_id": str(r.inserted_id), "amount": total}


@router.get("/tenant/checkout-payment-status/{payment_id}")
async def tenant_checkout_payment_status(payment_id: str, request: Request):
    """Inquilino: consulta si el Hosted Checkout ya fue pagado (verifica con el procesador)."""
    tenant = await auth_tenant_flex(request)
    from bson import ObjectId
    try:
        payment = await get_db().rental_payments.find_one({"_id": ObjectId(payment_id)})
    except Exception:
        payment = None
    if not payment or payment.get("tenant_id") != str(tenant["_id"]):
        raise HTTPException(status_code=404, detail="Pago no encontrado")

    if payment.get("status") == "completed":
        return {"success": True, "completed": True,
                "receipt_number": payment.get("receipt_number", ""),
                "amount": payment.get("total_paid", 0)}

    processor = payment.get("checkout_processor", "")
    doc = await _get_doc()
    cfg = _active_creds(doc["processors"].get(processor, {}))
    env = doc["processors"].get(processor, {}).get("environment", "sandbox")
    paid = False

    try:
        if processor == "stripe" and payment.get("checkout_external_id"):
            company = await get_db().rental_config.find_one({"type": "company"}) or {}
            sk = company.get("stripe_secret_key", "")
            if sk:
                import stripe as stripe_lib
                stripe_lib.api_key = sk
                session = stripe_lib.checkout.Session.retrieve(payment["checkout_external_id"])
                paid = session.get("payment_status") == "paid"
        elif processor == "square" and payment.get("checkout_order_id"):
            base = SQUARE_BASE.get(env, SQUARE_BASE["sandbox"])
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.get(f"{base}/orders/{payment['checkout_order_id']}",
                                     headers={"Authorization": f"Bearer {cfg.get('access_token', '')}",
                                              "Square-Version": SQUARE_VERSION})
            if r.status_code < 400:
                order = r.json().get("order", {})
                tenders = order.get("tenders", [])
                paid = order.get("state") == "COMPLETED" or any(
                    t.get("card_details", {}).get("status") == "CAPTURED" or t.get("type")
                    for t in tenders)
        elif processor == "clover" and payment.get("checkout_external_id"):
            base = CLOVER_BASE.get(env, CLOVER_BASE["sandbox"])
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.get(
                    f"{base}/invoicingcheckoutservice/v1/checkouts/{payment['checkout_external_id']}",
                    headers={"Authorization": f"Bearer {cfg.get('private_key', '')}",
                             "X-Clover-Merchant-Id": cfg.get("merchant_id", "")})
            if r.status_code < 400:
                status = (r.json().get("status") or "").upper()
                paid = status in ("PAID", "COMPLETE", "COMPLETED", "APPROVED")
        elif processor == "bofa" and payment.get("checkout_order_id"):
            # Buscar la transacción por reference_number en el TSS del gateway
            search_body = json.dumps({
                "query": f"clientReferenceInformation.code:{payment['checkout_order_id']}",
                "limit": 5, "sort": "id:desc", "timezone": "America/Chicago",
            }, separators=(",", ":")).encode()
            headers, host = _bofa_jwt_headers(cfg, env, "POST", "/tss/v2/searches", search_body)
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.post(f"https://{host}/tss/v2/searches",
                                      content=search_body, headers=headers)
            if r.status_code < 400:
                summaries = ((r.json().get("_embedded") or {}).get("transactionSummaries") or [])
                paid = any(
                    str((s.get("applicationInformation") or {}).get("reasonCode", "")) == "100"
                    for s in summaries)
        elif processor == "helcim" and payment.get("checkout_order_id"):
            ses = await get_db().helcim_checkout_sessions.find_one(
                {"_id": payment["checkout_order_id"]})
            paid = bool(ses and ses.get("status") == "paid")
    except Exception as e:
        logger.warning("Checkout status check failed (%s): %s", processor, e)

    # Respaldo: webhook ya registró el evento de pago
    if not paid:
        ext_ids = [v for v in (payment.get("checkout_order_id"), payment.get("checkout_external_id")) if v]
        if ext_ids:
            evt = await get_db().processor_webhook_events.find_one({
                "processor": processor,
                "$or": [{"matched_payment_id": str(payment["_id"])},
                        {"payload_ids": {"$in": ext_ids}}]})
            if evt:
                paid = True

    if paid:
        info = await _mark_checkout_completed(payment)
        return {"success": True, "completed": True,
                "receipt_number": info["receipt_number"],
                "amount": payment.get("total_paid", 0)}
    return {"success": True, "completed": False}


async def _try_complete_from_webhook(processor: str, candidate_ids: list, event_doc_id) -> None:
    """Al recibir un webhook, intenta completar el rental_payment que coincida."""
    ids = [i for i in candidate_ids if i]
    if not ids:
        return
    payment = await get_db().rental_payments.find_one({
        "checkout_processor": processor,
        "status": "pending_checkout",
        "$or": [{"checkout_order_id": {"$in": ids}}, {"checkout_external_id": {"$in": ids}}],
    })
    if payment:
        await _mark_checkout_completed(payment)
        await get_db().processor_webhook_events.update_one(
            {"_id": event_doc_id}, {"$set": {"matched_payment_id": str(payment["_id"])}})


# ────────────────────────────── WEBHOOKS ──────────────────────────────

@router.post("/webhooks/square")
async def square_webhook(request: Request):
    """Webhook de Square — verifica firma HMAC-SHA256 (URL + body) y registra el evento."""
    doc = await _get_doc()
    cfg = _active_creds(doc["processors"].get("square", {}))
    raw = await request.body()
    signature = request.headers.get("x-square-hmacsha256-signature", "")
    sig_key = cfg.get("webhook_signature_key", "")
    notif_url = cfg.get("webhook_url") or f"{_public_base_url()}/api/webhooks/square"

    if sig_key:
        expected = base64.b64encode(hmac.new(
            sig_key.encode(), notif_url.encode() + raw, hashlib.sha256).digest()).decode()
        if not signature or not hmac.compare_digest(expected, signature):
            raise HTTPException(status_code=403, detail="Firma de Square inválida")

    try:
        event = json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="JSON inválido")

    event_id = event.get("event_id") or hashlib.sha256(raw).hexdigest()
    db = get_db()
    existing = await db.processor_webhook_events.find_one({"event_id": event_id})
    if existing:
        return {"ok": True, "duplicate": True}
    await db.processor_webhook_events.insert_one({
        "processor": "square",
        "event_id": event_id,
        "type": event.get("type", ""),
        "payload": event,
        "payload_ids": [v for v in [
            (event.get("data", {}).get("object", {}).get("payment", {}) or {}).get("order_id"),
            (event.get("data", {}).get("object", {}).get("payment", {}) or {}).get("id"),
            event.get("data", {}).get("id"),
        ] if v],
        "verified": bool(sig_key),
        "received_at": datetime.now(timezone.utc),
    })
    logger.info("Square webhook: %s", event.get("type", "?"))
    # Completar pago de renta si el evento es de un pago exitoso
    pay_obj = (event.get("data", {}).get("object", {}) or {}).get("payment", {}) or {}
    if pay_obj.get("status") in ("COMPLETED", "APPROVED"):
        inserted = await db.processor_webhook_events.find_one({"event_id": event_id})
        await _try_complete_from_webhook("square", [pay_obj.get("order_id"), pay_obj.get("id")],
                                         inserted["_id"] if inserted else None)
    return {"ok": True}


@router.post("/webhooks/clover")
async def clover_webhook(request: Request):
    """Webhook de Clover Hosted Checkout — verifica Clover-Signature (t=..,v1=HMAC)."""
    doc = await _get_doc()
    cfg = _active_creds(doc["processors"].get("clover", {}))
    raw = await request.body()
    secret = cfg.get("webhook_signing_secret", "")
    header = request.headers.get("Clover-Signature", "")

    # Verificación de endpoint de Clover (envía verificationCode al registrar)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = {}
    if isinstance(payload, dict) and payload.get("verificationCode"):
        logger.info("Clover verificationCode recibido: %s", payload["verificationCode"])
        await get_db().processor_webhook_events.insert_one({
            "processor": "clover", "type": "verification",
            "verification_code": payload["verificationCode"],
            "received_at": datetime.now(timezone.utc)})
        return {"received": True, "verificationCode": payload["verificationCode"]}

    if secret:
        ok = False
        try:
            parts = dict(p.split("=", 1) for p in header.split(","))
            ts, supplied = parts["t"], parts["v1"]
            if abs(time.time() - int(ts)) <= 300:
                expected = hmac.new(secret.encode(), ts.encode() + b"." + raw,
                                    hashlib.sha256).hexdigest()
                ok = hmac.compare_digest(expected, supplied)
        except (KeyError, ValueError):
            ok = False
        if not ok:
            raise HTTPException(status_code=401, detail="Firma de Clover inválida")

    event_id = (payload.get("id") or payload.get("paymentId")
                or hashlib.sha256(raw).hexdigest())
    db = get_db()
    existing = await db.processor_webhook_events.find_one({"event_id": event_id})
    if existing:
        return {"received": True, "duplicate": True}
    await db.processor_webhook_events.insert_one({
        "processor": "clover",
        "event_id": event_id,
        "type": payload.get("status", "") or "event",
        "payload": payload,
        "payload_ids": [v for v in [payload.get("checkoutSessionId"), payload.get("id"),
                                    payload.get("paymentId")] if v],
        "verified": bool(secret),
        "received_at": datetime.now(timezone.utc),
    })
    logger.info("Clover webhook: %s", payload.get("status", "?"))
    if (payload.get("status") or "").upper() in ("PAID", "APPROVED", "COMPLETE", "COMPLETED", "SUCCESS"):
        inserted = await db.processor_webhook_events.find_one({"event_id": event_id})
        await _try_complete_from_webhook(
            "clover",
            [payload.get("checkoutSessionId"), payload.get("id"), payload.get("paymentId")],
            inserted["_id"] if inserted else None)
    return {"received": True}


# ─────────────── BANK OF AMERICA (Secure Acceptance) ───────────────

@router.get("/public/bofa-checkout/{session_id}")
async def bofa_checkout_page(session_id: str):
    """Página puente: renderiza el formulario firmado de Secure Acceptance y lo
    auto-envía al checkout hospedado de Bank of America."""
    import html as html_lib
    from fastapi.responses import HTMLResponse
    ses = await get_db().bofa_checkout_sessions.find_one({"_id": session_id})
    if not ses:
        raise HTTPException(status_code=404, detail="Sesión de pago no encontrada")
    inputs = "\n".join(
        f'<input type="hidden" name="{html_lib.escape(str(k))}" value="{html_lib.escape(str(v))}">'
        for k, v in ses["fields"].items())
    page = f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Redirigiendo a Bank of America…</title>
<style>body{{font-family:-apple-system,Segoe UI,Roboto,sans-serif;display:flex;align-items:center;
justify-content:center;height:100vh;margin:0;background:#f6f7f9;color:#1f2937}}
.box{{text-align:center}}.spin{{width:42px;height:42px;border:4px solid #e5e7eb;border-top-color:#dc2626;
border-radius:50%;margin:0 auto 16px;animation:s 1s linear infinite}}@keyframes s{{to{{transform:rotate(360deg)}}}}</style>
</head><body><div class="box"><div class="spin"></div>
<p>Conectando con el pago seguro de <b>Bank of America</b>…</p></div>
<form id="bofa" method="post" action="{html_lib.escape(ses['action'])}">
{inputs}
</form>
<script>document.getElementById('bofa').submit();</script>
</body></html>"""
    return HTMLResponse(page)


@router.post("/webhooks/bofa")
async def bofa_webhook(request: Request):
    """Notificación POST de Secure Acceptance (BofA) — verifica firma HMAC-SHA256."""
    form = {k: str(v) for k, v in (await request.form()).items()}
    doc = await _get_doc()
    cfg = _active_creds(doc["processors"].get("bofa", {}))
    secret = cfg.get("sa_secret_key", "")
    verified = False
    if secret and form.get("signed_field_names") and form.get("signature"):
        expected = _sa_sign(form, secret)
        verified = hmac.compare_digest(expected, form.get("signature", ""))
        if not verified:
            raise HTTPException(status_code=403, detail="Firma de Bank of America inválida")

    event_id = (form.get("transaction_id") or form.get("req_transaction_uuid")
                or hashlib.sha256(json.dumps(form, sort_keys=True).encode()).hexdigest())
    db = get_db()
    if await db.processor_webhook_events.find_one({"event_id": event_id, "processor": "bofa"}):
        return {"ok": True, "duplicate": True}
    payload_ids = [v for v in [form.get("req_transaction_uuid"),
                               form.get("req_reference_number"),
                               form.get("transaction_id")] if v]
    await db.processor_webhook_events.insert_one({
        "processor": "bofa",
        "event_id": event_id,
        "type": (form.get("decision") or "notification").lower(),
        "payload": form,
        "payload_ids": payload_ids,
        "verified": verified,
        "received_at": datetime.now(timezone.utc),
    })
    logger.info("BofA SA notificación: decision=%s ref=%s",
                form.get("decision"), form.get("req_reference_number"))
    if (form.get("decision") or "").upper() == "ACCEPT":
        inserted = await db.processor_webhook_events.find_one(
            {"event_id": event_id, "processor": "bofa"})
        await _try_complete_from_webhook("bofa", payload_ids,
                                         inserted["_id"] if inserted else None)
    return {"ok": True}


# ─────────────────── HELCIM (HelcimPay.js Hosted) ───────────────────

_HELCIM_BRIDGE_TEMPLATE = """<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pago seguro</title>
<script src="https://secure.helcim.app/helcim-pay/services/start.js"></script>
<style>body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;display:flex;align-items:center;
justify-content:center;height:100vh;margin:0;background:#f6f7f9;color:#1f2937}
.box{text-align:center;padding:20px}.spin{width:42px;height:42px;border:4px solid #e5e7eb;
border-top-color:#0ea5e9;border-radius:50%;margin:0 auto 16px;animation:s 1s linear infinite}
@keyframes s{to{transform:rotate(360deg)}}</style></head>
<body><div class="box"><div class="spin" id="spin"></div><p id="state">Abriendo pago seguro…</p></div>
<script>
var checkoutToken = __TOKEN__;
var redirectUrl = __REDIRECT__;
var state = document.getElementById('state');
window.addEventListener('message', function(ev) {
  if (!ev.data || ev.data.eventName !== 'helcim-pay-js-' + checkoutToken) return;
  if (ev.data.eventStatus === 'SUCCESS') {
    state.textContent = 'Confirmando pago…';
    var raw = typeof ev.data.eventMessage === 'string'
      ? ev.data.eventMessage : JSON.stringify(ev.data.eventMessage);
    fetch('/api/public/helcim-complete', {
      method: 'POST', headers: {'content-type': 'application/json'},
      body: JSON.stringify({checkout_token: checkoutToken, raw_data_response: raw})
    }).then(function(r){return r.json();}).then(function(res){
      state.textContent = res.status === 'paid'
        ? '✅ ¡Pago exitoso! Puedes volver a la app.'
        : (res.status === 'ach_pending'
           ? '🏦 Pago bancario iniciado — se confirmará en 1-3 días hábiles.'
           : '⚠️ No se pudo confirmar el pago.');
      document.getElementById('spin').style.display = 'none';
      if (redirectUrl) setTimeout(function(){ window.location.replace(redirectUrl); }, 2500);
    }).catch(function(){ state.textContent = '⚠️ Error confirmando el pago.'; });
  } else if (ev.data.eventStatus === 'ABORTED') {
    state.textContent = 'Pago cancelado o rechazado. Puedes volver a la app e intentar de nuevo.';
    document.getElementById('spin').style.display = 'none';
  }
});
appendHelcimPayIframe(checkoutToken, true);
</script></body></html>"""


@router.get("/public/helcim-checkout/{session_id}")
async def helcim_checkout_page(session_id: str):
    """Página puente que renderiza el modal HelcimPay (tarjeta + ACH)."""
    from fastapi.responses import HTMLResponse
    ses = await get_db().helcim_checkout_sessions.find_one({"_id": session_id})
    if not ses:
        raise HTTPException(status_code=404, detail="Sesión de pago no encontrada")
    page = (_HELCIM_BRIDGE_TEMPLATE
            .replace("__TOKEN__", json.dumps(ses["checkout_token"]))
            .replace("__REDIRECT__", json.dumps(ses.get("redirect_url") or "")))
    return HTMLResponse(page)


@router.post("/public/helcim-complete")
async def helcim_complete(request: Request):
    """Valida la respuesta de HelcimPay con el hash (secretToken) y completa el pago."""
    body = await request.json()
    checkout_token = body.get("checkout_token", "")
    ses = await get_db().helcim_checkout_sessions.find_one({"checkout_token": checkout_token})
    if not ses:
        raise HTTPException(status_code=400, detail="Checkout desconocido")
    if ses.get("status") == "paid":
        return {"status": "paid", "transaction_id": ses.get("transaction_id")}

    raw = body.get("raw_data_response") or ""
    try:
        outer = json.loads(raw) if isinstance(raw, str) else raw
        envelope = outer.get("data", outer)
        tx = envelope["data"]
        supplied_hash = envelope["hash"]
    except (TypeError, ValueError, KeyError):
        raise HTTPException(status_code=400, detail="Respuesta de Helcim malformada")

    canonical = json.dumps(tx, separators=(",", ":"), ensure_ascii=True)
    expected = hashlib.sha256((canonical + ses["secret_token"]).encode()).hexdigest()
    if not hmac.compare_digest(expected, supplied_hash):
        raise HTTPException(status_code=400, detail="Hash de transacción inválido")

    # Verificar monto y moneda contra la sesión (no confiar en el navegador)
    try:
        returned_cents = int(round(float(tx.get("amount", 0)) * 100))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Monto inválido")
    if returned_cents != ses["amount_cents"] or (tx.get("currency") or "USD") != "USD":
        raise HTTPException(status_code=400, detail="Monto o moneda no coinciden")

    status = str(tx.get("status", "")).upper()
    if status in ("APPROVED", "APPROVAL"):
        new_status = "paid"
    elif tx.get("statusAuth") == "PENDING" or tx.get("statusClearing") == "OPENED":
        new_status = "ach_pending"  # ACH: no marcar pagado hasta compensación
    else:
        new_status = "failed"

    txn_id = str(tx.get("transactionId") or tx.get("cardTransactionId") or "")
    await get_db().helcim_checkout_sessions.update_one(
        {"_id": ses["_id"], "status": {"$ne": "paid"}},
        {"$set": {"status": new_status, "transaction_id": txn_id,
                  "helcim_transaction": tx, "updated_at": datetime.now(timezone.utc)}})
    logger.info("Helcim checkout %s → %s (tx %s)", ses["_id"], new_status, txn_id)
    if new_status == "paid":
        await _try_complete_from_webhook(
            "helcim", [checkout_token, ses["_id"], txn_id], None)
    return {"status": new_status, "transaction_id": txn_id}
