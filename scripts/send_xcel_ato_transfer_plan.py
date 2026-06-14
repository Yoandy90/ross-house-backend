"""
One-shot script: Generate Xcel ATO Transfer Action Plan PDF
and email it to yoandyross@gmail.com via SendGrid.

The PDF includes:
- Executive summary of the user's specific situation (3 properties, 2 LLC-owned, all under personal Xcel)
- Phone call script for 1-800-895-4999
- Email template to send to Xcel Property Manager team
- Document checklist
- Step-by-step timeline
"""
import os
import base64
from pathlib import Path
from dotenv import load_dotenv

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(ENV_PATH)

from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Preformatted, PageBreak,
    Table, TableStyle, ListFlowable, ListItem,
)
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY

from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail, Attachment, FileContent, FileName, FileType, Disposition


PDF_PATH = "/tmp/Xcel_ATO_Transfer_ActionPlan.pdf"
RECIPIENT = "yoandyross@gmail.com"


CALL_SCRIPT = """━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PHONE CALL SCRIPT — Xcel Energy Property Manager Line
Number: 1-800-895-4999
Hours: Mon-Fri 7am-7pm CT, Sat 9am-5pm CT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[After connecting with an agent]

Hello, my name is Yoandy Ross. I am the Managing Member of
Ross House Rentals LLC, an active Texas limited liability
company. I'm calling to request two related changes for two
of my residential rental properties served by SPS in the
Texas Panhandle:

1. TRANSFER the existing residential service accounts from
   my personal name into the name of my LLC, Ross House
   Rentals LLC. Both properties are already titled in the
   LLC's name on the deed.

2. ENROLL both accounts in the AUTOMATIC TURN-ON (ATO)
   property manager program, so that when a tenant moves
   out, service automatically reverts to the LLC and stays
   active without disconnection.

The two properties are:

  • Property A: [STREET ADDRESS], Dumas, TX 79029
    Current account number: [ACCOUNT NUMBER]

  • Property B: [STREET ADDRESS], Dumas, TX 79029
    Current account number: [ACCOUNT NUMBER]

(I have a third property — my personal residence — which I
do NOT want to transfer. It stays under my personal name.)

I can email you the supporting documents right away:
  • Texas Certificate of Formation for Ross House Rentals LLC
  • Federal EIN confirmation letter (CP 575)
  • LLC Operating Agreement
  • My driver's license as Managing Member
  • Completed W-9 form in the LLC's name

Could you please:
  a) Send me the "Billing of Vacant Rental Property
     Agreement" for each property,
  b) Confirm what email address I should send my LLC
     documents to,
  c) Let me know if any security deposit will be required
     given my existing payment history, and
  d) Tell me the expected timeline for the transfer to
     take effect.

Also, I am the registered Green Button Connect Service
Provider for Ross House Rentals (Client ID:
9dcf7385177c67119581). I want to make sure the customer
account on these meters aligns with my Service Provider
registration so the OAuth flow can complete properly.

Thank you for your help.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""


EMAIL_TEMPLATE = """━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
EMAIL TEMPLATE — Backup if phone is busy
To: [Email provided by Xcel agent on call, typically
    propertymanagers@xcelenergy.com or a regional address]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Subject:
Account Transfer + ATO Enrollment Request - Ross House
Rentals LLC - Two Dumas TX Rental Properties

Body:

Hello Xcel Energy Property Manager Team,

I would like to formally request the transfer of two
existing residential service accounts from my personal
name (Yoandy Ross) into my LLC, Ross House Rentals LLC,
and simultaneously enroll both accounts in your Automatic
Turn-On (ATO) program.

ENTITY INFORMATION:
  - Legal Name:    Ross House Rentals LLC
  - State:         Texas
  - EIN:           [YOUR EIN HERE]
  - Managing Member: Yoandy Ross
  - Contact Email: yoandyross@gmail.com
  - Contact Phone: [YOUR PHONE]

PROPERTIES TO TRANSFER (both already titled in the LLC):

  PROPERTY A
    Address: [FULL ADDRESS], Dumas, TX 79029
    Current Xcel Account #: [ACCOUNT NUMBER]
    Premise / Meter #: [METER NUMBER IF KNOWN]

  PROPERTY B
    Address: [FULL ADDRESS], Dumas, TX 79029
    Current Xcel Account #: [ACCOUNT NUMBER]
    Premise / Meter #: [METER NUMBER IF KNOWN]

PROPERTY EXCLUDED FROM TRANSFER:
  My personal residence (not in the LLC) remains under
  my personal name. Please do NOT modify that account.

REQUESTED ACTIONS:
  1. Transfer ownership of the two accounts above from
     "Yoandy Ross" to "Ross House Rentals LLC".
  2. Enroll both accounts in the Automatic Turn-On (ATO)
     Property Manager Program so service automatically
     reverts to the LLC during tenant turnover.
  3. Provide and process the Billing of Vacant Rental
     Property Agreement for each property.
  4. Confirm the new account numbers (if changed) and the
     effective date of transfer.
  5. Let me know if a security deposit is required.

ATTACHED DOCUMENTS:
  - Texas Certificate of Formation (Ross House Rentals LLC)
  - IRS EIN Confirmation Letter (CP 575)
  - LLC Operating Agreement
  - Driver's License of Managing Member
  - W-9 Form (LLC name)

GREEN BUTTON CONTEXT:
I am also the registered Service Provider for Green Button
Connect on behalf of Ross House Rentals
(Client ID: 9dcf7385177c67119581). Aligning the customer
account ownership with my Service Provider registration
will help ensure smooth OAuth authorization for kWh data
sharing.

Please confirm receipt and let me know the expected
processing timeline. I am available to clarify anything by
phone or email.

Thank you for your assistance.

Best regards,
Yoandy Ross
Managing Member, Ross House Rentals LLC
yoandyross@gmail.com
[YOUR PHONE NUMBER]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""


def build_pdf(path: str) -> None:
    doc = SimpleDocTemplate(
        path, pagesize=LETTER,
        leftMargin=0.7 * inch, rightMargin=0.7 * inch,
        topMargin=0.7 * inch, bottomMargin=0.7 * inch,
        title="Xcel ATO Transfer Action Plan - Ross House Rentals",
        author="Ross House Rentals",
    )

    styles = getSampleStyleSheet()
    title = ParagraphStyle("T", parent=styles["Title"], fontSize=18,
                           textColor=colors.HexColor("#0E5AA7"), spaceAfter=4)
    sub = ParagraphStyle("S", parent=styles["Normal"], fontSize=10,
                         textColor=colors.HexColor("#555"), spaceAfter=12)
    section = ParagraphStyle("Sec", parent=styles["Heading2"], fontSize=14,
                             textColor=colors.HexColor("#C8102E"),
                             spaceBefore=14, spaceAfter=8)
    subsection = ParagraphStyle("Sub", parent=styles["Heading3"], fontSize=11,
                                textColor=colors.HexColor("#0E5AA7"),
                                spaceBefore=8, spaceAfter=4)
    body = ParagraphStyle("B", parent=styles["Normal"], fontSize=10,
                          leading=13, alignment=TA_LEFT)
    pre = ParagraphStyle("Pre", parent=styles["Code"], fontName="Courier",
                         fontSize=8, leading=10,
                         textColor=colors.HexColor("#222"))
    callout = ParagraphStyle("Call", parent=styles["Normal"], fontSize=10,
                             leading=14, textColor=colors.HexColor("#A14600"),
                             backColor=colors.HexColor("#FFF8E1"),
                             borderColor=colors.HexColor("#F5A623"),
                             borderWidth=1, borderPadding=8,
                             spaceBefore=8, spaceAfter=8)

    story = []

    # ═══ HEADER ═══
    story.append(Paragraph("Xcel Energy Account Transfer Action Plan", title))
    story.append(Paragraph(
        "Personalized for: Yoandy Ross · Ross House Rentals LLC · "
        "3 properties in Dumas, TX", sub
    ))

    # ═══ EXECUTIVE SUMMARY ═══
    story.append(Paragraph("Executive Summary", section))
    story.append(Paragraph(
        "Your current situation: <b>3 properties total</b>. Two are titled "
        "under <b>Ross House Rentals LLC</b> (rentals), one remains in your "
        "personal name (residence). However, <b>all three Xcel accounts are "
        "in your personal name</b>. This mismatch between property title and "
        "utility account creates a legal risk known as \"piercing the "
        "corporate veil\", which can expose your personal assets if a tenant "
        "sues you.", body
    ))
    story.append(Paragraph(
        "This document provides a ready-to-use phone script, email template, "
        "and document checklist to transfer the two rental property Xcel "
        "accounts into the LLC and enroll them in the Automatic Turn-On (ATO) "
        "program. Your personal residence stays untouched.", body
    ))

    # ═══ SITUATION TABLE ═══
    story.append(Paragraph("Current vs. Target State", section))
    data = [
        ["Property", "Title", "Xcel Account", "Action"],
        ["#1 Personal residence", "Yoandy Ross", "Yoandy Ross", "✓ No change"],
        ["#2 Rental", "Ross House Rentals LLC", "Yoandy Ross", "→ Transfer to LLC + ATO"],
        ["#3 Rental", "Ross House Rentals LLC", "Yoandy Ross", "→ Transfer to LLC + ATO"],
    ]
    t = Table(data, colWidths=[1.5 * inch, 1.8 * inch, 1.5 * inch, 2.0 * inch])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0E5AA7")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
        ("TOPPADDING", (0, 0), (-1, 0), 8),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CCCCCC")),
        ("BACKGROUND", (0, 1), (-1, 1), colors.HexColor("#E8F5E9")),
        ("BACKGROUND", (0, 2), (-1, 3), colors.HexColor("#FFF3E0")),
    ]))
    story.append(t)

    story.append(Paragraph(
        "<b>Important:</b> The electricity tariff for SPS Texas residential "
        "rentals does NOT change when the account moves to the LLC. You will "
        "pay the same Residential Tariff. Only the legal owner of the account "
        "changes.", callout
    ))

    # ═══ DOCUMENT CHECKLIST ═══
    story.append(PageBreak())
    story.append(Paragraph("Documents to Prepare Before Calling", section))
    items = [
        "<b>Texas Certificate of Formation</b> — issued by the Secretary of State "
        "when Ross House Rentals LLC was formed",
        "<b>IRS EIN Confirmation Letter (CP 575)</b> — federal tax ID issued for "
        "the LLC. If you don't have it, retrieve at irs.gov/EIN.",
        "<b>LLC Operating Agreement</b> — even a basic one signed by you as "
        "Managing Member",
        "<b>Your driver's license</b> — proof you are the Managing Member",
        "<b>W-9 form filled out in the LLC's name</b> — single-member LLC: "
        "use the LLC name, check the appropriate box for tax classification",
        "<b>Current Xcel account numbers</b> for properties #2 and #3 — find on "
        "your last bill or in the Xcel online portal",
        "<b>Full street addresses</b> of properties #2 and #3 in Dumas, TX",
    ]
    bullet_items = [
        ListItem(Paragraph(text, body), leftIndent=10, bulletColor=colors.HexColor("#C8102E"))
        for text in items
    ]
    story.append(ListFlowable(bullet_items, bulletType="bullet", start="•"))

    # ═══ CALL SCRIPT ═══
    story.append(PageBreak())
    story.append(Paragraph("Phone Call Script (English)", section))
    story.append(Paragraph(
        "<b>Call:</b> 1-800-895-4999 (Xcel Energy customer service)<br/>"
        "<b>Hours:</b> Mon-Fri 7am-7pm CT, Sat 9am-5pm CT<br/>"
        "<b>Tip:</b> When the IVR asks, say \"property manager\" or \"landlord "
        "agreement\" to be routed correctly.", body
    ))
    story.append(Spacer(1, 6))
    story.append(Preformatted(CALL_SCRIPT, pre))

    # ═══ EMAIL TEMPLATE ═══
    story.append(PageBreak())
    story.append(Paragraph("Email Template (Backup)", section))
    story.append(Paragraph(
        "Use this email if the call doesn't fully resolve the request, or if "
        "the agent asks you to send everything in writing.", body
    ))
    story.append(Spacer(1, 6))
    story.append(Preformatted(EMAIL_TEMPLATE, pre))

    # ═══ TIMELINE ═══
    story.append(PageBreak())
    story.append(Paragraph("Recommended Timeline", section))
    timeline = [
        ["Week", "Action"],
        ["This week", "Wait for Xcel Support response to the Green Button SAML email "
                     "you already sent. In parallel, call the Property Manager Line "
                     "(1-800-895-4999) to start the transfer of properties #2 and #3."],
        ["Week 1-2", "Send LLC documents to the email address Xcel provides. Receive "
                     "and sign the Billing of Vacant Rental Property Agreement (ATO) "
                     "for each property."],
        ["Week 2-3", "Xcel processes the transfer. You receive confirmation emails with "
                     "the new account holder name (Ross House Rentals LLC) and ATO "
                     "enrollment confirmation."],
        ["Week 3-4", "First bill arrives addressed to Ross House Rentals LLC. Update "
                     "your bookkeeping and verify in your Xcel online portal."],
        ["Week 4+",  "Re-authenticate Green Button OAuth from the mobile app so the "
                     "customer account (now the LLC) authorizes data sharing with the "
                     "Service Provider (also the LLC). Cleanest setup possible."],
    ]
    tt = Table(timeline, colWidths=[1.1 * inch, 5.6 * inch])
    tt.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0E5AA7")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CCCCCC")),
        ("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"),
        ("TEXTCOLOR", (0, 1), (0, -1), colors.HexColor("#C8102E")),
    ]))
    story.append(tt)

    # ═══ FOOTER ═══
    story.append(Spacer(1, 16))
    story.append(Paragraph(
        "<b>Expected outcome:</b> Both rental properties served under the LLC, "
        "automatic continuity between tenants (no reconnection fees, no AC loss "
        "in Texas summers), aligned Green Button accounts, full corporate veil "
        "protection, and clean Schedule E tax reporting.", body
    ))
    story.append(Spacer(1, 8))
    story.append(Paragraph(
        "<i>Document generated by Ross House Rentals platform · "
        "For internal use only · Do not share account numbers publicly</i>", sub
    ))

    doc.build(story)


def send_email(pdf_path: str, recipient: str) -> None:
    api_key = os.getenv("SENDGRID_API_KEY")
    from_email = os.getenv("SENDGRID_FROM_EMAIL", "info@rosstaxpreparation.com")
    if not api_key:
        raise RuntimeError("SENDGRID_API_KEY missing")

    with open(pdf_path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode()

    html = """
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 600px; margin: 0 auto;">
      <h2 style="color: #0E5AA7;">Plan de Acción: Transferir Cuentas Xcel a tu LLC</h2>
      <p>Hola Yoandy,</p>
      <p>Adjunto encontrarás el <b>plan de acción completo</b> para transferir las cuentas de Xcel Energy
      de las 2 propiedades de alquiler al nombre de Ross House Rentals LLC, incluyendo:</p>
      <ul>
        <li>✅ Resumen ejecutivo de tu situación actual (3 propiedades)</li>
        <li>✅ Tabla de estado actual vs objetivo</li>
        <li>✅ Lista de documentos a preparar</li>
        <li>✅ Script de llamada en inglés para 1-800-895-4999</li>
        <li>✅ Plantilla de email en inglés (por si te lo piden por escrito)</li>
        <li>✅ Cronograma recomendado (4-5 semanas total)</li>
      </ul>
      <p style="background:#FFF8E1;padding:12px;border-left:4px solid #F5A623;border-radius:4px;">
        <b>Recuerda:</b> Tu casa #1 (personal) NO se transfiere. Solo las #2 y #3 (ya están a nombre de la LLC en el deed).
      </p>
      <p style="background:#E8F5E9;padding:12px;border-left:4px solid #10B981;border-radius:4px;">
        <b>Bono:</b> La tarifa eléctrica NO sube. Es la misma <i>Residential Tariff</i> antes y después.
        Lo único que cambia es el titular legal de la cuenta.
      </p>
      <p>Saludos,<br/>Equipo Ross House Rentals</p>
    </div>
    """

    message = Mail(
        from_email=from_email,
        to_emails=recipient,
        subject="Plan de Acción Xcel: Transferir cuentas a Ross House Rentals LLC (PDF)",
        html_content=html,
    )
    message.attachment = Attachment(
        FileContent(encoded),
        FileName("Ross_House_Xcel_ATO_Transfer_Plan.pdf"),
        FileType("application/pdf"),
        Disposition("attachment"),
    )
    sg = SendGridAPIClient(api_key)
    res = sg.send(message)
    print(f"SendGrid: {res.status_code} → {recipient}")


if __name__ == "__main__":
    print("Generating PDF...")
    build_pdf(PDF_PATH)
    print(f"PDF: {PDF_PATH} ({os.path.getsize(PDF_PATH)} bytes)")
    print(f"Sending to {RECIPIENT}...")
    send_email(PDF_PATH, RECIPIENT)
    print("Done!")
