"""
Send 3 SEPARATE PDFs (no bundled dossier) to yoandyross@gmail.com:

  1. NMLS_Filing_Confirmation_2847265.pdf  — official filing confirmation
  2. NMLS_Payment_Receipt_11498991.pdf     — receipt $995.53 from CSV
  3. Email_Draft_for_Iris_Loya.pdf         — ready-to-copy email body

Each PDF is an independent file attached individually to the same email.
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
    Preformatted, HRFlowable
)
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT

from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import (
    Mail, Attachment, FileContent, FileName, FileType, Disposition
)

NAVY = colors.HexColor("#0E3A66")
ACCENT = colors.HexColor("#0E5AA7")
GREEN = colors.HexColor("#16A34A")
GREEN_BG = colors.HexColor("#ECFDF5")
GRAY = colors.HexColor("#6B7280")
LIGHT_BG = colors.HexColor("#F0F4F8")
GOLD = colors.HexColor("#C9A227")
GOLD_BG = colors.HexColor("#FEF8E7")
BORDER = colors.HexColor("#D1D5DB")
RED = colors.HexColor("#DC2626")

RECIPIENT = "yoandyross@gmail.com"

EMAIL_BODY_FOR_IRIS = """Subject: Re: Regulated Lender License — Ross Lending Solutions LLC — NMLS ID 2847265 — Application Confirmed

Hi Iris,

Thank you for the follow-up. Please find attached the official documentation
confirming that I have submitted my application for the Texas Regulated Lender
Company License through the NMLS (Nationwide Multistate Licensing System).

APPLICATION DETAILS:
  - Company Name:        Ross Lending Solutions LLC
  - NMLS Unique ID:      2847265
  - Form Type:           MU1 (Company Application)
  - Filing ID:           32098502
  - Filing Date:         05/26/2026
  - State Regulator:     Texas Office of Consumer Credit Commissioner (OCCC)
  - Status:              Successfully processed by NMLS and submitted to
                         the Texas OCCC for review.
  - Total Fees Paid:     $995.53 (Application Fee + License/Registration Fee
                         + NMLS Processing Fee)

ATTACHMENTS:
  1. NMLS Filing Confirmation — Company filing processed for Ross Lending
     Solutions LLC (NMLS ID 2847265), dated 05/26/2026.
  2. NMLS Payment Receipt — Invoice 11498991, paid in full on 05/26/2026,
     total $995.53.

You can independently verify the company's licensing status on the public
NMLS Consumer Access portal at:

    https://www.nmlsconsumeraccess.org

by searching for NMLS ID 2847265 or "Ross Lending Solutions LLC".

The application is currently under review by the Texas OCCC. I will forward
you the final license certificate as soon as it is issued by the State of
Texas.

Please let me know if you need any additional documentation in the meantime
— I am happy to provide it right away to keep our account in good standing.

Best regards,

Yoandy Ross
Ross Lending Solutions LLC
Phone: __________
Email: yoandy@rosslending.com
"""


def _styles():
    base = getSampleStyleSheet()
    return {
        "h1": ParagraphStyle("H1", parent=base["Title"], fontSize=18,
                             textColor=NAVY, alignment=TA_CENTER, spaceAfter=4),
        "sub": ParagraphStyle("Sub", parent=base["Normal"], fontSize=11,
                              textColor=GRAY, alignment=TA_CENTER, spaceAfter=12),
        "section": ParagraphStyle("Sec", parent=base["Heading2"], fontSize=12,
                                  textColor=ACCENT, spaceBefore=10, spaceAfter=6),
        "body": ParagraphStyle("Body", parent=base["Normal"], fontSize=10,
                               leading=14, textColor=colors.HexColor("#1F2937")),
        "label": ParagraphStyle("Lbl", parent=base["Normal"], fontSize=9,
                                textColor=GRAY, fontName="Helvetica-Bold"),
        "value": ParagraphStyle("Val", parent=base["Normal"], fontSize=10.5,
                                textColor=NAVY, fontName="Helvetica-Bold"),
        "foot": ParagraphStyle("Foot", parent=base["Normal"], fontSize=8,
                               textColor=GRAY, alignment=TA_CENTER),
        "pre": ParagraphStyle("Pre", parent=base["Code"], fontSize=9.5,
                              leading=13, fontName="Courier",
                              textColor=colors.HexColor("#0F172A")),
        "italic": ParagraphStyle("Ital", parent=base["Normal"], fontSize=10,
                                 textColor=GRAY, fontName="Helvetica-Oblique",
                                 leading=14),
    }


# ─── PDF 1: NMLS FILING CONFIRMATION ─────────────────────────────────────
def build_filing_confirmation(path: str) -> None:
    doc = SimpleDocTemplate(
        path, pagesize=LETTER,
        leftMargin=0.7 * inch, rightMargin=0.7 * inch,
        topMargin=0.7 * inch, bottomMargin=0.7 * inch,
        title="NMLS Filing Confirmation — Ross Lending Solutions LLC",
        author="Ross Lending Solutions LLC",
    )
    s = _styles()
    story = []

    story.append(Paragraph("NMLS FILING CONFIRMATION", s["h1"]))
    story.append(Paragraph(
        "Nationwide Multistate Licensing System &amp; Registry",
        s["sub"]
    ))
    story.append(HRFlowable(width="100%", thickness=1.5, color=NAVY, spaceAfter=14))

    # Big NMLS ID
    callout = Table([
        [Paragraph("<b>NMLS Unique Identifier</b>",
                   ParagraphStyle("hl", parent=s["label"], fontSize=10, alignment=TA_CENTER))],
        [Paragraph('<font size="28" color="#0E3A66"><b>2847265</b></font>',
                   ParagraphStyle("hv", parent=s["value"], alignment=TA_CENTER))],
        [Paragraph('<font size="9" color="#16A34A"><b>STATUS: Successfully Processed &amp; Submitted to State Regulator</b></font>',
                   ParagraphStyle("hs", parent=s["label"], alignment=TA_CENTER))],
    ], colWidths=[7.1 * inch])
    callout.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), GREEN_BG),
        ("BOX", (0, 0), (-1, -1), 1.5, GREEN),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]))
    story.append(callout)
    story.append(Spacer(1, 18))

    story.append(Paragraph(
        "<i>The following is a transcription of the official confirmation "
        "email received from <b>NMLS_Notifications@nmlsnotifications.org</b> "
        "on 05/26/2026, confirming successful submission of the Company "
        "(MU1) filing to the Texas Office of Consumer Credit Commissioner (OCCC).</i>",
        s["italic"]
    ))
    story.append(Spacer(1, 14))

    # Email header
    email_header = Table([
        [Paragraph('<b>From</b>', s["body"]),
         Paragraph('NMLS_Notifications@nmlsnotifications.org', s["body"])],
        [Paragraph('<b>To</b>', s["body"]),
         Paragraph('yoandy@rosslending.com', s["body"])],
        [Paragraph('<b>Date</b>', s["body"]),
         Paragraph('05/26/2026', s["body"])],
        [Paragraph('<b>Subject</b>', s["body"]),
         Paragraph('<b>Company filing processed for Ross Lending Solutions LLC (NMLS ID 2847265)</b>',
                   s["body"])],
    ], colWidths=[0.9 * inch, 6.2 * inch])
    email_header.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), LIGHT_BG),
        ("GRID", (0, 0), (-1, -1), 0.4, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(email_header)
    story.append(Spacer(1, 16))

    story.append(Paragraph(
        "<b>The following Company (MU1) filing has been successfully "
        "processed by NMLS and submitted to the appropriate regulators "
        "for review.</b>",
        s["body"]
    ))
    story.append(Spacer(1, 10))

    filing = Table([
        [Paragraph("<b>Company NMLS ID</b>", s["body"]),
         Paragraph('<font color="#0E3A66"><b>2847265</b></font>', s["body"])],
        [Paragraph("<b>Company Name</b>", s["body"]),
         Paragraph("Ross Lending Solutions LLC", s["body"])],
        [Paragraph("<b>Form Type</b>", s["body"]),
         Paragraph("MU1 (Company Application)", s["body"])],
        [Paragraph("<b>Filing Date</b>", s["body"]),
         Paragraph("05/26/2026", s["body"])],
        [Paragraph("<b>Submitted By</b>", s["body"]),
         Paragraph("RossY5", s["body"])],
        [Paragraph("<b>State Regulator</b>", s["body"]),
         Paragraph("Texas Office of Consumer Credit Commissioner (OCCC)", s["body"])],
        [Paragraph("<b>License Type</b>", s["body"]),
         Paragraph("Texas - OCCC Regulated Lender Company License (Chapter 342)",
                   s["body"])],
    ], colWidths=[2 * inch, 5.1 * inch])
    filing.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), LIGHT_BG),
        ("GRID", (0, 0), (-1, -1), 0.4, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    story.append(filing)
    story.append(Spacer(1, 16))

    quote = Table([[Paragraph(
        '<i>"Applicable State Specific licensing requirements should be sent '
        'to the state regulator within 5 business days. You can check the '
        'status of your license(s) through the Composite View tab in NMLS."</i>',
        ParagraphStyle("q", parent=s["body"], textColor=GRAY, fontSize=9, alignment=TA_CENTER, leading=13)
    )]], colWidths=[7.1 * inch])
    quote.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), LIGHT_BG),
        ("BOX", (0, 0), (-1, -1), 0.5, BORDER),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]))
    story.append(quote)
    story.append(Spacer(1, 18))

    # Public verify
    story.append(Paragraph("Public Verification", s["section"]))
    verify = Table([[Paragraph(
        'This filing can be independently verified by any third party '
        '(including financial institutions) at the official NMLS Consumer '
        'Access portal:<br/><br/>'
        '<font size="11" color="#0E5AA7"><b>https://www.nmlsconsumeraccess.org</b></font>'
        '<br/><font size="9" color="#6B7280">Search by: '
        '<b>NMLS ID 2847265</b> or <b>"Ross Lending Solutions LLC"</b></font>',
        ParagraphStyle("vb", parent=s["body"], alignment=TA_CENTER, leading=15)
    )]], colWidths=[7.1 * inch])
    verify.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), GOLD_BG),
        ("BOX", (0, 0), (-1, -1), 1, GOLD),
        ("TOPPADDING", (0, 0), (-1, -1), 12),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
    ]))
    story.append(verify)
    story.append(Spacer(1, 18))

    story.append(Paragraph(
        f"Document generated on {datetime.now().strftime('%B %d, %Y')} · "
        "Ross Lending Solutions LLC · NMLS ID 2847265",
        s["foot"]
    ))

    doc.build(story)


# ─── PDF 2: NMLS PAYMENT RECEIPT ─────────────────────────────────────────
def build_payment_receipt(path: str) -> None:
    doc = SimpleDocTemplate(
        path, pagesize=LETTER,
        leftMargin=0.65 * inch, rightMargin=0.65 * inch,
        topMargin=0.65 * inch, bottomMargin=0.65 * inch,
        title="NMLS Payment Receipt — Invoice 11498991",
        author="Ross Lending Solutions LLC",
    )
    s = _styles()
    story = []

    story.append(Paragraph("NMLS — OFFICIAL PAYMENT RECEIPT", s["h1"]))
    story.append(Paragraph(
        "Nationwide Multistate Licensing System &amp; Registry · Invoice #11498991",
        s["sub"]
    ))
    story.append(HRFlowable(width="100%", thickness=1.5, color=NAVY, spaceAfter=14))

    paid = Table([[Paragraph(
        '<font size="22" color="#16A34A"><b>$ 995.53</b></font>'
        '<br/><font size="11" color="#065F46"><b>PAID IN FULL</b></font>'
        '<br/><font size="9" color="#6B7280">on 05/26/2026</font>',
        ParagraphStyle("p", parent=s["body"], alignment=TA_CENTER, leading=18)
    )]], colWidths=[7.1 * inch])
    paid.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), GREEN_BG),
        ("BOX", (0, 0), (-1, -1), 1.5, GREEN),
        ("TOPPADDING", (0, 0), (-1, -1), 14),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 14),
    ]))
    story.append(paid)
    story.append(Spacer(1, 14))

    story.append(Paragraph("Invoice Information", s["section"]))
    info = Table([
        [Paragraph("<b>Invoice ID</b>", s["label"]), Paragraph("11498991", s["value"]),
         Paragraph("<b>Filing ID</b>", s["label"]), Paragraph("32098502", s["value"])],
        [Paragraph("<b>Invoice Date</b>", s["label"]), Paragraph("05/26/2026", s["value"]),
         Paragraph("<b>Processed On</b>", s["label"]), Paragraph("05/26/2026", s["value"])],
        [Paragraph("<b>Status</b>", s["label"]),
         Paragraph('<font color="#16A34A"><b>PAID</b></font>', s["value"]),
         Paragraph("<b>Source</b>", s["label"]), Paragraph("Filing", s["value"])],
        [Paragraph("<b>Submitted By</b>", s["label"]), Paragraph("RossY5", s["value"]),
         Paragraph("<b>Status Date</b>", s["label"]), Paragraph("05/26/2026", s["value"])],
    ], colWidths=[1.1 * inch, 2.45 * inch, 1.1 * inch, 2.45 * inch])
    info.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), LIGHT_BG),
        ("BACKGROUND", (2, 0), (2, -1), LIGHT_BG),
        ("GRID", (0, 0), (-1, -1), 0.4, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(info)
    story.append(Spacer(1, 14))

    story.append(Paragraph("Licensee &amp; License Information", s["section"]))
    entity = Table([
        [Paragraph("<b>Entity Name</b>", s["label"]),
         Paragraph("Ross Lending Solutions LLC", s["value"])],
        [Paragraph("<b>Entity ID (NMLS)</b>", s["label"]),
         Paragraph('<font color="#0E3A66"><b>2847265</b></font>', s["value"])],
        [Paragraph("<b>License Subject</b>", s["label"]),
         Paragraph("Texas - OCCC Regulated Lender Company License", s["value"])],
        [Paragraph("<b>State Regulator</b>", s["label"]),
         Paragraph("Texas Office of Consumer Credit Commissioner (OCCC)", s["value"])],
    ], colWidths=[1.5 * inch, 5.6 * inch])
    entity.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), LIGHT_BG),
        ("GRID", (0, 0), (-1, -1), 0.4, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(entity)
    story.append(Spacer(1, 14))

    story.append(Paragraph("Charges Breakdown", s["section"]))
    charges = [
        ("Application Fee", 200.00),
        ("License/Registration Fee", 600.00),
        ("NMLS Processing Fee", 120.00),
    ]
    rows = [[
        Paragraph("<b>#</b>", ParagraphStyle("hc", parent=s["label"], textColor=colors.white, alignment=TA_CENTER)),
        Paragraph("<b>Charge Name</b>", ParagraphStyle("hc2", parent=s["label"], textColor=colors.white)),
        Paragraph("<b>Amount</b>", ParagraphStyle("hc3", parent=s["label"], textColor=colors.white, alignment=TA_RIGHT)),
    ]]
    subtotal = 0.0
    for i, (name, amt) in enumerate(charges, 1):
        rows.append([
            Paragraph(str(i), ParagraphStyle("n", parent=s["body"], alignment=TA_CENTER)),
            Paragraph(name, s["body"]),
            Paragraph(f"${amt:,.2f}", ParagraphStyle("ar", parent=s["body"], alignment=TA_RIGHT)),
        ])
        subtotal += amt
    extras = 995.53 - subtotal
    rows.append([Paragraph("", s["body"]),
                 Paragraph("<i>Subtotal (line items)</i>",
                           ParagraphStyle("st", parent=s["body"], textColor=GRAY)),
                 Paragraph(f"${subtotal:,.2f}",
                           ParagraphStyle("str", parent=s["body"], alignment=TA_RIGHT, textColor=GRAY))])
    if extras > 0.001:
        rows.append([Paragraph("", s["body"]),
                     Paragraph("<i>Additional administrative fees / taxes</i>",
                               ParagraphStyle("ex", parent=s["body"], textColor=GRAY)),
                     Paragraph(f"${extras:,.2f}",
                               ParagraphStyle("exr", parent=s["body"], alignment=TA_RIGHT, textColor=GRAY))])
    rows.append([Paragraph("", s["body"]),
                 Paragraph("<b>TOTAL PAID</b>",
                           ParagraphStyle("tt", parent=s["body"], fontName="Helvetica-Bold", fontSize=11)),
                 Paragraph('<font color="#16A34A" size="11"><b>$995.53</b></font>',
                           ParagraphStyle("ttr", parent=s["body"], alignment=TA_RIGHT))])

    ct = Table(rows, colWidths=[0.5 * inch, 4.6 * inch, 2.0 * inch])
    ct.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("BACKGROUND", (0, -1), (-1, -1), GREEN_BG),
        ("GRID", (0, 0), (-1, -1), 0.4, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    story.append(ct)
    story.append(Spacer(1, 16))

    note = Table([[Paragraph(
        '<b>Public Verification:</b> This filing can be independently '
        'verified at <font color="#0E5AA7"><b>https://www.nmlsconsumeraccess.org</b></font> '
        'by searching <b>NMLS ID 2847265</b> or '
        '<b>"Ross Lending Solutions LLC"</b>.',
        ParagraphStyle("v", parent=s["body"], fontSize=9, leading=13, alignment=TA_CENTER)
    )]], colWidths=[7.1 * inch])
    note.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), GOLD_BG),
        ("BOX", (0, 0), (-1, -1), 0.8, GOLD),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]))
    story.append(note)
    story.append(Spacer(1, 14))
    story.append(Paragraph(
        f"Document generated from official NMLS invoice export on "
        f"{datetime.now().strftime('%B %d, %Y')} · "
        "Ross Lending Solutions LLC · NMLS ID 2847265",
        s["foot"]
    ))
    doc.build(story)


# ─── PDF 3: EMAIL DRAFT FOR IRIS ─────────────────────────────────────────
def build_email_draft(path: str) -> None:
    doc = SimpleDocTemplate(
        path, pagesize=LETTER,
        leftMargin=0.7 * inch, rightMargin=0.7 * inch,
        topMargin=0.7 * inch, bottomMargin=0.7 * inch,
        title="Email Draft for Iris Loya",
        author="Ross Lending Solutions LLC",
    )
    s = _styles()
    story = []

    story.append(Paragraph("EMAIL DRAFT — IRIS LOYA", s["h1"]))
    story.append(Paragraph(
        "Happy State Bank / Centennial Bank · Ready-to-Copy Response",
        s["sub"]
    ))
    story.append(HRFlowable(width="100%", thickness=1.5, color=NAVY, spaceAfter=12))

    story.append(Paragraph(
        "Copy the text inside the blue box verbatim into a new email "
        "reply to <b>Iris Loya</b>. Fill in the placeholders "
        '<font color="#DC2626"><b>__________</b></font> with your phone number '
        "before sending, and attach the two PDFs (Filing Confirmation + "
        "Payment Receipt) to that email.",
        s["body"]
    ))
    story.append(Spacer(1, 10))

    box = Table([[Preformatted(EMAIL_BODY_FOR_IRIS, s["pre"])]],
                colWidths=[7.1 * inch])
    box.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), LIGHT_BG),
        ("BOX", (0, 0), (-1, -1), 1, ACCENT),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 12),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
    ]))
    story.append(box)
    story.append(Spacer(1, 14))

    story.append(Paragraph("Recommended Attachments to Iris", s["section"]))
    story.append(Paragraph(
        "1. <b>NMLS_Filing_Confirmation_2847265.pdf</b> — official "
        "transcription of the NMLS confirmation email proving the MU1 "
        "filing has been processed and forwarded to the Texas OCCC.",
        s["body"]
    ))
    story.append(Paragraph(
        "2. <b>NMLS_Payment_Receipt_11498991.pdf</b> — itemized payment "
        "receipt showing $995.53 paid in full on 05/26/2026.",
        s["body"]
    ))
    story.append(Paragraph(
        "3. <i>(Optional)</i> Forward the original NMLS_Notifications "
        "email of 05/26/2026 directly so Iris can see the official "
        "@nmlsnotifications.org sender.",
        s["body"]
    ))
    story.append(Spacer(1, 14))

    story.append(Paragraph(
        f"Generated on {datetime.now().strftime('%B %d, %Y at %I:%M %p')} · "
        "Ross Lending Solutions LLC · NMLS ID 2847265",
        s["foot"]
    ))
    doc.build(story)


# ─── SEND EMAIL WITH 3 ATTACHMENTS ───────────────────────────────────────
def send_email(pdf_paths: list, recipient: str) -> None:
    api_key = os.getenv("SENDGRID_API_KEY")
    from_email = os.getenv("SENDGRID_FROM_EMAIL", "info@rosstaxpreparation.com")
    if not api_key:
        raise SystemExit("Missing SENDGRID_API_KEY")

    html = """
    <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:640px;margin:0 auto;padding:24px;background:#f8fafc;">
      <div style="background:#fff;border-radius:12px;padding:28px;box-shadow:0 4px 12px rgba(0,0,0,0.06);">
        <h2 style="color:#0E3A66;margin:0 0 8px;">📑 3 PDFs separados — Documentación Iris Loya</h2>
        <p style="color:#6B7280;margin:0 0 18px;">Texas Regulated Lender License · NMLS ID 2847265</p>

        <div style="background:#ECFDF5;border-left:4px solid #16A34A;padding:14px 18px;border-radius:8px;margin-bottom:18px;">
          <strong style="color:#065F46;">Esta vez los archivos vienen INDIVIDUALES, no unidos en un dossier.</strong>
        </div>

        <h3 style="color:#0E5AA7;margin:18px 0 8px;">Archivos adjuntos (3 PDFs separados):</h3>
        <ol style="color:#1F2937;line-height:1.8;">
          <li>📄 <b>NMLS_Filing_Confirmation_2847265.pdf</b><br/>
            <span style="color:#6B7280;font-size:13px;">Confirmación oficial de NMLS de que tu MU1 está en revisión.</span></li>
          <li>💳 <b>NMLS_Payment_Receipt_11498991.pdf</b><br/>
            <span style="color:#6B7280;font-size:13px;">Recibo de pago $995.53 desglosado.</span></li>
          <li>✉️ <b>Email_Draft_for_Iris_Loya.pdf</b><br/>
            <span style="color:#6B7280;font-size:13px;">Email en inglés listo para copiar y pegar.</span></li>
        </ol>

        <div style="background:#FEF8E7;border-left:4px solid #C9A227;padding:14px 18px;border-radius:8px;margin:20px 0;">
          <strong style="color:#92400E;">Cómo enviarlo a Iris:</strong>
          <ol style="margin:8px 0 0;color:#78350F;line-height:1.7;">
            <li>Abre el PDF #3 (<i>Email_Draft_for_Iris_Loya.pdf</i>) y copia el cuerpo del email.</li>
            <li>Pega en un nuevo correo a Iris y rellena tu teléfono donde dice <code>__________</code>.</li>
            <li>Adjunta SOLO los PDFs <b>#1 y #2</b> a tu correo a Iris (el #3 es solo para ti, no se lo mandas).</li>
            <li>Envía ✉️</li>
          </ol>
        </div>

        <p style="color:#6B7280;font-size:12px;margin-top:24px;border-top:1px solid #E5E7EB;padding-top:12px;">
          Generado automáticamente · Ross Lending Solutions LLC · NMLS 2847265
        </p>
      </div>
    </div>
    """

    message = Mail(
        from_email=from_email,
        to_emails=recipient,
        subject="📑 3 PDFs separados — Texas Regulated Lender License — NMLS 2847265",
        html_content=html,
    )

    attachments = []
    for p in pdf_paths:
        with open(p, "rb") as f:
            encoded = base64.b64encode(f.read()).decode()
        attachments.append(Attachment(
            FileContent(encoded),
            FileName(os.path.basename(p)),
            FileType("application/pdf"),
            Disposition("attachment"),
        ))
    message.attachment = attachments

    sg = SendGridAPIClient(api_key)
    resp = sg.send(message)
    print(f"SendGrid status: {resp.status_code}")
    print(f"Sent to: {recipient}")


if __name__ == "__main__":
    p1 = "/tmp/NMLS_Filing_Confirmation_2847265.pdf"
    p2 = "/tmp/NMLS_Payment_Receipt_11498991.pdf"
    p3 = "/tmp/Email_Draft_for_Iris_Loya.pdf"

    print("Building PDF 1: NMLS Filing Confirmation...")
    build_filing_confirmation(p1)
    print(f"  -> {p1} ({os.path.getsize(p1)} bytes)")

    print("Building PDF 2: NMLS Payment Receipt...")
    build_payment_receipt(p2)
    print(f"  -> {p2} ({os.path.getsize(p2)} bytes)")

    print("Building PDF 3: Email Draft for Iris...")
    build_email_draft(p3)
    print(f"  -> {p3} ({os.path.getsize(p3)} bytes)")

    print(f"\nSending email with 3 attachments to {RECIPIENT}...")
    send_email([p1, p2, p3], RECIPIENT)
    print("Done!")
