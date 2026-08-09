"""1099-NEC para proveedores (contratistas).

- Suma pagos 'paid' del año por proveedor (colección provider_payments).
- REGLA IRS: pagos con tarjeta o redes de terceros (PayPal/Venmo/CashApp/Stripe card)
  NO van en 1099-NEC (los reporta el procesador en 1099-K). Cash/check/Zelle/ACH/wire SÍ.
- Umbral: $600+/año → requiere 1099-NEC.
- W-9 del proveedor se guarda en service_providers.w9 {legal_name, business_name,
  tax_classification, tin_type, tin, address, city, state, zip}.
- Payer (tu LLC) en app_settings {_id:'tax_1099'}.
- PDF: formulario sustituto Copy B (para el proveedor) con reportlab.
  NOTA: la Copy A al IRS se presenta electrónicamente (IRIS/Tax1099) — el CSV
  de exportación tiene todos los campos para eso.
"""
import io
import os
import asyncio
import logging
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, HTTPException, Request, Response

from rental.shared import get_db, auth_admin

try:
    from zoneinfo import ZoneInfo
    CT = ZoneInfo("America/Chicago")
except Exception:
    CT = timezone.utc

router = APIRouter()
logger = logging.getLogger(__name__)

# Métodos reportables en 1099-NEC (el resto los cubre 1099-K del procesador)
NEC_METHODS = ("cash", "check", "zelle", "wire", "stripe_ach", "other")
EXCLUDED_METHODS = ("stripe_card", "paypal", "venmo", "cashapp")

DEFAULT_PAYER = {
    "name": "Ross House Rentals LLC",
    "ein": "",
    "address": "305 Bruce Ave",
    "city": "Dumas", "state": "TX", "zip": "79029",
    "phone": "(806) 934-2018",
}


async def _get_payer() -> dict:
    doc = await get_db().app_settings.find_one({"_id": "tax_1099"}) or {}
    return {**DEFAULT_PAYER, **{k: v for k, v in doc.items() if k != "_id"}}


def _mask_tin(tin: str) -> str:
    t = (tin or "").replace("-", "").strip()
    return f"***-**-{t[-4:]}" if len(t) >= 4 else ""


def _year_range(year: int):
    return datetime(year, 1, 1), datetime(year + 1, 1, 1)


async def _provider_totals(year: int) -> dict:
    """{provider_id: {reportable, excluded, count}} para pagos 'paid' del año."""
    start, end = _year_range(year)
    out: dict = {}
    pipeline = [
        {"$match": {"status": "paid", "paid_at": {"$gte": start, "$lt": end}}},
        {"$group": {"_id": {"p": "$provider_id", "m": "$method"},
                    "total": {"$sum": "$amount"}, "n": {"$sum": 1}}},
    ]
    async for row in get_db().provider_payments.aggregate(pipeline):
        pid, method = row["_id"]["p"], row["_id"]["m"]
        d = out.setdefault(pid, {"reportable": 0.0, "excluded": 0.0, "count": 0})
        if method in EXCLUDED_METHODS:
            d["excluded"] += row["total"]
        else:
            d["reportable"] += row["total"]
        d["count"] += row["n"]
    return out


@router.get('/admin/1099/summary')
async def summary_1099(request: Request, year: int = 0):
    await auth_admin(request)
    db = get_db()
    year = year or datetime.utcnow().year
    totals = await _provider_totals(year)
    providers = {p["_id"]: p async for p in db.service_providers.find({})}
    rows = []
    for pid, t in totals.items():
        p = providers.get(pid, {})
        w9 = p.get("w9") or {}
        w9_complete = bool(w9.get("legal_name") and w9.get("tin") and w9.get("address"))
        rows.append({
            "provider_id": pid,
            "name": p.get("name", "(eliminado)"),
            "email": p.get("email", ""),
            "reportable": round(t["reportable"], 2),
            "excluded": round(t["excluded"], 2),
            "payments_count": t["count"],
            "needs_1099": t["reportable"] >= 600,
            "w9_complete": w9_complete,
            "w9": {**{k: w9.get(k, "") for k in
                      ("legal_name", "business_name", "tax_classification",
                       "tin_type", "address", "city", "state", "zip")},
                   "tin_masked": _mask_tin(w9.get("tin", ""))},
        })
    rows.sort(key=lambda r: -r["reportable"])
    payer = await _get_payer()
    return {"success": True, "year": year, "rows": rows,
            "payer": payer, "payer_complete": bool(payer.get("ein")),
            "totals": {
                "providers_needing_1099": sum(1 for r in rows if r["needs_1099"]),
                "missing_w9": sum(1 for r in rows if r["needs_1099"] and not r["w9_complete"]),
                "total_reportable": round(sum(r["reportable"] for r in rows), 2),
            }}


@router.put('/admin/1099/payer')
async def save_payer(request: Request):
    """Body: {name, ein, address, city, state, zip, phone}"""
    await auth_admin(request)
    data = await request.json()
    sets = {k: str(data[k]).strip() for k in
            ("name", "ein", "address", "city", "state", "zip", "phone") if k in data}
    if not sets:
        raise HTTPException(status_code=400, detail="Sin cambios")
    sets["updated_at"] = datetime.utcnow()
    await get_db().app_settings.update_one({"_id": "tax_1099"}, {"$set": sets}, upsert=True)
    return {"success": True, "payer": await _get_payer()}


@router.put('/admin/1099/providers/{provider_id}/w9')
async def save_w9(provider_id: str, request: Request):
    """Body: {legal_name, business_name?, tax_classification?, tin_type (ein|ssn), tin,
    address, city, state, zip}"""
    await auth_admin(request)
    db = get_db()
    p = await db.service_providers.find_one({"_id": provider_id})
    if not p:
        raise HTTPException(status_code=404, detail="Proveedor no encontrado")
    data = await request.json()
    w9 = p.get("w9") or {}
    for k in ("legal_name", "business_name", "tax_classification", "tin_type",
              "address", "city", "state", "zip"):
        if k in data:
            w9[k] = str(data[k]).strip()
    if "tin" in data:
        tin = str(data["tin"]).replace("-", "").replace(" ", "")
        if tin and not (tin.isdigit() and len(tin) == 9):
            raise HTTPException(status_code=400, detail="TIN debe tener 9 dígitos")
        w9["tin"] = tin
    w9["updated_at"] = datetime.utcnow()
    await db.service_providers.update_one({"_id": provider_id}, {"$set": {"w9": w9}})
    return {"success": True}


IRS_1099NEC_TEMPLATE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                    "assets", "f1099nec.pdf")


def _build_1099_pdf(payer: dict, provider: dict, w9: dict, amount: float, year: int) -> bytes:
    """Rellena el formulario OFICIAL del IRS (f1099nec.pdf) — Copy B + instrucciones
    para el destinatario. Página 3 = Copy B, página 4 = Instructions for Recipient."""
    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(IRS_1099NEC_TEMPLATE)
    writer = PdfWriter()
    writer.append(reader, pages=[3, 4])  # Copy B + instrucciones

    P = "topmostSubform[0].CopyB[0]."
    L, R = P + "LeftCol[0].", P + "RightCol[0]."
    rec_name = w9.get("legal_name") or provider.get("name", "")
    if w9.get("business_name"):
        rec_name = f"{rec_name} / {w9['business_name']}"
    fields = {
        P + "PgHeader[0].CalendarYear[0].f2_1[0]": str(year)[-2:],
        # PAYER (izquierda)
        L + "f2_2[0]": payer.get("name", ""),
        L + "f2_3[0]": payer.get("address", ""),
        L + "f2_5[0]": payer.get("city", ""),
        L + "f2_6[0]": payer.get("phone", ""),
        L + "f2_7[0]": payer.get("state", ""),
        L + "f2_9[0]": payer.get("zip", ""),
        L + "f2_10[0]": payer.get("ein", ""),
        # RECIPIENT
        L + "f2_11[0]": w9.get("tin", ""),
        L + "f2_12[0]": rec_name,
        L + "f2_13[0]": w9.get("address", ""),
        L + "f2_15[0]": w9.get("city", ""),
        L + "f2_16[0]": w9.get("state", ""),
        L + "f2_18[0]": w9.get("zip", ""),
        L + "f2_19[0]": str(provider.get("_id", ""))[:20],  # account number
        # Box 1a — Nonemployee compensation
        R + "f2_20[0]": f"{amount:,.2f}",
    }
    writer.update_page_form_field_values(writer.pages[0], fields, auto_regenerate=False)
    try:
        writer.set_need_appearances_writer(True)
    except Exception:
        pass

    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


@router.get('/admin/1099/providers/{provider_id}/pdf')
async def pdf_1099(provider_id: str, request: Request, year: int = 0):
    await auth_admin(request)
    db = get_db()
    year = year or datetime.utcnow().year
    p = await db.service_providers.find_one({"_id": provider_id})
    if not p:
        raise HTTPException(status_code=404, detail="Proveedor no encontrado")
    totals = await _provider_totals(year)
    amount = totals.get(provider_id, {}).get("reportable", 0.0)
    if amount <= 0:
        raise HTTPException(status_code=400, detail=f"Sin pagos reportables en {year}")
    payer = await _get_payer()
    pdf = _build_1099_pdf(payer, p, p.get("w9") or {}, amount, year)
    return Response(content=pdf, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="1099-NEC-{year}-{p.get("name","proveedor").replace(" ","_")}.pdf"'})


async def _email_copy_b(db, p: dict, amount: float, year: int, payer: dict) -> bool:
    """Envía la Copy B del 1099-NEC por email al contratista (PDF oficial adjunto)."""
    pdf = _build_1099_pdf(payer, p, p.get("w9") or {}, amount, year)

    import base64
    from sendgrid import SendGridAPIClient
    from sendgrid.helpers.mail import (Mail, Attachment, FileContent, FileName,
                                       FileType, Disposition)
    sg_key, from_email = await _sendgrid_creds(db)
    if not sg_key:
        return False
    lang = p.get("language_pref", "es")
    if lang == "es":
        subject = f"Formulario 1099-NEC {year} — Ross House Rentals"
        body = (f"Hola {p.get('name')},\n\nAdjunto encontrarás tu formulario 1099-NEC del año "
                f"{year} por ${amount:,.2f} en compensación de no-empleado.\n"
                "Guárdalo para tu declaración de impuestos.\n\n— Ross House Rentals")
    else:
        subject = f"Form 1099-NEC {year} — Ross House Rentals"
        body = (f"Hi {p.get('name')},\n\nAttached is your {year} Form 1099-NEC for "
                f"${amount:,.2f} in nonemployee compensation.\nKeep it for your tax return."
                "\n\n— Ross House Rentals")
    msg = Mail(from_email=(from_email, "Ross House Rentals"),
               to_emails=p["email"], subject=subject,
               html_content=f"<pre style='font-family:system-ui'>{body}</pre>")
    msg.attachment = Attachment(FileContent(base64.b64encode(pdf).decode()),
                                FileName(f"1099-NEC-{year}.pdf"), FileType("application/pdf"),
                                Disposition("attachment"))
    resp = SendGridAPIClient(sg_key).send(msg)
    if resp.status_code not in (200, 201, 202):
        return False
    await db.service_providers.update_one(
        {"_id": p["_id"]},
        {"$set": {f"form_1099_sent.{year}": datetime.utcnow()}})
    return True


@router.post('/admin/1099/providers/{provider_id}/email')
async def email_1099(provider_id: str, request: Request, year: int = 0):
    """Envía el 1099-NEC Copy B por email al proveedor (adjunto PDF)."""
    await auth_admin(request)
    db = get_db()
    year = year or datetime.utcnow().year
    p = await db.service_providers.find_one({"_id": provider_id})
    if not p:
        raise HTTPException(status_code=404, detail="Proveedor no encontrado")
    if not p.get("email"):
        raise HTTPException(status_code=400, detail="El proveedor no tiene email")
    totals = await _provider_totals(year)
    amount = totals.get(provider_id, {}).get("reportable", 0.0)
    if amount <= 0:
        raise HTTPException(status_code=400, detail=f"Sin pagos reportables en {year}")
    payer = await _get_payer()
    if not await _email_copy_b(db, p, amount, year, payer):
        raise HTTPException(status_code=502, detail="SendGrid no pudo enviar el email")
    return {"success": True, "message": f"1099-NEC {year} enviado a {p['email']}"}


@router.get('/admin/1099/export/csv')
async def export_1099_csv(request: Request, year: int = 0):
    """CSV con todos los campos para e-file (Tax1099/IRIS)."""
    await auth_admin(request)
    db = get_db()
    year = year or datetime.utcnow().year
    totals = await _provider_totals(year)
    payer = await _get_payer()
    lines = ["payer_name,payer_ein,payer_address,tax_year,recipient_name,recipient_business,"
             "recipient_tin_type,recipient_tin,recipient_address,recipient_city,recipient_state,"
             "recipient_zip,box1_nonemployee_compensation,needs_1099,w9_complete"]

    def q(s):
        s = str(s or "")
        return f'"{s.replace(chr(34), chr(34)*2)}"' if ("," in s or '"' in s) else s

    for pid, t in totals.items():
        p = await db.service_providers.find_one({"_id": pid}) or {}
        w9 = p.get("w9") or {}
        w9_ok = bool(w9.get("legal_name") and w9.get("tin") and w9.get("address"))
        lines.append(",".join([
            q(payer.get("name")), q(payer.get("ein")),
            q(f"{payer.get('address')}, {payer.get('city')}, {payer.get('state')} {payer.get('zip')}"),
            str(year),
            q(w9.get("legal_name") or p.get("name", "")), q(w9.get("business_name", "")),
            q(w9.get("tin_type", "")), q(w9.get("tin", "")),
            q(w9.get("address", "")), q(w9.get("city", "")), q(w9.get("state", "")),
            q(w9.get("zip", "")),
            f"{t['reportable']:.2f}",
            "YES" if t["reportable"] >= 600 else "NO",
            "YES" if w9_ok else "NO",
        ]))
    return Response(content="\n".join(lines), media_type="text/csv", headers={
        "Content-Disposition": f'attachment; filename="1099-NEC-{year}.csv"'})


# ═══════════════════════════════════════════════════════════════
# Alerta automática: contratista cruza $600 → genera 1099-NEC y avisa
# + Recordatorio de deadline (31 de enero) con dirección de envío
# ═══════════════════════════════════════════════════════════════

ADMIN_EMAIL = "yoandyross@gmail.com"


async def _sendgrid_creds(db):
    sg_key = os.getenv('SENDGRID_API_KEY', '')
    from_email = os.getenv('SENDGRID_FROM_EMAIL', 'info@rosshouserentals.com')
    if not sg_key:
        cfg = await db.api_config.find_one({'_id': 'main'}) or {}
        sg_key = cfg.get('sendgrid_api_key', '')
        from_email = cfg.get('sendgrid_from_email', from_email)
    return sg_key, from_email


async def _send_admin_email(db, subject: str, html: str,
                            attachment: tuple | None = None) -> bool:
    """Email al admin, con adjunto PDF opcional (filename, bytes)."""
    sg_key, from_email = await _sendgrid_creds(db)
    if not sg_key:
        logger.warning("[1099] SendGrid no configurado — alerta no enviada")
        return False
    try:
        import base64
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import (Mail, Email, To, Content, Attachment,
                                           FileContent, FileName, FileType, Disposition)
        mail = Mail(from_email=Email(from_email, "Ross House Rentals"),
                    to_emails=To(ADMIN_EMAIL), subject=subject,
                    html_content=Content("text/html", html))
        if attachment:
            fname, data = attachment
            mail.attachment = Attachment(FileContent(base64.b64encode(data).decode()),
                                         FileName(fname), FileType("application/pdf"),
                                         Disposition("attachment"))
        resp = SendGridAPIClient(sg_key).send(mail)
        return resp.status_code in (200, 201, 202)
    except Exception as e:
        logger.exception(f"[1099] email admin falló: {e}")
        return False


def _w9_address_html(w9: dict) -> str:
    if w9.get("address"):
        return (f"{w9.get('legal_name') or ''}<br/>{w9.get('address','')}<br/>"
                f"{w9.get('city','')}, {w9.get('state','')} {w9.get('zip','')}")
    return "<span style='color:#b91c1c;font-weight:bold'>⚠️ SIN W-9 — pide dirección y TIN</span>"


async def check_1099_threshold(provider_id: str):
    """Llamar tras registrar/marcar un pago 'paid'. Si el contratista cruza $600
    reportables en el año: genera el 1099-NEC oficial (si tiene W-9) y alerta al
    admin por email (una sola vez por año)."""
    try:
        db = get_db()
        year = datetime.utcnow().year
        start, end = _year_range(year)
        total = 0.0
        async for row in db.provider_payments.aggregate([
                {"$match": {"provider_id": provider_id, "status": "paid",
                            "paid_at": {"$gte": start, "$lt": end},
                            "method": {"$nin": list(EXCLUDED_METHODS)}}},
                {"$group": {"_id": None, "t": {"$sum": "$amount"}}}]):
            total = row["t"]
        if total < 600:
            return
        p = await db.service_providers.find_one({"_id": provider_id})
        if not p or (p.get("tax_1099_alerts") or {}).get(str(year)):
            return  # ya alertado este año

        w9 = p.get("w9") or {}
        w9_ok = bool(w9.get("legal_name") and w9.get("tin") and w9.get("address"))
        payer = await _get_payer()
        attachment = None
        w9_requested = False
        if w9_ok and payer.get("ein"):
            try:
                pdf = _build_1099_pdf(payer, p, w9, total, year)
                attachment = (f"1099-NEC-{year}-{p.get('name', 'contratista').replace(' ', '_')}.pdf", pdf)
            except Exception as e:
                logger.warning(f"[1099] PDF automático falló para {provider_id}: {e}")
        elif not w9_ok and p.get("email"):
            # Pedir el W-9 digital automáticamente al contratista
            try:
                w9_requested = await send_w9_request(db, p)
            except Exception as e:
                logger.warning(f"[1099] W-9 request automático falló: {e}")

        name = p.get("name", "Contratista")
        subject = f"📋 1099-NEC requerido: {name} superó $600 en {year} (${total:,.2f})"
        html = f"""
        <div style="font-family:system-ui,Arial,sans-serif;max-width:600px;margin:0 auto">
          <div style="background:#0f172a;color:#fff;padding:18px 22px;border-radius:12px 12px 0 0">
            <h2 style="margin:0;font-size:18px">📋 Umbral 1099-NEC alcanzado</h2>
          </div>
          <div style="border:1px solid #e2e8f0;border-top:0;padding:22px;border-radius:0 0 12px 12px">
            <p><b>{name}</b>{f" ({p.get('company_name')})" if p.get('company_name') else ""} acumula
            <b style="color:#b91c1c">${total:,.2f}</b> en pagos reportables (cash/check/Zelle/ACH/wire)
            en {year} — supera el umbral de $600 del IRS y <b>requiere formulario 1099-NEC</b>.</p>
            <table style="width:100%;border-collapse:collapse;font-size:14px;margin:12px 0">
              <tr><td style="padding:6px 0;color:#64748b">W-9:</td>
                  <td>{"✅ Completo" if w9_ok else ("📩 <b>Se le envió automáticamente el W-9 digital</b> — te aviso cuando lo complete" if w9_requested else "<b style='color:#b91c1c'>❌ FALTA — pídeselo ahora para evitar retención del 24% (backup withholding)</b>")}</td></tr>
              <tr><td style="padding:6px 0;color:#64748b">Dirección de envío (Copy B):</td>
                  <td>{_w9_address_html(w9)}</td></tr>
              <tr><td style="padding:6px 0;color:#64748b">Formulario:</td>
                  <td>{"📎 Adjunto en este email (se regenera con el total final en enero)" if attachment else "Se generará cuando el W-9 esté completo"}</td></tr>
            </table>
            <p style="font-size:13px;color:#64748b">📅 <b>Fechas límite:</b> enviar Copy B al contratista y
            presentar Copy A al IRS (e-file IRIS) antes del <b>31 de enero de {year + 1}</b>.
            Te lo recordaré automáticamente en enero con la lista completa.</p>
            <a href="https://www.rosshouserentals.com/admin/contabilidad"
               style="display:inline-block;background:#C41428;color:#fff;padding:10px 18px;border-radius:8px;
               text-decoration:none;font-weight:bold;font-size:14px">Ver panel 1099 →</a>
          </div>
        </div>"""
        sent = await _send_admin_email(db, subject, html, attachment)
        await db.service_providers.update_one(
            {"_id": provider_id},
            {"$set": {f"tax_1099_alerts.{year}": {
                "total_at_alert": round(total, 2),
                "alerted_at": datetime.utcnow(),
                "w9_complete": w9_ok,
                "w9_requested": w9_requested,
                "pdf_attached": bool(attachment),
                "email_sent": sent,
            }}})
        logger.info(f"[1099] alerta $600 enviada para {name} (${total:,.2f}, email={sent})")
    except Exception as e:
        logger.exception(f"[1099] check_1099_threshold falló para {provider_id}: {e}")


# ─── Recordatorio de deadline: 10 y 28 de enero, 9AM CT ─────────

DEADLINE_FIRE_DATES = [(1, 10), (1, 28)]
DEADLINE_FIRE_HOUR = 9
DEADLINE_CHECK_SECONDS = 60 * 60


async def send_1099_deadline_reminder(db, year: int = 0) -> dict:
    """Email al admin con la lista de 1099-NEC a enviar antes del 31 de enero:
    contratista, monto, estado del W-9 y DIRECCIÓN DE ENVÍO de la Copy B."""
    year = year or (datetime.utcnow().year - 1)  # en enero se reporta el año anterior
    totals = await _provider_totals(year)
    providers = {p["_id"]: p async for p in db.service_providers.find({})}
    rows, n, missing_w9 = "", 0, 0
    for pid, t in sorted(totals.items(), key=lambda kv: -kv[1]["reportable"]):
        if t["reportable"] < 600:
            continue
        n += 1
        p = providers.get(pid, {})
        w9 = p.get("w9") or {}
        w9_ok = bool(w9.get("legal_name") and w9.get("tin") and w9.get("address"))
        if not w9_ok:
            missing_w9 += 1
        rows += f"""
        <tr style="border-bottom:1px solid #e2e8f0">
          <td style="padding:8px 6px"><b>{p.get('name', '(eliminado)')}</b>
              {f"<br/><span style='color:#64748b;font-size:11px'>{p.get('company_name')}</span>" if p.get('company_name') else ""}</td>
          <td style="padding:8px 6px;text-align:right;font-weight:bold">${t['reportable']:,.2f}</td>
          <td style="padding:8px 6px;text-align:center">{"✅" if w9_ok else "<b style='color:#b91c1c'>❌</b>"}</td>
          <td style="padding:8px 6px;font-size:12px">{_w9_address_html(w9)}</td>
        </tr>"""
    if n == 0:
        logger.info(f"[1099] deadline reminder: sin contratistas ≥$600 en {year}")
        return {"success": True, "sent": 0, "skipped": f"Sin contratistas con $600+ en {year}"}

    subject = f"🚨 1099-NEC {year}: {n} contratista(s) — enviar antes del 31 de enero"
    html = f"""
    <div style="font-family:system-ui,Arial,sans-serif;max-width:680px;margin:0 auto">
      <div style="background:#7c2d12;color:#fff;padding:18px 22px;border-radius:12px 12px 0 0">
        <h2 style="margin:0;font-size:18px">🚨 Deadline 1099-NEC: 31 de enero</h2>
        <p style="margin:6px 0 0;font-size:13px;opacity:.85">Año fiscal {year} · {n} contratista(s) requieren formulario
        {f" · <b>{missing_w9} sin W-9</b>" if missing_w9 else ""}</p>
      </div>
      <div style="border:1px solid #e2e8f0;border-top:0;padding:22px;border-radius:0 0 12px 12px">
        <table style="width:100%;border-collapse:collapse;font-size:13px">
          <tr style="text-align:left;color:#64748b;font-size:11px;text-transform:uppercase">
            <th style="padding:6px">Contratista</th><th style="padding:6px;text-align:right">Box 1a</th>
            <th style="padding:6px;text-align:center">W-9</th><th style="padding:6px">📮 Dirección de envío (Copy B)</th>
          </tr>
          {rows}
        </table>
        <div style="background:#fef3c7;border:1px solid #fbbf24;border-radius:8px;padding:12px;margin-top:14px;font-size:13px">
          <b>✅ Checklist antes del 31 de enero:</b><br/>
          1. Descarga cada 1099-NEC del panel (o usa el botón de email al contratista)<br/>
          2. Envía la <b>Copy B</b> a cada contratista a la dirección indicada arriba (o por email)<br/>
          3. Presenta la <b>Copy A</b> al IRS electrónicamente vía
             <a href="https://www.irs.gov/filing/e-file-forms-1099-with-iris" style="color:#0891b2">IRIS</a>
             (gratis) o exporta el CSV del panel para Tax1099
        </div>
        <a href="https://www.rosshouserentals.com/admin/contabilidad"
           style="display:inline-block;background:#C41428;color:#fff;padding:10px 18px;border-radius:8px;
           text-decoration:none;font-weight:bold;font-size:14px;margin-top:14px">Abrir panel 1099 →</a>
      </div>
    </div>"""
    sent = await _send_admin_email(db, subject, html)
    await db.app_settings.update_one(
        {"_id": "tax_1099_reminders"},
        {"$set": {"last_run_at": datetime.now(timezone.utc)}}, upsert=True)
    logger.info(f"[1099] deadline reminder enviado ({n} contratistas, email={sent})")
    return {"success": True, "sent": 1 if sent else 0, "providers": n, "missing_w9": missing_w9}


@router.post('/admin/1099/deadline-reminder/send')
async def manual_deadline_reminder(request: Request, year: int = 0):
    """Dispara manualmente el recordatorio de deadline (por defecto: año en curso)."""
    await auth_admin(request)
    result = await send_1099_deadline_reminder(get_db(), year or datetime.utcnow().year)
    return result


# ─── Envío masivo de Copy B por email (15 de enero, opcional auto/manual) ──

COPYB_FIRE_DATE = (1, 15)


async def send_copyb_batch(db, year: int = 0) -> dict:
    """Envía la Copy B por email a todos los contratistas ≥$600 con W-9 completo
    y email registrado (salta los ya enviados ese año). Devuelve el detalle y
    manda un resumen al admin con los que quedan pendientes de envío postal."""
    year = year or (datetime.utcnow().year - 1)
    totals = await _provider_totals(year)
    payer = await _get_payer()
    sent, no_email, no_w9, already = [], [], [], []
    for pid, t in sorted(totals.items(), key=lambda kv: -kv[1]["reportable"]):
        if t["reportable"] < 600:
            continue
        p = await db.service_providers.find_one({"_id": pid})
        if not p:
            continue
        w9 = p.get("w9") or {}
        w9_ok = bool(w9.get("legal_name") and w9.get("tin") and w9.get("address"))
        entry = {"name": p.get("name", "?"), "amount": round(t["reportable"], 2),
                 "email": p.get("email", ""), "w9": w9}
        if (p.get("form_1099_sent") or {}).get(str(year)):
            already.append(entry)
        elif not w9_ok:
            no_w9.append(entry)
            # Pedir W-9 digital automáticamente (con cooldown de 14 días)
            try:
                await send_w9_request(db, p)
            except Exception:
                pass
        elif not p.get("email"):
            no_email.append(entry)
        else:
            ok = False
            try:
                ok = await _email_copy_b(db, p, t["reportable"], year, payer)
            except Exception as e:
                logger.warning(f"[1099] Copy B email falló para {pid}: {e}")
            (sent if ok else no_email).append(entry)

    # Resumen al admin: enviados + pendientes de correo postal con dirección
    if sent or no_email or no_w9:
        def _rows(entries, note=""):
            return "".join(
                f"<tr style='border-bottom:1px solid #e2e8f0'>"
                f"<td style='padding:6px'><b>{e['name']}</b></td>"
                f"<td style='padding:6px;text-align:right'>${e['amount']:,.2f}</td>"
                f"<td style='padding:6px;font-size:12px'>{note or e['email'] or _w9_address_html(e['w9'])}</td></tr>"
                for e in entries)
        html = f"""
        <div style="font-family:system-ui,Arial,sans-serif;max-width:640px;margin:0 auto">
          <div style="background:#0f172a;color:#fff;padding:18px 22px;border-radius:12px 12px 0 0">
            <h2 style="margin:0;font-size:18px">📧 Copy B 1099-NEC {year} — resumen de envío</h2>
          </div>
          <div style="border:1px solid #e2e8f0;border-top:0;padding:22px;border-radius:0 0 12px 12px;font-size:14px">
            {f"<p>✅ <b>Enviados por email ({len(sent)}):</b></p><table style='width:100%;border-collapse:collapse'>{_rows(sent)}</table>" if sent else ""}
            {f"<p style='margin-top:14px'>📮 <b>Sin email — imprime y envía por correo ({len(no_email)}):</b></p><table style='width:100%;border-collapse:collapse'>{_rows(no_email)}</table>" if no_email else ""}
            {f"<p style='margin-top:14px'>⚠️ <b>Sin W-9 — consíguelo YA ({len(no_w9)}):</b></p><table style='width:100%;border-collapse:collapse'>{_rows(no_w9, 'Falta W-9')}</table>" if no_w9 else ""}
            {f"<p style='margin-top:14px;color:#64748b;font-size:12px'>Ya enviados antes: {len(already)}</p>" if already else ""}
            <p style="font-size:12px;color:#64748b;margin-top:14px">Deadline: 31 de enero — Copy B al contratista y Copy A al IRS (IRIS).</p>
          </div>
        </div>"""
        await _send_admin_email(
            db, f"📧 Copy B {year}: {len(sent)} enviadas por email · {len(no_email) + len(no_w9)} pendientes", html)

    await db.app_settings.update_one(
        {"_id": "tax_1099_reminders"},
        {"$set": {"copyb_last_run_at": datetime.now(timezone.utc)}}, upsert=True)
    logger.info(f"[1099] Copy B batch {year}: {len(sent)} enviadas, {len(no_email)} sin email, "
                f"{len(no_w9)} sin W-9, {len(already)} ya enviadas")
    return {"success": True, "year": year,
            "sent": len(sent), "no_email": len(no_email),
            "no_w9": len(no_w9), "already_sent": len(already),
            "detail": {"sent": [e["name"] for e in sent],
                       "no_email": [e["name"] for e in no_email],
                       "no_w9": [e["name"] for e in no_w9]}}


@router.get('/admin/1099/copyb-config')
async def get_copyb_config(request: Request):
    await auth_admin(request)
    cfg = await get_db().app_settings.find_one({"_id": "tax_1099_reminders"}) or {}
    return {"success": True,
            "auto_send_copyb": bool(cfg.get("auto_send_copyb", True)),
            "copyb_last_run_at": cfg.get("copyb_last_run_at")}


@router.put('/admin/1099/copyb-config')
async def save_copyb_config(request: Request):
    """Body: {auto_send_copyb: bool} — True = envío automático el 15 de enero."""
    await auth_admin(request)
    data = await request.json()
    auto = bool(data.get("auto_send_copyb", True))
    await get_db().app_settings.update_one(
        {"_id": "tax_1099_reminders"},
        {"$set": {"auto_send_copyb": auto}}, upsert=True)
    return {"success": True, "auto_send_copyb": auto}


@router.post('/admin/1099/copyb/send-batch')
async def manual_copyb_batch(request: Request, year: int = 0):
    """Dispara manualmente el envío masivo de Copy B (por defecto: año en curso)."""
    await auth_admin(request)
    return await send_copyb_batch(get_db(), year or datetime.utcnow().year)


async def _copyb_should_run(db) -> bool:
    cfg = await db.app_settings.find_one({"_id": "tax_1099_reminders"}) or {}
    if not cfg.get("auto_send_copyb", True):
        return False  # modo manual
    now_ct = datetime.now(CT)
    if (now_ct.month, now_ct.day) != COPYB_FIRE_DATE or now_ct.hour != DEADLINE_FIRE_HOUR:
        return False
    last_run = cfg.get("copyb_last_run_at")
    if last_run and isinstance(last_run, datetime):
        if last_run.tzinfo is None:
            last_run = last_run.replace(tzinfo=timezone.utc)
        if (datetime.now(timezone.utc) - last_run) < timedelta(days=2):
            return False
    return True


async def _deadline_should_run(db) -> bool:
    cfg = await db.app_settings.find_one({"_id": "tax_1099_reminders"}) or {}
    if not cfg.get("enabled", True):
        return False
    now_ct = datetime.now(CT)
    if (now_ct.month, now_ct.day) not in DEADLINE_FIRE_DATES or now_ct.hour != DEADLINE_FIRE_HOUR:
        return False
    last_run = cfg.get("last_run_at")
    if last_run and isinstance(last_run, datetime):
        if last_run.tzinfo is None:
            last_run = last_run.replace(tzinfo=timezone.utc)
        if (datetime.now(timezone.utc) - last_run) < timedelta(days=2):
            return False
    return True


# ─── W-9 digital: el contratista lo completa desde su teléfono ──

SITE_BASE = "https://www.rosshouserentals.com"
W9_RESEND_DAYS = 14


async def send_w9_request(db, p: dict) -> bool:
    """Emailea al contratista un link a su formulario W-9 digital (móvil).
    No re-envía si ya se le pidió hace menos de W9_RESEND_DAYS días.
    Envíos: 1º solicitud → 2º recordatorio → 3º ÚLTIMO AVISO (retención 24%).
    Después del 3º no envía más (escala al admin vía w9_reminder_sweep)."""
    if not p.get("email"):
        return False
    req = p.get("w9_request") or {}
    if req.get("completed_at"):
        return False
    sends = req.get("sends") or 0
    if sends >= 3:
        return False
    last = req.get("sent_at")
    if isinstance(last, datetime) and \
            (datetime.utcnow() - last) < timedelta(days=W9_RESEND_DAYS):
        return False
    import secrets
    token = req.get("token") or secrets.token_urlsafe(24)
    url = f"{SITE_BASE}/w9/{token}"
    name = p.get("name", "")
    lang = p.get("language_pref", "es")
    if lang == "es":
        if sends == 0:
            subject = "Acción requerida: completa tu W-9 (2 minutos) — Ross House Rentals"
            lead = (f"<p>Hola {name},</p><p>Este año te hemos pagado <b>$600 o más</b> por tus "
                    "servicios, así que el IRS nos exige tener tu <b>formulario W-9</b> en archivo "
                    "para emitirte el 1099-NEC.</p>")
        elif sends == 1:
            subject = "Recordatorio: aún falta tu W-9 (2 minutos) — Ross House Rentals"
            lead = (f"<p>Hola {name},</p><p>Te escribimos hace unos días pero aún <b>no hemos "
                    "recibido tu W-9</b>. Lo necesitamos para emitirte tu 1099-NEC correctamente.</p>")
        else:
            subject = "🚨 ÚLTIMO AVISO: sin tu W-9 retendremos el 24% de tus pagos"
            lead = (f"<p>Hola {name},</p><p>Este es nuestro <b>último aviso</b>: si no recibimos tu "
                    "W-9, la ley del IRS nos <b>obliga</b> a retener el <b>24%</b> de tus próximos "
                    "pagos (backup withholding). Evítalo completándolo hoy — toma 2 minutos.</p>")
        html = f"""
        <div style="font-family:system-ui,Arial,sans-serif;max-width:520px;margin:0 auto">
          {lead}
          <p style="text-align:center;margin:24px 0">
            <a href="{url}" style="background:#C41428;color:#fff;padding:14px 28px;border-radius:10px;
            text-decoration:none;font-weight:bold;font-size:16px">📝 Completar mi W-9</a></p>
          <p style="font-size:13px;color:#64748b">Tus datos van cifrados y solo se usan para el
          formulario de impuestos.</p>
          <p>— Yoandy Ross · Ross House Rentals LLC · (806) 934-2018</p>
        </div>"""
    else:
        if sends == 0:
            subject = "Action required: complete your W-9 (2 minutes) — Ross House Rentals"
            lead = (f"<p>Hi {name},</p><p>We've paid you <b>$600 or more</b> for your services this "
                    "year, so the IRS requires us to have your <b>Form W-9</b> on file to issue "
                    "your 1099-NEC.</p>")
        elif sends == 1:
            subject = "Reminder: we still need your W-9 (2 minutes) — Ross House Rentals"
            lead = (f"<p>Hi {name},</p><p>We reached out a few days ago but <b>haven't received "
                    "your W-9 yet</b>. We need it to issue your 1099-NEC correctly.</p>")
        else:
            subject = "🚨 FINAL NOTICE: without your W-9 we must withhold 24% of your payments"
            lead = (f"<p>Hi {name},</p><p>This is our <b>final notice</b>: if we don't receive your "
                    "W-9, IRS rules <b>require</b> us to withhold <b>24%</b> of your future payments "
                    "(backup withholding). Avoid it by completing it today — takes 2 minutes.</p>")
        html = f"""
        <div style="font-family:system-ui,Arial,sans-serif;max-width:520px;margin:0 auto">
          {lead}
          <p style="text-align:center;margin:24px 0">
            <a href="{url}" style="background:#C41428;color:#fff;padding:14px 28px;border-radius:10px;
            text-decoration:none;font-weight:bold;font-size:16px">📝 Complete my W-9</a></p>
          <p style="font-size:13px;color:#64748b">Your data is encrypted and only used for the tax form.</p>
          <p>— Yoandy Ross · Ross House Rentals LLC · (806) 934-2018</p>
        </div>"""
    sg_key, from_email = await _sendgrid_creds(db)
    if not sg_key:
        return False
    try:
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import Mail
        msg = Mail(from_email=(from_email, "Ross House Rentals"),
                   to_emails=p["email"], subject=subject, html_content=html)
        resp = SendGridAPIClient(sg_key).send(msg)
        if resp.status_code not in (200, 201, 202):
            return False
    except Exception as e:
        logger.warning(f"[1099] W-9 request email falló para {p['_id']}: {e}")
        return False
    await db.service_providers.update_one(
        {"_id": p["_id"]},
        {"$set": {"w9_request": {**req, "token": token,
                                 "sent_at": datetime.utcnow(),
                                 "sends": (req.get("sends") or 0) + 1}}})
    logger.info(f"[1099] W-9 request enviado a {p.get('name')} <{p['email']}>")
    return True


async def w9_reminder_sweep(db) -> dict:
    """Barrido diario: re-envía la solicitud de W-9 a quienes no la completaron
    (recordatorio a los 14 días y ÚLTIMO AVISO a los 28). Si tras el 3er envío
    pasan otros 14 días sin respuesta, escala al admin para aplicar la
    retención del 24% (una sola vez)."""
    reminded, escalated = [], []
    async for p in db.service_providers.find(
            {"w9_request.token": {"$exists": True},
             "w9_request.completed_at": {"$exists": False}}):
        req = p.get("w9_request") or {}
        sends = req.get("sends") or 0
        last = req.get("sent_at")
        overdue = isinstance(last, datetime) and \
            (datetime.utcnow() - last) >= timedelta(days=W9_RESEND_DAYS)
        if sends < 3 and overdue:
            try:
                if await send_w9_request(db, p):
                    reminded.append({"name": p.get("name", ""), "send_num": sends + 1})
            except Exception as e:
                logger.warning(f"[1099] W-9 reminder falló para {p['_id']}: {e}")
        elif sends >= 3 and overdue and not req.get("escalated_at"):
            escalated.append(p.get("name", ""))
            await db.service_providers.update_one(
                {"_id": p["_id"]},
                {"$set": {"w9_request.escalated_at": datetime.utcnow()}})
            try:
                await _send_admin_email(
                    db, f"🚨 Aplica retención del 24%: {p.get('name','')} ignoró 3 avisos de W-9",
                    f"<div style='font-family:system-ui'><p><b>{p.get('name','')}</b> "
                    f"({p.get('email','')}) recibió la solicitud de W-9 y 2 recordatorios "
                    f"y <b>no lo completó</b>.</p>"
                    f"<p>El IRS te obliga a aplicar <b>backup withholding del 24%</b> en sus "
                    f"próximos pagos hasta que entregue el W-9 (y depositarlo con el Form 945).</p>"
                    f"<p>También puedes llamarlo: {p.get('phone','sin teléfono')} — o pedirle el "
                    f"W-9 en papel.</p></div>")
            except Exception:
                pass
    if reminded or escalated:
        logger.info(f"[1099] W-9 sweep: {len(reminded)} recordatorios, {len(escalated)} escalados")
    return {"success": True, "reminded": reminded, "escalated": escalated}


@router.post('/admin/1099/w9-reminders/run')
async def manual_w9_sweep(request: Request):
    """Ejecuta manualmente el barrido de recordatorios de W-9."""
    await auth_admin(request)
    return await w9_reminder_sweep(get_db())


async def _w9_sweep_should_run(db) -> bool:
    """Una vez al día (10AM CT)."""
    now_ct = datetime.now(CT)
    if now_ct.hour != 10:
        return False
    cfg = await db.app_settings.find_one({"_id": "tax_1099_reminders"}) or {}
    last = cfg.get("w9_sweep_last_at")
    if last and isinstance(last, datetime):
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        if (datetime.now(timezone.utc) - last) < timedelta(hours=20):
            return False
    return True


@router.post('/admin/1099/providers/{provider_id}/request-w9')
async def request_w9(provider_id: str, request: Request):
    """Envía (o re-envía) al contratista el link del W-9 digital."""
    await auth_admin(request)
    db = get_db()
    p = await db.service_providers.find_one({"_id": provider_id})
    if not p:
        raise HTTPException(status_code=404, detail="Proveedor no encontrado")
    if not p.get("email"):
        raise HTTPException(status_code=400, detail="El proveedor no tiene email")
    # Re-envío manual: resetear el cooldown
    if (p.get("w9_request") or {}).get("sent_at"):
        await db.service_providers.update_one(
            {"_id": provider_id}, {"$unset": {"w9_request.sent_at": ""}})
        p = await db.service_providers.find_one({"_id": provider_id})
    if not await send_w9_request(db, p):
        raise HTTPException(status_code=502, detail="No se pudo enviar el email")
    return {"success": True, "message": f"Solicitud de W-9 enviada a {p['email']}"}


@router.get('/public/w9/{token}')
async def public_w9_get(token: str):
    db = get_db()
    p = await db.service_providers.find_one({"w9_request.token": token})
    if not p:
        raise HTTPException(status_code=404, detail="Enlace no válido")
    w9 = p.get("w9") or {}
    return {"success": True,
            "name": p.get("name", ""),
            "company": p.get("company_name", ""),
            "lang": p.get("language_pref", "es"),
            "completed": bool((p.get("w9_request") or {}).get("completed_at")),
            "prefill": {k: w9.get(k, "") for k in
                        ("legal_name", "business_name", "tax_classification",
                         "tin_type", "address", "city", "state", "zip")}}


@router.post('/public/w9/{token}')
async def public_w9_submit(token: str, request: Request):
    """El contratista envía su W-9 desde el formulario móvil."""
    db = get_db()
    p = await db.service_providers.find_one({"w9_request.token": token})
    if not p:
        raise HTTPException(status_code=404, detail="Enlace no válido")
    data = await request.json()
    legal_name = str(data.get("legal_name", "")).strip()
    tin = str(data.get("tin", "")).replace("-", "").replace(" ", "")
    tin_type = data.get("tin_type", "ssn")
    address = str(data.get("address", "")).strip()
    city = str(data.get("city", "")).strip()
    state = str(data.get("state", "")).strip().upper()[:2]
    zipc = str(data.get("zip", "")).strip()
    if not legal_name:
        raise HTTPException(status_code=422, detail="Nombre legal requerido")
    if not (tin.isdigit() and len(tin) == 9):
        raise HTTPException(status_code=422, detail="El SSN/EIN debe tener 9 dígitos")
    if not (address and city and state and zipc):
        raise HTTPException(status_code=422, detail="Dirección completa requerida")
    if not data.get("certified"):
        raise HTTPException(status_code=422, detail="Debes certificar la información")
    w9 = {
        "legal_name": legal_name,
        "business_name": str(data.get("business_name", "")).strip(),
        "tax_classification": str(data.get("tax_classification", "individual")).strip(),
        "tin_type": tin_type if tin_type in ("ssn", "ein") else "ssn",
        "tin": tin,
        "address": address, "city": city, "state": state, "zip": zipc,
        "signature": str(data.get("signature", legal_name)).strip()[:80],
        "signed_at": datetime.utcnow(),
        "source": "digital_form",
        "updated_at": datetime.utcnow(),
    }
    await db.service_providers.update_one(
        {"_id": p["_id"]},
        {"$set": {"w9": w9, "w9_request.completed_at": datetime.utcnow()}})
    # Avisar al admin
    try:
        await _send_admin_email(
            db, f"✅ W-9 recibido: {p.get('name','')}",
            f"<div style='font-family:system-ui'><p><b>{p.get('name','')}</b> completó su W-9 digital.</p>"
            f"<p>Nombre legal: <b>{legal_name}</b><br/>TIN: ***-**-{tin[-4:]} ({w9['tin_type'].upper()})<br/>"
            f"Dirección: {address}, {city}, {state} {zipc}</p>"
            f"<p>Ya puedes generar su 1099-NEC desde el panel.</p></div>")
    except Exception:
        pass
    logger.info(f"[1099] W-9 digital completado por {p.get('name')}")
    return {"success": True}


async def tax_1099_deadline_loop():
    logger.info("🚀 1099-NEC cron started (deadline Jan 10/28 + Copy B Jan 15 + W-9 sweep diario)")
    while True:
        try:
            db = get_db()
            if db is not None:
                if await _deadline_should_run(db):
                    await send_1099_deadline_reminder(db)
                if await _copyb_should_run(db):
                    await send_copyb_batch(db)
                if await _w9_sweep_should_run(db):
                    await w9_reminder_sweep(db)
                    await db.app_settings.update_one(
                        {"_id": "tax_1099_reminders"},
                        {"$set": {"w9_sweep_last_at": datetime.now(timezone.utc)}},
                        upsert=True)
        except Exception as e:
            logger.exception(f"[1099] deadline loop error: {e}")
        await asyncio.sleep(DEADLINE_CHECK_SECONDS)
