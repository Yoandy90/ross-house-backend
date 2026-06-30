"""
Jasmine Apartments (Dumas, TX) - Investment Investigation Report PDF
Generate a professional multi-page PDF dossier and email it via SendGrid
to yoandyross@gmail.com.
"""
import os
import base64
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(ENV_PATH)

from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    PageBreak, HRFlowable, KeepTogether
)
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT, TA_JUSTIFY

from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import (
    Mail, Attachment, FileContent, FileName, FileType, Disposition
)

NAVY = colors.HexColor("#0E3A66")
ACCENT = colors.HexColor("#0E5AA7")
GREEN = colors.HexColor("#16A34A")
GREEN_BG = colors.HexColor("#ECFDF5")
RED = colors.HexColor("#DC2626")
RED_BG = colors.HexColor("#FEF2F2")
AMBER = colors.HexColor("#D97706")
AMBER_BG = colors.HexColor("#FEF3C7")
GRAY = colors.HexColor("#6B7280")
DARK = colors.HexColor("#1F2937")
LIGHT_BG = colors.HexColor("#F0F4F8")
BORDER = colors.HexColor("#D1D5DB")
GOLD = colors.HexColor("#C9A227")
GOLD_BG = colors.HexColor("#FEF8E7")

PDF_PATH = "/tmp/Jasmine_Apartments_Investigation_Report.pdf"
RECIPIENT = "yoandyross@gmail.com"


def styles():
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("T", parent=base["Title"], fontSize=20,
                                textColor=NAVY, alignment=TA_CENTER, spaceAfter=6),
        "sub": ParagraphStyle("S", parent=base["Normal"], fontSize=11,
                              textColor=GRAY, alignment=TA_CENTER, spaceAfter=14),
        "h2": ParagraphStyle("H2", parent=base["Heading2"], fontSize=14,
                             textColor=NAVY, spaceBefore=14, spaceAfter=8,
                             fontName="Helvetica-Bold"),
        "h3": ParagraphStyle("H3", parent=base["Heading3"], fontSize=12,
                             textColor=ACCENT, spaceBefore=10, spaceAfter=6,
                             fontName="Helvetica-Bold"),
        "body": ParagraphStyle("B", parent=base["Normal"], fontSize=10,
                               leading=14, textColor=DARK, alignment=TA_JUSTIFY),
        "bullet": ParagraphStyle("Bu", parent=base["Normal"], fontSize=10,
                                 leading=14, textColor=DARK,
                                 leftIndent=14, bulletIndent=2),
        "small": ParagraphStyle("Sm", parent=base["Normal"], fontSize=9,
                                textColor=GRAY, leading=12),
        "label": ParagraphStyle("L", parent=base["Normal"], fontSize=9,
                                textColor=GRAY, fontName="Helvetica-Bold"),
        "value": ParagraphStyle("V", parent=base["Normal"], fontSize=10,
                                textColor=DARK),
        "valueBold": ParagraphStyle("VB", parent=base["Normal"], fontSize=10.5,
                                    textColor=NAVY, fontName="Helvetica-Bold"),
        "foot": ParagraphStyle("F", parent=base["Normal"], fontSize=8,
                               textColor=GRAY, alignment=TA_CENTER),
        "warning": ParagraphStyle("W", parent=base["Normal"], fontSize=10,
                                  leading=14, textColor=RED,
                                  fontName="Helvetica-Bold"),
    }


def kv_table(rows, col_widths=None):
    """Simple key-value 2-column table."""
    s = styles()
    data = []
    for k, v in rows:
        data.append([Paragraph(f"<b>{k}</b>", s["label"]),
                     Paragraph(v, s["value"])])
    cw = col_widths or [1.9 * inch, 5.0 * inch]
    t = Table(data, colWidths=cw)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), LIGHT_BG),
        ("GRID", (0, 0), (-1, -1), 0.4, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


def callout_box(text, bg=GREEN_BG, border=GREEN, text_color=None):
    s = styles()
    style = ParagraphStyle("cb", parent=s["body"], textColor=(text_color or DARK),
                           leading=15, alignment=TA_LEFT)
    t = Table([[Paragraph(text, style)]], colWidths=[6.9 * inch])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg),
        ("BOX", (0, 0), (-1, -1), 1.2, border),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]))
    return t


def build_pdf(path: str) -> None:
    doc = SimpleDocTemplate(
        path, pagesize=LETTER,
        leftMargin=0.65 * inch, rightMargin=0.65 * inch,
        topMargin=0.65 * inch, bottomMargin=0.7 * inch,
        title="Jasmine Apartments - Investment Investigation Report",
        author="Ross House Rentals LLC",
    )
    s = styles()
    story = []

    # ═══ COVER ═══════════════════════════════════════════════════════
    story.append(Spacer(1, 30))
    story.append(Paragraph("INVESTMENT INVESTIGATION REPORT", s["title"]))
    story.append(Paragraph(
        "Jasmine Apartments &amp; Portfolio - Dumas, Texas",
        ParagraphStyle("c", parent=s["sub"], fontSize=14, textColor=NAVY, spaceAfter=4)
    ))
    story.append(Paragraph(
        "1301 S Maddox Ave, Dumas, TX 79029 - 142 units / 28 buildings",
        s["sub"]
    ))
    story.append(HRFlowable(width="100%", thickness=1.5, color=NAVY, spaceAfter=24))

    # Big price callout
    price_box = Table([[
        Paragraph(
            '<font size="11" color="#6B7280"><b>ASKING PRICE</b></font><br/>'
            '<font size="34" color="#0E3A66"><b>$9,940,000</b></font><br/>'
            '<font size="11" color="#16A34A"><b>STATUS: ACTIVELY FOR SALE</b></font><br/>'
            '<font size="9" color="#6B7280">142 units total - $70,000 per unit (avg)</font>',
            ParagraphStyle("pb", parent=s["body"], alignment=TA_CENTER, leading=22)
        )
    ]], colWidths=[6.9 * inch])
    price_box.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), GREEN_BG),
        ("BOX", (0, 0), (-1, -1), 1.5, GREEN),
        ("TOPPADDING", (0, 0), (-1, -1), 18),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 18),
    ]))
    story.append(price_box)
    story.append(Spacer(1, 22))

    # Summary at glance
    story.append(Paragraph("Property At-a-Glance", s["h2"]))
    story.append(kv_table([
        ("Property Name", "Jasmine Apartments (English) / Apartamentos Jazmin (Spanish)"),
        ("Main Address", "1301 S Maddox Ave, Dumas, TX 79029"),
        ("Property Type", "Garden-style multifamily (single-story)"),
        ("Total Units", "142 units across 28 buildings (5 properties bundled)"),
        ("Year Built", "1984 (~42 years old) - Class C asset"),
        ("Occupancy", "93-95% (high), with waiting list of pre-deposited tenants"),
        ("Asking Price", '<font color="#0E3A66"><b>$9,940,000</b></font> (full portfolio)'),
        ("Owner Tenure", "Same group since 2005 (~21 years long-term hold)"),
        ("Reason for Sale", "Seller retired or relocating to another country"),
        ("Recent CapEx", "BRAND-NEW ROOF completed December 2023 (all portfolio)"),
        ("Management", "Yardi 360 platform + full on-site team"),
        ("Listed On", "LoopNet / CoStar - Flyer dated May 2024"),
    ]))
    story.append(Spacer(1, 12))

    story.append(Paragraph(
        f"<i>Report generated on {datetime.now().strftime('%B %d, %Y')} for Yoandy Ross - "
        "Ross House Rentals LLC.</i>",
        s["small"]
    ))
    story.append(PageBreak())

    # ═══ PORTFOLIO COMPOSITION ═══════════════════════════════════════
    story.append(Paragraph("1. Portfolio Composition (CRITICAL)", s["h2"]))

    story.append(callout_box(
        '<b>IMPORTANT:</b> The $9.94M asking price covers a <b>5-property portfolio</b> '
        "(142 total units), <b>not just Jasmine Apartments at 1301 S Maddox</b>. "
        "Jasmine is the largest of the bundle (80 units / 56% of total).",
        bg=AMBER_BG, border=AMBER, text_color=DARK
    ))
    story.append(Spacer(1, 10))

    portfolio = [
        ["Address", "Units", "Unit Mix"],
        ["1301 S Maddox Ave (Jasmine)", "80", "20 fourplexes"],
        ["100 Plum Ave", "20", "4 x 3BR, 4 x 1BR, 12 misc (all 1BA)"],
        ["1725 East 1st St", "20", "4 x 3BR, 4 x 1BR, 12 misc (all 1BA)"],
        ["1801 East 1st St", "12", "2BR / 2BA"],
        ["1401 East St", "10", "2BR / 2BA"],
        ["TOTAL", "142", "28 buildings across 5 properties"],
    ]
    pt = Table(portfolio, colWidths=[2.7 * inch, 1.2 * inch, 3.0 * inch])
    pt.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("BACKGROUND", (0, -1), (-1, -1), GREEN_BG),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.4, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(pt)
    story.append(Spacer(1, 14))

    # Unit mix
    story.append(Paragraph("Total Unit Mix (Across All 142 Units)", s["h3"]))
    unit_mix = [
        ["Unit Type", "# Units", "Sq Ft Range"],
        ["1 BR / 1 BA", "39", "604 - 654 sq ft"],
        ["2 BR / 1 BA", "52", "716 - 1,140 sq ft"],
        ["2 BR / 2 BA", "13", "~1,150 sq ft"],
        ["3 BR / 1 BA", "38", "926 - 982 sq ft"],
        ["TOTAL", "142", ""],
    ]
    ut = Table(unit_mix, colWidths=[2.2 * inch, 1.5 * inch, 3.2 * inch])
    ut.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("BACKGROUND", (0, -1), (-1, -1), LIGHT_BG),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.4, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(ut)
    story.append(Spacer(1, 12))

    # Current rents
    story.append(Paragraph("Current Asking Rents (Per Public Listings)", s["h3"]))
    story.append(Paragraph(
        "Rental rates observed on Apartments.com, Zumper, Trulia, ForRent: "
        "<b>$500 - $695/month</b>. Specific units listed at $675/mo (Zumper) "
        "and $695/mo (ApartmentFinder). Considered <b>affordable Class C</b> "
        "for the Dumas/Amarillo metro area.",
        s["body"]
    ))
    story.append(PageBreak())

    # ═══ FINANCIAL VALUATION ═════════════════════════════════════════
    story.append(Paragraph("2. Independent Market Valuation", s["h2"]))

    story.append(Paragraph("Estimated Net Operating Income (NOI)", s["h3"]))
    noi_table = [
        ["Concept", "Calculation", "Annual Value"],
        ["Gross Potential Income (GPI)", "142 units x $600 avg/mo x 12", "$1,022,400"],
        ["Less: Vacancy & Collection (~7%)", "", "$71,568"],
        ["Effective Gross Income (EGI)", "", "$950,832"],
        ["Less: Operating Expenses (~50% TX Class C)", "", "$475,416"],
        ["NET OPERATING INCOME (NOI)", "", "~$475,000 - $510,000"],
    ]
    nt = Table(noi_table, colWidths=[3.0 * inch, 2.0 * inch, 1.9 * inch])
    nt.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("BACKGROUND", (0, -1), (-1, -1), GREEN_BG),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.4, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (-1, 0), (-1, -1), "RIGHT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(nt)
    story.append(Spacer(1, 14))

    story.append(Paragraph("Valuation by Capitalization Method", s["h3"]))
    cap_table = [
        ["Cap Rate", "Implied Value", "Comment"],
        ["5.5%", "$8.6M - $9.3M", "Aggressive (Texas avg Q1 2026 - CBRE)"],
        ["6.0%", "$7.9M - $8.5M", "Moderate valuation"],
        ["6.5%", "$7.3M - $7.85M", "Class C / tertiary market"],
        ["7.0%", "$6.8M - $7.3M", "Realistic for Dumas (tertiary)"],
        ["7.5%", "$6.3M - $6.8M", "Conservative buyer position"],
    ]
    ct = Table(cap_table, colWidths=[1.2 * inch, 2.0 * inch, 3.7 * inch])
    ct.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.4, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(ct)
    story.append(Spacer(1, 12))

    story.append(Paragraph("Bottom Line", s["h3"]))
    story.append(callout_box(
        '<b>The $9,940,000 asking price is on the AGGRESSIVE (high) end of the range.</b><br/><br/>'
        '- For Class C / 1984-built / tertiary market (Dumas), realistic cap rate is <b>6.5-7.5%</b>.<br/>'
        '- <b>Fair Market Value estimate: $7.0M - $8.5M</b>.<br/>'
        '- <b>Expected negotiation margin: $1.5M - $2.5M below asking</b>.<br/>'
        '- The property has been on market since May 2024 without selling - '
        "this is a clear market signal the price is too high.",
        bg=AMBER_BG, border=AMBER, text_color=DARK
    ))
    story.append(PageBreak())

    # ═══ SELLER & BROKER ═════════════════════════════════════════════
    story.append(Paragraph("3. Seller / Broker Contact Information", s["h2"]))
    story.append(kv_table([
        ("Brokerage", "Kuruvila Realty Associates"),
        ("Contact Person", "Joe Kuruvila"),
        ("Phone (TX)", "<b>(806) 922-7221</b>"),
        ("Cell Phone", "<b>(954) 478-5071</b>"),
        ("Email", "<b>JOE3359@gmail.com</b>"),
        ("Office", "5600 NW 102nd Ave, Suite I, Sunrise, FL 33351"),
        ("Owner LLC", "Not disclosed in public flyer - lookup needed"),
    ]))
    story.append(Spacer(1, 10))

    story.append(Paragraph("How to Identify the Owner LLC", s["h3"]))
    story.append(Paragraph(
        "To identify the legal owning entity, consult the "
        "<b>Moore County Appraisal District (MCAD)</b>:",
        s["body"]
    ))
    story.append(Paragraph(
        '- Web search portal: <font color="#0E5AA7">'
        '<b>https://esearch.co.moore.tx.us</b></font> '
        '(search by address "1301 S Maddox Ave")',
        s["bullet"]
    ))
    story.append(Paragraph(
        "- Phone: <b>(806) 935-4193</b>",
        s["bullet"]
    ))
    story.append(Paragraph(
        "- Office: 419 Success Blvd., Dumas, TX 79029",
        s["bullet"]
    ))
    story.append(Spacer(1, 14))

    # ═══ RECENT IMPROVEMENTS ═════════════════════════════════════════
    story.append(Paragraph("4. Recent Improvements (Value-Add)", s["h2"]))
    improvements = [
        ["Improvement", "Detail", "Status"],
        ["Brand-new roof (entire portfolio)", "Completed December 2023", "Done (less than 1 year old)"],
        ["Central A/C", "10 units with brand-new central AC", "Done"],
        ["Property management", "Yardi 360 platform (premium)", "In place"],
    ]
    it = Table(improvements, colWidths=[2.5 * inch, 2.5 * inch, 1.9 * inch])
    it.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.4, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(it)
    story.append(Spacer(1, 14))

    # Utilities
    story.append(Paragraph("Utilities Configuration", s["h3"]))
    story.append(Paragraph(
        "<b>62 units:</b> Master water meter + individual electric meters - NO gas on property.",
        s["body"]
    ))
    story.append(Paragraph(
        "<b>80 units:</b> Individual electric, gas, and water meters (fully separated).",
        s["body"]
    ))
    story.append(Paragraph(
        "<b>Heating/Cooling:</b> Central heat and cooling throughout.",
        s["body"]
    ))
    story.append(PageBreak())

    # ═══ MARKET DRIVERS ══════════════════════════════════════════════
    story.append(Paragraph("5. Economic Drivers of Dumas, TX", s["h2"]))
    story.append(Paragraph(
        "Dumas is 40 miles north of Amarillo via US-287, with steady population "
        "growth and strong tenant demand driven by major industrial employers:",
        s["body"]
    ))
    story.append(Spacer(1, 8))
    employers = [
        ["Employer", "Employees", "Impact"],
        ["JBS USA (meat processing plant)", "4,000", "Largest local employer"],
        ["Valero Energy (refinery)", "3,500", "Stable, higher salaries"],
        ["Cheesecake Factory", "-", "New operation"],
        ["Project Matador (data center)", "In development", "Future growth"],
    ]
    et = Table(employers, colWidths=[2.6 * inch, 1.4 * inch, 2.9 * inch])
    et.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.4, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(et)
    story.append(Spacer(1, 14))

    story.append(callout_box(
        "Dumas is a <b>steady-growth city</b> with constant demand for affordable "
        "housing for industrial workers. Class C properties here naturally show "
        "high occupancy - aligns with the 93-95% reported by Jasmine.",
        bg=GREEN_BG, border=GREEN
    ))
    story.append(Spacer(1, 14))

    story.append(Paragraph("Amarillo/Dumas Multifamily Market Outlook 2026", s["h3"]))
    story.append(Paragraph(
        "<b>- Average Texas multifamily cap rate (Q1 2026):</b> 5.6%",
        s["bullet"]
    ))
    story.append(Paragraph(
        "<b>- Class C cap rate range:</b> 5.4% - 7.0%",
        s["bullet"]
    ))
    story.append(Paragraph(
        "<b>- Expected vacancy 2025:</b> 6.2% (Jasmine performs better at 5-7%)",
        s["bullet"]
    ))
    story.append(Paragraph(
        "<b>- 2026 outlook:</b> moderate cap rate compression - favors buyers acting now",
        s["bullet"]
    ))
    story.append(Paragraph(
        "<b>- Best Dumas opportunities:</b> Value-add Class B/C, targeting 5.5-7.0% going-in",
        s["bullet"]
    ))
    story.append(PageBreak())

    # ═══ RISKS ═══════════════════════════════════════════════════════
    story.append(Paragraph("6. Due Diligence Risks", s["h2"]))

    risks = [
        ("YELLOW", "Asset Age (1984)",
         "Despite the new roof, plumbing, electrical, and HVAC may be original. "
         "Significant capital expenditure likely in 5-10 years. "
         "Budget $1,500-2,500/unit/year in reserves."),
        ("RED", "Employer concentration",
         "If JBS or Valero scale back operations, vacancy would spike fast. "
         "Tenant base diversification is limited."),
        ("YELLOW", "Remote operation",
         "Florida-based seller with on-site manager works, but a new local "
         "owner (like Ross House Rentals) would have operational advantage."),
        ("YELLOW", "Bundled portfolio",
         "The 5 properties are not contiguous - may complicate management "
         "vs. buying only the main Jasmine site at 1301 S Maddox."),
        ("RED", "Operational complexity",
         "142 units requires dedicated team or professional property "
         "management (~5-7% of gross income). Not a hands-off investment."),
    ]
    for level, title, desc in risks:
        color_bg = RED_BG if level == "RED" else AMBER_BG
        color_border = RED if level == "RED" else AMBER
        emoji = "[HIGH RISK]" if level == "RED" else "[MEDIUM RISK]"
        story.append(callout_box(
            f'<font color="{"#DC2626" if level == "RED" else "#D97706"}">'
            f'<b>{emoji} - {title}</b></font><br/>{desc}',
            bg=color_bg, border=color_border
        ))
        story.append(Spacer(1, 6))
    story.append(PageBreak())

    # ═══ RECOMMENDATIONS ═════════════════════════════════════════════
    story.append(Paragraph("7. Strategic Recommendations", s["h2"]))
    story.append(Paragraph(
        "<b>For Ross House Rentals LLC + Ross Lending Solutions LLC:</b>",
        s["body"]
    ))
    story.append(Spacer(1, 6))

    recs = [
        ("Request T-12 and full rent roll from Joe Kuruvila",
         "Without these documents, any valuation is speculation. Ask for "
         "trailing 12-month income/expense statement and current rent roll in Excel."),
        ("Ask if seller will split the portfolio",
         "Inquire whether he will sell only Jasmine (1301 S Maddox, 80 units) "
         "separately. That alone is ~$5-6M and far more manageable for a first acquisition."),
        ("Suggested initial offer",
         "<b>$7.5M - $8.0M</b> for the full portfolio (~6.0-6.5% cap rate assuming "
         "NOI ~$475K). Negotiation ceiling: $8.5M."),
        ("Leverage Ross Lending Solutions LLC",
         "Once the Texas Regulated Lender License is approved, you can structure "
         "internal financing or partial owner-financing arrangements."),
        ("Visit the property in person before any commitment",
         "Joe Kuruvila lives in Florida - the on-site team must give you the tour. "
         "Inspect roof, electrical, plumbing, parking, common areas."),
        ("Lien and tax history check",
         "Pull Moore County Appraisal District records to verify: current owner LLC, "
         "appraised value vs asking, annual property taxes, any IRS/state liens, "
         "or prior disputes."),
    ]
    for i, (title, desc) in enumerate(recs, 1):
        story.append(Paragraph(f"<b>{i}. {title}</b>", s["body"]))
        story.append(Paragraph(desc, ParagraphStyle("rd", parent=s["body"],
                                                     leftIndent=14, spaceAfter=8)))

    story.append(PageBreak())

    # ═══ NEXT STEPS ══════════════════════════════════════════════════
    story.append(Paragraph("8. Suggested Next Steps", s["h2"]))

    story.append(Paragraph("Action Plan (in priority order):", s["h3"]))

    step1 = (
        '<b>STEP 1 - Call Joe Kuruvila TODAY at (806) 922-7221</b> or '
        '(954) 478-5071 and request:'
    )
    story.append(Paragraph(step1, s["body"]))
    for item in [
        "T-12 (Trailing 12 months income/expenses)",
        "Current rent roll (Excel format)",
        "Confirmation whether portfolio can be split (Jasmine only)",
        "Latest property tax bill (Moore CAD)",
        "Recent inspection reports (HVAC, plumbing, roof warranty)",
        "Capex history for the last 5 years",
    ]:
        story.append(Paragraph(f"- {item}", s["bullet"]))
    story.append(Spacer(1, 10))

    story.append(Paragraph(
        '<b>STEP 2 - Verify on Moore CAD '
        '(<font color="#0E5AA7">https://esearch.co.moore.tx.us</font>):</b>',
        s["body"]
    ))
    for item in [
        "Current owner LLC name",
        "2025 appraised value vs $9.94M asking",
        "Annual property tax burden",
        "Any tax delinquencies or liens",
    ]:
        story.append(Paragraph(f"- {item}", s["bullet"]))
    story.append(Spacer(1, 10))

    story.append(Paragraph(
        "<b>STEP 3 - Connect with a LOCAL Amarillo/Dumas broker</b> for an "
        "independent broker opinion of value (BOV). Suggested firms:",
        s["body"]
    ))
    for item in [
        "Lee &amp; Associates - Amarillo",
        "NAI Amarillo",
        "Coldwell Banker Commercial - Amarillo",
        "Marcus &amp; Millichap - Texas Panhandle desk",
    ]:
        story.append(Paragraph(f"- {item}", s["bullet"]))
    story.append(Spacer(1, 14))

    story.append(callout_box(
        '<b>Report prepared by:</b> Ross House Rentals LLC internal '
        'investment research<br/>'
        '<b>Date:</b> ' + datetime.now().strftime('%B %d, %Y') + '<br/>'
        '<b>Sources:</b> LoopNet flyer (May 2024), Apartments.com, '
        'Zumper, Trulia, ForRent, CBRE Q1 2026 cap rate report, '
        'Moore County Appraisal District, Texas Real Estate Research '
        'Center (TAMU) 2026 Forecast.',
        bg=LIGHT_BG, border=BORDER
    ))
    story.append(Spacer(1, 14))

    story.append(Paragraph(
        '<b>DISCLAIMER:</b> This report is for internal investment '
        'analysis purposes only. All financial estimates (NOI, cap rate '
        'valuation) are inferences based on publicly available data and '
        'industry benchmarks. Actual investment decisions should be based '
        'on verified financial documents (T-12, rent roll, audited '
        'statements) obtained directly from the seller. Consult a licensed '
        "Texas commercial real estate broker and CPA before any binding offer.",
        ParagraphStyle("disc", parent=s["small"], textColor=GRAY,
                       alignment=TA_JUSTIFY, fontSize=8.5, leading=12)
    ))
    story.append(Spacer(1, 14))

    story.append(Paragraph(
        f"Ross House Rentals LLC - Internal Investment Research - "
        f"Page generated on {datetime.now().strftime('%B %d, %Y at %I:%M %p')}",
        s["foot"]
    ))

    doc.build(story)


def send_email(pdf_path: str, recipient: str) -> None:
    api_key = os.getenv("SENDGRID_API_KEY")
    from_email = os.getenv("SENDGRID_FROM_EMAIL", "info@rosshouserentals.com")
    if not api_key:
        raise SystemExit("Missing SENDGRID_API_KEY")

    with open(pdf_path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode()

    html = """
    <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:640px;margin:0 auto;padding:24px;background:#f8fafc;">
      <div style="background:#fff;border-radius:12px;padding:28px;box-shadow:0 4px 12px rgba(0,0,0,0.06);">
        <h2 style="color:#0E3A66;margin:0 0 8px;">🏢 Investigación: Apartamentos Jasmine (Dumas, TX)</h2>
        <p style="color:#6B7280;margin:0 0 20px;">Reporte ejecutivo de inversión inmobiliaria - 8 páginas</p>

        <div style="background:#ECFDF5;border-left:4px solid #16A34A;padding:14px 18px;border-radius:8px;margin-bottom:18px;">
          <strong style="color:#065F46;">$9,940,000 ASKING - 142 unidades - EN VENTA</strong><br/>
          <span style="color:#065F46;font-size:13px;">Portafolio de 5 propiedades en Dumas, TX (Jasmine como principal con 80 unidades)</span>
        </div>

        <h3 style="color:#0E5AA7;margin:18px 0 8px;">El PDF incluye:</h3>
        <ol style="color:#1F2937;line-height:1.8;">
          <li><b>At-a-Glance</b> - Resumen ejecutivo con precio destacado</li>
          <li><b>Composición del portafolio</b> - Las 5 propiedades agrupadas + unit mix</li>
          <li><b>Valoración independiente</b> - NOI estimado + tabla cap rate</li>
          <li><b>Contacto del broker</b> - Joe Kuruvila + cómo encontrar la LLC dueña</li>
          <li><b>Mejoras recientes</b> - Techo nuevo Dic 2023, A/C, Yardi 360</li>
          <li><b>Drivers económicos de Dumas</b> - JBS, Valero, etc</li>
          <li><b>Riesgos de due diligence</b> - 5 riesgos categorizados</li>
          <li><b>Recomendaciones estratégicas</b> - Oferta sugerida + plan de acción</li>
        </ol>

        <div style="background:#FEF3C7;border-left:4px solid #D97706;padding:14px 18px;border-radius:8px;margin:20px 0;">
          <strong style="color:#92400E;">Conclusión clave del análisis:</strong>
          <p style="margin:8px 0 0;color:#78350F;line-height:1.6;">
            El asking de $9.94M está en el extremo alto. Valor justo estimado: <b>$7.0M - $8.5M</b>.
            Oferta inicial recomendada: <b>$7.5M - $8.0M</b>. El hecho de que lleve en el mercado desde Mayo 2024 confirma que está sobrevalorado.
          </p>
        </div>

        <p style="color:#6B7280;font-size:12px;margin-top:24px;border-top:1px solid #E5E7EB;padding-top:12px;">
          Ross House Rentals LLC - Internal Investment Research
        </p>
      </div>
    </div>
    """

    message = Mail(
        from_email=from_email,
        to_emails=recipient,
        subject="🏢 Jasmine Apartments Investigation - Dumas, TX - $9.94M Portfolio (8-page PDF)",
        html_content=html,
    )
    message.attachment = Attachment(
        FileContent(encoded),
        FileName("Jasmine_Apartments_Investigation_Report.pdf"),
        FileType("application/pdf"),
        Disposition("attachment"),
    )
    sg = SendGridAPIClient(api_key)
    resp = sg.send(message)
    print(f"SendGrid status: {resp.status_code}")
    print(f"Sent to: {recipient}")


if __name__ == "__main__":
    print("Building PDF...")
    build_pdf(PDF_PATH)
    print(f"PDF created: {PDF_PATH} ({os.path.getsize(PDF_PATH)} bytes)")
    print(f"Sending to {RECIPIENT}...")
    send_email(PDF_PATH, RECIPIENT)
    print("Done!")
