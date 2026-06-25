"""
Generates a PDF of /app/memory/ROSS_HOUSE_ARCHITECTURE.md and emails it
to the owner via SendGrid.
"""
import os
import sys
import base64
import re
from pathlib import Path
from dotenv import load_dotenv

# Load env from backend folder
ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(ENV_PATH)

from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib.colors import HexColor, black
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Preformatted, PageBreak,
    Table, TableStyle,
)
from reportlab.lib.enums import TA_LEFT

import sendgrid
from sendgrid.helpers.mail import (
    Mail, Attachment, FileContent, FileName, FileType, Disposition,
)

MD_PATH = Path("/app/memory/ROSS_HOUSE_ARCHITECTURE.md")
PDF_PATH = Path("/app/memory/ROSS_HOUSE_ARCHITECTURE.pdf")
TO_EMAIL = os.environ.get("ARCH_TO_EMAIL", "yoandyross@gmail.com")
FROM_EMAIL = os.environ.get("SENDGRID_FROM_EMAIL", "info@rosstaxpreparation.com")
SENDGRID_KEY = os.environ["SENDGRID_API_KEY"]


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _inline(text: str) -> str:
    """Basic inline markdown -> reportlab markup."""
    t = _escape(text)
    # bold **x**
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    # italic *x* (avoid lists)
    t = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<i>\1</i>", t)
    # inline code `x`
    t = re.sub(r"`([^`]+)`", r'<font face="Courier" color="#b91c1c">\1</font>', t)
    return t


def build_pdf():
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle(
        "H1", parent=styles["Heading1"], fontSize=20, textColor=HexColor("#0f172a"),
        spaceBefore=14, spaceAfter=10,
    )
    h2 = ParagraphStyle(
        "H2", parent=styles["Heading2"], fontSize=15, textColor=HexColor("#1e3a8a"),
        spaceBefore=12, spaceAfter=8,
    )
    h3 = ParagraphStyle(
        "H3", parent=styles["Heading3"], fontSize=12, textColor=HexColor("#1e40af"),
        spaceBefore=8, spaceAfter=4,
    )
    body = ParagraphStyle(
        "Body", parent=styles["BodyText"], fontSize=10, leading=14, alignment=TA_LEFT,
    )
    code = ParagraphStyle(
        "Code", parent=styles["Code"], fontName="Courier", fontSize=8.5, leading=11,
        backColor=HexColor("#f1f5f9"), textColor=HexColor("#0f172a"),
        leftIndent=6, rightIndent=6, spaceBefore=4, spaceAfter=8,
    )

    doc = SimpleDocTemplate(
        str(PDF_PATH), pagesize=LETTER,
        leftMargin=0.7 * inch, rightMargin=0.7 * inch,
        topMargin=0.7 * inch, bottomMargin=0.7 * inch,
        title="Ross House Rentals - Arquitectura",
        author="Yoandy Ross",
    )

    story = []
    md = MD_PATH.read_text(encoding="utf-8")
    lines = md.splitlines()

    in_code = False
    code_buffer = []
    table_buffer = []
    in_table = False

    def flush_table():
        nonlocal table_buffer, in_table
        if not table_buffer:
            in_table = False
            return
        rows = []
        for raw in table_buffer:
            cells = [c.strip() for c in raw.strip().strip("|").split("|")]
            # skip alignment row (---)
            if all(re.fullmatch(r":?-+:?", c) for c in cells):
                continue
            rows.append([Paragraph(_inline(c), body) for c in cells])
        if rows:
            tbl = Table(rows, hAlign="LEFT", colWidths=None, repeatRows=1)
            tbl.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), HexColor("#1e3a8a")),
                ("TEXTCOLOR", (0, 0), (-1, 0), HexColor("#ffffff")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 0.4, HexColor("#cbd5e1")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [HexColor("#ffffff"), HexColor("#f8fafc")]),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]))
            story.append(tbl)
            story.append(Spacer(1, 8))
        table_buffer = []
        in_table = False

    for raw_line in lines:
        line = raw_line.rstrip()

        # code fences
        if line.strip().startswith("```"):
            flush_table()
            if in_code:
                story.append(Preformatted("\n".join(code_buffer), code))
                code_buffer = []
                in_code = False
            else:
                in_code = True
            continue
        if in_code:
            code_buffer.append(raw_line)
            continue

        # table detection
        if line.lstrip().startswith("|") and line.rstrip().endswith("|"):
            in_table = True
            table_buffer.append(line)
            continue
        elif in_table:
            flush_table()

        if not line.strip():
            story.append(Spacer(1, 6))
            continue

        # Headers
        if line.startswith("### "):
            story.append(Paragraph(_inline(line[4:]), h3))
        elif line.startswith("## "):
            story.append(Paragraph(_inline(line[3:]), h2))
        elif line.startswith("# "):
            story.append(Paragraph(_inline(line[2:]), h1))
        elif line.strip() == "---":
            story.append(Spacer(1, 6))
            story.append(Table(
                [[""]], colWidths=[7 * inch],
                style=TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.5, HexColor("#94a3b8"))]),
            ))
            story.append(Spacer(1, 6))
        elif re.match(r"^\s*[-*]\s+", line):
            content = re.sub(r"^\s*[-*]\s+", "", line)
            story.append(Paragraph("&bull;&nbsp;" + _inline(content), body))
        elif re.match(r"^\s*\d+\.\s+", line):
            story.append(Paragraph(_inline(line.strip()), body))
        elif line.startswith("    "):
            # indented (treat as preformatted block)
            story.append(Preformatted(raw_line, code))
        else:
            story.append(Paragraph(_inline(line), body))

    flush_table()
    if in_code and code_buffer:
        story.append(Preformatted("\n".join(code_buffer), code))

    doc.build(story)
    print(f"PDF generated: {PDF_PATH} ({PDF_PATH.stat().st_size // 1024} KB)")


def send_email():
    with open(PDF_PATH, "rb") as f:
        encoded = base64.b64encode(f.read()).decode()

    sg = sendgrid.SendGridAPIClient(api_key=SENDGRID_KEY)

    message = Mail(
        from_email=FROM_EMAIL,
        to_emails=TO_EMAIL,
        subject="Ross House Rentals — Arquitectura del Proyecto (PDF)",
        html_content=(
            "<p>Hola Yoandy,</p>"
            "<p>Adjunto encontrarás el documento <b>Ross House Rentals — Arquitectura "
            "del Proyecto</b> en formato PDF, listo para compartir con otro agente de Emergent.</p>"
            "<p>Incluye:</p>"
            "<ul>"
            "<li>Backend FastAPI (estructura de <code>rental/</code>, routers, "
            "colecciones MongoDB e integraciones).</li>"
            "<li>Web App Next.js 14 (portales Admin / Tenant / Investor / Landlord).</li>"
            "<li>Mobile App Expo Router (tabs, auth, Stripe, OCR, push).</li>"
            "<li>Convenciones globales y blueprint replicable a otros verticales.</li>"
            "</ul>"
            "<p>— Generado automáticamente desde la plataforma.</p>"
        ),
    )

    attachment = Attachment(
        FileContent(encoded),
        FileName("Ross_House_Rentals_Arquitectura.pdf"),
        FileType("application/pdf"),
        Disposition("attachment"),
    )
    message.attachment = attachment

    response = sg.send(message)
    print(f"Email sent to {TO_EMAIL} | status={response.status_code}")


if __name__ == "__main__":
    build_pdf()
    send_email()
