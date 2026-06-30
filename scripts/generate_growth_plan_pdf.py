#!/usr/bin/env python3
"""
Ross House Rentals LLC - Business Growth Plan PDF Generator
============================================================
Generates a professional PDF with real estate investment strategies
and sends it via email to the owner.
"""

import os
import sys
from datetime import datetime
from io import BytesIO

# Add parent directory to path for imports
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


def create_growth_plan_pdf():
    """Generate the business growth plan PDF"""
    
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=0.75*inch,
        leftMargin=0.75*inch,
        topMargin=0.75*inch,
        bottomMargin=0.75*inch
    )
    
    # Custom styles
    styles = getSampleStyleSheet()
    
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=24,
        spaceAfter=30,
        alignment=TA_CENTER,
        textColor=colors.HexColor('#1a365d'),
        fontName='Helvetica-Bold'
    )
    
    subtitle_style = ParagraphStyle(
        'CustomSubtitle',
        parent=styles['Normal'],
        fontSize=14,
        spaceAfter=20,
        alignment=TA_CENTER,
        textColor=colors.HexColor('#4a5568')
    )
    
    heading_style = ParagraphStyle(
        'CustomHeading',
        parent=styles['Heading2'],
        fontSize=16,
        spaceBefore=20,
        spaceAfter=10,
        textColor=colors.HexColor('#2c5282'),
        fontName='Helvetica-Bold'
    )
    
    subheading_style = ParagraphStyle(
        'CustomSubHeading',
        parent=styles['Heading3'],
        fontSize=13,
        spaceBefore=15,
        spaceAfter=8,
        textColor=colors.HexColor('#2d3748'),
        fontName='Helvetica-Bold'
    )
    
    body_style = ParagraphStyle(
        'CustomBody',
        parent=styles['Normal'],
        fontSize=11,
        spaceAfter=10,
        alignment=TA_JUSTIFY,
        leading=16
    )
    
    highlight_style = ParagraphStyle(
        'Highlight',
        parent=styles['Normal'],
        fontSize=11,
        spaceAfter=10,
        backColor=colors.HexColor('#e6f3ff'),
        borderPadding=10,
        leading=16
    )
    
    # Build content
    story = []
    
    # ═══════════════════════════════════════════════════════════════
    # COVER PAGE
    # ═══════════════════════════════════════════════════════════════
    story.append(Spacer(1, 1.5*inch))
    story.append(Paragraph("ROSS HOUSE RENTALS LLC", title_style))
    story.append(Spacer(1, 0.3*inch))
    story.append(HRFlowable(width="60%", thickness=2, color=colors.HexColor('#2c5282')))
    story.append(Spacer(1, 0.3*inch))
    story.append(Paragraph("Plan Estratégico de Crecimiento Inmobiliario", subtitle_style))
    story.append(Paragraph("2025 - 2027", subtitle_style))
    story.append(Spacer(1, 0.5*inch))
    story.append(Paragraph(
        "Estrategia para escalar de 2 propiedades unifamiliares<br/>a un portafolio de 6-8+ unidades multifamiliares",
        ParagraphStyle('CoverDesc', parent=body_style, alignment=TA_CENTER, fontSize=12)
    ))
    story.append(Spacer(1, 1*inch))
    story.append(Paragraph(f"Preparado: {datetime.now().strftime('%d de %B, %Y')}", 
                          ParagraphStyle('Date', parent=body_style, alignment=TA_CENTER, textColor=colors.gray)))
    story.append(PageBreak())
    
    # ═══════════════════════════════════════════════════════════════
    # EXECUTIVE SUMMARY
    # ═══════════════════════════════════════════════════════════════
    story.append(Paragraph("RESUMEN EJECUTIVO", heading_style))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor('#e2e8f0')))
    story.append(Spacer(1, 0.2*inch))
    
    story.append(Paragraph(
        """Este plan detalla una estrategia realista y conservadora para que Ross House Rentals LLC 
        escale de su portafolio actual de 2 propiedades unifamiliares a un mínimo de 6-8 unidades 
        dentro de los próximos 18-24 meses. La estrategia se basa en el apalancamiento del equity 
        existente y productos financieros específicos diseñados para inversionistas inmobiliarios.""",
        body_style
    ))
    
    # Current Portfolio Table
    story.append(Spacer(1, 0.2*inch))
    story.append(Paragraph("Portafolio Actual", subheading_style))
    
    portfolio_data = [
        ['Propiedad', 'Valor Estimado', 'Deuda', 'Equity Disponible'],
        ['Propiedad #1 (Dumas, TX)', '$117,000', '$0', '$117,000'],
        ['Propiedad #2 (Dumas, TX)', '$134,000', '$0', '$134,000'],
        ['TOTAL', '$251,000', '$0', '$251,000'],
    ]
    
    portfolio_table = Table(portfolio_data, colWidths=[2.2*inch, 1.5*inch, 1.3*inch, 1.5*inch])
    portfolio_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2c5282')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#e6f3ff')),
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cbd5e0')),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
    ]))
    story.append(portfolio_table)
    story.append(Paragraph("✅ Propiedades libres de deuda - 100% equity disponible", 
                          ParagraphStyle('Note', parent=body_style, fontSize=10, textColor=colors.HexColor('#10b981'), fontName='Helvetica-Bold')))
    
    story.append(PageBreak())
    
    # ═══════════════════════════════════════════════════════════════
    # FINANCING OPTIONS
    # ═══════════════════════════════════════════════════════════════
    story.append(Paragraph("OPCIONES DE FINANCIAMIENTO", heading_style))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor('#e2e8f0')))
    story.append(Spacer(1, 0.2*inch))
    
    # Option 1: HELOC
    story.append(Paragraph("1. HELOC (Línea de Crédito con Garantía Hipotecaria)", subheading_style))
    story.append(Paragraph(
        """Una HELOC te permite acceder a tu equity como una línea de crédito renovable. 
        Es ideal para fondos de emergencia, reparaciones, o como enganche para nuevas propiedades.""",
        body_style
    ))
    
    heloc_data = [
        ['Característica', 'Detalle'],
        ['LTV Máximo Típico', '80-85% del valor de la propiedad'],
        ['Tasa de Interés (2025)', '8.5% - 10.5% variable (Prime + margen)'],
        ['Período de Retiro', '5-10 años (solo pagas intereses)'],
        ['Período de Pago', '10-20 años adicionales'],
        ['Acceso Estimado', '$150,000 - $200,000 entre ambas propiedades (80% LTV)'],
    ]
    
    heloc_table = Table(heloc_data, colWidths=[2.5*inch, 4*inch])
    heloc_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#48bb78')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('ALIGN', (0, 0), (0, -1), 'LEFT'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cbd5e0')),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('BACKGROUND', (0, 1), (0, -1), colors.HexColor('#f0fff4')),
    ]))
    story.append(heloc_table)
    story.append(Spacer(1, 0.1*inch))
    
    story.append(Paragraph("<b>Pros:</b> Flexibilidad, solo pagas lo que usas, tasas más bajas que tarjetas de crédito", body_style))
    story.append(Paragraph("<b>Contras:</b> Tasa variable, tu casa es colateral, algunos bancos no ofrecen HELOC en propiedades de inversión", body_style))
    
    # Option 2: Cash-Out Refinance
    story.append(Spacer(1, 0.2*inch))
    story.append(Paragraph("2. Cash-Out Refinance (Refinanciamiento con Retiro de Efectivo)", subheading_style))
    story.append(Paragraph(
        """Reemplazas tu hipoteca actual por una nueva más grande y recibes la diferencia en efectivo. 
        Ideal cuando las tasas son favorables o cuando necesitas una suma grande de una vez.""",
        body_style
    ))
    
    cashout_data = [
        ['Característica', 'Detalle'],
        ['LTV Máximo (Inversión)', '70-75% del valor actual'],
        ['Tasa de Interés (2025)', '7.5% - 8.5% fija (propiedades de inversión)'],
        ['Costos de Cierre', '2-5% del monto del préstamo'],
        ['Tiempo de Procesamiento', '30-45 días'],
        ['Acceso Estimado', '$175,000 - $210,000 entre ambas propiedades (70-75% LTV)'],
    ]
    
    cashout_table = Table(cashout_data, colWidths=[2.5*inch, 4*inch])
    cashout_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4299e1')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('ALIGN', (0, 0), (0, -1), 'LEFT'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cbd5e0')),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('BACKGROUND', (0, 1), (0, -1), colors.HexColor('#ebf8ff')),
    ]))
    story.append(cashout_table)
    story.append(Spacer(1, 0.1*inch))
    
    story.append(Paragraph("<b>Pros:</b> Tasa fija, pago predecible, suma grande disponible", body_style))
    story.append(Paragraph("<b>Contras:</b> Costos de cierre, aumenta tu deuda total, proceso más largo", body_style))
    
    story.append(PageBreak())
    
    # Option 3: DSCR Loans
    story.append(Paragraph("3. DSCR Loans (Debt Service Coverage Ratio)", subheading_style))
    story.append(Paragraph(
        """Los préstamos DSCR son IDEALES para inversionistas. No verifican tus ingresos personales (W-2). 
        En su lugar, califican basándose en si la renta de la propiedad cubre el pago de la hipoteca. 
        Esta es probablemente tu MEJOR OPCIÓN para comprar propiedades adicionales.""",
        highlight_style
    ))
    
    dscr_data = [
        ['Característica', 'Detalle'],
        ['Ratio Mínimo DSCR', '1.0 - 1.25 (Renta / Pago Hipoteca)'],
        ['Enganche Requerido', '20-25% del precio de compra'],
        ['Tasa de Interés (2025)', '7.0% - 9.0% fija'],
        ['Verificación de Ingresos', 'NO REQUERIDA - Solo avalúo y contrato de renta'],
        ['Entidades Permitidas', 'LLC, Corp, o nombre personal'],
        ['Tipos de Propiedad', '1-4 unidades, multifamily pequeño, comercial'],
    ]
    
    dscr_table = Table(dscr_data, colWidths=[2.5*inch, 4*inch])
    dscr_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#9f7aea')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('ALIGN', (0, 0), (0, -1), 'LEFT'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cbd5e0')),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('BACKGROUND', (0, 1), (0, -1), colors.HexColor('#faf5ff')),
    ]))
    story.append(dscr_table)
    story.append(Spacer(1, 0.1*inch))
    
    story.append(Paragraph("<b>Pros:</b> No verifican W-2/taxes personales, proceso rápido (2-3 semanas), ideal para LLC", body_style))
    story.append(Paragraph("<b>Contras:</b> Tasas ligeramente más altas, requiere enganche sustancial", body_style))
    
    story.append(Spacer(1, 0.2*inch))
    story.append(Paragraph("<b>Prestamistas DSCR Recomendados (2025):</b>", body_style))
    
    lenders = ListFlowable([
        ListItem(Paragraph("Kiavi (kiavi.com) - Especialista en DSCR, cierre rápido", body_style)),
        ListItem(Paragraph("Lima One Capital - Buenas tasas para multifamily pequeño", body_style)),
        ListItem(Paragraph("Visio Lending - Excelente para LLCs", body_style)),
        ListItem(Paragraph("New Silver - Proceso 100% online", body_style)),
        ListItem(Paragraph("Angel Oak (Prime) - Tasas competitivas con buen crédito", body_style)),
    ], bulletType='bullet', start='•')
    story.append(lenders)
    
    story.append(PageBreak())
    
    # ═══════════════════════════════════════════════════════════════
    # STRATEGY: BRRRR
    # ═══════════════════════════════════════════════════════════════
    story.append(Paragraph("ESTRATEGIA BRRRR ADAPTADA", heading_style))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor('#e2e8f0')))
    story.append(Spacer(1, 0.2*inch))
    
    story.append(Paragraph(
        """La estrategia BRRRR (Buy, Rehab, Rent, Refinance, Repeat) es perfecta para maximizar 
        tu capital. Aquí está cómo aplicarla a tu situación:""",
        body_style
    ))
    
    brrrr_steps = [
        ("<b>BUY (Comprar):</b>", "Busca propiedades por debajo del valor de mercado. Duplex/triplex en Dumas o Amarillo con necesidad de reparaciones (ARV: $180k-$250k). Usa HELOC o Cash-out como enganche."),
        ("<b>REHAB (Rehabilitar):</b>", "Invierte $15,000-$30,000 en mejoras que aumenten valor y renta: cocinas, baños, pisos, HVAC. Aumenta renta potencial un 15-25%."),
        ("<b>RENT (Rentar):</b>", "Estabiliza la propiedad con inquilinos calificados. Renta objetivo: Duplex $1,400-$1,800/mes total, Triplex $2,000-$2,700/mes."),
        ("<b>REFINANCE (Refinanciar):</b>", "Después de 6-12 meses, refinancia con DSCR loan al nuevo valor (post-rehab). Recupera tu inversión inicial."),
        ("<b>REPEAT (Repetir):</b>", "Usa el capital recuperado para comprar la siguiente propiedad. Ciclo de 12-18 meses por propiedad."),
    ]
    
    for title, desc in brrrr_steps:
        story.append(Paragraph(title, subheading_style))
        story.append(Paragraph(desc, body_style))
    
    story.append(PageBreak())
    
    # ═══════════════════════════════════════════════════════════════
    # ACTION PLAN
    # ═══════════════════════════════════════════════════════════════
    story.append(Paragraph("PLAN DE ACCIÓN 2025-2027", heading_style))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor('#e2e8f0')))
    story.append(Spacer(1, 0.2*inch))
    
    # Phase 1
    story.append(Paragraph("FASE 1: Preparación (Meses 1-3)", subheading_style))
    phase1_tasks = ListFlowable([
        ListItem(Paragraph("Verificar saldos exactos de hipotecas actuales y calcular equity real", body_style)),
        ListItem(Paragraph("Obtener avalúos actualizados de ambas propiedades ($300-$500 cada uno)", body_style)),
        ListItem(Paragraph("Aplicar para HELOC en al menos una propiedad (recomendar: la de $134k)", body_style)),
        ListItem(Paragraph("Mejorar credit score si está bajo 700 (pagar tarjetas, no abrir cuentas nuevas)", body_style)),
        ListItem(Paragraph("Crear LLC si no existe (Ross House Rentals LLC) para protección", body_style)),
        ListItem(Paragraph("Establecer cuenta bancaria comercial separada", body_style)),
    ], bulletType='bullet', start='✓')
    story.append(phase1_tasks)
    
    # Phase 2
    story.append(Spacer(1, 0.15*inch))
    story.append(Paragraph("FASE 2: Primera Adquisición Multifamily (Meses 4-9)", subheading_style))
    phase2_tasks = ListFlowable([
        ListItem(Paragraph("Objetivo: Duplex o Triplex en Dumas/Amarillo por $150k-$200k", body_style)),
        ListItem(Paragraph("Buscar propiedades off-market (contactar agentes locales, mailers, driving for dollars)", body_style)),
        ListItem(Paragraph("Usar $35k-$50k de HELOC como enganche + costos de cierre", body_style)),
        ListItem(Paragraph("Financiar el resto con DSCR loan (sin verificación de W-2)", body_style)),
        ListItem(Paragraph("Presupuesto de rehab: $15k-$25k (cosmético + funcional)", body_style)),
        ListItem(Paragraph("Meta: Propiedad estabilizada generando cash flow de $400-$700/mes", body_style)),
    ], bulletType='bullet', start='✓')
    story.append(phase2_tasks)
    
    # Phase 3
    story.append(Spacer(1, 0.15*inch))
    story.append(Paragraph("FASE 3: Refinanciamiento y Consolidación (Meses 10-15)", subheading_style))
    phase3_tasks = ListFlowable([
        ListItem(Paragraph("Refinanciar la nueva propiedad al valor post-rehab (ARV)", body_style)),
        ListItem(Paragraph("Recuperar $25k-$40k del capital invertido", body_style)),
        ListItem(Paragraph("Pagar HELOC o mantener para siguiente adquisición", body_style)),
        ListItem(Paragraph("Evaluar performance de todas las propiedades", body_style)),
        ListItem(Paragraph("Optimizar rentas si están por debajo del mercado", body_style)),
    ], bulletType='bullet', start='✓')
    story.append(phase3_tasks)
    
    # Phase 4
    story.append(Spacer(1, 0.15*inch))
    story.append(Paragraph("FASE 4: Segunda Adquisición (Meses 16-24)", subheading_style))
    phase4_tasks = ListFlowable([
        ListItem(Paragraph("Repetir proceso con segunda propiedad multifamily", body_style)),
        ListItem(Paragraph("Objetivo: 4-plex o pequeño complejo de 5-6 unidades ($250k-$350k)", body_style)),
        ListItem(Paragraph("Usar equity acumulado + cash flow de propiedades existentes", body_style)),
        ListItem(Paragraph("Considerar Commercial Multifamily Loan para 5+ unidades", body_style)),
        ListItem(Paragraph("Meta final: 8+ unidades totales, cash flow $2,500-$4,000/mes", body_style)),
    ], bulletType='bullet', start='✓')
    story.append(phase4_tasks)
    
    story.append(PageBreak())
    
    # ═══════════════════════════════════════════════════════════════
    # FINANCIAL PROJECTIONS
    # ═══════════════════════════════════════════════════════════════
    story.append(Paragraph("PROYECCIONES FINANCIERAS", heading_style))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor('#e2e8f0')))
    story.append(Spacer(1, 0.2*inch))
    
    story.append(Paragraph("Escenario Conservador - 24 Meses", subheading_style))
    
    projection_data = [
        ['Métrica', 'Actual', 'Año 1', 'Año 2'],
        ['Total de Unidades', '2', '6-8', '12-15'],
        ['Valor del Portafolio', '$251,000', '$550,000', '$900,000'],
        ['Deuda Total', '$0', '~$300,000', '~$500,000'],
        ['Equity Neto', '$251,000', '~$250,000', '~$400,000'],
        ['Cash Flow Mensual Bruto', '$1,800*', '$4,500', '$8,000'],
        ['Cash Flow Mensual Neto', '$400*', '$1,500', '$3,000'],
    ]
    
    proj_table = Table(projection_data, colWidths=[2.5*inch, 1.5*inch, 1.5*inch, 1.5*inch])
    proj_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2c5282')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('ALIGN', (1, 0), (-1, -1), 'CENTER'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cbd5e0')),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('BACKGROUND', (0, 1), (0, -1), colors.HexColor('#edf2f7')),
        ('FONTNAME', (0, 1), (0, -1), 'Helvetica-Bold'),
        ('BACKGROUND', (-1, 1), (-1, -1), colors.HexColor('#c6f6d5')),
    ]))
    story.append(proj_table)
    story.append(Paragraph("*Estimaciones basadas en rentas típicas de Dumas, TX", 
                          ParagraphStyle('Note', parent=body_style, fontSize=9, textColor=colors.gray)))
    
    story.append(Spacer(1, 0.3*inch))
    story.append(Paragraph("Ejemplo de Deal - Triplex en Dumas", subheading_style))
    
    deal_data = [
        ['Concepto', 'Monto'],
        ['Precio de Compra', '$175,000'],
        ['Enganche (25%)', '$43,750'],
        ['Costos de Cierre (3%)', '$5,250'],
        ['Rehabilitación', '$20,000'],
        ['INVERSIÓN TOTAL', '$69,000'],
        ['', ''],
        ['Préstamo DSCR (75% LTV)', '$131,250'],
        ['Tasa de Interés', '8.0%'],
        ['Pago Mensual (P&I)', '$963'],
        ['', ''],
        ['Renta Mensual (3 unidades @ $650)', '$1,950'],
        ['(-) Pago Hipoteca', '-$963'],
        ['(-) Impuestos/Seguro', '-$250'],
        ['(-) Mantenimiento (10%)', '-$195'],
        ['(-) Vacancia (5%)', '-$98'],
        ['CASH FLOW NETO MENSUAL', '$444'],
        ['', ''],
        ['Cash-on-Cash Return', '7.7% anual'],
    ]
    
    deal_table = Table(deal_data, colWidths=[4*inch, 2.5*inch])
    deal_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#48bb78')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cbd5e0')),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('BACKGROUND', (0, 5), (-1, 5), colors.HexColor('#c6f6d5')),
        ('FONTNAME', (0, 5), (-1, 5), 'Helvetica-Bold'),
        ('BACKGROUND', (0, 16), (-1, 16), colors.HexColor('#c6f6d5')),
        ('FONTNAME', (0, 16), (-1, 16), 'Helvetica-Bold'),
        ('BACKGROUND', (0, 18), (-1, 18), colors.HexColor('#faf5ff')),
        ('FONTNAME', (0, 18), (-1, 18), 'Helvetica-Bold'),
    ]))
    story.append(deal_table)
    
    story.append(PageBreak())
    
    # ═══════════════════════════════════════════════════════════════
    # RISKS AND MITIGATION
    # ═══════════════════════════════════════════════════════════════
    story.append(Paragraph("RIESGOS Y MITIGACIÓN", heading_style))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor('#e2e8f0')))
    story.append(Spacer(1, 0.2*inch))
    
    risks = [
        ("Tasas de Interés Altas", "Las tasas actuales (7-9%) son más altas que hace 2 años, pero los DSCR loans permiten refinanciar fácilmente cuando bajen. Comprar ahora con menos competencia puede ser ventajoso."),
        ("Vacancia", "Dumas tiene economía dependiente de energía. Mitiga diversificando tipos de inquilinos y manteniendo reservas de 3-6 meses de gastos."),
        ("Reparaciones Inesperadas", "Siempre inspeccionar propiedades antes de comprar. Mantener fondo de emergencia de $10k+ y considerar home warranty para primeros años."),
        ("Apalancamiento Excesivo", "No exceder 75% LTV en portafolio total. Mantener ratio deuda-ingresos saludable para acceso a financiamiento futuro."),
        ("Problemas con Inquilinos", "Screening riguroso, usar tu app de Ross House Rentals para verificación. Contratos sólidos y política de tolerancia cero para pagos tardíos."),
    ]
    
    for risk_title, risk_desc in risks:
        story.append(Paragraph(f"<b>{risk_title}</b>", subheading_style))
        story.append(Paragraph(risk_desc, body_style))
    
    # ═══════════════════════════════════════════════════════════════
    # NEXT STEPS
    # ═══════════════════════════════════════════════════════════════
    story.append(Spacer(1, 0.3*inch))
    story.append(Paragraph("PRÓXIMOS PASOS INMEDIATOS", heading_style))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor('#e2e8f0')))
    story.append(Spacer(1, 0.2*inch))
    
    next_steps = ListFlowable([
        ListItem(Paragraph("<b>Esta semana:</b> Solicitar estados de cuenta de hipotecas actuales para verificar saldos exactos", body_style)),
        ListItem(Paragraph("<b>Próximos 14 días:</b> Contactar 2-3 bancos locales sobre HELOC (First Bank of Dumas, Happy State Bank, Amarillo National)", body_style)),
        ListItem(Paragraph("<b>Próximos 30 días:</b> Obtener pre-aprobación de al menos un prestamista DSCR (Kiavi, Visio)", body_style)),
        ListItem(Paragraph("<b>Próximos 60 días:</b> Comenzar búsqueda activa de duplex/triplex en Dumas y Amarillo", body_style)),
        ListItem(Paragraph("<b>Próximos 90 días:</b> Tener primera oferta en propiedad objetivo", body_style)),
    ], bulletType='bullet', start='→')
    story.append(next_steps)
    
    # Footer
    story.append(Spacer(1, 0.5*inch))
    story.append(HRFlowable(width="100%", thickness=2, color=colors.HexColor('#2c5282')))
    story.append(Spacer(1, 0.2*inch))
    story.append(Paragraph(
        "Ross House Rentals LLC | Plan de Crecimiento 2025-2027",
        ParagraphStyle('Footer', parent=body_style, alignment=TA_CENTER, textColor=colors.gray, fontSize=9)
    ))
    story.append(Paragraph(
        "Este documento es solo para propósitos informativos y no constituye asesoría financiera profesional.",
        ParagraphStyle('Disclaimer', parent=body_style, alignment=TA_CENTER, textColor=colors.gray, fontSize=8)
    ))
    
    # Build PDF
    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()


def send_email_with_pdf(pdf_content: bytes, recipient_email: str):
    """Send the PDF via SendGrid"""
    
    api_key = os.environ.get("SENDGRID_API_KEY")
    from_email = os.environ.get("SENDGRID_FROM_EMAIL", "info@rosshouserentals.com")
    
    if not api_key:
        raise ValueError("SENDGRID_API_KEY not found in environment")
    
    # Create message
    message = Mail(
        from_email=from_email,
        to_emails=recipient_email,
        subject="📊 Plan Estratégico de Crecimiento - Ross House Rentals LLC",
        html_content="""
        <html>
        <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
            <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
                <h2 style="color: #2c5282;">🏠 Ross House Rentals LLC</h2>
                <h3>Plan Estratégico de Crecimiento Inmobiliario 2025-2027</h3>
                
                <p>Adjunto encontrarás el plan detallado para escalar tu portafolio de 2 propiedades 
                unifamiliares a 6-8+ unidades multifamiliares.</p>
                
                <h4>El documento incluye:</h4>
                <ul>
                    <li>✅ Análisis de tu equity actual</li>
                    <li>✅ Opciones de financiamiento (HELOC, Cash-Out, DSCR Loans)</li>
                    <li>✅ Estrategia BRRRR adaptada</li>
                    <li>✅ Plan de acción por fases (24 meses)</li>
                    <li>✅ Proyecciones financieras</li>
                    <li>✅ Ejemplo de deal con números reales</li>
                    <li>✅ Análisis de riesgos y mitigación</li>
                    <li>✅ Próximos pasos inmediatos</li>
                </ul>
                
                <p style="background: #e6f3ff; padding: 15px; border-radius: 5px;">
                    <strong>Próximo paso recomendado:</strong> Verificar saldos exactos de tus hipotecas 
                    actuales y contactar bancos locales sobre HELOC esta semana.
                </p>
                
                <p>¡Éxito en tu crecimiento inmobiliario!</p>
                
                <hr style="border: none; border-top: 1px solid #e2e8f0; margin: 20px 0;">
                <p style="font-size: 12px; color: #718096;">
                    Ross House Rentals LLC<br>
                    Generado automáticamente el {date}
                </p>
            </div>
        </body>
        </html>
        """.format(date=datetime.now().strftime('%d de %B, %Y'))
    )
    
    # Attach PDF
    encoded_pdf = base64.b64encode(pdf_content).decode()
    attachment = Attachment(
        FileContent(encoded_pdf),
        FileName('Ross_House_Rentals_Plan_Crecimiento_2025.pdf'),
        FileType('application/pdf'),
        Disposition('attachment')
    )
    message.attachment = attachment
    
    # Send
    sg = sendgrid.SendGridAPIClient(api_key=api_key)
    response = sg.send(message)
    
    return {
        "status_code": response.status_code,
        "success": response.status_code in [200, 201, 202],
        "message": "Email enviado exitosamente" if response.status_code in [200, 201, 202] else f"Error: {response.body}"
    }


def main():
    """Main execution"""
    print("=" * 60)
    print("Ross House Rentals - Generador de Plan de Crecimiento")
    print("=" * 60)
    
    recipient = "yoandyross@gmail.com"
    
    print("\n📄 Generando PDF...")
    pdf_content = create_growth_plan_pdf()
    print(f"   ✅ PDF generado ({len(pdf_content):,} bytes)")
    
    # Save a local copy
    local_path = "/app/ross-house-backend/Ross_House_Plan_Crecimiento_2025.pdf"
    with open(local_path, "wb") as f:
        f.write(pdf_content)
    print(f"   ✅ Copia local guardada: {local_path}")
    
    print(f"\n📧 Enviando email a {recipient}...")
    try:
        result = send_email_with_pdf(pdf_content, recipient)
        if result["success"]:
            print(f"   ✅ {result['message']}")
            print(f"   📬 Status Code: {result['status_code']}")
        else:
            print(f"   ❌ {result['message']}")
    except Exception as e:
        print(f"   ❌ Error enviando email: {e}")
        import traceback
        traceback.print_exc()
    
    print("\n" + "=" * 60)
    print("✅ Proceso completado")
    print("=" * 60)


if __name__ == "__main__":
    main()
