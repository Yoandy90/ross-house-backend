"""
Utility Payments Router — Ross House Rentals
Handles bill payments for utility services (electricity, gas, water, etc.)
Supports walk-in payments at the office and tracks commissions.
"""
import logging
from datetime import datetime, timedelta
from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request

router = APIRouter()
logger = logging.getLogger(__name__)


def get_db():
    from rental.shared import get_db as _get_db
    return _get_db()


async def auth_admin(request: Request):
    from rental.shared import auth_admin as _auth_admin
    return await _auth_admin(request)


# ═══════════════════════════════════════════════════════════════
# UTILITY PROVIDERS (configurable)
# ═══════════════════════════════════════════════════════════════
DEFAULT_PROVIDERS = [
    {"id": "xcel_energy", "name": "Xcel Energy", "type": "electricity", "icon": "⚡", "color": "#F59E0B"},
    {"id": "atmos_energy", "name": "Atmos Energy", "type": "gas", "icon": "🔥", "color": "#EF4444"},
    {"id": "city_of_dumas_water", "name": "Ciudad de Dumas - Agua", "type": "water", "icon": "💧", "color": "#3B82F6"},
    {"id": "windstream", "name": "Windstream / Kinetic", "type": "internet", "icon": "🌐", "color": "#8B5CF6"},
    {"id": "sparklight", "name": "Sparklight", "type": "internet", "icon": "🌐", "color": "#8B5CF6"},
    {"id": "plains_internet", "name": "Plains Internet", "type": "internet", "icon": "🌐", "color": "#8B5CF6"},
    {"id": "att", "name": "AT&T", "type": "phone", "icon": "📱", "color": "#06B6D4"},
    {"id": "tmobile", "name": "T-Mobile", "type": "phone", "icon": "📱", "color": "#EC4899"},
    {"id": "directv", "name": "DirecTV", "type": "tv", "icon": "📺", "color": "#6366F1"},
    {"id": "other", "name": "Otro Servicio", "type": "other", "icon": "📄", "color": "#6B7280"},
]


# ═══════════════════════════════════════════════════════════════
# GET PROVIDERS
# ═══════════════════════════════════════════════════════════════
@router.get('/admin/utility-payments/providers')
async def list_providers(request: Request):
    """List all configured utility providers"""
    await auth_admin(request)
    # Check DB for custom providers
    custom = await get_db().utility_providers.find().to_list(100)
    if custom:
        providers = []
        for p in custom:
            p['_id'] = str(p['_id'])
            providers.append(p)
        return {"success": True, "providers": providers}
    return {"success": True, "providers": DEFAULT_PROVIDERS}


# ═══════════════════════════════════════════════════════════════
# CREATE UTILITY PAYMENT
# ═══════════════════════════════════════════════════════════════
@router.post('/admin/utility-payments')
async def create_utility_payment(request: Request):
    """Register a walk-in or phone utility bill payment"""
    user = await auth_admin(request)
    data = await request.json()

    required = ['provider_id', 'provider_name', 'customer_name', 'account_number', 'amount', 'payment_method']
    for field in required:
        if not data.get(field):
            raise HTTPException(status_code=400, detail=f"Campo requerido: {field}")

    amount = float(data.get('amount', 0))
    convenience_fee = float(data.get('convenience_fee', 0))
    commission = float(data.get('commission', 0))

    # Generate transaction number
    today = datetime.utcnow().strftime('%Y%m%d')
    count = await get_db().utility_payments.count_documents({
        "created_at": {"$gte": datetime.utcnow().replace(hour=0, minute=0, second=0)}
    })
    tx_number = f"UP-{today}-{count + 1:04d}"

    payment = {
        "tx_number": tx_number,
        "provider_id": data['provider_id'],
        "provider_name": data['provider_name'],
        "provider_type": data.get('provider_type', 'other'),
        "customer_name": data['customer_name'],
        "customer_phone": data.get('customer_phone', ''),
        "customer_email": data.get('customer_email', ''),
        "account_number": data['account_number'],
        "amount": amount,
        "convenience_fee": convenience_fee,
        "commission": commission,
        "total_collected": amount + convenience_fee,
        "payment_method": data['payment_method'],
        "payment_date": data.get('payment_date', datetime.utcnow().strftime('%Y-%m-%d')),
        "reference_number": data.get('reference_number', ''),
        "notes": data.get('notes', ''),
        "status": "completed",
        "is_tenant": data.get('is_tenant', False),
        "tenant_id": data.get('tenant_id', ''),
        "processed_by": user.get('email', ''),
        "created_at": datetime.utcnow(),
    }

    result = await get_db().utility_payments.insert_one(payment)
    payment['_id'] = str(result.inserted_id)

    return {
        "success": True,
        "payment": payment,
        "tx_number": tx_number,
        "message": f"Pago {tx_number} registrado exitosamente"
    }


# ═══════════════════════════════════════════════════════════════
# LIST UTILITY PAYMENTS
# ═══════════════════════════════════════════════════════════════
@router.get('/admin/utility-payments')
async def list_utility_payments(request: Request):
    """List all utility payments with filters"""
    await auth_admin(request)
    params = dict(request.query_params)

    query = {}
    if params.get('search'):
        s = params['search']
        query["$or"] = [
            {"customer_name": {"$regex": s, "$options": "i"}},
            {"account_number": {"$regex": s, "$options": "i"}},
            {"tx_number": {"$regex": s, "$options": "i"}},
            {"provider_name": {"$regex": s, "$options": "i"}},
        ]
    if params.get('provider_id'):
        query['provider_id'] = params['provider_id']
    if params.get('date_from'):
        query.setdefault('payment_date', {})['$gte'] = params['date_from']
    if params.get('date_to'):
        query.setdefault('payment_date', {})['$lte'] = params['date_to']

    payments = await get_db().utility_payments.find(query).sort("created_at", -1).to_list(500)
    for p in payments:
        p['_id'] = str(p['_id'])

    return {"success": True, "payments": payments, "count": len(payments)}


# ═══════════════════════════════════════════════════════════════
# DASHBOARD STATS
# ═══════════════════════════════════════════════════════════════
@router.get('/admin/utility-payments/stats')
async def utility_payment_stats(request: Request):
    """Get utility payment statistics"""
    await auth_admin(request)

    now = datetime.utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=today_start.weekday())
    month_start = today_start.replace(day=1)

    # Today's stats
    today_payments = await get_db().utility_payments.find(
        {"created_at": {"$gte": today_start}}
    ).to_list(1000)

    # This week
    week_payments = await get_db().utility_payments.find(
        {"created_at": {"$gte": week_start}}
    ).to_list(1000)

    # This month
    month_payments = await get_db().utility_payments.find(
        {"created_at": {"$gte": month_start}}
    ).to_list(5000)

    def calc_stats(payments):
        total_amount = sum(p.get('amount', 0) for p in payments)
        total_fees = sum(p.get('convenience_fee', 0) for p in payments)
        total_commission = sum(p.get('commission', 0) for p in payments)
        return {
            "count": len(payments),
            "total_amount": round(total_amount, 2),
            "total_fees": round(total_fees, 2),
            "total_commission": round(total_commission, 2),
            "total_revenue": round(total_fees + total_commission, 2),
        }

    # By provider this month
    provider_stats = {}
    for p in month_payments:
        pid = p.get('provider_name', 'Otro')
        if pid not in provider_stats:
            provider_stats[pid] = {"count": 0, "total": 0}
        provider_stats[pid]["count"] += 1
        provider_stats[pid]["total"] += p.get('amount', 0)

    return {
        "success": True,
        "today": calc_stats(today_payments),
        "week": calc_stats(week_payments),
        "month": calc_stats(month_payments),
        "by_provider": provider_stats,
    }


# ═══════════════════════════════════════════════════════════════
# GET SINGLE PAYMENT (for receipt)
# ═══════════════════════════════════════════════════════════════
@router.get('/admin/utility-payments/{payment_id}')
async def get_utility_payment(payment_id: str, request: Request):
    """Get a single utility payment detail"""
    await auth_admin(request)
    payment = await get_db().utility_payments.find_one({"_id": ObjectId(payment_id)})
    if not payment:
        raise HTTPException(status_code=404, detail="Pago no encontrado")
    payment['_id'] = str(payment['_id'])
    return {"success": True, "payment": payment}


# ═══════════════════════════════════════════════════════════════
# DELETE PAYMENT
# ═══════════════════════════════════════════════════════════════
@router.delete('/admin/utility-payments/{payment_id}')
async def delete_utility_payment(payment_id: str, request: Request):
    """Delete a utility payment record"""
    await auth_admin(request)
    result = await get_db().utility_payments.delete_one({"_id": ObjectId(payment_id)})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Pago no encontrado")
    return {"success": True, "message": "Pago eliminado"}


# ═══════════════════════════════════════════════════════════════
# GENERATE RECEIPT PDF
# ═══════════════════════════════════════════════════════════════
@router.get('/admin/utility-payments/{payment_id}/receipt')
async def generate_receipt(payment_id: str, request: Request):
    """Generate a printable receipt PDF for a utility payment (premium design)."""
    await auth_admin(request)
    payment = await get_db().utility_payments.find_one({"_id": ObjectId(payment_id)})
    if not payment:
        raise HTTPException(status_code=404, detail="Pago no encontrado")

    import io, base64, os
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.lib import colors as _colors
    from reportlab.lib.colors import HexColor, white
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable, Image as RLImage
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT

    BRAND_RED = HexColor('#C8102E')
    BRAND_CHARCOAL = HexColor('#1F2937')
    BORDER_GRAY = HexColor('#E5E7EB')
    MUTED = HexColor('#6B7280')
    GREEN = HexColor('#10B981')

    # Locate logo
    logo_path = None
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for name in ['ross_house_logo.png', 'company_logo.png']:
        p = os.path.join(base, 'assets', name)
        if os.path.exists(p):
            logo_path = p
            break

    buf = io.BytesIO()
    # 4.5" x 9" thermal-style receipt
    doc = SimpleDocTemplate(
        buf, pagesize=(4.5 * inch, 9 * inch),
        topMargin=0.25 * inch, bottomMargin=0.25 * inch,
        leftMargin=0.25 * inch, rightMargin=0.25 * inch,
        title=f"Recibo Servicio — {payment.get('tx_number','')}",
    )

    base_styles = getSampleStyleSheet()
    S = {
        'hero_title': ParagraphStyle('hero_title', parent=base_styles['Normal'],
            fontSize=13, leading=15, textColor=white,
            fontName='Helvetica-Bold', alignment=TA_LEFT),
        'hero_sub': ParagraphStyle('hero_sub', parent=base_styles['Normal'],
            fontSize=7, leading=9, textColor=HexColor('#FFD1D1'),
            fontName='Helvetica', alignment=TA_LEFT),
        'amount_big': ParagraphStyle('amount_big', parent=base_styles['Normal'],
            fontSize=28, leading=32, textColor=BRAND_CHARCOAL,
            fontName='Helvetica-Bold', alignment=TA_CENTER),
        'amount_lbl': ParagraphStyle('amount_lbl', parent=base_styles['Normal'],
            fontSize=7, leading=10, textColor=MUTED,
            fontName='Helvetica', alignment=TA_CENTER),
        'badge': ParagraphStyle('badge', parent=base_styles['Normal'],
            fontSize=8, leading=10, textColor=white,
            fontName='Helvetica-Bold', alignment=TA_CENTER),
        'section': ParagraphStyle('section', parent=base_styles['Normal'],
            fontSize=7, leading=10, textColor=MUTED,
            fontName='Helvetica-Bold'),
        'lbl': ParagraphStyle('lbl', parent=base_styles['Normal'],
            fontSize=7.5, leading=10, textColor=MUTED,
            fontName='Helvetica'),
        'val': ParagraphStyle('val', parent=base_styles['Normal'],
            fontSize=9, leading=12, textColor=BRAND_CHARCOAL,
            fontName='Helvetica-Bold'),
        'val_small': ParagraphStyle('val_small', parent=base_styles['Normal'],
            fontSize=8, leading=10, textColor=BRAND_CHARCOAL,
            fontName='Helvetica'),
        'total_lbl': ParagraphStyle('total_lbl', parent=base_styles['Normal'],
            fontSize=10, leading=13, textColor=BRAND_CHARCOAL,
            fontName='Helvetica-Bold', alignment=TA_LEFT),
        'total_val': ParagraphStyle('total_val', parent=base_styles['Normal'],
            fontSize=13, leading=16, textColor=BRAND_RED,
            fontName='Helvetica-Bold', alignment=TA_RIGHT),
        'footer': ParagraphStyle('footer', parent=base_styles['Normal'],
            fontSize=6.5, leading=9, textColor=MUTED,
            fontName='Helvetica', alignment=TA_CENTER),
    }

    elements = []

    # ─── HERO with logo + title ───
    if logo_path:
        try:
            logo = RLImage(logo_path, width=0.7 * inch, height=0.7 * inch, kind='proportional')
        except Exception:
            logo = Paragraph("ROSS<br/>HOUSE", S['hero_title'])
    else:
        logo = Paragraph("ROSS<br/>HOUSE", S['hero_title'])

    hero_title = [
        Paragraph("RECIBO", S['hero_title']),
        Spacer(1, 1),
        Paragraph("DE SERVICIO", S['hero_title']),
        Spacer(1, 2),
        Paragraph("Utility Payment Receipt", S['hero_sub']),
    ]
    hero = Table(
        [[logo, hero_title]],
        colWidths=[0.85 * inch, 3.0 * inch],
        rowHeights=[0.75 * inch],
    )
    hero.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), BRAND_CHARCOAL),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('ALIGN', (0, 0), (0, 0), 'CENTER'),
    ]))
    elements.append(hero)
    elements.append(HRFlowable(width="100%", thickness=3, color=BRAND_RED))
    elements.append(Spacer(1, 10))

    # ─── Receipt number / date pill ───
    tx_num = payment.get('tx_number') or str(payment_id)[-8:]
    pay_date = str(payment.get('payment_date', ''))[:10]
    meta = Table(
        [[
            Paragraph(f"<b>Recibo N.°</b><br/>{tx_num}", S['val_small']),
            Paragraph(f"<b>Fecha</b><br/>{pay_date}", S['val_small']),
        ]],
        colWidths=[2.0 * inch, 1.95 * inch],
    )
    meta.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), HexColor('#F9FAFB')),
        ('BOX', (0, 0), (-1, -1), 0.5, BORDER_GRAY),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
    ]))
    elements.append(meta)
    elements.append(Spacer(1, 12))

    # ─── Total + PAGADO badge ───
    amount = float(payment.get('amount', 0) or 0)
    fee = float(payment.get('convenience_fee', 0) or 0)
    total = float(payment.get('total_collected', amount + fee) or 0)

    elements.append(Paragraph("TOTAL PAGADO  •  TOTAL PAID", S['amount_lbl']))
    elements.append(Spacer(1, 2))
    elements.append(Paragraph(f"${total:,.2f}", S['amount_big']))
    elements.append(Spacer(1, 4))
    badge = Table([[Paragraph("✓ PAGADO  •  PAID", S['badge'])]],
                  colWidths=[1.7 * inch], rowHeights=[0.22 * inch])
    badge.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), GREEN),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
    ]))
    badge_wrap = Table([[badge]], colWidths=[3.95 * inch])
    badge_wrap.setStyle(TableStyle([('ALIGN', (0, 0), (-1, -1), 'CENTER'), ('LEFTPADDING',(0,0),(-1,-1),0), ('RIGHTPADDING',(0,0),(-1,-1),0)]))
    elements.append(badge_wrap)
    elements.append(Spacer(1, 14))

    # ─── Provider / Customer details ───
    method_map = {'cash': 'Efectivo', 'check': 'Cheque', 'card': 'Tarjeta', 'money_order': 'Money Order'}
    pm = method_map.get(payment.get('payment_method', ''), payment.get('payment_method', ''))

    rows = [
        [Paragraph("Proveedor", S['lbl']), Paragraph(payment.get('provider_name', '—'), S['val'])],
        [Paragraph("Cliente", S['lbl']), Paragraph(payment.get('customer_name', '—'), S['val'])],
        [Paragraph("N.° Cuenta", S['lbl']), Paragraph(payment.get('account_number', '—'), S['val'])],
        [Paragraph("Método de Pago", S['lbl']), Paragraph(pm, S['val'])],
    ]
    if payment.get('reference_number'):
        rows.append([Paragraph("Ref. Externa", S['lbl']), Paragraph(payment['reference_number'], S['val_small'])])

    details = Table(rows, colWidths=[1.3 * inch, 2.65 * inch])
    details.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('LINEBELOW', (0, 0), (-1, -2), 0.4, BORDER_GRAY),
    ]))
    elements.append(details)
    elements.append(Spacer(1, 12))

    # ─── Money breakdown ───
    money_rows = [
        [Paragraph("Monto factura", S['lbl']), Paragraph(f"${amount:,.2f}", S['val'])],
    ]
    if fee > 0:
        money_rows.append([Paragraph("Cargo por servicio", S['lbl']), Paragraph(f"${fee:,.2f}", S['val'])])

    money = Table(money_rows, colWidths=[2.55 * inch, 1.4 * inch])
    money.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    elements.append(money)

    # Total bar (highlighted)
    total_bar = Table(
        [[Paragraph("TOTAL PAGADO", S['total_lbl']),
          Paragraph(f"${total:,.2f}", S['total_val'])]],
        colWidths=[2.4 * inch, 1.55 * inch],
    )
    total_bar.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), HexColor('#FFF5F7')),
        ('LINEABOVE', (0, 0), (-1, 0), 2, BRAND_RED),
        ('LINEBELOW', (0, 0), (-1, 0), 2, BRAND_RED),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    elements.append(Spacer(1, 6))
    elements.append(total_bar)
    elements.append(Spacer(1, 12))

    # Notes
    if payment.get('notes'):
        elements.append(Paragraph("<b>Nota:</b>", S['lbl']))
        elements.append(Paragraph(payment['notes'], S['val_small']))
        elements.append(Spacer(1, 8))

    # ─── Footer ───
    elements.append(HRFlowable(width="100%", thickness=0.5, color=BORDER_GRAY))
    elements.append(Spacer(1, 6))
    elements.append(Paragraph("<b>Ross House Rentals LLC</b>", S['footer']))
    elements.append(Paragraph("Dumas, TX  •  Centro de Pagos de Servicios", S['footer']))
    elements.append(Spacer(1, 4))
    elements.append(Paragraph("Gracias por su pago. Este recibo es su comprobante oficial.", S['footer']))
    if payment.get('processed_by'):
        elements.append(Paragraph(f"Procesado por: {payment['processed_by']}", S['footer']))

    doc.build(elements)
    pdf_b64 = base64.b64encode(buf.getvalue()).decode()

    return {
        "success": True,
        "pdf_base64": pdf_b64,
        "filename": f"Recibo_Servicios_{payment.get('tx_number', payment_id)}.pdf"
    }
