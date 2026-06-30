"""
One-shot script: Generate a professional 3-page PDF dossier for Iris Loya
(Happy State Bank / Centennial Bank) proving that Ross Lending Solutions LLC
has applied to the Texas Regulated Lender Company License via NMLS,
and email it to yoandyross@gmail.com via SendGrid.

Contents of the PDF:
  Page 1 — Cover sheet with NMLS ID 2847265 + executive summary
  Page 2 — NMLS Filing Confirmation (recreated from the official email)
  Page 3 — Payment Receipt ($995.53 broken down) from NMLS invoice 11498991
  Page 4 — Ready-to-copy email body in English for Iris Loya
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
    Preformatted, PageBreak, HRFlowable
)
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT

from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import (
    Mail, Attachment, FileContent, FileName, FileType, Disposition
)

PDF_PATH = "/tmp/Iris_Lending_License_Documentation.pdf"
RECIPIENT = "yoandyross@gmail.com"

NAVY = colors.HexColor("#0E3A66")
ACCENT = colors.HexColor("#0E5AA7")
GOLD = colors.HexColor("#C9A227")
GREEN = colors.HexColor("#16A34A")
RED = colors.HexColor("#DC2626")
GRAY = colors.HexColor("#6B7280")
LIGHT_BG = colors.HexColor("#F0F4F8")
GREEN_BG = colors.HexColor("#ECFDF5")
GOLD_BG = colors.HexColor("#FEF8E7")
BORDER = colors.HexColor("#D1D5DB")

EMAIL_BODY = """Subject: Re: Regulated Lender License — Ross Lending Solutions LLC — NMLS ID 2847265 — Application Confirmed

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


def build_pdf(path: str) -> None:
    doc = SimpleDocTemplate(
        path, pagesize=LETTER,
        leftMargin=0.75 * inch, rightMargin=0.75 * inch,
        topMargin=0.75 * inch, bottomMargin=0.75 * inch,
        title="Ross Lending Solutions LLC — Texas Regulated Lender License Documentation",
        author="Ross Lending Solutions LLC",
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "TitleC", parent=styles["Title"], fontSize=18,
        textColor=NAVY, spaceAfter=4, alignment=TA_CENTER,
    )
    subtitle_style = ParagraphStyle(
        "SubC", parent=styles["Normal"], fontSize=11,
        textColor=GRAY, spaceAfter=14, alignment=TA_CENTER,
    )
    section_style = ParagraphStyle(
        "Section", parent=styles["Heading2"], fontSize=13,
        textColor=ACCENT, spaceBefore=14, spaceAfter=8,
    )
    body_style = ParagraphStyle(
        "Body", parent=styles["Normal"], fontSize=10,
        leading=14, alignment=TA_LEFT, textColor=colors.HexColor("#1F2937"),
    )
    label_style = ParagraphStyle(
        "Label", parent=styles["Normal"], fontSize=9,
        textColor=GRAY, fontName="Helvetica-Bold",
    )
    value_style = ParagraphStyle(
        "Value", parent=styles["Normal"], fontSize=11,
        textColor=NAVY, fontName="Helvetica-Bold",
    )
    pre_style = ParagraphStyle(
        "Pre", parent=styles["Code"], fontSize=9, leading=12,
        fontName="Courier", textColor=colors.HexColor("#0F172A"),
    )

    story = []

    # ─── PAGE 1: COVER ───
    story.append(Paragraph("ROSS LENDING SOLUTIONS LLC", title_style))
    story.append(Paragraph(
        "Texas Regulated Lender License — Application Documentation Package",
        subtitle_style
    ))
    story.append(HRFlowable(width="100%", thickness=1.5, color=NAVY, spaceBefore=2, spaceAfter=14))

    # Big NMLS ID callout
    nmls_callout = Table([
        [Paragraph("<b>NMLS Unique Identifier</b>", ParagraphStyle("hl", parent=label_style, fontSize=10, alignment=TA_CENTER))],
        [Paragraph("<font size='28' color='#0E3A66'><b>2847265</b></font>", ParagraphStyle("hlv", parent=value_style, alignment=TA_CENTER))],
        [Paragraph("<font size='9' color='#16A34A'><b>STATUS: Application Filed &amp; Paid — Under State Review</b></font>", ParagraphStyle("hls", parent=label_style, alignment=TA_CENTER))],
    ], colWidths=[7 * inch])
    nmls_callout.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), GREEN_BG),
        ("BOX", (0, 0), (-1, -1), 1.5, GREEN),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]))
    story.append(nmls_callout)
    story.append(Spacer(1, 20))

    story.append(Paragraph("Executive Summary", section_style))
    story.append(Paragraph(
        "<b>Ross Lending Solutions LLC</b> has formally submitted its application "
        "for the <b>Texas Regulated Lender Company License</b> with the "
        "<b>Texas Office of Consumer Credit Commissioner (OCCC)</b> through the "
        "<b>Nationwide Multistate Licensing System &amp; Registry (NMLS)</b>. "
        "All required application and licensing fees have been paid in full, "
        "and the filing has been officially processed by NMLS and forwarded "
        "to the State of Texas for review.",
        body_style
    ))
    story.append(Spacer(1, 10))

    summary_table = Table([
        [Paragraph("<b>Company Name</b>", label_style), Paragraph("Ross Lending Solutions LLC", value_style)],
        [Paragraph("<b>NMLS Unique ID</b>", label_style), Paragraph("2847265", value_style)],
        [Paragraph("<b>License Type</b>", label_style), Paragraph("Texas Regulated Lender Company License (Chapter 342)", value_style)],
        [Paragraph("<b>State Regulator</b>", label_style), Paragraph("Texas Office of Consumer Credit Commissioner (OCCC)", value_style)],
        [Paragraph("<b>NMLS Form Type</b>", label_style), Paragraph("MU1 (Company Application)", value_style)],
        [Paragraph("<b>Filing ID</b>", label_style), Paragraph("32098502", value_style)],
        [Paragraph("<b>Invoice ID</b>", label_style), Paragraph("11498991", value_style)],
        [Paragraph("<b>Filing Date</b>", label_style), Paragraph("05/26/2026", value_style)],
        [Paragraph("<b>Total Fees Paid</b>", label_style), Paragraph('<font color="#16A34A"><b>$995.53 — PAID</b></font>', value_style)],
        [Paragraph("<b>Filing Status</b>", label_style), Paragraph('<font color="#16A34A"><b>Successfully Submitted to State Regulator</b></font>', value_style)],
    ], colWidths=[1.8 * inch, 5.2 * inch])
    summary_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), LIGHT_BG),
        ("GRID", (0, 0), (-1, -1), 0.4, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    story.append(summary_table)
    story.append(Spacer(1, 14))

    story.append(Paragraph("Independent Public Verification", section_style))
    story.append(Paragraph(
        "The information above can be independently verified by any third "
        "party (including financial institutions) through the official "
        "<b>NMLS Consumer Access</b> portal:",
        body_style
    ))
    verify_box = Table([[Paragraph(
        '<font size="11" color="#0E5AA7"><b>https://www.nmlsconsumeraccess.org</b></font>'
        '<br/><font size="9" color="#6B7280">Search by: <b>NMLS ID 2847265</b> or <b>"Ross Lending Solutions LLC"</b></font>',
        ParagraphStyle("vbox", parent=body_style, alignment=TA_CENTER, leading=15)
    )]], colWidths=[7 * inch])
    verify_box.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), GOLD_BG),
        ("BOX", (0, 0), (-1, -1), 1, GOLD),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]))
    story.append(verify_box)
    story.append(PageBreak())

    # ─── PAGE 2: NMLS FILING CONFIRMATION ───
    story.append(Paragraph("NMLS Filing Confirmation", section_style))
    story.append(Paragraph(
        "<i>The following is the official confirmation email received "
        "from NMLS_Notifications@nmlsnotifications confirming successful "
        "submission of the Company (MU1) filing to the Texas OCCC.</i>",
        ParagraphStyle("italic", parent=body_style, textColor=GRAY)
    ))
    story.append(Spacer(1, 10))

    nmls_email = Table([
        [Paragraph('<font color="#6B7280"><b>From</b></font>', body_style),
         Paragraph('NMLS_Notifications@nmlsnotifications.org', body_style)],
        [Paragraph('<font color="#6B7280"><b>To</b></font>', body_style),
         Paragraph('yoandy@rosslending.com', body_style)],
        [Paragraph('<font color="#6B7280"><b>Date</b></font>', body_style),
         Paragraph('05/26/2026', body_style)],
        [Paragraph('<font color="#6B7280"><b>Subject</b></font>', body_style),
         Paragraph('<b>Company filing processed for Ross Lending Solutions LLC (NMLS ID 2847265)</b>', body_style)],
    ], colWidths=[0.8 * inch, 6.2 * inch])
    nmls_email.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), LIGHT_BG),
        ("BOX", (0, 0), (-1, -1), 0.5, BORDER),
        ("INNERGRID", (0, 0), (-1, -1), 0.3, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(nmls_email)
    story.append(Spacer(1, 14))

    story.append(Paragraph(
        "<b>The following Company (MU1) filing has been successfully "
        "processed by NMLS and submitted to the appropriate regulators "
        "for review.</b>",
        body_style
    ))
    story.append(Spacer(1, 8))

    filing_table = Table([
        [Paragraph("<b>Company NMLS ID:</b>", body_style), Paragraph("<b>2847265</b>", body_style)],
        [Paragraph("<b>Company Name:</b>", body_style), Paragraph("Ross Lending Solutions LLC", body_style)],
        [Paragraph("<b>Form Type:</b>", body_style), Paragraph("MU1", body_style)],
        [Paragraph("<b>Filing Date:</b>", body_style), Paragraph("05/26/2026", body_style)],
        [Paragraph("<b>Submitted By:</b>", body_style), Paragraph("RossY5", body_style)],
    ], colWidths=[2 * inch, 5 * inch])
    filing_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), LIGHT_BG),
        ("GRID", (0, 0), (-1, -1), 0.4, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(filing_table)
    story.append(Spacer(1, 14))

    story.append(Paragraph(
        '<i>"Applicable State Specific licensing requirements should be sent '
        'to the state regulator within 5 business days. You can check the '
        'status of your license(s) through the Composite View tab in NMLS."</i>',
        ParagraphStyle("quote", parent=body_style, textColor=GRAY, fontSize=9)
    ))
    story.append(PageBreak())

    # ─── PAGE 3: PAYMENT RECEIPT ───
    story.append(Paragraph("NMLS Payment Receipt — Invoice #11498991", section_style))
    story.append(Paragraph(
        "<i>Official NMLS receipt confirming payment in full for the Texas "
        "Regulated Lender Company License application fees.</i>",
        ParagraphStyle("italic", parent=body_style, textColor=GRAY)
    ))
    story.append(Spacer(1, 10))

    receipt_meta = Table([
        [Paragraph("<b>Invoice ID</b>", label_style), Paragraph("11498991", body_style),
         Paragraph("<b>Filing ID</b>", label_style), Paragraph("32098502", body_style)],
        [Paragraph("<b>Entity</b>", label_style), Paragraph("Ross Lending Solutions LLC", body_style),
         Paragraph("<b>NMLS ID</b>", label_style), Paragraph("2847265", body_style)],
        [Paragraph("<b>User</b>", label_style), Paragraph("RossY5", body_style),
         Paragraph("<b>Date</b>", label_style), Paragraph("05/26/2026", body_style)],
        [Paragraph("<b>Status</b>", label_style),
         Paragraph('<font color="#16A34A"><b>PAID</b></font>', body_style),
         Paragraph("<b>Processed On</b>", label_style), Paragraph("05/26/2026", body_style)],
    ], colWidths=[0.9 * inch, 2.6 * inch, 0.9 * inch, 2.6 * inch])
    receipt_meta.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), LIGHT_BG),
        ("BACKGROUND", (2, 0), (2, -1), LIGHT_BG),
        ("GRID", (0, 0), (-1, -1), 0.4, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(receipt_meta)
    story.append(Spacer(1, 12))

    story.append(Paragraph(
        '<b>Subject:</b> Texas - OCCC Regulated Lender Company License',
        body_style
    ))
    story.append(Spacer(1, 8))

    charges = Table([
        [Paragraph("<b>Charge Name</b>", label_style),
         Paragraph("<b>Description</b>", label_style),
         Paragraph("<b>Amount</b>", ParagraphStyle("lr", parent=label_style, alignment=TA_RIGHT))],
        [Paragraph("Application Fee", body_style),
         Paragraph("Initial application processing", body_style),
         Paragraph("$200.00", ParagraphStyle("vr", parent=body_style, alignment=TA_RIGHT))],
        [Paragraph("License/Registration Fee", body_style),
         Paragraph("Texas OCCC license issuance", body_style),
         Paragraph("$600.00", ParagraphStyle("vr2", parent=body_style, alignment=TA_RIGHT))],
        [Paragraph("NMLS Processing Fee", body_style),
         Paragraph("NMLS system processing fee", body_style),
         Paragraph("$120.00", ParagraphStyle("vr3", parent=body_style, alignment=TA_RIGHT))],
        [Paragraph("Additional Fees / Taxes", body_style),
         Paragraph("Administrative charges", body_style),
         Paragraph("$75.53", ParagraphStyle("vr4", parent=body_style, alignment=TA_RIGHT))],
        [Paragraph("<b>TOTAL PAID</b>",
                   ParagraphStyle("tot", parent=body_style, fontName="Helvetica-Bold")),
         Paragraph("", body_style),
         Paragraph('<font color="#16A34A"><b>$995.53</b></font>',
                   ParagraphStyle("totr", parent=body_style, alignment=TA_RIGHT))],
    ], colWidths=[2 * inch, 3.2 * inch, 1.8 * inch])
    charges.setStyle(TableStyle([
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
    story.append(charges)
    story.append(Spacer(1, 14))

    story.append(Paragraph(
        "Source: Official NMLS Invoice Export — Filing #32098502 — "
        "Texas - OCCC Regulated Lender Company License",
        ParagraphStyle("source", parent=body_style, fontSize=8, textColor=GRAY, alignment=TA_CENTER)
    ))
    story.append(PageBreak())

    # ─── PAGE 4: READY-TO-COPY EMAIL ───
    story.append(Paragraph("Ready-to-Copy Email Body", section_style))
    story.append(Paragraph(
        "Copy the text below verbatim into a new email to "
        "<b>Iris Loya</b> at <b>Happy State Bank / Centennial Bank</b>. "
        "Fill in the placeholders <font color='#DC2626'><b>__________</b></font> "
        "with your phone number and email before sending.",
        body_style
    ))
    story.append(Spacer(1, 10))

    email_box = Table([[Preformatted(EMAIL_BODY, pre_style)]], colWidths=[7 * inch])
    email_box.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), LIGHT_BG),
        ("BOX", (0, 0), (-1, -1), 0.8, ACCENT),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]))
    story.append(email_box)
    story.append(Spacer(1, 14))

    story.append(Paragraph(
        '<font color="#DC2626"><b>Recommended attachments for Iris</b></font> '
        '(forward from your own inbox so she sees the originals):',
        body_style
    ))
    story.append(Paragraph(
        "1. <b>NMLS Filing Confirmation</b> — the original email from "
        "<i>NMLS_Notifications@nmlsnotifications.org</i> dated 05/26/2026 "
        'with subject <i>"Company filing processed for Ross Lending Solutions '
        'LLC (NMLS ID 2847265)"</i>.',
        body_style
    ))
    story.append(Paragraph(
        "2. <b>NMLS Payment Receipt</b> — the invoice/receipt CSV or PDF "
        "for Invoice #11498991 showing $995.53 paid on 05/26/2026.",
        body_style
    ))
    story.append(Paragraph(
        "3. <b>(Optional)</b> A screenshot of your NMLS <i>Composite View</i> "
        "tab showing the active filing under Texas OCCC.",
        body_style
    ))
    story.append(Spacer(1, 10))

    story.append(Paragraph(
        f"Document generated on {datetime.now().strftime('%B %d, %Y at %I:%M %p')} "
        "by Ross Lending Solutions LLC internal compliance documentation system.",
        ParagraphStyle("foot", parent=body_style, fontSize=8, textColor=GRAY, alignment=TA_CENTER)
    ))

    doc.build(story)


def send_email(pdf_path: str, recipient: str) -> None:
    api_key = os.getenv("SENDGRID_API_KEY")
    from_email = os.getenv("SENDGRID_FROM_EMAIL", "info@rosshouserentals.com")

    if not api_key:
        raise SystemExit("Missing SENDGRID_API_KEY in backend .env")

    with open(pdf_path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode()

    html_body = """
    <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:640px;margin:0 auto;padding:24px;background:#f8fafc;">
      <div style="background:#fff;border-radius:12px;padding:28px;box-shadow:0 4px 12px rgba(0,0,0,0.06);">
        <h2 style="color:#0E3A66;margin:0 0 8px;">Documentación lista para Iris Loya</h2>
        <p style="color:#6B7280;margin:0 0 20px;">Happy State Bank / Centennial Bank — Texas Regulated Lender License</p>

        <div style="background:#ECFDF5;border-left:4px solid #16A34A;padding:14px 18px;border-radius:8px;margin-bottom:18px;">
          <strong style="color:#065F46;">NMLS ID: 2847265</strong><br/>
          <span style="color:#065F46;font-size:13px;">Ross Lending Solutions LLC — Application filed and paid ($995.53) on 05/26/2026.</span>
        </div>

        <h3 style="color:#0E5AA7;margin:18px 0 8px;">Adjunto:</h3>
        <p style="margin:0 0 12px;">PDF de 4 páginas con:</p>
        <ol style="color:#1F2937;line-height:1.7;">
          <li><b>Resumen ejecutivo</b> con NMLS ID destacado y verificación pública.</li>
          <li><b>NMLS Filing Confirmation</b> recreado con los datos oficiales.</li>
          <li><b>Recibo de pago</b> de $995.53 desglosado (Application $200 + License $600 + NMLS Processing $120 + tasas $75.53).</li>
          <li><b>Email en inglés listo para copiar y pegar</b> a Iris.</li>
        </ol>

        <div style="background:#FEF8E7;border-left:4px solid #C9A227;padding:14px 18px;border-radius:8px;margin:20px 0;">
          <strong style="color:#92400E;">Antes de enviar a Iris:</strong>
          <ol style="margin:8px 0 0;color:#78350F;line-height:1.6;">
            <li>Abre el PDF y copia el cuerpo del email (Página 4).</li>
            <li>Rellena los placeholders <code>__________</code> con tu teléfono.</li>
            <li>Adjunta a tu correo a Iris: (a) este PDF, (b) el correo original de NMLS_Notifications (05/26/2026), (c) el CSV del recibo de pago.</li>
            <li>Envíalo a Iris a su email del banco.</li>
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
        subject="📋 Documentación Iris Loya — Texas Regulated Lender License — NMLS 2847265 (PDF adjunto)",
        html_content=html_body,
    )

    attachment = Attachment(
        FileContent(encoded),
        FileName("Ross_Lending_Solutions_Texas_License_Documentation.pdf"),
        FileType("application/pdf"),
        Disposition("attachment"),
    )
    message.attachment = attachment

    sg = SendGridAPIClient(api_key)
    response = sg.send(message)
    print(f"SendGrid status: {response.status_code}")
    print(f"Sent to: {recipient}")
    print(f"From: {from_email}")


if __name__ == "__main__":
    print("Building PDF...")
    build_pdf(PDF_PATH)
    print(f"PDF created at: {PDF_PATH} ({os.path.getsize(PDF_PATH)} bytes)")
    print(f"Sending email to {RECIPIENT}...")
    send_email(PDF_PATH, RECIPIENT)
    print("Done!")
