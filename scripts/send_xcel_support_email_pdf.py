"""Generate a PDF of the Xcel Green Button SAML support email and
email it to the owner via SendGrid (so the user can copy/paste it)."""
import os, base64
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib.colors import HexColor
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Preformatted

import sendgrid
from sendgrid.helpers.mail import (
    Mail, Attachment, FileContent, FileName, FileType, Disposition,
)

PDF_PATH = Path("/app/memory/XCEL_SUPPORT_EMAIL.pdf")
TO_EMAIL = os.environ.get("ARCH_TO_EMAIL", "yoandyross@gmail.com")
FROM_EMAIL = os.environ.get("SENDGRID_FROM_EMAIL", "info@rosstaxpreparation.com")
SENDGRID_KEY = os.environ["SENDGRID_API_KEY"]

SUBJECT_LINE = "SAML SSO blank page — Service Provider Client ID 9dcf7385177c67119581"

EMAIL_BODY = """Hello Green Button team,

I'm Yoandy Ross from Ross House Rentals LLC. Our Service Provider
integration is fully configured and the smoke tests pass:

- GET /ApplicationInformation/e4d0930e-7cd4-5290-a207-5b8de92fe9f0
  -> HTTP 200 (returns valid Atom XML)

- GET /ReadServiceStatus (client_credentials, scope FB=34_35)
  -> HTTP 200 (returns valid Atom XML)

However, when a customer clicks the OAuth authorization link:

  https://myenergy.xcelenergy.com/greenbutton-connect/gbc/espi/1_1/oauth/authorize
  ?response_type=code
  &client_id=9dcf7385177c67119581
  &redirect_uri=https://rosshouserentals.com/tenant/utilities?callback=greenbutton
  &state=...
  &scope=FB=4_5_15_16_18_19_31_32_33_34_35_36;IntervalDuration=900;BlockDuration=monthly;HistoryLength=34128000

They are redirected to your SAML SSO endpoint (wsservices.xcelenergy.com)
which returns a BLANK PAGE - no login form, no error. Tested on Safari iOS,
Chrome desktop, Chrome incognito - all show blank.

Could your team please:
  1. Confirm the SAML configuration for our Service Provider is active
  2. Verify wsservices.xcelenergy.com SSO is reachable from external clients
  3. Check whether our customer-facing scope string requires a different format

The redirect_uri registered on our end is exactly:
  https://rosshouserentals.com/tenant/utilities?callback=greenbutton

Application ID: e4d0930e-7cd4-5290-a207-5b8de92fe9f0
Client ID:      9dcf7385177c67119581
Service Provider name: Ross House Rentals LLC

Thanks for your help,

Yoandy Ross
Ross House Rentals LLC
yoandyross@gmail.com
"""


def build_pdf():
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("H1", parent=styles["Heading1"], fontSize=18,
                        textColor=HexColor("#0f172a"), spaceAfter=12)
    label = ParagraphStyle("Label", parent=styles["BodyText"],
                           fontSize=10, textColor=HexColor("#475569"),
                           spaceAfter=2)
    value = ParagraphStyle("Value", parent=styles["BodyText"],
                           fontSize=11, textColor=HexColor("#0f172a"),
                           spaceAfter=10, leading=14)
    code = ParagraphStyle("Code", parent=styles["Code"], fontName="Courier",
                          fontSize=9, leading=12,
                          backColor=HexColor("#f1f5f9"),
                          textColor=HexColor("#0f172a"),
                          leftIndent=6, rightIndent=6,
                          spaceBefore=4, spaceAfter=8)

    doc = SimpleDocTemplate(
        str(PDF_PATH), pagesize=LETTER,
        leftMargin=0.7*inch, rightMargin=0.7*inch,
        topMargin=0.7*inch, bottomMargin=0.7*inch,
        title="Xcel Support Email - Ross House Rentals",
        author="Yoandy Ross",
    )

    story = [
        Paragraph("Xcel Green Button — SAML SSO Support Request", h1),
        Spacer(1, 8),

        Paragraph("To:", label),
        Paragraph("greenbuttonsupport@xcelenergy.com", value),

        Paragraph("Subject:", label),
        Paragraph(SUBJECT_LINE, value),

        Paragraph("Body (copy and paste this in your email client):", label),
        Spacer(1, 4),
        Preformatted(EMAIL_BODY, code),
    ]
    doc.build(story)
    print(f"PDF: {PDF_PATH} ({PDF_PATH.stat().st_size // 1024} KB)")


def send_email():
    with open(PDF_PATH, "rb") as f:
        encoded = base64.b64encode(f.read()).decode()
    sg = sendgrid.SendGridAPIClient(api_key=SENDGRID_KEY)
    message = Mail(
        from_email=FROM_EMAIL,
        to_emails=TO_EMAIL,
        subject="Email para Xcel Green Button Support (PDF para copiar/pegar)",
        html_content=(
            "<p>Hola Yoandy,</p>"
            "<p>Adjunto el PDF con el texto listo para copiar y pegar en "
            "un email a <b>greenbuttonsupport@xcelenergy.com</b> sobre la "
            "pagina blanca del SAML SSO.</p>"
            "<p>Incluye el subject sugerido y el body completo en ingles "
            "con todos los detalles tecnicos (Client ID, Application ID, "
            "smoke tests passing, redirect URI exacto).</p>"
            "<p>Abre el PDF, copia el texto en tu cliente de email, y "
            "envialo. Esperan responder en hasta 10 dias habiles.</p>"
            "<p>— Generado automaticamente desde la plataforma.</p>"
        ),
    )
    attachment = Attachment(
        FileContent(encoded),
        FileName("Xcel_Support_Email.pdf"),
        FileType("application/pdf"),
        Disposition("attachment"),
    )
    message.attachment = attachment
    resp = sg.send(message)
    print(f"Email -> {TO_EMAIL} | status={resp.status_code}")


if __name__ == "__main__":
    build_pdf()
    send_email()
