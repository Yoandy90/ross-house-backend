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
import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, Response

from rental.shared import get_db, auth_admin

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


def _build_1099_pdf(payer: dict, provider: dict, w9: dict, amount: float, year: int) -> bytes:
    """Formulario sustituto 1099-NEC Copy B (para el proveedor)."""
    from reportlab.lib.pagesizes import LETTER
    from reportlab.lib.units import inch
    from reportlab.pdfgen import canvas as rl_canvas

    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=LETTER)
    W, H = LETTER
    m = 0.6 * inch
    top = H - m

    def box(x, y, w, h, label="", value="", vsize=10, bold=True):
        c.setLineWidth(0.8)
        c.rect(x, y - h, w, h)
        if label:
            c.setFont("Helvetica", 6.5)
            c.drawString(x + 4, y - 10, label)
        if value:
            c.setFont("Helvetica-Bold" if bold else "Helvetica", vsize)
            c.drawString(x + 4, y - h + 6, str(value))

    # Encabezado
    c.setFont("Helvetica-Bold", 16)
    c.drawString(m, top, f"Form 1099-NEC")
    c.setFont("Helvetica", 9)
    c.drawString(m + 130, top + 2, f"Nonemployee Compensation — {year}")
    c.setFont("Helvetica-Bold", 10)
    c.drawRightString(W - m, top, "Copy B — For Recipient")
    c.setFont("Helvetica", 7)
    c.drawRightString(W - m, top - 11, "(Substitute statement — IRS Pub. 1179)")
    y = top - 30

    colw = (W - 2 * m) / 2
    # Payer
    payer_txt = (f"{payer.get('name','')}\n{payer.get('address','')}\n"
                 f"{payer.get('city','')}, {payer.get('state','')} {payer.get('zip','')}\n"
                 f"{payer.get('phone','')}")
    box(m, y, colw, 78, "PAYER'S name, street address, city, state, ZIP, phone")
    c.setFont("Helvetica", 9)
    ty = y - 24
    for line in payer_txt.split("\n"):
        c.drawString(m + 4, ty, line)
        ty -= 12
    # Box 1 (importe)
    box(m + colw, y, colw / 2, 39, "1. Nonemployee compensation", f"$ {amount:,.2f}", 12)
    box(m + colw + colw / 2, y, colw / 2, 39, "4. Federal income tax withheld", "$ 0.00")
    box(m + colw, y - 39, colw, 39, "Tax year", str(year))
    y -= 88

    box(m, y, colw / 2, 34, "PAYER'S TIN (EIN)", payer.get("ein", ""))
    box(m + colw / 2, y, colw / 2, 34, "RECIPIENT'S TIN",
        _mask_tin(w9.get("tin", "")) or "(pendiente W-9)")
    box(m + colw, y, colw, 34, "Account number", provider.get("_id", "")[:18])
    y -= 44

    rec_name = w9.get("legal_name") or provider.get("name", "")
    rec_biz = w9.get("business_name", "")
    rec_addr = w9.get("address", "") or ""
    rec_csz = f"{w9.get('city','')}, {w9.get('state','')} {w9.get('zip','')}".strip(", ")
    box(m, y, W - 2 * m, 68, "RECIPIENT'S name, business name, street address, city, state, ZIP")
    c.setFont("Helvetica-Bold", 10)
    c.drawString(m + 4, y - 24, rec_name)
    c.setFont("Helvetica", 9)
    ty = y - 37
    for line in [rec_biz, rec_addr, rec_csz]:
        if line:
            c.drawString(m + 4, ty, line)
            ty -= 12
    y -= 90

    c.setFont("Helvetica", 7.5)
    for i, line in enumerate([
        "This is important tax information and is being furnished to the IRS. If you are required to file a return,",
        "a negligence penalty or other sanction may be imposed on you if this income is taxable and the IRS",
        "determines that it has not been reported. / Esta es información fiscal importante que se envía al IRS.",
        f"Generado por Ross House Rentals — {datetime.utcnow().strftime('%Y-%m-%d')}",
    ]):
        c.drawString(m, y - i * 10, line)

    c.showPage()
    c.save()
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
    pdf = _build_1099_pdf(payer, p, p.get("w9") or {}, amount, year)

    import base64
    import os
    from sendgrid import SendGridAPIClient
    from sendgrid.helpers.mail import (Mail, Attachment, FileContent, FileName,
                                       FileType, Disposition)
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
    msg = Mail(from_email=(os.environ.get("SENDGRID_FROM_EMAIL", "info@rosshouserentals.com"),
                           "Ross House Rentals"),
               to_emails=p["email"], subject=subject,
               html_content=f"<pre style='font-family:system-ui'>{body}</pre>")
    att = Attachment(FileContent(base64.b64encode(pdf).decode()),
                     FileName(f"1099-NEC-{year}.pdf"), FileType("application/pdf"),
                     Disposition("attachment"))
    msg.attachment = att
    resp = SendGridAPIClient(os.environ["SENDGRID_API_KEY"]).send(msg)
    if resp.status_code not in (200, 201, 202):
        raise HTTPException(status_code=502, detail=f"SendGrid HTTP {resp.status_code}")
    await db.service_providers.update_one(
        {"_id": provider_id},
        {"$set": {f"form_1099_sent.{year}": datetime.utcnow()}})
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
