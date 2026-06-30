"""
One-shot script: Generate a PDF with the Xcel Energy Support email draft
and send it to yoandyross@gmail.com via SendGrid.
"""
import os
import base64
from io import BytesIO
from pathlib import Path
from dotenv import load_dotenv

# Load env from backend root
ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(ENV_PATH)

from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Preformatted, PageBreak
)
from reportlab.lib.enums import TA_LEFT

from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import (
    Mail, Attachment, FileContent, FileName, FileType, Disposition
)


PDF_PATH = "/tmp/Xcel_Support_Email_Draft.pdf"
RECIPIENT = "yoandyross@gmail.com"

EMAIL_BODY = """Hello Xcel Energy Green Button Support Team,

I am writing to request assistance with an issue I am experiencing during
the OAuth 2.0 authorization flow for the Green Button Connect My Data API.

ISSUE DESCRIPTION:
When my customers click the "Connect Xcel Energy" button in my application,
they are correctly redirected to the Xcel authorization URL. However, the
SAML Single Sign-On page at "wsservices.xcelenergy.com" loads as a
completely blank white page in all major browsers (Safari, Chrome, Firefox),
preventing customers from entering their Xcel Energy login credentials to
authorize data sharing.

The flow never reaches the consent screen because the login form itself
fails to render. The page returns HTTP 200 but with an empty body.

ACCOUNT / APPLICATION DETAILS:
- Client ID: 9dcf7385177c67119581
- Application Information Id: e4d0930e-7cd4-5290-a207-5b8de92fe9f0
- Application Name (in Developer Portal): Ross House Rentals
- Use Case: Energy monitoring SaaS for rental property management

AUTHORIZATION URL PATTERN USED:
https://api.xcelenergy.com/PEV/oauth/authorize
  ?response_type=code
  &client_id=9dcf7385177c67119581
  &redirect_uri=<MY_REGISTERED_REDIRECT_URI>
  &scope=FB%3D4_5_15_16_18_19_31_32_33_34_35_36%3BIntervalDuration%3D900%3BBlockDuration%3Dmonthly%3BHistoryLength%3D34128000
  &state=<RANDOM_STATE>

WHAT I HAVE ALREADY VERIFIED:
1. Client ID and Client Secret are correct and active in my developer
   account.
2. The redirect_uri in the request matches exactly the one registered in
   the Xcel Developer Portal (including https:// scheme).
3. The OAuth scope string follows the ESPI standard format.
4. Tested in incognito/private mode and across multiple browsers - the
   blank page issue persists.
5. Browser DevTools shows the SAML SSO page returns HTTP 200 but with
   an empty/blank body. No JavaScript errors, no redirects after that point.

REQUESTS:
1. Could you please confirm the approval status of my Service Provider
   registration (Client ID: 9dcf7385177c67119581)? Is my account fully
   authorized for production OAuth flows, or is it still in sandbox /
   pending review state?

2. My developer profile has some fields I am unsure about because my
   business model is a B2B/B2C SaaS platform (not a brick-and-mortar
   business). Specifically:
   - "YELP Id" - I do not have a Yelp business listing because we are
     a software vendor.
   - "License number" - We are not a state-licensed contractor; we are
     a software/technology company.
   Could you confirm whether these fields are required for full
   activation? If so, what value should I enter for a software vendor
   use case?

3. Is there a different authorization endpoint I should be using for
   testing versus production?

4. Could the Xcel team please check the SAML SSO server logs around my
   Client ID (9dcf7385177c67119581) to identify why the login page is
   failing to render?

USE CASE CONTEXT:
My application helps rental property owners and their tenants monitor
electricity consumption (kWh) on a per-property basis, so utility costs
can be transparently billed to tenants based on actual usage rather than
flat estimates. This is a B2B/B2C SaaS platform serving small landlords
and their renters in Xcel Energy service territories.

I would greatly appreciate any guidance you can provide. I am happy to
share additional screenshots, HAR files from browser DevTools, or jump
on a brief screen-share call to debug this together.

Thank you very much for your time and support.

Best regards,

<YOUR FULL NAME>
Ross House Rentals
Phone: <YOUR PHONE NUMBER>
Email: <YOUR EMAIL>
Developer Portal Account: <YOUR XCEL DEV PORTAL LOGIN EMAIL>
"""


def build_pdf(path: str) -> None:
    doc = SimpleDocTemplate(
        path,
        pagesize=LETTER,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
        title="Xcel Energy Green Button Support - Email Draft",
        author="Ross House Rentals",
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "TitleCustom",
        parent=styles["Title"],
        fontSize=16,
        textColor=colors.HexColor("#0E5AA7"),
        spaceAfter=10,
    )
    label_style = ParagraphStyle(
        "Label",
        parent=styles["Normal"],
        fontSize=10,
        textColor=colors.HexColor("#555555"),
        spaceAfter=4,
    )
    value_style = ParagraphStyle(
        "Value",
        parent=styles["Normal"],
        fontSize=11,
        textColor=colors.HexColor("#111111"),
        spaceAfter=10,
    )
    section_style = ParagraphStyle(
        "Section",
        parent=styles["Heading3"],
        fontSize=12,
        textColor=colors.HexColor("#0E5AA7"),
        spaceBefore=12,
        spaceAfter=6,
    )
    body_style = ParagraphStyle(
        "Body",
        parent=styles["Normal"],
        fontSize=10,
        leading=13,
        alignment=TA_LEFT,
    )

    story = []
    story.append(Paragraph("Xcel Energy - Green Button Support Email Draft", title_style))
    story.append(Paragraph(
        "This document contains the ready-to-send email to Xcel Energy's "
        "Green Button Connect support team regarding the SAML SSO blank page issue.",
        body_style
    ))
    story.append(Spacer(1, 12))

    # Email metadata
    story.append(Paragraph("EMAIL HEADER", section_style))
    story.append(Paragraph("To:", label_style))
    story.append(Paragraph("GreenButtonConnectSupport@xcelenergy.com", value_style))
    story.append(Paragraph("CC (optional):", label_style))
    story.append(Paragraph("DeveloperSupport@xcelenergy.com", value_style))
    story.append(Paragraph("Subject:", label_style))
    story.append(Paragraph(
        "Green Button Connect - SAML SSO Blank Page During OAuth Authorization "
        "Flow (Client ID: 9dcf7385177c67119581)",
        value_style
    ))

    story.append(Spacer(1, 8))
    story.append(Paragraph("EMAIL BODY (copy/paste below)", section_style))

    # Use Preformatted to preserve line breaks & indentation
    pre_style = ParagraphStyle(
        "Pre",
        parent=styles["Code"],
        fontName="Courier",
        fontSize=9,
        leading=11,
        textColor=colors.HexColor("#222222"),
    )
    story.append(Preformatted(EMAIL_BODY, pre_style))

    story.append(PageBreak())
    story.append(Paragraph("Placeholders You Still Need To Fill", section_style))
    placeholders = [
        ("<MY_REGISTERED_REDIRECT_URI>", "The Redirect URI you registered in the Xcel Developer Portal"),
        ("<YOUR FULL NAME>", "Your full legal name"),
        ("<YOUR PHONE NUMBER>", "Your contact phone number"),
        ("<YOUR EMAIL>", "Your contact email address"),
        ("<YOUR XCEL DEV PORTAL LOGIN EMAIL>", "The email you use to log in to the Xcel Developer Portal"),
    ]
    for tag, desc in placeholders:
        story.append(Paragraph(f"<b>{tag}</b>", body_style))
        story.append(Paragraph(desc, label_style))
        story.append(Spacer(1, 6))

    story.append(Spacer(1, 10))
    story.append(Paragraph("Security Reminder", section_style))
    story.append(Paragraph(
        "Do NOT include your Client Secret or Registration Token in this email. "
        "Only the Client ID and Application Information Id are safe to share with support.",
        body_style
    ))

    doc.build(story)


def send_email(pdf_path: str, recipient: str) -> None:
    api_key = os.getenv("SENDGRID_API_KEY")
    from_email = os.getenv("SENDGRID_FROM_EMAIL", "info@rosshouserentals.com")
    if not api_key:
        raise RuntimeError("SENDGRID_API_KEY missing in environment")

    with open(pdf_path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode()

    html_body = """
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 600px; margin: 0 auto;">
      <h2 style="color: #0E5AA7;">Borrador de Email para Xcel Energy</h2>
      <p>Hola Yoandy,</p>
      <p>Adjunto encontrarás el <strong>borrador del email en inglés</strong> listo
      para enviar al equipo de soporte de Xcel Energy Green Button Connect,
      explicando el problema de la pantalla en blanco del SAML SSO.</p>
      <h3 style="color: #0E5AA7;">Cómo usarlo:</h3>
      <ol>
        <li>Abre el PDF adjunto.</li>
        <li>Copia el cuerpo del email del PDF.</li>
        <li>Rellena los marcadores (entre &lt; &gt;) con tus datos personales.</li>
        <li>Envíalo a <strong>GreenButtonConnectSupport@xcelenergy.com</strong>.</li>
      </ol>
      <p style="background:#FFF8E1;padding:12px;border-left:4px solid #F5A623;border-radius:4px;">
        <strong>Recordatorio de seguridad:</strong> NO incluyas tu Client Secret
        ni Registration Token en el email. Solo el Client ID y Application
        Information Id son seguros de compartir con soporte.
      </p>
      <p>Saludos,<br/>Equipo Ross House Rentals</p>
    </div>
    """

    message = Mail(
        from_email=from_email,
        to_emails=recipient,
        subject="Borrador Email Xcel Support - Ross House Rentals (PDF Adjunto)",
        html_content=html_body,
    )

    attachment = Attachment(
        FileContent(encoded),
        FileName("Xcel_Support_Email_Draft.pdf"),
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
    print("Generating PDF...")
    build_pdf(PDF_PATH)
    print(f"PDF created at: {PDF_PATH} ({os.path.getsize(PDF_PATH)} bytes)")

    print(f"Sending email to {RECIPIENT}...")
    send_email(PDF_PATH, RECIPIENT)
    print("Done!")
