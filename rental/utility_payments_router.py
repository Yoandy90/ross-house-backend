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
    from rental.shared import get_rental_db
    return get_rental_db()


async def auth_admin(request: Request):
    from rental.shared import verify_admin_token
    return await verify_admin_token(request)


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
    """Generate a printable receipt PDF for a utility payment"""
    await auth_admin(request)
    payment = await get_db().utility_payments.find_one({"_id": ObjectId(payment_id)})
    if not payment:
        raise HTTPException(status_code=404, detail="Pago no encontrado")

    import io, base64
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.lib.colors import HexColor, white
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
    from reportlab.lib.enums import TA_CENTER

    buf = io.BytesIO()
    # Half-page receipt
    doc = SimpleDocTemplate(buf, pagesize=(4.5*inch, 8*inch),
                            topMargin=0.3*inch, bottomMargin=0.3*inch,
                            leftMargin=0.3*inch, rightMargin=0.3*inch)

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle('RTitle', fontName='Helvetica-Bold', fontSize=14, alignment=TA_CENTER, textColor=HexColor('#C8102E')))
    styles.add(ParagraphStyle('RSubtitle', fontName='Helvetica', fontSize=8, alignment=TA_CENTER, textColor=HexColor('#6B7280')))
    styles.add(ParagraphStyle('RBody', fontName='Helvetica', fontSize=9, textColor=HexColor('#374151'), leading=13))
    styles.add(ParagraphStyle('RBold', fontName='Helvetica-Bold', fontSize=9, textColor=HexColor('#1F2937'), leading=13))
    styles.add(ParagraphStyle('RSmall', fontName='Helvetica', fontSize=7, textColor=HexColor('#9CA3AF'), alignment=TA_CENTER))

    elements = []
    elements.append(Paragraph("ROSS HOUSE RENTALS LLC", styles['RTitle']))
    elements.append(Paragraph("Centro de Pagos de Servicios", styles['RSubtitle']))
    elements.append(Paragraph("Dumas, TX", styles['RSubtitle']))
    elements.append(Spacer(1, 8))
    elements.append(HRFlowable(width="100%", thickness=1, color=HexColor('#C8102E')))
    elements.append(Spacer(1, 6))
    elements.append(Paragraph(f"<b>Recibo #{payment.get('tx_number', '')}</b>", styles['RBold']))
    elements.append(Paragraph(f"Fecha: {payment.get('payment_date', '')}", styles['RBody']))
    elements.append(Spacer(1, 8))

    data = [
        ['Proveedor:', payment.get('provider_name', '')],
        ['Cliente:', payment.get('customer_name', '')],
        ['No. Cuenta:', payment.get('account_number', '')],
        ['Método:', {'cash': 'Efectivo', 'check': 'Cheque', 'card': 'Tarjeta', 'money_order': 'Money Order'}.get(payment.get('payment_method', ''), payment.get('payment_method', ''))],
    ]
    t = Table(data, colWidths=[90, 200])
    t.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    elements.append(t)
    elements.append(Spacer(1, 8))
    elements.append(HRFlowable(width="100%", thickness=0.5, color=HexColor('#D1D5DB')))
    elements.append(Spacer(1, 6))

    amount = payment.get('amount', 0)
    fee = payment.get('convenience_fee', 0)
    total = payment.get('total_collected', amount + fee)

    money_data = [
        ['Monto de Factura:', f"${amount:,.2f}"],
        ['Cargo por Servicio:', f"${fee:,.2f}"],
    ]
    if fee > 0:
        money_data.append(['', ''])
    money_data.append(['TOTAL PAGADO:', f"${total:,.2f}"])

    mt = Table(money_data, colWidths=[150, 140])
    mt.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, -2), 'Helvetica'),
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('FONTSIZE', (0, -1), (-1, -1), 12),
        ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
        ('LINEABOVE', (0, -1), (-1, -1), 1, HexColor('#C8102E')),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, -1), (-1, -1), 6),
    ]))
    elements.append(mt)
    elements.append(Spacer(1, 10))

    if payment.get('reference_number'):
        elements.append(Paragraph(f"Ref: {payment['reference_number']}", styles['RBody']))
    if payment.get('notes'):
        elements.append(Paragraph(f"Nota: {payment['notes']}", styles['RBody']))

    elements.append(Spacer(1, 12))
    elements.append(HRFlowable(width="100%", thickness=0.5, color=HexColor('#D1D5DB')))
    elements.append(Spacer(1, 6))
    elements.append(Paragraph("Gracias por su pago. Este recibo es su comprobante.", styles['RSmall']))
    elements.append(Paragraph("Ross House Rentals LLC — Dumas, TX", styles['RSmall']))
    elements.append(Paragraph(f"Procesado por: {payment.get('processed_by', '')}", styles['RSmall']))

    doc.build(elements)
    pdf_b64 = base64.b64encode(buf.getvalue()).decode()

    return {
        "success": True,
        "pdf_base64": pdf_b64,
        "filename": f"Recibo_{payment.get('tx_number', payment_id)}.pdf"
    }
