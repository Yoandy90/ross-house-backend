#!/usr/bin/env python3
"""
Ross House Rentals - APIs Pendientes y Análisis de Propiedad
=============================================================
Genera PDF con todas las APIs faltantes y análisis de 217 W 7th St
"""

import os
import sys
from datetime import datetime
from io import BytesIO

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env'))

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, 
    PageBreak, ListFlowable, ListItem, HRFlowable
)
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY

import sendgrid
from sendgrid.helpers.mail import Mail, Attachment, FileContent, FileName, FileType, Disposition
import base64


def create_apis_report_pdf():
    """Generate the APIs and property analysis PDF"""
    
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=0.75*inch,
        leftMargin=0.75*inch,
        topMargin=0.75*inch,
        bottomMargin=0.75*inch
    )
    
    styles = getSampleStyleSheet()
    
    title_style = ParagraphStyle(
        'CustomTitle', parent=styles['Heading1'],
        fontSize=22, spaceAfter=20, alignment=TA_CENTER,
        textColor=colors.HexColor('#1a365d'), fontName='Helvetica-Bold'
    )
    
    heading_style = ParagraphStyle(
        'CustomHeading', parent=styles['Heading2'],
        fontSize=14, spaceBefore=15, spaceAfter=8,
        textColor=colors.HexColor('#2c5282'), fontName='Helvetica-Bold'
    )
    
    subheading_style = ParagraphStyle(
        'SubHeading', parent=styles['Heading3'],
        fontSize=12, spaceBefore=10, spaceAfter=5,
        textColor=colors.HexColor('#4a5568'), fontName='Helvetica-Bold'
    )
    
    body_style = ParagraphStyle(
        'CustomBody', parent=styles['Normal'],
        fontSize=10, spaceAfter=8, alignment=TA_JUSTIFY, leading=14
    )
    
    small_style = ParagraphStyle(
        'Small', parent=styles['Normal'],
        fontSize=9, textColor=colors.HexColor('#666666'), leading=12
    )
    
    story = []
    
    # ═══════════════════════════════════════════════════════════════
    # COVER PAGE
    # ═══════════════════════════════════════════════════════════════
    story.append(Spacer(1, 1*inch))
    story.append(Paragraph("ROSS HOUSE RENTALS LLC", title_style))
    story.append(HRFlowable(width="60%", thickness=2, color=colors.HexColor('#2c5282')))
    story.append(Spacer(1, 0.3*inch))
    story.append(Paragraph("Reporte de APIs Pendientes<br/>& Análisis de Propiedad", 
                          ParagraphStyle('Subtitle', parent=body_style, fontSize=14, alignment=TA_CENTER)))
    story.append(Spacer(1, 0.5*inch))
    story.append(Paragraph(f"Preparado: {datetime.now().strftime('%d de Junio, %Y')}", 
                          ParagraphStyle('Date', parent=body_style, alignment=TA_CENTER, textColor=colors.gray)))
    story.append(PageBreak())
    
    # ═══════════════════════════════════════════════════════════════
    # SECTION 1: APIs PENDIENTES
    # ═══════════════════════════════════════════════════════════════
    story.append(Paragraph("PARTE 1: APIS Y SERVICIOS PENDIENTES DE IMPLEMENTAR", title_style))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor('#e2e8f0')))
    story.append(Spacer(1, 0.2*inch))
    
    story.append(Paragraph(
        """A continuación se detallan todas las APIs y servicios de terceros que aún no están 
        completamente implementados en la plataforma Ross House Rentals. Cada sección incluye 
        el proveedor, costo, instrucciones de obtención, y prioridad recomendada.""",
        body_style
    ))
    
    # ─────────────────────────────────────────────────────────────────
    # 1. STRIPE CONNECT
    # ─────────────────────────────────────────────────────────────────
    story.append(Paragraph("1. STRIPE CONNECT - Procesamiento de Pagos", heading_style))
    story.append(Paragraph("<b>Estado Actual:</b> Parcialmente implementado (requiere API keys de producción)", body_style))
    story.append(Paragraph("<b>Prioridad:</b> ⭐⭐⭐⭐⭐ CRÍTICA", body_style))
    
    stripe_data = [
        ['Concepto', 'Detalle'],
        ['Costo de Setup', 'GRATIS - Sin tarifas de configuración'],
        ['Tarifa por Transacción (Tarjeta)', '2.9% + $0.30 por transacción'],
        ['Tarifa por ACH/Banco', '0.8% (máx $5.00) por transacción'],
        ['Instant Payouts', '1% del volumen del payout'],
        ['Tarjetas Internacionales', '+1% adicional'],
        ['Conversión de Moneda', '+1% adicional'],
        ['Mensualidad', 'GRATIS'],
    ]
    
    stripe_table = Table(stripe_data, colWidths=[2.5*inch, 4*inch])
    stripe_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#635bff')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cbd5e0')),
        ('BACKGROUND', (0, 1), (0, -1), colors.HexColor('#f0f0ff')),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]))
    story.append(stripe_table)
    story.append(Spacer(1, 0.1*inch))
    
    story.append(Paragraph("<b>Cómo Obtenerlo:</b>", subheading_style))
    stripe_steps = ListFlowable([
        ListItem(Paragraph("Ir a <b>stripe.com/connect</b> y crear cuenta de plataforma", body_style)),
        ListItem(Paragraph("Verificar identidad del negocio (EIN, dirección, representante legal)", body_style)),
        ListItem(Paragraph("Activar modo Live y obtener API Keys de producción", body_style)),
        ListItem(Paragraph("Configurar webhooks para notificaciones de pagos", body_style)),
    ], bulletType='bullet', start='→')
    story.append(stripe_steps)
    
    story.append(Paragraph("<b>Estimación Mensual (20 propiedades, $1,500 renta promedio):</b>", small_style))
    story.append(Paragraph("• Si inquilinos pagan con tarjeta: ~$900 + $6 = <b>$906/mes en fees</b>", small_style))
    story.append(Paragraph("• Si inquilinos pagan con ACH: ~$240 máx = <b>$240/mes en fees</b>", small_style))
    
    # ─────────────────────────────────────────────────────────────────
    # 2. PLAID - Verificación de Ingresos
    # ─────────────────────────────────────────────────────────────────
    story.append(Spacer(1, 0.2*inch))
    story.append(Paragraph("2. PLAID - Verificación de Ingresos y Conexión Bancaria", heading_style))
    story.append(Paragraph("<b>Estado Actual:</b> NO implementado (MOCKED)", body_style))
    story.append(Paragraph("<b>Prioridad:</b> ⭐⭐⭐⭐ ALTA", body_style))
    
    plaid_data = [
        ['Producto', 'Costo Estimado'],
        ['Auth (Verificación de cuenta)', '$0.30 - $0.50 por conexión'],
        ['Identity (Verificar identidad)', '$1.50 - $2.00 por verificación'],
        ['Income (Verificación de ingresos)', '$1.00 - $3.00 por verificación'],
        ['Assets (Verificación de activos)', '$3.00 - $5.00 por verificación'],
        ['Transactions (Historial)', '$0.10 - $0.25 por cuenta/mes'],
        ['Balance Check', '$0.10 - $0.20 por consulta'],
    ]
    
    plaid_table = Table(plaid_data, colWidths=[3*inch, 3.5*inch])
    plaid_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#00d066')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cbd5e0')),
        ('BACKGROUND', (0, 1), (0, -1), colors.HexColor('#e6fff0')),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]))
    story.append(plaid_table)
    story.append(Spacer(1, 0.1*inch))
    
    story.append(Paragraph("<b>Cómo Obtenerlo:</b>", subheading_style))
    plaid_steps = ListFlowable([
        ListItem(Paragraph("Ir a <b>plaid.com</b> y crear cuenta de desarrollador", body_style)),
        ListItem(Paragraph("Completar aplicación para acceso Production (requiere revisión)", body_style)),
        ListItem(Paragraph("Tiempo de aprobación: 1-2 semanas típicamente", body_style)),
        ListItem(Paragraph("Contacto: sales@plaid.com para volumen empresarial", body_style)),
    ], bulletType='bullet', start='→')
    story.append(plaid_steps)
    
    story.append(Paragraph("<b>Estimación:</b> $50-150/mes para 20-50 verificaciones de inquilinos", small_style))
    
    # ─────────────────────────────────────────────────────────────────
    # 3. TRANSUNION SMARTMOVE - Background Checks
    # ─────────────────────────────────────────────────────────────────
    story.append(Spacer(1, 0.2*inch))
    story.append(Paragraph("3. TRANSUNION SMARTMOVE - Verificación de Antecedentes", heading_style))
    story.append(Paragraph("<b>Estado Actual:</b> NO implementado (UI existe pero sin integración real)", body_style))
    story.append(Paragraph("<b>Prioridad:</b> ⭐⭐⭐⭐ ALTA", body_style))
    
    tu_data = [
        ['Paquete', 'Contenido', 'Precio'],
        ['SmartCheck Basic', 'ResidentScore + Antecedentes Criminales', '$25'],
        ['SmartCheck Plus', 'Basic + Reporte de Crédito + Evictions', '$40'],
        ['SmartCheck Premium', 'Plus + Income Insights + Verificación ID', '$48'],
    ]
    
    tu_table = Table(tu_data, colWidths=[1.8*inch, 3*inch, 1.2*inch])
    tu_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#00a3e0')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cbd5e0')),
        ('ALIGN', (2, 0), (2, -1), 'CENTER'),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]))
    story.append(tu_table)
    story.append(Spacer(1, 0.1*inch))
    
    story.append(Paragraph("<b>Cómo Obtenerlo:</b>", subheading_style))
    tu_steps = ListFlowable([
        ListItem(Paragraph("Ir a <b>mysmartmove.com</b> y crear cuenta de landlord", body_style)),
        ListItem(Paragraph("Para integración API: contactar partner@transunion.com", body_style)),
        ListItem(Paragraph("Modelo pay-as-you-go: sin suscripción mensual", body_style)),
        ListItem(Paragraph("El inquilino puede pagar el screening directamente", body_style)),
    ], bulletType='bullet', start='→')
    story.append(tu_steps)
    
    story.append(Paragraph("<b>Nota:</b> Puede pasarse el costo al inquilino ($25-$48 por aplicación)", small_style))
    
    story.append(PageBreak())
    
    # ─────────────────────────────────────────────────────────────────
    # 4. ESUSU - Rent Reporting / Credit Builder
    # ─────────────────────────────────────────────────────────────────
    story.append(Paragraph("4. ESUSU - Reporteo de Renta a Bureaus de Crédito", heading_style))
    story.append(Paragraph("<b>Estado Actual:</b> NO implementado (Credit Builder es simulación)", body_style))
    story.append(Paragraph("<b>Prioridad:</b> ⭐⭐⭐ MEDIA", body_style))
    
    esusu_data = [
        ['Modelo', 'Costo', 'Notas'],
        ['Setup Fee (Landlord)', '$3,500 una vez', 'Integración inicial'],
        ['Por Unidad/Mes', '$2.50/unidad', 'Descuentos por volumen'],
        ['Tenant Direct (App)', '$10/mes', 'El inquilino paga directamente'],
        ['Via Zillow Partnership', '$20/año', 'Alternativa para inquilinos'],
    ]
    
    esusu_table = Table(esusu_data, colWidths=[2*inch, 1.5*inch, 2.5*inch])
    esusu_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#7c3aed')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cbd5e0')),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]))
    story.append(esusu_table)
    story.append(Spacer(1, 0.1*inch))
    
    story.append(Paragraph("<b>Cómo Obtenerlo:</b>", subheading_style))
    esusu_steps = ListFlowable([
        ListItem(Paragraph("Contactar: <b>partners@esusu.com</b>", body_style)),
        ListItem(Paragraph("Website: esusurent.com/partners", body_style)),
        ListItem(Paragraph("Alternativa: Dirigir inquilinos a usar app Esusu directamente ($10/mes)", body_style)),
        ListItem(Paragraph("Documentación API disponible tras firma de contrato", body_style)),
    ], bulletType='bullet', start='→')
    story.append(esusu_steps)
    
    story.append(Paragraph("<b>Estimación (20 unidades):</b> $3,500 setup + $50/mes = ~$4,100 primer año", small_style))
    
    # ─────────────────────────────────────────────────────────────────
    # 5. QUICKBOOKS - Contabilidad
    # ─────────────────────────────────────────────────────────────────
    story.append(Spacer(1, 0.2*inch))
    story.append(Paragraph("5. QUICKBOOKS ONLINE - Integración Contable", heading_style))
    story.append(Paragraph("<b>Estado Actual:</b> NO implementado", body_style))
    story.append(Paragraph("<b>Prioridad:</b> ⭐⭐⭐ MEDIA", body_style))
    
    qbo_data = [
        ['Tier', 'Mensualidad', 'API Credits/Mes', 'Overage'],
        ['Builder (Gratis)', '$0', '500,000', 'Bloqueado'],
        ['Silver', '$300', '1,000,000', '$3.50/1000 calls'],
        ['Gold', '$1,700', '10,000,000', 'Reducido'],
        ['Platinum', '$4,500', '75,000,000', 'Mejor tarifa'],
    ]
    
    qbo_table = Table(qbo_data, colWidths=[1.5*inch, 1.2*inch, 1.5*inch, 1.8*inch])
    qbo_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2ca01c')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cbd5e0')),
        ('ALIGN', (1, 0), (-1, -1), 'CENTER'),
        ('BACKGROUND', (0, 1), (-1, 1), colors.HexColor('#e6ffe6')),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]))
    story.append(qbo_table)
    story.append(Spacer(1, 0.1*inch))
    
    story.append(Paragraph("<b>Cómo Obtenerlo:</b>", subheading_style))
    qbo_steps = ListFlowable([
        ListItem(Paragraph("Ir a <b>developer.intuit.com</b> y crear cuenta de desarrollador", body_style)),
        ListItem(Paragraph("Crear app y obtener Client ID y Client Secret", body_style)),
        ListItem(Paragraph("El tier Builder ($0) es suficiente para la mayoría de casos", body_style)),
        ListItem(Paragraph("API calls de lectura consumen credits; escritura es gratis", body_style)),
    ], bulletType='bullet', start='→')
    story.append(qbo_steps)
    
    story.append(Paragraph("<b>Recomendación:</b> Comenzar con tier Builder (GRATIS) - 500k calls/mes es suficiente", small_style))
    
    # ─────────────────────────────────────────────────────────────────
    # 6. XCEL ENERGY - Green Button Connect
    # ─────────────────────────────────────────────────────────────────
    story.append(Spacer(1, 0.2*inch))
    story.append(Paragraph("6. XCEL ENERGY - Green Button Connect (Datos de Utilidades)", heading_style))
    story.append(Paragraph("<b>Estado Actual:</b> Aplicación enviada - Pendiente aprobación", body_style))
    story.append(Paragraph("<b>Prioridad:</b> ⭐⭐ BAJA (Nice to have)", body_style))
    
    story.append(Paragraph("<b>Costo:</b> GRATIS - No hay costo de API", body_style))
    story.append(Spacer(1, 0.1*inch))
    
    story.append(Paragraph("<b>Cómo Obtenerlo:</b>", subheading_style))
    xcel_steps = ListFlowable([
        ListItem(Paragraph("Descargar formulario: xcelenergy.com Green Button Service Application", body_style)),
        ListItem(Paragraph("Completar información de empresa y servicios", body_style)),
        ListItem(Paragraph("Enviar a: <b>greenbuttonsupport@xcelenergy.com</b>", body_style)),
        ListItem(Paragraph("Tiempo de procesamiento: ~10 días hábiles", body_style)),
        ListItem(Paragraph("Áreas: Texas, Colorado, Minnesota, Wisconsin, etc.", body_style)),
    ], bulletType='bullet', start='→')
    story.append(xcel_steps)
    
    story.append(Paragraph("<b>Uso:</b> Permitir a inquilinos compartir datos de consumo de energía automáticamente", small_style))
    
    story.append(PageBreak())
    
    # ═══════════════════════════════════════════════════════════════
    # RESUMEN DE COSTOS
    # ═══════════════════════════════════════════════════════════════
    story.append(Paragraph("RESUMEN DE COSTOS ESTIMADOS", heading_style))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor('#e2e8f0')))
    story.append(Spacer(1, 0.2*inch))
    
    summary_data = [
        ['Servicio', 'Setup', 'Mensual (20 unidades)', 'Prioridad'],
        ['Stripe Connect', '$0', '$240-$900*', '⭐⭐⭐⭐⭐'],
        ['Plaid Income', '$0', '$50-$150', '⭐⭐⭐⭐'],
        ['TransUnion SmartMove', '$0', '$0 (pay per use)', '⭐⭐⭐⭐'],
        ['Esusu Rent Reporting', '$3,500', '$50', '⭐⭐⭐'],
        ['QuickBooks API', '$0', '$0 (tier Builder)', '⭐⭐⭐'],
        ['Xcel Green Button', '$0', '$0', '⭐⭐'],
        ['TOTAL ESTIMADO', '$3,500', '$340-$1,100/mes', ''],
    ]
    
    summary_table = Table(summary_data, colWidths=[2*inch, 1.2*inch, 1.8*inch, 1*inch])
    summary_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1a365d')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cbd5e0')),
        ('ALIGN', (1, 0), (-1, -1), 'CENTER'),
        ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#e6f3ff')),
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    story.append(summary_table)
    story.append(Paragraph("*Depende de si inquilinos pagan con tarjeta (2.9%) o ACH (0.8%)", small_style))
    
    story.append(PageBreak())
    
    # ═══════════════════════════════════════════════════════════════
    # PARTE 2: ANÁLISIS DE PROPIEDAD
    # ═══════════════════════════════════════════════════════════════
    story.append(Paragraph("PARTE 2: ANÁLISIS DE PROPIEDAD", title_style))
    story.append(Paragraph("217 W 7th St, Dumas, TX 79029", 
                          ParagraphStyle('Address', parent=heading_style, alignment=TA_CENTER)))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor('#e2e8f0')))
    story.append(Spacer(1, 0.2*inch))
    
    story.append(Paragraph("Información de la Propiedad", heading_style))
    
    property_data = [
        ['Característica', 'Detalle'],
        ['Dirección', '217 W 7th St (incluye 213-217)'],
        ['Ciudad/Estado', 'Dumas, TX 79029'],
        ['Tipo de Propiedad', 'Multifamily / Apartamentos'],
        ['Tamaño Total', '9,360 sq ft (según registros)'],
        ['Unidades', 'Apts 1, 2, 3, 4, 5, 6 (6 unidades)'],
        ['Estado Actual', 'OFF MARKET - No listado para venta'],
        ['Valor Estimado (Realtor.com)', '$183,200 (estimado #213)'],
    ]
    
    property_table = Table(property_data, colWidths=[2.5*inch, 4*inch])
    property_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#48bb78')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cbd5e0')),
        ('BACKGROUND', (0, 1), (0, -1), colors.HexColor('#f0fff4')),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    story.append(property_table)
    
    story.append(Spacer(1, 0.2*inch))
    story.append(Paragraph("Análisis de Valor y Potencial", heading_style))
    
    story.append(Paragraph(
        """<b>Situación Actual:</b> La propiedad en 217 W 7th St (que parece incluir 213-217 como 
        un lote combinado) NO está actualmente listada para venta en los portales principales 
        (Zillow, Realtor.com, Redfin). El valor estimado por Realtor.com es de aproximadamente 
        <b>$183,200</b>, pero este es solo un estimado automatizado.""",
        body_style
    ))
    
    story.append(Paragraph(
        """<b>Tamaño y Unidades:</b> Con 9,360 sq ft y 6 unidades, cada unidad promedia 
        aproximadamente 1,560 sq ft, lo cual es considerable para apartamentos en Dumas. 
        Esto sugiere que podrían ser unidades de 2-3 recámaras.""",
        body_style
    ))
    
    story.append(Spacer(1, 0.15*inch))
    story.append(Paragraph("Estimación de Valor de Mercado (6 Unidades)", subheading_style))
    
    valuation_data = [
        ['Método de Valuación', 'Cálculo', 'Valor Estimado'],
        ['Estimado Automatizado', 'Realtor.com', '$183,200'],
        ['Por Ingreso (Cap Rate 8%)', '$650 x 6 x 12 / 0.08', '$585,000'],
        ['Por Ingreso (Cap Rate 10%)', '$650 x 6 x 12 / 0.10', '$468,000'],
        ['Precio por Unidad (mercado)', '$60,000 - $90,000 x 6', '$360,000 - $540,000'],
        ['Precio por Sq Ft', '$40 - $60 x 9,360', '$374,400 - $561,600'],
    ]
    
    valuation_table = Table(valuation_data, colWidths=[2.3*inch, 2.2*inch, 1.5*inch])
    valuation_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#3182ce')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cbd5e0')),
        ('ALIGN', (2, 0), (2, -1), 'RIGHT'),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]))
    story.append(valuation_table)
    
    story.append(Spacer(1, 0.2*inch))
    story.append(Paragraph("Análisis de Cash Flow Potencial", subheading_style))
    
    cashflow_data = [
        ['Concepto', 'Mensual', 'Anual'],
        ['Renta Potencial (6 x $650)', '$3,900', '$46,800'],
        ['(-) Vacancia (5%)', '-$195', '-$2,340'],
        ['Ingreso Efectivo', '$3,705', '$44,460'],
        ['(-) Impuestos (~1.8%)', '-$300', '-$3,600'],
        ['(-) Seguro', '-$200', '-$2,400'],
        ['(-) Mantenimiento (10%)', '-$390', '-$4,680'],
        ['(-) Administración (8%)', '-$312', '-$3,744'],
        ['NOI (Net Operating Income)', '$2,503', '$30,036'],
        ['', '', ''],
        ['Si compras a $400,000:', '', ''],
        ['(-) Hipoteca (7.5%, 25 años)', '-$2,955', '-$35,460'],
        ['Cash Flow Neto', '-$452', '-$5,424'],
        ['', '', ''],
        ['Si compras a $300,000:', '', ''],
        ['(-) Hipoteca (7.5%, 25 años)', '-$2,216', '-$26,592'],
        ['Cash Flow Neto', '+$287', '+$3,444'],
    ]
    
    cashflow_table = Table(cashflow_data, colWidths=[3.5*inch, 1.25*inch, 1.25*inch])
    cashflow_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2c5282')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cbd5e0')),
        ('ALIGN', (1, 0), (-1, -1), 'RIGHT'),
        ('BACKGROUND', (0, 8), (-1, 8), colors.HexColor('#e6f3ff')),
        ('FONTNAME', (0, 8), (-1, 8), 'Helvetica-Bold'),
        ('BACKGROUND', (0, 12), (-1, 12), colors.HexColor('#ffe6e6')),
        ('BACKGROUND', (0, 16), (-1, 16), colors.HexColor('#e6ffe6')),
        ('FONTNAME', (0, 12), (-1, 12), 'Helvetica-Bold'),
        ('FONTNAME', (0, 16), (-1, 16), 'Helvetica-Bold'),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(cashflow_table)
    
    story.append(Spacer(1, 0.2*inch))
    story.append(Paragraph("Recomendaciones", heading_style))
    
    recommendations = ListFlowable([
        ListItem(Paragraph("<b>Contactar al propietario:</b> Como no está listada, considera enviar una carta de interés o contactar directamente al dueño a través de registros del condado.", body_style)),
        ListItem(Paragraph("<b>Precio objetivo:</b> Para que tenga sentido financiero, busca negociar por debajo de $350,000, idealmente $280,000-$320,000.", body_style)),
        ListItem(Paragraph("<b>Inspección:</b> Un edificio de 9,360 sq ft puede tener costos ocultos significativos. Presupuesto de inspección: $500-$800.", body_style)),
        ListItem(Paragraph("<b>Financiamiento:</b> Usa DSCR loan o Commercial loan para 6 unidades. Enganche típico: 25%.", body_style)),
        ListItem(Paragraph("<b>Rentas actuales:</b> Verifica las rentas actuales - si están por debajo de mercado, hay oportunidad de value-add.", body_style)),
    ], bulletType='bullet', start='→')
    story.append(recommendations)
    
    # Footer
    story.append(Spacer(1, 0.5*inch))
    story.append(HRFlowable(width="100%", thickness=2, color=colors.HexColor('#2c5282')))
    story.append(Paragraph(
        f"Ross House Rentals LLC | Reporte generado {datetime.now().strftime('%d/%m/%Y')}",
        ParagraphStyle('Footer', parent=body_style, alignment=TA_CENTER, fontSize=9, textColor=colors.gray)
    ))
    
    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()


def send_email_with_pdf(pdf_content: bytes, recipient_email: str):
    """Send the PDF via SendGrid"""
    
    api_key = os.environ.get("SENDGRID_API_KEY")
    from_email = os.environ.get("SENDGRID_FROM_EMAIL", "info@rosstaxpreparation.com")
    
    if not api_key:
        raise ValueError("SENDGRID_API_KEY not found")
    
    message = Mail(
        from_email=from_email,
        to_emails=recipient_email,
        subject="📋 Reporte: APIs Pendientes y Análisis 217 W 7th St - Ross House Rentals",
        html_content="""
        <html>
        <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
            <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
                <h2 style="color: #2c5282;">🏠 Ross House Rentals LLC</h2>
                <h3>Reporte de APIs Pendientes & Análisis de Propiedad</h3>
                
                <p>Adjunto encontrarás el reporte completo con:</p>
                
                <h4>Parte 1: APIs y Servicios Pendientes</h4>
                <ul>
                    <li>✅ Stripe Connect - Procesamiento de pagos</li>
                    <li>✅ Plaid - Verificación de ingresos</li>
                    <li>✅ TransUnion SmartMove - Background checks</li>
                    <li>✅ Esusu - Rent reporting / Credit builder</li>
                    <li>✅ QuickBooks - Integración contable</li>
                    <li>✅ Xcel Energy Green Button - Datos de utilidades</li>
                </ul>
                <p>Cada servicio incluye: costos, instrucciones de obtención, y prioridad.</p>
                
                <h4>Parte 2: Análisis de Propiedad</h4>
                <ul>
                    <li>📍 217 W 7th St, Dumas, TX 79029</li>
                    <li>🏢 6 unidades (Apt 1-6)</li>
                    <li>📐 9,360 sq ft total</li>
                    <li>💰 Análisis de valor y cash flow</li>
                    <li>📊 Recomendaciones de compra</li>
                </ul>
                
                <p style="background: #e6f3ff; padding: 15px; border-radius: 5px;">
                    <strong>Nota:</strong> La propiedad NO está actualmente listada para venta. 
                    Se recomienda contactar al propietario directamente.
                </p>
                
                <hr style="border: none; border-top: 1px solid #e2e8f0; margin: 20px 0;">
                <p style="font-size: 12px; color: #718096;">
                    Ross House Rentals LLC<br>
                    Generado: {date}
                </p>
            </div>
        </body>
        </html>
        """.format(date=datetime.now().strftime('%d de Junio, %Y'))
    )
    
    encoded_pdf = base64.b64encode(pdf_content).decode()
    attachment = Attachment(
        FileContent(encoded_pdf),
        FileName('Ross_House_APIs_Pendientes_Analisis_Propiedad.pdf'),
        FileType('application/pdf'),
        Disposition('attachment')
    )
    message.attachment = attachment
    
    sg = sendgrid.SendGridAPIClient(api_key=api_key)
    response = sg.send(message)
    
    return {
        "status_code": response.status_code,
        "success": response.status_code in [200, 201, 202],
    }


def main():
    print("=" * 60)
    print("Ross House Rentals - Reporte de APIs y Análisis")
    print("=" * 60)
    
    recipient = "yoandyross@gmail.com"
    
    print("\n📄 Generando PDF...")
    pdf_content = create_apis_report_pdf()
    print(f"   ✅ PDF generado ({len(pdf_content):,} bytes)")
    
    local_path = "/app/ross-house-backend/Ross_House_APIs_Analisis.pdf"
    with open(local_path, "wb") as f:
        f.write(pdf_content)
    print(f"   ✅ Copia local: {local_path}")
    
    print(f"\n📧 Enviando a {recipient}...")
    try:
        result = send_email_with_pdf(pdf_content, recipient)
        if result["success"]:
            print(f"   ✅ Email enviado exitosamente!")
        else:
            print(f"   ❌ Error al enviar")
    except Exception as e:
        print(f"   ❌ Error: {e}")
    
    print("\n" + "=" * 60)


if __name__ == "__main__":
    main()
