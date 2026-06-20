"""
Simplified email draft for Iris Loya — single-page PDF.
Use when attaching only the NMLS email screenshot + CSV receipt to Iris.
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
from reportlab.lib.enums import TA_LEFT, TA_CENTER

from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import (
    Mail, Attachment, FileContent, FileName, FileType, Disposition
)

NAVY = colors.HexColor("#0E3A66")
ACCENT = colors.HexColor("#0E5AA7")
GRAY = colors.HexColor("#6B7280")
LIGHT_BG = colors.HexColor("#F0F4F8")
GREEN_BG = colors.HexColor("#ECFDF5")
GREEN = colors.HexColor("#16A34A")

PDF_PATH = "/tmp/Email_Simplified_for_Iris.pdf"
RECIPIENT = "yoandyross@gmail.com"

EMAIL_BODY = """Subject: Re: Regulated Lender License - Ross Lending Solutions LLC - NMLS ID 2847265

Hi Iris,

Thank you for the follow-up. Attached please find the official
documentation confirming that Ross Lending Solutions LLC has filed
its application for the Texas Regulated Lender Company License
through the NMLS system on 05/26/2026:

  - Screenshot of the NMLS filing confirmation email
    (NMLS ID 2847265, MU1 form submitted to Texas OCCC)

  - NMLS invoice / payment receipt - $995.53 paid in full
    (Invoice #11498991)

You can also verify the company status directly at
https://www.nmlsconsumeraccess.org by searching NMLS ID 2847265
or "Ross Lending Solutions LLC".

The application is currently under review by the Texas OCCC.
I will forward the final license certificate as soon as it is
issued.

Please let me know if you need anything else.

Best regards,

Yoandy Ross
Ross Lending Solutions LLC
Phone: __________
Email: yoandy@rosslending.com
"""


def build_pdf(path: str) -> None:
    doc = SimpleDocTemplate(
        path, pagesize=LETTER,
        leftMargin=0.7 * inch, rightMargin=0.7 * inch,
        topMargin=0.7 * inch, bottomMargin=0.7 * inch,
        title="Email Draft for Iris Loya - Simplified",
        author="Ross Lending Solutions LLC",
    )

    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("H1", parent=styles["Title"], fontSize=18,
                        textColor=NAVY, alignment=TA_CENTER, spaceAfter=4)
    sub = ParagraphStyle("Sub", parent=styles["Normal"], fontSize=11,
                         textColor=GRAY, alignment=TA_CENTER, spaceAfter=12)
    body = ParagraphStyle("Body", parent=styles["Normal"], fontSize=10,
                          leading=14, textColor=colors.HexColor("#1F2937"))
    section = ParagraphStyle("Sec", parent=styles["Heading2"], fontSize=12,
                             textColor=ACCENT, spaceBefore=10, spaceAfter=6)
    pre = ParagraphStyle("Pre", parent=styles["Code"], fontSize=10,
                         leading=14, fontName="Courier",
                         textColor=colors.HexColor("#0F172A"))
    foot = ParagraphStyle("Foot", parent=styles["Normal"], fontSize=8,
                          textColor=GRAY, alignment=TA_CENTER)

    story = []
    story.append(Paragraph("EMAIL DRAFT - IRIS LOYA", h1))
    story.append(Paragraph(
        "Simplified Version - For use with NMLS email screenshot + CSV receipt",
        sub
    ))
    story.append(HRFlowable(width="100%", thickness=1.5, color=NAVY, spaceAfter=14))

    instr = Table([[Paragraph(
        '<b>How to use:</b> Copy the text inside the blue box into a new '
        "reply to Iris Loya. Fill in your phone number where you see "
        '<font color="#DC2626"><b>__________</b></font>. Then attach to '
        "your email: (1) the screenshot of the NMLS confirmation email, "
        "and (2) the CSV invoice receipt. Send it.",
        ParagraphStyle("i", parent=body, leading=15)
    )]], colWidths=[7.1 * inch])
    instr.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), GREEN_BG),
        ("BOX", (0, 0), (-1, -1), 0.8, GREEN),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]))
    story.append(instr)
    story.append(Spacer(1, 14))

    story.append(Paragraph("Email Body (copy verbatim)", section))

    box = Table([[Preformatted(EMAIL_BODY, pre)]], colWidths=[7.1 * inch])
    box.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), LIGHT_BG),
        ("BOX", (0, 0), (-1, -1), 1, ACCENT),
        ("LEFTPADDING", (0, 0), (-1, -1), 14),
        ("RIGHTPADDING", (0, 0), (-1, -1), 14),
        ("TOPPADDING", (0, 0), (-1, -1), 12),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
    ]))
    story.append(box)
    story.append(Spacer(1, 14))

    story.append(Paragraph(
        f"Generated on {datetime.now().strftime('%B %d, %Y at %I:%M %p')} "
        "- Ross Lending Solutions LLC - NMLS ID 2847265",
        foot
    ))
    doc.build(story)


def send_email(pdf_path: str, recipient: str) -> None:
    api_key = os.getenv("SENDGRID_API_KEY")
    from_email = os.getenv("SENDGRID_FROM_EMAIL", "info@rosstaxpreparation.com")
    if not api_key:
        raise SystemExit("Missing SENDGRID_API_KEY")

    with open(pdf_path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode()

    html = """
    <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:640px;margin:0 auto;padding:24px;background:#f8fafc;">
      <div style="background:#fff;border-radius:12px;padding:28px;box-shadow:0 4px 12px rgba(0,0,0,0.06);">
        <h2 style="color:#0E3A66;margin:0 0 8px;">✉️ Email simplificado para Iris</h2>
        <p style="color:#6B7280;margin:0 0 18px;">Versión corta - para usar con screenshot del email NMLS + CSV del recibo</p>

        <div style="background:#ECFDF5;border-left:4px solid #16A34A;padding:14px 18px;border-radius:8px;margin-bottom:18px;">
          <strong style="color:#065F46;">Copia el cuerpo del PDF y adjunta a tu correo a Iris:</strong>
          <ol style="margin:8px 0 0;color:#065F46;line-height:1.7;">
            <li>El screenshot del email de NMLS_Notifications (NMLS ID 2847265)</li>
            <li>El CSV del recibo de pago ($995.53)</li>
          </ol>
        </div>

        <p style="color:#1F2937;line-height:1.6;">
          No te olvides de rellenar tu teléfono donde dice <code>__________</code>.
        </p>

        <p style="color:#6B7280;font-size:12px;margin-top:24px;border-top:1px solid #E5E7EB;padding-top:12px;">
          Ross Lending Solutions LLC - NMLS 2847265
        </p>
      </div>
    </div>
    """

    message = Mail(
        from_email=from_email,
        to_emails=recipient,
        subject="✉️ Email simplificado para Iris - NMLS 2847265 (PDF adjunto)",
        html_content=html,
    )
    message.attachment = Attachment(
        FileContent(encoded),
        FileName("Email_Simplified_for_Iris.pdf"),
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
    print(f"Sending email to {RECIPIENT}...")
    send_email(PDF_PATH, RECIPIENT)
    print("Done!")
