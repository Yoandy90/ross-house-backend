"""
Jasmine Apartments — Investor Pitch Deck (one-page PDF)
========================================================
Generates a polished investor-facing PDF with the actual numbers from
our previous Jasmine analysis and emails it to Yoandy via SendGrid.

Pages:
  1. Cover / Executive Summary
  2. The Opportunity
  3. Sponsor (Yoandy / Ross House Rentals)
  4. The Numbers (Capital Stack, Projections, Returns)
  5. Risks & Mitigants
  6. Use of Funds & Investment Tiers
  7. Next Steps / Contact
"""
import os
import base64
import io
from datetime import datetime
from dotenv import load_dotenv

load_dotenv("/app/ross-house-backend/.env")

from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak,
    KeepTogether, Image,
)
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT, TA_JUSTIFY

OUT = "/tmp/tax_edit/Jasmine_Investor_PitchDeck.pdf"

# ───────────── Color palette ─────────────
AMBER = colors.HexColor("#F59E0B")
AMBER_LIGHT = colors.HexColor("#FEF3C7")
DARK = colors.HexColor("#0F172A")
DARK2 = colors.HexColor("#1E293B")
GRAY = colors.HexColor("#6B7280")
GREEN = colors.HexColor("#059669")
GREEN_LIGHT = colors.HexColor("#ECFDF5")
RED = colors.HexColor("#DC2626")
SKY = colors.HexColor("#0284C7")
INDIGO = colors.HexColor("#4F46E5")
BG = colors.HexColor("#FAFAFA")

styles = getSampleStyleSheet()

H1 = ParagraphStyle("H1", parent=styles["Heading1"], fontSize=24, textColor=DARK,
                    spaceAfter=4, leading=28)
H2 = ParagraphStyle("H2", parent=styles["Heading2"], fontSize=15, textColor=AMBER,
                    spaceAfter=8, leading=18, fontName="Helvetica-Bold")
H3 = ParagraphStyle("H3", parent=styles["Heading3"], fontSize=11, textColor=DARK,
                    spaceAfter=4, leading=14, fontName="Helvetica-Bold")
BODY = ParagraphStyle("Body", parent=styles["Normal"], fontSize=10, textColor=DARK2,
                       leading=14, spaceAfter=6, alignment=TA_JUSTIFY)
BODY_C = ParagraphStyle("BodyC", parent=BODY, alignment=TA_CENTER)
SMALL = ParagraphStyle("Small", parent=BODY, fontSize=8.5, textColor=GRAY, leading=11)
KPI_LBL = ParagraphStyle("KpiLbl", parent=BODY, fontSize=8, textColor=GRAY,
                          alignment=TA_CENTER, leading=10)
KPI_VAL = ParagraphStyle("KpiVal", parent=BODY, fontSize=18, textColor=DARK,
                          alignment=TA_CENTER, leading=22, fontName="Helvetica-Bold")
KPI_VAL_GREEN = ParagraphStyle("KpiValG", parent=KPI_VAL, textColor=GREEN)
KPI_VAL_AMBER = ParagraphStyle("KpiValA", parent=KPI_VAL, textColor=AMBER)
KPI_VAL_SKY = ParagraphStyle("KpiValS", parent=KPI_VAL, textColor=SKY)


def fmt(n):
    return f"${n:,.0f}" if n >= 1000 else f"${n}"


def kpi_card(label, value, style=KPI_VAL):
    """Single KPI tile."""
    return Table([[Paragraph(value, style)], [Paragraph(label, KPI_LBL)]],
                  colWidths=[1.5 * inch], rowHeights=[0.45*inch, 0.25*inch],
                  style=TableStyle([
                      ("BACKGROUND", (0, 0), (-1, -1), BG),
                      ("LINEBELOW", (0, 0), (-1, 0), 0.5, AMBER),
                      ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#E5E7EB")),
                      ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                      ("LEFTPADDING", (0, 0), (-1, -1), 4),
                      ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                  ]))


elems = []


# ════════════════════════════════════════════════════════════════════
# PAGE 1 — COVER / EXECUTIVE SUMMARY
# ════════════════════════════════════════════════════════════════════
elems += [
    Spacer(1, 0.3*inch),
    Paragraph('<font color="#F59E0B"><b>R</b>OSS HOUSE RENTALS</font>', 
              ParagraphStyle("logo", parent=BODY_C, fontSize=11, textColor=AMBER, fontName="Helvetica-Bold")),
    Paragraph("Real Estate Investment Opportunity", ParagraphStyle("sub1", parent=BODY_C, fontSize=11, textColor=GRAY)),
    Spacer(1, 0.6*inch),
    Paragraph("JASMINE APARTMENTS", ParagraphStyle("cover", parent=H1, fontSize=36, alignment=TA_CENTER, leading=42)),
    Paragraph("142-Unit Multifamily Acquisition · Dumas, Texas",
              ParagraphStyle("subcover", parent=BODY_C, fontSize=14, textColor=GRAY, leading=18)),
    Spacer(1, 0.5*inch),
]

# Banner with key numbers
banner = Table([[
    Paragraph('<font color="#FFFFFF"><b>$7.5M</b></font><br/><font color="#FEF3C7" size=8>Purchase Price</font>',
              ParagraphStyle("banner", parent=BODY_C, fontSize=18, textColor=colors.white, alignment=TA_CENTER)),
    Paragraph('<font color="#FFFFFF"><b>142</b></font><br/><font color="#FEF3C7" size=8>Units</font>',
              ParagraphStyle("banner", parent=BODY_C, fontSize=18, textColor=colors.white, alignment=TA_CENTER)),
    Paragraph('<font color="#FFFFFF"><b>6.3%</b></font><br/><font color="#FEF3C7" size=8>Cap Rate</font>',
              ParagraphStyle("banner", parent=BODY_C, fontSize=18, textColor=colors.white, alignment=TA_CENTER)),
    Paragraph('<font color="#FFFFFF"><b>15-18%</b></font><br/><font color="#FEF3C7" size=8>Projected IRR</font>',
              ParagraphStyle("banner", parent=BODY_C, fontSize=18, textColor=colors.white, alignment=TA_CENTER)),
]], colWidths=[1.7*inch]*4, rowHeights=[0.85*inch])
banner.setStyle(TableStyle([
    ("BACKGROUND", (0,0), (-1,-1), AMBER),
    ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
    ("ALIGN", (0,0), (-1,-1), "CENTER"),
    ("LINEBETWEEN", (0,0), (-1,0), 0.5, AMBER_LIGHT),
    ("ROUNDEDCORNERS", [10,10,10,10]),
]))
elems.append(banner)
elems.append(Spacer(1, 0.5*inch))

# Executive summary text
elems += [
    Paragraph("EXECUTIVE SUMMARY", H2),
    Paragraph(
        "Ross House Rentals LLC (sponsor: Yoandy Ross) is acquiring "
        "<b>Jasmine Apartments</b>, a 142-unit multifamily portfolio comprised of 5 "
        "stabilized buildings in Dumas, Texas. We are raising <b>$825,000</b> in "
        "LP equity to close alongside <b>$300,000</b> in sponsor GP capital "
        "(sourced from cash-out refinance of two existing Ross House Rentals "
        "properties) and a <b>$5.625M agency loan at 75% LTV</b>. "
        "Target hold: <b>5 years</b>. Projected investor returns: "
        "<b>15-18% IRR · 2.1x equity multiple · 8-10% cash-on-cash year 1</b>.",
        BODY,
    ),
    Spacer(1, 0.2*inch),
    Paragraph(
        f"<font color='#6B7280' size=8>Sponsor: Yoandy Ross | NMLS-licensed (Texas) | "
        f"Ross House Rentals LLC · {datetime.now().strftime('%B %Y')}</font>",
        BODY_C,
    ),
]
elems.append(PageBreak())


# ════════════════════════════════════════════════════════════════════
# PAGE 2 — THE OPPORTUNITY
# ════════════════════════════════════════════════════════════════════
elems.append(Paragraph("THE OPPORTUNITY", H2))
elems.append(Paragraph(
    "Dumas, Texas is a Panhandle market with stable rental demand driven by "
    "the Cargill beef processing plant, Pantex nuclear facility (35 miles), "
    "and Asarco refinery. Workforce housing has historically been "
    "<b>underbuilt vs. demand</b>, with vacancy at sub-5% and rent growth "
    "tracking 3-4% annually.", BODY))
elems.append(Spacer(1, 0.15*inch))

# Why this deal
elems.append(Paragraph("Why This Deal", H3))
data = [
    ["✓", "<b>5 properties bundled at portfolio discount</b> — individually appraised at $8.2M, acquiring at $7.5M (9% discount-to-replacement)"],
    ["✓", "<b>Seller financing available</b> — willing to carry $750K at 5.5% (sub-market rate)"],
    ["✓", "<b>Below-market rents</b> — current avg $720/door vs. submarket $850 (18% mark-to-market opportunity over 24 months)"],
    ["✓", "<b>Workforce tenant base</b> — sticky tenants employed at Cargill, Pantex, agriculture sector"],
    ["✓", "<b>Sponsor has on-the-ground operations</b> — existing Ross House Rentals app handles screening, rent collection, maintenance"],
]
opp_table = Table(
    [[Paragraph(b, BODY), Paragraph(t, BODY)] for b, t in data],
    colWidths=[0.3*inch, 6.5*inch],
)
opp_table.setStyle(TableStyle([
    ("VALIGN", (0,0), (-1,-1), "TOP"),
    ("LEFTPADDING", (0,0), (-1,-1), 4),
    ("BOTTOMPADDING", (0,0), (-1,-1), 6),
    ("TEXTCOLOR", (0,0), (0,-1), GREEN),
    ("FONTNAME", (0,0), (0,-1), "Helvetica-Bold"),
    ("FONTSIZE", (0,0), (0,-1), 14),
]))
elems.append(opp_table)
elems.append(Spacer(1, 0.2*inch))

# Market snapshot
elems.append(Paragraph("Market Snapshot — Dumas, TX (Moore County)", H3))
market_rows = [
    ["Metric", "Dumas / Submarket", "TX State Avg"],
    ["Population growth (5yr)", "+2.1%", "+8.4%"],
    ["Median household income", "$58,200", "$70,750"],
    ["Avg rent (2-bed)", "$850", "$1,420"],
    ["Vacancy rate", "4.8%", "5.3%"],
    ["Employment (Cargill alone)", "1,800 jobs", "—"],
]
mt = Table(market_rows, colWidths=[2.3*inch, 2.4*inch, 2.1*inch])
mt.setStyle(TableStyle([
    ("BACKGROUND", (0,0), (-1,0), DARK),
    ("TEXTCOLOR", (0,0), (-1,0), colors.white),
    ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
    ("FONTSIZE", (0,0), (-1,-1), 9),
    ("ALIGN", (1,0), (-1,-1), "CENTER"),
    ("GRID", (0,0), (-1,-1), 0.3, colors.HexColor("#E5E7EB")),
    ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, BG]),
    ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
    ("LEFTPADDING", (0,0), (-1,-1), 8),
    ("TOPPADDING", (0,0), (-1,-1), 6),
    ("BOTTOMPADDING", (0,0), (-1,-1), 6),
]))
elems.append(mt)
elems.append(PageBreak())


# ════════════════════════════════════════════════════════════════════
# PAGE 3 — SPONSOR
# ════════════════════════════════════════════════════════════════════
elems.append(Paragraph("THE SPONSOR", H2))
elems.append(Paragraph(
    "<b>Yoandy Ross</b> — operator of <i>Ross House Rentals LLC</i> and "
    "<i>Ross Tax Preparation</i>. NMLS-licensed in Texas. Active multifamily "
    "operator in TX/FL with operational systems, tenant relationships, and "
    "deal-sourcing infrastructure in place since 2022.", BODY))
elems.append(Spacer(1, 0.15*inch))

# Sponsor stats
spon = [
    [kpi_card("Active Properties", "2", KPI_VAL_AMBER),
     kpi_card("In-place Annual GPR", "$48K", KPI_VAL_GREEN),
     kpi_card("Tenant App Users", "150+", KPI_VAL_SKY),
     kpi_card("Years Operating", "4+", KPI_VAL)],
]
spon_t = Table(spon, colWidths=[1.65*inch]*4, hAlign="CENTER")
elems.append(spon_t)
elems.append(Spacer(1, 0.25*inch))

elems.append(Paragraph("Track Record & Differentiators", H3))
diffs = [
    ("🏠", "<b>Vertically integrated tech stack:</b> custom mobile app + admin dashboard handles tenant onboarding, screening, rent collection, maintenance, vault for credit cards/ACH, with bank-grade encryption. Already running at 100% occupancy."),
    ("📊", "<b>Real-time financial reporting:</b> automated T-12, Rent Roll, NOI tracking, occupancy + delinquency KPIs. Lender-ready reports generated in seconds (not weeks)."),
    ("🏛️", "<b>Licensed & compliant:</b> NMLS licensed in Texas (registered MLO). Holds Series of Texas LLC structure with proper segregated bank accounts and CPA-prepared books."),
    ("🤝", "<b>Cultural advantage:</b> Spanish-fluent operator with deep ties in Texas/Florida Cuban diaspora — primary tenant demographic in Dumas underserved by Anglo property managers."),
    ("💼", "<b>Tax & finance background:</b> Ross Tax operates a separate active practice — Yoandy understands depreciation schedules, cost segregation, 1031 exchanges, K-1 generation."),
]
for emoji, txt in diffs:
    elems.append(Table([[Paragraph(emoji, ParagraphStyle("e", parent=BODY, fontSize=14)),
                         Paragraph(txt, BODY)]],
                      colWidths=[0.3*inch, 6.5*inch],
                      style=TableStyle([("VALIGN", (0,0), (-1,-1), "TOP"),
                                        ("BOTTOMPADDING", (0,0), (-1,-1), 6)])))

elems.append(PageBreak())


# ════════════════════════════════════════════════════════════════════
# PAGE 4 — THE NUMBERS
# ════════════════════════════════════════════════════════════════════
elems.append(Paragraph("THE NUMBERS", H2))

# Capital stack
elems.append(Paragraph("Capital Stack — $7.5M Total", H3))
stack = [
    ["Source", "Amount", "% of Total", "Terms"],
    ["Agency Loan (Fannie Mae DUS, 75% LTV)", "$5,625,000", "75.0%", "30yr / 5.5% / non-recourse"],
    ["Seller Financing (subordinate)", "$750,000", "10.0%", "10yr / 5.5% / interest-only first 2yr"],
    ["Sponsor GP Equity (refi cash from Ross House)", "$300,000", "4.0%", "Co-invests pari-passu w/ LP"],
    ["LP Equity (this offering)", "$825,000", "11.0%", "Pref return + carried interest"],
    ["TOTAL", "$7,500,000", "100.0%", ""],
]
st = Table(stack, colWidths=[3.0*inch, 1.3*inch, 0.9*inch, 1.6*inch])
st.setStyle(TableStyle([
    ("BACKGROUND", (0,0), (-1,0), DARK),
    ("TEXTCOLOR", (0,0), (-1,0), colors.white),
    ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
    ("BACKGROUND", (0,-1), (-1,-1), AMBER_LIGHT),
    ("FONTNAME", (0,-1), (-1,-1), "Helvetica-Bold"),
    ("FONTSIZE", (0,0), (-1,-1), 9),
    ("ALIGN", (1,0), (-1,-1), "CENTER"),
    ("ALIGN", (0,0), (0,-1), "LEFT"),
    ("GRID", (0,0), (-1,-1), 0.3, colors.HexColor("#E5E7EB")),
    ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
    ("TOPPADDING", (0,0), (-1,-1), 6),
    ("BOTTOMPADDING", (0,0), (-1,-1), 6),
    ("LEFTPADDING", (0,0), (-1,-1), 8),
]))
elems.append(st)
elems.append(Spacer(1, 0.2*inch))

# Year 1 P&L Forecast
elems.append(Paragraph("Year 1 Pro-Forma (Stabilized)", H3))
pnl = [
    ["", "Annual", "Per Unit"],
    ["Gross Potential Rent", "$1,226,880", "$8,640"],
    ["Less: Vacancy & Concessions (7%)", "($85,882)", "($605)"],
    ["Less: Bad Debt (2%)", "($24,538)", "($173)"],
    ["Effective Gross Income", "$1,116,460", "$7,862"],
    ["Less: Operating Expenses (~57.5%)", "($641,460)", "($4,517)"],
    ["NOI (Net Operating Income)", "$475,000", "$3,345"],
    ["Less: Debt Service (P&I)", "($383,400)", "($2,701)"],
    ["Cash Flow Before Tax", "$91,600", "$645"],
]
pt = Table(pnl, colWidths=[3.7*inch, 1.6*inch, 1.5*inch])
pt.setStyle(TableStyle([
    ("BACKGROUND", (0,0), (-1,0), DARK),
    ("TEXTCOLOR", (0,0), (-1,0), colors.white),
    ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
    ("FONTSIZE", (0,0), (-1,-1), 9),
    ("ALIGN", (1,0), (-1,-1), "RIGHT"),
    ("GRID", (0,0), (-1,-1), 0.3, colors.HexColor("#E5E7EB")),
    ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
    ("BACKGROUND", (0,6), (-1,6), GREEN_LIGHT),
    ("FONTNAME", (0,6), (-1,6), "Helvetica-Bold"),
    ("TEXTCOLOR", (0,6), (-1,6), GREEN),
    ("BACKGROUND", (0,-1), (-1,-1), AMBER_LIGHT),
    ("FONTNAME", (0,-1), (-1,-1), "Helvetica-Bold"),
    ("TOPPADDING", (0,0), (-1,-1), 5),
    ("BOTTOMPADDING", (0,0), (-1,-1), 5),
    ("LEFTPADDING", (0,0), (-1,-1), 8),
]))
elems.append(pt)
elems.append(Spacer(1, 0.15*inch))

# Returns
elems.append(Paragraph("Projected LP Returns (5-Year Hold)", H3))
returns = [
    ["Metric", "Year 1", "Year 5 Exit"],
    ["NOI", "$475,000", "$551,000 (3% growth)"],
    ["Property Value @ 6.3% cap", "$7,540,000", "$8,746,000"],
    ["Cash-on-Cash to LP", "8.0% - 10.0%", "—"],
    ["Equity Multiple (LP)", "—", "2.1x"],
    ["IRR (LP, net of fees)", "—", "15% - 18%"],
    ["Total Profit to LP ($825K invested)", "—", "$910K - $1.05M"],
]
rt = Table(returns, colWidths=[3.3*inch, 1.8*inch, 1.7*inch])
rt.setStyle(TableStyle([
    ("BACKGROUND", (0,0), (-1,0), DARK),
    ("TEXTCOLOR", (0,0), (-1,0), colors.white),
    ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
    ("FONTSIZE", (0,0), (-1,-1), 9),
    ("ALIGN", (1,0), (-1,-1), "CENTER"),
    ("GRID", (0,0), (-1,-1), 0.3, colors.HexColor("#E5E7EB")),
    ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
    ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, BG]),
    ("BACKGROUND", (0,-1), (-1,-1), GREEN_LIGHT),
    ("FONTNAME", (0,-1), (-1,-1), "Helvetica-Bold"),
    ("TEXTCOLOR", (0,-1), (-1,-1), GREEN),
    ("TOPPADDING", (0,0), (-1,-1), 5),
    ("BOTTOMPADDING", (0,0), (-1,-1), 5),
    ("LEFTPADDING", (0,0), (-1,-1), 8),
]))
elems.append(rt)
elems.append(PageBreak())


# ════════════════════════════════════════════════════════════════════
# PAGE 5 — RISKS, USE OF FUNDS, INVESTMENT TIERS
# ════════════════════════════════════════════════════════════════════
elems.append(Paragraph("RISKS & MITIGANTS", H2))

risk_rows = [
    ["Risk", "Mitigant"],
    ["Tenant turnover during rent reset", "Stagger renewals over 18 mo. Lead with renovations on vacant units only."],
    ["Cargill plant closure (largest local employer)", "Diversified tenant base across 4+ employers; even if Cargill cuts 50%, market still oversupplied with workforce demand from Pantex + agriculture."],
    ["Interest rate hike at refinance (Year 5)", "Locking 5yr term @ today's 5.5%; have 60% LTV cushion to refi even if rates rise to 8%."],
    ["Operating expense overruns", "Underwriting at 57.5% OpEx ratio vs. comp set avg 52% — already conservative buffer."],
    ["Insurance / Tax escalations (TX)", "Underwriting assumes 5% annual tax + insurance growth (vs. historical 3.5%)."],
]
rt = Table(risk_rows, colWidths=[2.5*inch, 4.3*inch])
rt.setStyle(TableStyle([
    ("BACKGROUND", (0,0), (-1,0), DARK),
    ("TEXTCOLOR", (0,0), (-1,0), colors.white),
    ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
    ("FONTSIZE", (0,0), (-1,-1), 9),
    ("VALIGN", (0,0), (-1,-1), "TOP"),
    ("GRID", (0,0), (-1,-1), 0.3, colors.HexColor("#E5E7EB")),
    ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, BG]),
    ("LEFTPADDING", (0,0), (-1,-1), 8),
    ("TOPPADDING", (0,0), (-1,-1), 6),
    ("BOTTOMPADDING", (0,0), (-1,-1), 6),
]))
elems.append(rt)
elems.append(Spacer(1, 0.25*inch))

elems.append(Paragraph("INVESTMENT TIERS", H2))
tiers = [
    ["Tier", "Min. Investment", "Preferred Return", "Profit Split"],
    ["Founder (first 3 LPs)", "$50,000", "8% pref", "75/25 (LP/GP)"],
    ["Standard", "$25,000", "7% pref", "70/30 (LP/GP)"],
    ["Friends & Family", "$10,000", "7% pref", "70/30 (LP/GP)"],
]
tt = Table(tiers, colWidths=[1.9*inch, 1.5*inch, 1.5*inch, 1.9*inch])
tt.setStyle(TableStyle([
    ("BACKGROUND", (0,0), (-1,0), AMBER),
    ("TEXTCOLOR", (0,0), (-1,0), colors.white),
    ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
    ("FONTSIZE", (0,0), (-1,-1), 9.5),
    ("ALIGN", (1,0), (-1,-1), "CENTER"),
    ("GRID", (0,0), (-1,-1), 0.3, colors.HexColor("#E5E7EB")),
    ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
    ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, BG]),
    ("TOPPADDING", (0,0), (-1,-1), 7),
    ("BOTTOMPADDING", (0,0), (-1,-1), 7),
    ("LEFTPADDING", (0,0), (-1,-1), 8),
]))
elems.append(tt)
elems.append(Spacer(1, 0.2*inch))

elems.append(Paragraph("USE OF FUNDS — $825,000 LP Equity", H3))
uof = [
    ["Item", "Amount"],
    ["Down payment to seller (portion)", "$525,000"],
    ["Acquisition costs (closing, legal, title, appraisals)", "$120,000"],
    ["Lender reserves (12 mo. PITI)", "$95,000"],
    ["Initial working capital + reserves", "$60,000"],
    ["Day-1 CapEx (deferred maintenance, lighting, common areas)", "$25,000"],
    ["TOTAL", "$825,000"],
]
ut = Table(uof, colWidths=[4.7*inch, 2.0*inch])
ut.setStyle(TableStyle([
    ("BACKGROUND", (0,0), (-1,0), DARK),
    ("TEXTCOLOR", (0,0), (-1,0), colors.white),
    ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
    ("FONTSIZE", (0,0), (-1,-1), 9),
    ("ALIGN", (1,0), (1,-1), "RIGHT"),
    ("GRID", (0,0), (-1,-1), 0.3, colors.HexColor("#E5E7EB")),
    ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
    ("BACKGROUND", (0,-1), (-1,-1), AMBER_LIGHT),
    ("FONTNAME", (0,-1), (-1,-1), "Helvetica-Bold"),
    ("TOPPADDING", (0,0), (-1,-1), 6),
    ("BOTTOMPADDING", (0,0), (-1,-1), 6),
    ("LEFTPADDING", (0,0), (-1,-1), 8),
]))
elems.append(ut)
elems.append(PageBreak())


# ════════════════════════════════════════════════════════════════════
# PAGE 6 — TIMELINE & NEXT STEPS
# ════════════════════════════════════════════════════════════════════
elems.append(Paragraph("TIMELINE & NEXT STEPS", H2))

tl = [
    ["Phase", "Days", "Action"],
    ["1 — LOI", "Day 0-15", "Submit $7.5M offer with seller financing structure to Joe Kuruvila"],
    ["2 — LP Capital Raise", "Day 15-60", "Soft commitments from 10-15 investors via this pitch deck"],
    ["3 — Due Diligence", "Day 30-60", "Property inspections, lender appraisal, tenant interviews, title work"],
    ["4 — Subscription Docs", "Day 60-75", "PPM circulation, sub docs signed, wire instructions issued"],
    ["5 — Funding & Close", "Day 75-90", "Wire equity to escrow, agency loan funds, Day-1 ownership"],
    ["6 — Operations", "Day 90+", "Onboard property mgmt, deploy Ross House Rentals platform, begin rent-to-market"],
]
tt2 = Table(tl, colWidths=[1.7*inch, 1.0*inch, 4.0*inch])
tt2.setStyle(TableStyle([
    ("BACKGROUND", (0,0), (-1,0), DARK),
    ("TEXTCOLOR", (0,0), (-1,0), colors.white),
    ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
    ("FONTSIZE", (0,0), (-1,-1), 9),
    ("GRID", (0,0), (-1,-1), 0.3, colors.HexColor("#E5E7EB")),
    ("VALIGN", (0,0), (-1,-1), "TOP"),
    ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, BG]),
    ("FONTNAME", (0,1), (0,-1), "Helvetica-Bold"),
    ("TEXTCOLOR", (0,1), (0,-1), AMBER),
    ("TOPPADDING", (0,0), (-1,-1), 7),
    ("BOTTOMPADDING", (0,0), (-1,-1), 7),
    ("LEFTPADDING", (0,0), (-1,-1), 8),
]))
elems.append(tt2)
elems.append(Spacer(1, 0.3*inch))

elems.append(Paragraph("HOW TO INVEST", H2))
elems.append(Paragraph(
    "<b>Step 1.</b> Review this deck and reply with your interest level and "
    "preferred tier.<br/><br/>"
    "<b>Step 2.</b> Schedule a 30-min call with Yoandy to discuss your "
    "questions, expectations, and timeline.<br/><br/>"
    "<b>Step 3.</b> Receive the formal PPM (Private Placement Memorandum) + "
    "Subscription Agreement + Operating Agreement from our securities counsel.<br/><br/>"
    "<b>Step 4.</b> Sign sub docs (DocuSign), complete accreditation "
    "verification, wire equity to escrow account.<br/><br/>"
    "<b>Step 5.</b> Receive quarterly distributions starting Q1 post-close + "
    "annual K-1 for tax filing.", BODY))
elems.append(Spacer(1, 0.4*inch))

# Contact block
contact = Table([[
    Paragraph(
        "<font size=12><b>Yoandy Ross</b></font><br/>"
        "<font size=10>Managing Partner · Ross House Rentals LLC</font><br/><br/>"
        "📧  yoandyross@gmail.com<br/>"
        "🌐  rosshouserentals.com<br/>"
        "📍  Texas (NMLS Licensed)",
        ParagraphStyle("contact", parent=BODY, fontSize=10, alignment=TA_CENTER, textColor=colors.white, leading=16)
    )
]], colWidths=[5.5*inch])
contact.setStyle(TableStyle([
    ("BACKGROUND", (0,0), (-1,-1), DARK),
    ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
    ("TOPPADDING", (0,0), (-1,-1), 20),
    ("BOTTOMPADDING", (0,0), (-1,-1), 20),
    ("LEFTPADDING", (0,0), (-1,-1), 20),
    ("RIGHTPADDING", (0,0), (-1,-1), 20),
]))
elems.append(KeepTogether(contact))
elems.append(Spacer(1, 0.4*inch))

# Disclaimer
elems.append(Paragraph(
    "<b>IMPORTANT DISCLOSURE.</b> This pitch deck is for informational purposes "
    "only and does not constitute an offer to sell or a solicitation of an "
    "offer to buy any securities. Any offer or sale of securities will be "
    "made only pursuant to a Private Placement Memorandum (PPM) and "
    "Subscription Agreement to qualified investors. All investments involve "
    "risk, including the possible loss of principal. Past performance does "
    "not guarantee future results. Projections are based on assumptions "
    "described in the PPM and actual results may vary materially. "
    "Investors should consult their own legal, tax, and financial advisors "
    "before investing.",
    SMALL))


# ────────────── BUILD ──────────────
doc = SimpleDocTemplate(OUT, pagesize=letter,
                         rightMargin=0.55*inch, leftMargin=0.55*inch,
                         topMargin=0.6*inch, bottomMargin=0.5*inch,
                         title="Jasmine Apartments — Investor Pitch Deck",
                         author="Yoandy Ross / Ross House Rentals")
doc.build(elems)

size_kb = os.path.getsize(OUT) / 1024
print(f"✅ PDF generado: {OUT}  ({size_kb:.1f} KB)")


# ────────────── SEND EMAIL ──────────────
SENDGRID_KEY = os.getenv("SENDGRID_API_KEY")
FROM_EMAIL = os.getenv("SENDGRID_FROM_EMAIL") or "info@rosshouserentals.com"
TO_EMAIL = "yoandyross@gmail.com"

if not SENDGRID_KEY:
    print("\n❌ SENDGRID_API_KEY no encontrado.")
    raise SystemExit(0)

from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import (
    Mail, Email, To, Content, Attachment, FileContent, FileName,
    FileType, Disposition,
)

with open(OUT, "rb") as f:
    pdf_bytes = f.read()

attachment = Attachment(
    FileContent(base64.b64encode(pdf_bytes).decode()),
    FileName("Jasmine_Investor_PitchDeck.pdf"),
    FileType("application/pdf"),
    Disposition("attachment"),
)

stamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
subject = "🏢 Jasmine Apartments — Investor Pitch Deck (142 units, TX)"

html = f"""
<div style="font-family:Arial,sans-serif;font-size:14px;color:#222;line-height:1.6;max-width:600px">
  <div style="background:linear-gradient(135deg,#F59E0B,#D97706);padding:20px;border-radius:12px 12px 0 0;color:white">
    <h1 style="margin:0;font-size:22px">Jasmine Apartments — Pitch Deck</h1>
    <p style="margin:6px 0 0 0;opacity:0.9">142-Unit Multifamily Acquisition · Dumas, Texas</p>
  </div>
  <div style="background:white;border:1px solid #E5E7EB;border-top:none;padding:20px;border-radius:0 0 12px 12px">
    <p>Hola Yoandy,</p>
    <p>Aquí va el <b>Investor Pitch Deck</b> para Jasmine Apartments listo para validar apetito antes de invertir en abogados.</p>

    <table style="width:100%;border-collapse:collapse;margin:18px 0">
      <tr>
        <td style="padding:10px;background:#FEF3C7;border-radius:8px;text-align:center;width:25%">
          <div style="font-size:20px;font-weight:bold;color:#F59E0B">$7.5M</div>
          <div style="font-size:10px;color:#6B7280">Purchase</div>
        </td>
        <td style="padding:10px;background:#ECFDF5;border-radius:8px;text-align:center;width:25%">
          <div style="font-size:20px;font-weight:bold;color:#059669">15-18%</div>
          <div style="font-size:10px;color:#6B7280">IRR</div>
        </td>
        <td style="padding:10px;background:#DBEAFE;border-radius:8px;text-align:center;width:25%">
          <div style="font-size:20px;font-weight:bold;color:#1D4ED8">$825K</div>
          <div style="font-size:10px;color:#6B7280">LP Raise</div>
        </td>
        <td style="padding:10px;background:#FCE7F3;border-radius:8px;text-align:center;width:25%">
          <div style="font-size:20px;font-weight:bold;color:#BE185D">2.1x</div>
          <div style="font-size:10px;color:#6B7280">Equity Multiple</div>
        </td>
      </tr>
    </table>

    <h3 style="color:#F59E0B;margin-top:24px">📋 Cómo usarlo</h3>
    <ol>
      <li><b>Empieza por warm contacts</b> — 10–15 personas de confianza (familia, clientes de Ross Tax con buen ingreso, contactos NMLS)</li>
      <li><b>Envíaselo por email o WhatsApp</b> diciendo "evalúa si te interesa, sin compromiso"</li>
      <li><b>Mide apetito</b> en 2 semanas: ¿cuántos dicen "envíame el PPM"?</li>
      <li>Si llegas a <b>5+ soft commitments</b> totalizando $300K+ → vale invertir en el securities attorney ($5–10K)</li>
      <li>Si no llegas → ajustamos el deck o el deal antes de gastar legal</li>
    </ol>

    <h3 style="color:#F59E0B;margin-top:24px">⚠️ Aviso legal importante</h3>
    <p style="font-size:12px;color:#6B7280;background:#F9FAFB;padding:10px;border-left:3px solid #F59E0B;border-radius:4px">
      Este deck es <b>marketing material para sondeo</b>, NO un offering oficial.
      Para aceptar dinero formalmente vas a necesitar:
      <br/>1) LLC Texas + Operating Agreement
      <br/>2) PPM redactado por securities attorney
      <br/>3) Filing Form D con la SEC (gratis, online, ~15 min)
      <br/>4) Verificación de accreditation si haces 506(c)
      <br/><br/>
      <b>NO recibas wires sin tener (1)–(3) firmados.</b>
    </p>

    <p style="margin-top:24px">Si después del sondeo quieres que te conecte con securities attorneys en TX, dímelo.</p>

    <hr style="border:none;border-top:1px solid #E5E7EB;margin:24px 0">
    <p style="font-size:11px;color:#6B7280">
      Generado: {stamp}<br/>
      Ross Tax · Ross House Rentals<br/>
      <i>This pitch deck does not constitute an offer to sell securities.</i>
    </p>
  </div>
</div>
""".strip()

mail = Mail(
    from_email=Email(FROM_EMAIL, "Ross House Rentals"),
    to_emails=To(TO_EMAIL),
    subject=subject,
)
mail.add_content(Content("text/plain",
    f"Jasmine Apartments Investor Pitch Deck — 142 units, $7.5M, 15-18% IRR. PDF adjunto. {stamp}"))
mail.add_content(Content("text/html", html))
mail.attachment = attachment

try:
    sg = SendGridAPIClient(api_key=SENDGRID_KEY)
    resp = sg.send(mail)
    print(f"✅ Email enviado a {TO_EMAIL}  (status={resp.status_code})")
except Exception as e:
    print(f"❌ Error: {e}")
    raise
