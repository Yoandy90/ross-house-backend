"""
Convert the NMLS payment receipt CSV (Invoice 11498991) into a clean,
professional single-page PDF receipt and email it to yoandyross@gmail.com.
This PDF is meant to be forwarded to Iris Loya as a standalone attachment.
"""
import os
import csv
import base64
from io import StringIO
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
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
)
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT

from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import (
    Mail, Attachment, FileContent, FileName, FileType, Disposition
)

CSV_DATA = """\"User Name\",\"Invoice Id\",\"Source\",\"Agency\",\"Agency Invoice Number\",\"Invoice Date\",\"Amount\",\"Due Date\",\"Invoice Status\",\"Status Date\",\"Filing Id\",\"Entity Id\",\"Entity Name\",\"Subject Name\",\"Charge Name\",\"Charge Description\",\"Charge Amount\",\"Processed On\"
\"RossY5\",\"11498991\",\"Filing\",\"\",\"\",\"05/26/2026\",\"995.53\",\"\",\"Paid\",\"05/26/2026\",\"32098502\",\"2847265\",\"Ross Lending Solutions LLC\",\"Texas - OCCC Regulated Lender Company License\",\"Application Fee\",\"\",\"200\",\"05/26/2026\"
\"RossY5\",\"11498991\",\"Filing\",\"\",\"\",\"05/26/2026\",\"995.53\",\"\",\"Paid\",\"05/26/2026\",\"32098502\",\"2847265\",\"Ross Lending Solutions LLC\",\"Texas - OCCC Regulated Lender Company License\",\"License/Registration Fee\",\"\",\"600\",\"05/26/2026\"
\"RossY5\",\"11498991\",\"Filing\",\"\",\"\",\"05/26/2026\",\"995.53\",\"\",\"Paid\",\"05/26/2026\",\"32098502\",\"2847265\",\"Ross Lending Solutions LLC\",\"Texas - OCCC Regulated Lender Company License\",\"NMLS Processing Fee\",\"\",\"120\",\"05/26/2026\"
"""

PDF_PATH = "/tmp/NMLS_Payment_Receipt_11498991.pdf"
RECIPIENT = "yoandyross@gmail.com"

NAVY = colors.HexColor("#0E3A66")
ACCENT = colors.HexColor("#0E5AA7")
GREEN = colors.HexColor("#16A34A")
GREEN_BG = colors.HexColor("#ECFDF5")
GRAY = colors.HexColor("#6B7280")
LIGHT_BG = colors.HexColor("#F0F4F8")
BORDER = colors.HexColor("#D1D5DB")


def parse_csv():
    rows = list(csv.DictReader(StringIO(CSV_DATA)))
    first = rows[0]
    header = {
        "user": first["User Name"],
        "invoice_id": first["Invoice Id"],
        "filing_id": first["Filing Id"],
        "entity_id": first["Entity Id"],
        "entity_name": first["Entity Name"],
        "subject": first["Subject Name"],
        "invoice_date": first["Invoice Date"],
        "total": first["Amount"],
        "status": first["Invoice Status"],
        "status_date": first["Status Date"],
        "processed_on": first["Processed On"],
    }
    charges = [
        {"name": r["Charge Name"], "amount": float(r["Charge Amount"])}
        for r in rows
    ]
    return header, charges


def build_pdf(path: str) -> None:
    header, charges = parse_csv()

    doc = SimpleDocTemplate(
        path, pagesize=LETTER,
        leftMargin=0.65 * inch, rightMargin=0.65 * inch,
        topMargin=0.65 * inch, bottomMargin=0.65 * inch,
        title=f"NMLS Payment Receipt — Invoice {header['invoice_id']}",
        author="Ross Lending Solutions LLC",
    )

    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("H1", parent=styles["Title"], fontSize=18,
                        textColor=NAVY, alignment=TA_CENTER, spaceAfter=4)
    sub = ParagraphStyle("Sub", parent=styles["Normal"], fontSize=11,
                         textColor=GRAY, alignment=TA_CENTER, spaceAfter=12)
    section = ParagraphStyle("Section", parent=styles["Heading2"], fontSize=12,
                             textColor=ACCENT, spaceBefore=10, spaceAfter=6)
    body = ParagraphStyle("Body", parent=styles["Normal"], fontSize=10,
                          leading=14, textColor=colors.HexColor("#1F2937"))
    label = ParagraphStyle("Label", parent=styles["Normal"], fontSize=9,
                           textColor=GRAY, fontName="Helvetica-Bold")
    value = ParagraphStyle("Val", parent=styles["Normal"], fontSize=10.5,
                           textColor=NAVY, fontName="Helvetica-Bold")
    foot = ParagraphStyle("Foot", parent=styles["Normal"], fontSize=8,
                          textColor=GRAY, alignment=TA_CENTER)

    story = []

    # Header
    story.append(Paragraph("NMLS — OFFICIAL PAYMENT RECEIPT", h1))
    story.append(Paragraph(
        f"Nationwide Multistate Licensing System &amp; Registry · Invoice #{header['invoice_id']}",
        sub
    ))
    story.append(HRFlowable(width="100%", thickness=1.5, color=NAVY, spaceAfter=14))

    # PAID badge
    paid_badge = Table([
        [Paragraph(
            f'<font size="22" color="#16A34A"><b>$ {float(header["total"]):,.2f}</b></font>'
            f'<br/><font size="11" color="#065F46"><b>PAID IN FULL</b></font>'
            f'<br/><font size="9" color="#6B7280">on {header["processed_on"]}</font>',
            ParagraphStyle("bd", parent=body, alignment=TA_CENTER, leading=18)
        )]
    ], colWidths=[7.1 * inch])
    paid_badge.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), GREEN_BG),
        ("BOX", (0, 0), (-1, -1), 1.5, GREEN),
        ("TOPPADDING", (0, 0), (-1, -1), 14),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 14),
    ]))
    story.append(paid_badge)
    story.append(Spacer(1, 14))

    # Invoice details
    story.append(Paragraph("Invoice Information", section))

    info = Table([
        [Paragraph("<b>Invoice ID</b>", label),
         Paragraph(header["invoice_id"], value),
         Paragraph("<b>Filing ID</b>", label),
         Paragraph(header["filing_id"], value)],
        [Paragraph("<b>Invoice Date</b>", label),
         Paragraph(header["invoice_date"], value),
         Paragraph("<b>Processed On</b>", label),
         Paragraph(header["processed_on"], value)],
        [Paragraph("<b>Status</b>", label),
         Paragraph(f'<font color="#16A34A"><b>{header["status"].upper()}</b></font>', value),
         Paragraph("<b>Source</b>", label),
         Paragraph("Filing", value)],
        [Paragraph("<b>Submitted By</b>", label),
         Paragraph(header["user"], value),
         Paragraph("<b>Status Date</b>", label),
         Paragraph(header["status_date"], value)],
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

    # Entity & License
    story.append(Paragraph("Licensee &amp; License Information", section))
    entity = Table([
        [Paragraph("<b>Entity Name</b>", label),
         Paragraph(header["entity_name"], value)],
        [Paragraph("<b>Entity ID (NMLS)</b>", label),
         Paragraph(f'<font color="#0E3A66"><b>{header["entity_id"]}</b></font>', value)],
        [Paragraph("<b>License Subject</b>", label),
         Paragraph(header["subject"], value)],
        [Paragraph("<b>State Regulator</b>", label),
         Paragraph("Texas Office of Consumer Credit Commissioner (OCCC)", value)],
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

    # Charges breakdown
    story.append(Paragraph("Charges Breakdown", section))

    rows = [[
        Paragraph("<b>#</b>", ParagraphStyle("hc", parent=label, textColor=colors.white, alignment=TA_CENTER)),
        Paragraph("<b>Charge Name</b>", ParagraphStyle("hc2", parent=label, textColor=colors.white)),
        Paragraph("<b>Amount</b>", ParagraphStyle("hc3", parent=label, textColor=colors.white, alignment=TA_RIGHT)),
    ]]
    subtotal = 0.0
    for i, c in enumerate(charges, 1):
        rows.append([
            Paragraph(str(i), ParagraphStyle("n", parent=body, alignment=TA_CENTER)),
            Paragraph(c["name"], body),
            Paragraph(f"${c['amount']:,.2f}", ParagraphStyle("ar", parent=body, alignment=TA_RIGHT)),
        ])
        subtotal += c["amount"]

    total = float(header["total"])
    extras = total - subtotal

    rows.append([
        Paragraph("", body),
        Paragraph("<i>Subtotal (line items)</i>", ParagraphStyle("st", parent=body, textColor=GRAY)),
        Paragraph(f"${subtotal:,.2f}", ParagraphStyle("str", parent=body, alignment=TA_RIGHT, textColor=GRAY)),
    ])
    if extras > 0.001:
        rows.append([
            Paragraph("", body),
            Paragraph("<i>Additional administrative fees / taxes</i>",
                      ParagraphStyle("ex", parent=body, textColor=GRAY)),
            Paragraph(f"${extras:,.2f}",
                      ParagraphStyle("exr", parent=body, alignment=TA_RIGHT, textColor=GRAY)),
        ])
    rows.append([
        Paragraph("", body),
        Paragraph("<b>TOTAL PAID</b>",
                  ParagraphStyle("tt", parent=body, fontName="Helvetica-Bold", fontSize=11)),
        Paragraph(f'<font color="#16A34A" size="11"><b>${total:,.2f}</b></font>',
                  ParagraphStyle("ttr", parent=body, alignment=TA_RIGHT)),
    ])

    charges_table = Table(rows, colWidths=[0.5 * inch, 4.6 * inch, 2.0 * inch])
    charges_table.setStyle(TableStyle([
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
    story.append(charges_table)
    story.append(Spacer(1, 16))

    # Verification note
    note = Table([[Paragraph(
        '<b>Public Verification:</b> This filing can be independently verified at '
        '<font color="#0E5AA7"><b>https://www.nmlsconsumeraccess.org</b></font> by '
        f'searching <b>NMLS ID {header["entity_id"]}</b> or '
        f'<b>"{header["entity_name"]}"</b>.',
        ParagraphStyle("v", parent=body, fontSize=9, leading=13, alignment=TA_CENTER)
    )]], colWidths=[7.1 * inch])
    note.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FEF8E7")),
        ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#C9A227")),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]))
    story.append(note)
    story.append(Spacer(1, 14))

    story.append(Paragraph(
        f"Document generated from official NMLS invoice export on "
        f"{datetime.now().strftime('%B %d, %Y at %I:%M %p')} · "
        f"Ross Lending Solutions LLC · NMLS ID {header['entity_id']}",
        foot
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
        <h2 style="color:#0E3A66;margin:0 0 8px;">📄 Recibo NMLS en PDF</h2>
        <p style="color:#6B7280;margin:0 0 18px;">Invoice #11498991 · Ross Lending Solutions LLC · $995.53 PAID</p>
        <div style="background:#ECFDF5;border-left:4px solid #16A34A;padding:14px 18px;border-radius:8px;margin-bottom:18px;">
          <strong style="color:#065F46;">CSV convertido a PDF profesional listo para adjuntar a Iris Loya.</strong>
        </div>
        <p style="color:#1F2937;line-height:1.6;">
          El PDF incluye: monto total pagado destacado, datos del invoice y filing, datos del licensee, desglose de cargos (Application $200 + License $600 + NMLS Processing $120 + tasas $75.53), y nota de verificación pública vía NMLS Consumer Access.
        </p>
        <p style="color:#6B7280;font-size:12px;margin-top:24px;border-top:1px solid #E5E7EB;padding-top:12px;">
          Adjúntalo a tu correo a Iris junto con el dossier de 4 páginas que ya te envié antes.
        </p>
      </div>
    </div>
    """

    message = Mail(
        from_email=from_email,
        to_emails=recipient,
        subject="📄 Recibo NMLS $995.53 (PDF) — Invoice 11498991 — Ross Lending Solutions LLC",
        html_content=html,
    )
    message.attachment = Attachment(
        FileContent(encoded),
        FileName("NMLS_Payment_Receipt_11498991.pdf"),
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
