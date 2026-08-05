"""
Tenant Screening Router (provider-agnostic)
============================================
Credit / background screening workflow attached to rental applications.

Works today WITHOUT a screening API:
  - Admin requests a screening for an application (SmartMove / BoomScreen / other)
  - Applicant receives a branded email with the screening link / instructions
  - Admin tracks status: requested -> in_progress -> completed (or cancelled)
  - Admin records results (credit score, income, criminal/eviction records,
    recommendation) and uploads the report PDF.

When the user registers for the SmartMove or Boom API, the request step can be
swapped for a real API call without changing the data model.
"""
import base64
import logging
from datetime import datetime

from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request, Response

from rental.shared import get_db, auth_admin

logger = logging.getLogger(__name__)
router = APIRouter()

SCREENING_STATUSES = {"requested", "in_progress", "completed", "cancelled"}
PROVIDERS = {"smartmove", "boomscreen", "other"}
PROVIDER_LABELS = {
    "smartmove": "TransUnion SmartMove",
    "boomscreen": "BoomScreen",
    "other": "Proveedor de screening",
}
RECOMMENDATIONS = {"", "approve", "conditional", "reject"}
MAX_REPORT_BYTES = 10 * 1024 * 1024  # 10 MB

DEFAULT_RESULTS = {
    "credit_score": None,
    "income_verified": None,       # True / False / None
    "criminal_records": "",        # '', 'clean', 'found'
    "eviction_records": "",        # '', 'clean', 'found'
    "recommendation": "",          # '', 'approve', 'conditional', 'reject'
    "notes": "",
}


def serialize_screening(s: dict | None) -> dict | None:
    if not s:
        return None
    # Legacy/manual schema: screening waived by landlord decision
    if s.get("type") == "waived" and "status" not in s:
        return {
            "status": "waived",
            "provider": "",
            "screening_link": "",
            "requested_at": "",
            "requested_by": "",
            "completed_at": "",
            "email_sent": False,
            "reason": s.get("reason", ""),
            "results": dict(DEFAULT_RESULTS),
            "report": None,
        }
    report = s.get("report") or None
    return {
        "status": s.get("status", "requested"),
        "provider": s.get("provider", "smartmove"),
        "screening_link": s.get("screening_link", ""),
        "requested_at": s.get("requested_at").isoformat() if s.get("requested_at") else "",
        "requested_by": s.get("requested_by", ""),
        "completed_at": s.get("completed_at").isoformat() if s.get("completed_at") else "",
        "email_sent": bool(s.get("email_sent")),
        "results": {**DEFAULT_RESULTS, **(s.get("results") or {})},
        "report": {
            "filename": report.get("filename", ""),
            "size": report.get("size", 0),
            "uploaded_at": report.get("uploaded_at").isoformat() if report.get("uploaded_at") else "",
        } if report else None,
    }


async def _get_application(app_id: str) -> dict:
    if not ObjectId.is_valid(app_id):
        raise HTTPException(status_code=400, detail="ID inválido")
    a = await get_db().rental_applications.find_one({"_id": ObjectId(app_id)})
    if not a:
        raise HTTPException(status_code=404, detail="Aplicación no encontrada")
    return a


def _screening_email_html(name: str, provider: str, link: str) -> tuple[str, str, str]:
    """Returns (subject, plain_body, html)."""
    from rental.tenant_leads_router import _render_branded_email

    provider_label = PROVIDER_LABELS.get(provider, PROVIDER_LABELS["other"])
    first_name = (name or "").strip().split(" ")[0] or "Hola"
    subject = "Siguiente paso: verificación de crédito y antecedentes — Ross House Rentals"

    if link:
        step_html = (
            f"<p>Para continuar con tu aplicación de renta, el siguiente paso es completar la "
            f"verificación de crédito y antecedentes a través de <strong>{provider_label}</strong>.</p>"
            f"<p>Haz clic en el botón de abajo para iniciar el proceso. Toma unos 10&nbsp;minutos y "
            f"la tarifa del reporte la paga el aplicante directamente al proveedor.</p>"
        )
        step_plain = (
            f"Para continuar con tu aplicación de renta, completa la verificación de crédito y "
            f"antecedentes con {provider_label} en este enlace: {link}\n"
            f"Toma unos 10 minutos y la tarifa del reporte se paga directamente al proveedor."
        )
    else:
        step_html = (
            f"<p>Para continuar con tu aplicación de renta, el siguiente paso es la verificación de "
            f"crédito y antecedentes a través de <strong>{provider_label}</strong>.</p>"
            f"<p>En breve recibirás un correo directamente de {provider_label} con una invitación "
            f"para completar el proceso en línea. Toma unos 10&nbsp;minutos y la tarifa del reporte "
            f"la paga el aplicante directamente al proveedor.</p>"
        )
        step_plain = (
            f"Para continuar con tu aplicación de renta, en breve recibirás un correo de "
            f"{provider_label} con la invitación para completar la verificación de crédito y "
            f"antecedentes. Toma unos 10 minutos y la tarifa se paga directamente al proveedor."
        )

    content_html = (
        f"<p style='font-size:16px;'>Hola <strong>{first_name}</strong>,</p>"
        f"{step_html}"
        "<p>Tu información se procesa de forma segura por el proveedor de screening; "
        "Ross House Rentals nunca ve tu número de Seguro Social.</p>"
        "<p>Si tienes preguntas, responde a este correo o llámanos al (806)&nbsp;934-2018.</p>"
    )
    html = _render_branded_email(
        title="Verificación de crédito y antecedentes",
        eyebrow="Aplicación de renta · Siguiente paso",
        content_html=content_html,
        cta_label="Iniciar verificación" if link else None,
        cta_url=link or None,
    )
    plain = (
        f"Hola {first_name},\n\n{step_plain}\n\n"
        "Tu información se procesa de forma segura por el proveedor de screening.\n"
        "Preguntas: (806) 934-2018 — Ross House Rentals"
    )
    return subject, plain, html


@router.post('/admin/rental-applications/{app_id}/screening/request')
async def request_screening(app_id: str, request: Request):
    """Admin: request a credit/background screening for an application."""
    user = await auth_admin(request)
    a = await _get_application(app_id)
    data = await request.json()

    provider = (data.get("provider") or "smartmove").lower().strip()
    if provider not in PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Proveedor inválido. Use: {sorted(PROVIDERS)}")
    link = (data.get("screening_link") or "").strip()
    if link and not link.lower().startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="El enlace debe empezar con http(s)://")
    send_email = bool(data.get("send_email", True))

    email_sent = False
    if send_email:
        to_email = (a.get("email") or "").strip()
        if not to_email:
            raise HTTPException(status_code=400, detail="La aplicación no tiene email; desactiva el envío de correo")
        from rental.tenant_leads_router import _send_email
        subject, plain, html = _screening_email_html(a.get("name", ""), provider, link)
        email_sent = await _send_email(to_email, subject, plain, html=html)

    screening = {
        "status": "requested",
        "provider": provider,
        "screening_link": link,
        "requested_at": datetime.utcnow(),
        "requested_by": user.get("email") or user.get("name") or "",
        "email_sent": email_sent,
        "results": dict(DEFAULT_RESULTS),
        "report": (a.get("screening") or {}).get("report"),  # keep report if re-requesting
    }
    update = {"screening": screening, "updated_at": datetime.utcnow()}
    # Move brand-new applications into 'reviewing' automatically
    if (a.get("status") or "new") == "new":
        update["status"] = "reviewing"
        update["reviewed_by"] = user.get("email") or ""
        update["reviewed_at"] = datetime.utcnow()

    await get_db().rental_applications.update_one({"_id": a["_id"]}, {"$set": update})
    return {"success": True, "screening": serialize_screening(screening), "email_sent": email_sent}


@router.patch('/admin/rental-applications/{app_id}/screening')
async def update_screening(app_id: str, request: Request):
    """Admin: update screening status and/or results."""
    await auth_admin(request)
    a = await _get_application(app_id)
    screening = a.get("screening")
    if not screening or screening.get("type") == "waived":
        raise HTTPException(status_code=400, detail="Esta aplicación no tiene screening solicitado")
    data = await request.json()

    sets: dict = {}
    if "status" in data:
        st = (data.get("status") or "").lower().strip()
        if st not in SCREENING_STATUSES:
            raise HTTPException(status_code=400, detail=f"Status inválido. Use: {sorted(SCREENING_STATUSES)}")
        sets["screening.status"] = st
        if st == "completed":
            sets["screening.completed_at"] = datetime.utcnow()

    if "provider" in data:
        pv = (data.get("provider") or "").lower().strip()
        if pv not in PROVIDERS:
            raise HTTPException(status_code=400, detail=f"Proveedor inválido. Use: {sorted(PROVIDERS)}")
        sets["screening.provider"] = pv

    if "screening_link" in data:
        sets["screening.screening_link"] = (data.get("screening_link") or "").strip()

    results = data.get("results")
    if isinstance(results, dict):
        if "credit_score" in results:
            cs = results.get("credit_score")
            if cs in (None, ""):
                sets["screening.results.credit_score"] = None
            else:
                try:
                    cs = int(cs)
                except (TypeError, ValueError):
                    raise HTTPException(status_code=400, detail="credit_score debe ser un número")
                if not 300 <= cs <= 850:
                    raise HTTPException(status_code=400, detail="credit_score debe estar entre 300 y 850")
                sets["screening.results.credit_score"] = cs
        if "income_verified" in results:
            iv = results.get("income_verified")
            sets["screening.results.income_verified"] = None if iv is None else bool(iv)
        for key in ("criminal_records", "eviction_records"):
            if key in results:
                val = (results.get(key) or "").lower().strip()
                if val not in ("", "clean", "found"):
                    raise HTTPException(status_code=400, detail=f"{key} debe ser '', 'clean' o 'found'")
                sets[f"screening.results.{key}"] = val
        if "recommendation" in results:
            rec = (results.get("recommendation") or "").lower().strip()
            if rec not in RECOMMENDATIONS:
                raise HTTPException(status_code=400, detail=f"recommendation inválida. Use: {sorted(RECOMMENDATIONS)}")
            sets["screening.results.recommendation"] = rec
        if "notes" in results:
            sets["screening.results.notes"] = (results.get("notes") or "").strip()

    if not sets:
        raise HTTPException(status_code=400, detail="No hay cambios a aplicar")
    sets["updated_at"] = datetime.utcnow()

    await get_db().rental_applications.update_one({"_id": a["_id"]}, {"$set": sets})
    a = await get_db().rental_applications.find_one({"_id": a["_id"]})
    return {"success": True, "screening": serialize_screening(a.get("screening"))}


@router.post('/admin/rental-applications/{app_id}/screening/report')
async def upload_screening_report(app_id: str, request: Request):
    """Admin: attach the screening report PDF (base64) to an application."""
    user = await auth_admin(request)
    a = await _get_application(app_id)
    if not a.get("screening") or (a.get("screening") or {}).get("type") == "waived":
        raise HTTPException(status_code=400, detail="Esta aplicación no tiene screening solicitado")
    data = await request.json()

    filename = (data.get("filename") or "screening-report.pdf").strip()
    content_type = (data.get("content_type") or "application/pdf").strip()
    b64 = data.get("data_base64") or ""
    if "," in b64[:80]:  # strip data URI prefix if present
        b64 = b64.split(",", 1)[1]
    try:
        raw = base64.b64decode(b64, validate=True)
    except Exception:
        raise HTTPException(status_code=400, detail="data_base64 inválido")
    if not raw:
        raise HTTPException(status_code=400, detail="Archivo vacío")
    if len(raw) > MAX_REPORT_BYTES:
        raise HTTPException(status_code=400, detail="El archivo excede el límite de 10 MB")

    now = datetime.utcnow()
    await get_db().screening_reports.update_one(
        {"application_id": str(a["_id"])},
        {"$set": {
            "application_id": str(a["_id"]),
            "filename": filename,
            "content_type": content_type,
            "size": len(raw),
            "data_b64": base64.b64encode(raw).decode(),
            "uploaded_at": now,
            "uploaded_by": user.get("email") or "",
        }},
        upsert=True,
    )
    await get_db().rental_applications.update_one(
        {"_id": a["_id"]},
        {"$set": {
            "screening.report": {"filename": filename, "size": len(raw), "uploaded_at": now},
            "updated_at": now,
        }},
    )
    return {"success": True, "report": {"filename": filename, "size": len(raw), "uploaded_at": now.isoformat()}}


@router.get('/admin/rental-applications/{app_id}/screening/report')
async def download_screening_report(app_id: str, request: Request):
    """Admin: download the screening report."""
    await auth_admin(request)
    if not ObjectId.is_valid(app_id):
        raise HTTPException(status_code=400, detail="ID inválido")
    doc = await get_db().screening_reports.find_one({"application_id": app_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Reporte no encontrado")
    raw = base64.b64decode(doc["data_b64"])
    return Response(
        content=raw,
        media_type=doc.get("content_type", "application/pdf"),
        headers={"Content-Disposition": f'attachment; filename="{doc.get("filename", "screening-report.pdf")}"'},
    )
