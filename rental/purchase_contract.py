"""
Purchase Contract PDF — Contrato de compraventa en efectivo (AS-IS)
====================================================================
Genera un contrato profesional de compra cash (US Letter) pre-llenado
para las oportunidades del Deal Finder:
  Comprador: Ross House Rentals LLC (o payer configurado)
  Vendedor / propiedad / precio: del lead
  Casa de título: seleccionada del catálogo `title_companies`

NOTA: Es una plantilla informativa tipo contrato cash AS-IS común en
Texas. No sustituye el formulario TREC ni asesoría legal.
"""

from datetime import datetime, timedelta
from io import BytesIO

from reportlab.lib.pagesizes import LETTER
from reportlab.lib.units import inch
from reportlab.lib.colors import HexColor
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_JUSTIFY, TA_CENTER
from reportlab.platypus import (BaseDocTemplate, Frame, PageTemplate,
                                Paragraph, Spacer, Table, TableStyle)

NAVY = HexColor("#0F2A4A")
GOLD = HexColor("#B8860B")
GRAY = HexColor("#555555")
LIGHT = HexColor("#F4F6F9")


def _money(n: float) -> str:
    return f"${n:,.2f}"


def build_contract_pdf(*, buyer: dict, seller_name: str, seller_address: str,
                       property_address: str, legal_description: str,
                       county_name: str, price: float, earnest_money: float,
                       closing_days: int, title_co: dict,
                       title_policy_paid_by: str = "Buyer",
                       special_terms: str = "") -> bytes:
    """Devuelve el PDF (bytes) del contrato de compra cash AS-IS."""
    buf = BytesIO()
    W, H = LETTER

    st_body = ParagraphStyle("body", fontName="Times-Roman", fontSize=10.5,
                             leading=14.5, alignment=TA_JUSTIFY, textColor=HexColor("#1a1a1a"))
    st_sec = ParagraphStyle("sec", fontName="Times-Bold", fontSize=11,
                            leading=14, spaceBefore=10, spaceAfter=3, textColor=NAVY)
    st_small = ParagraphStyle("small", fontName="Times-Italic", fontSize=8.5,
                              leading=11, textColor=GRAY)
    st_title = ParagraphStyle("title", fontName="Times-Bold", fontSize=16,
                              leading=20, alignment=TA_CENTER, textColor=NAVY)
    st_sub = ParagraphStyle("sub", fontName="Times-Roman", fontSize=10,
                            leading=13, alignment=TA_CENTER, textColor=GRAY)

    def _hdr(c, d):
        c.saveState()
        c.setFillColor(NAVY)
        c.rect(0, H - 0.42 * inch, W, 0.42 * inch, stroke=0, fill=1)
        c.setFillColor(HexColor("#FFFFFF"))
        c.setFont("Times-Bold", 9)
        c.drawString(0.9 * inch, H - 0.29 * inch,
                     (buyer.get("company") or buyer.get("name") or "Ross House Rentals LLC").upper())
        c.setFont("Times-Roman", 8)
        c.drawRightString(W - 0.9 * inch, H - 0.29 * inch,
                          "REAL ESTATE PURCHASE AGREEMENT — CASH / AS-IS")
        c.setStrokeColor(GOLD)
        c.setLineWidth(2)
        c.line(0, H - 0.44 * inch, W, H - 0.44 * inch)
        # Footer
        c.setFont("Times-Italic", 7.5)
        c.setFillColor(GRAY)
        c.drawCentredString(W / 2, 0.5 * inch,
                            f"Page {c.getPageNumber()} · {property_address} · "
                            f"Buyer initials: ______   Seller initials: ______")
        c.restoreState()

    frame = Frame(0.9 * inch, 0.75 * inch, W - 1.8 * inch, H - 1.45 * inch, id="main")
    doc = BaseDocTemplate(buf, pagesize=LETTER)
    doc.addPageTemplates([PageTemplate(id="pg", frames=[frame], onPage=_hdr)])

    today = datetime.now()
    closing_date = today + timedelta(days=closing_days)
    buyer_name = buyer.get("company") or buyer.get("name") or "Ross House Rentals LLC"
    buyer_addr = f"{buyer.get('address', '')}, {buyer.get('city', '')}, {buyer.get('state', '')} {buyer.get('zip', '')}".strip(", ")
    tc_name = title_co.get("name", "")
    tc_officer = title_co.get("escrow_officer", "")
    tc_addr = title_co.get("address", "")
    tc_phone = title_co.get("phone", "")

    story = [
        Spacer(1, 4),
        Paragraph("REAL ESTATE PURCHASE AGREEMENT", st_title),
        Paragraph("(Cash Transaction — Property Sold AS-IS)", st_sub),
        Spacer(1, 10),
    ]

    # Parties table
    parties = Table([
        [Paragraph("<b>SELLER</b>", st_body), Paragraph("<b>BUYER</b>", st_body)],
        [Paragraph(f"{seller_name or '________________________'}<br/>{seller_address or ''}", st_body),
         Paragraph(f"{buyer_name}<br/>{buyer_addr}<br/>{buyer.get('phone', '')}", st_body)],
    ], colWidths=[(W - 1.8 * inch) / 2] * 2)
    parties.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), LIGHT),
        ("BOX", (0, 0), (-1, -1), 0.75, NAVY),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, HexColor("#C9D2DE")),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(parties)
    story.append(Spacer(1, 6))

    secs: list[tuple[str, str]] = [
        ("1. PROPERTY.",
         f"Seller agrees to sell and Buyer agrees to buy the real property located at "
         f"<b>{property_address}</b>, {county_name}, Texas, together with all improvements and "
         f"fixtures, legally described as: <i>{legal_description or 'See deed of record'}</i> "
         f"(the “Property”)."),
        ("2. PURCHASE PRICE.",
         f"The total purchase price is <b>{_money(price)}</b> (USD), payable in <b>CASH</b> at "
         f"closing. This Agreement is <b>not</b> contingent on Buyer obtaining financing."),
        ("3. EARNEST MONEY.",
         f"Within 3 business days after the effective date, Buyer shall deposit "
         f"<b>{_money(earnest_money)}</b> as earnest money with the Title Company named below, "
         f"to be applied to the purchase price at closing."),
        ("4. TITLE COMPANY / CLOSING AGENT.",
         f"<b>{tc_name}</b>{f', Attn: {tc_officer}' if tc_officer else ''}"
         f"{f', {tc_addr}' if tc_addr else ''}{f' · Tel {tc_phone}' if tc_phone else ''}. "
         f"Closing funds must be sent by <b>wire transfer only</b> per the Title Company's "
         f"written wiring instructions, verbally verified before sending."),
        ("5. TITLE & CONVEYANCE.",
         f"Seller shall convey good and marketable title by <b>General Warranty Deed</b>, free "
         f"of liens and encumbrances except those accepted in writing by Buyer. The owner's "
         f"policy of title insurance shall be paid by <b>{title_policy_paid_by}</b>."),
        ("6. PROPERTY TAXES & LIENS.",
         "All delinquent property taxes, municipal liens, and assessments against the Property "
         "shall be paid from Seller's proceeds at closing. Current-year taxes shall be prorated "
         "to the closing date."),
        ("7. AS-IS CONDITION.",
         "Buyer accepts the Property in its present <b>AS-IS, WHERE-IS</b> condition. Buyer may "
         "inspect the Property before closing; Seller makes no warranties regarding condition. "
         "Seller shall not remove fixtures or cause damage after the effective date."),
        ("8. CLOSING.",
         f"Closing shall occur on or before <b>{closing_date.strftime('%B %d, %Y')}</b> "
         f"({closing_days} days from the effective date) at the Title Company's office, or "
         f"earlier by mutual agreement. Possession is delivered to Buyer at closing and funding."),
        ("9. CLOSING COSTS.",
         "Each party shall pay its customary closing costs in Texas. Buyer pays recording fees "
         "for the deed; Seller pays for release of any existing liens."),
        ("10. DEFAULT.",
         "If Buyer defaults, Seller's sole remedy is retention of the earnest money as "
         "liquidated damages. If Seller defaults, Buyer may seek specific performance or a "
         "refund of the earnest money."),
    ]
    if special_terms.strip():
        secs.append(("11. SPECIAL TERMS.", special_terms.strip()))
    secs.append((f"{'12' if special_terms.strip() else '11'}. ENTIRE AGREEMENT.",
                 "This Agreement contains the entire agreement between the parties and may only "
                 "be amended in writing signed by both parties. It is binding on heirs, "
                 "successors, and assigns, and is governed by the laws of the State of Texas."))

    for head, body in secs:
        story.append(Paragraph(head, st_sec))
        story.append(Paragraph(body, st_body))

    story.append(Spacer(1, 18))

    # Signatures
    sig = Table([
        [Paragraph("_________________________________<br/><b>SELLER</b><br/>"
                   f"{seller_name or ''}<br/>Date: ____________________", st_body),
         Paragraph("_________________________________<br/><b>BUYER</b><br/>"
                   f"{buyer_name}<br/>By: Yoandy Ross, Member<br/>Date: ____________________", st_body)],
    ], colWidths=[(W - 1.8 * inch) / 2] * 2)
    sig.setStyle(TableStyle([
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 12),
    ]))
    story.append(sig)
    story.append(Spacer(1, 14))
    story.append(Paragraph(
        "Effective Date: the date the last party signs. — Prepared "
        f"{today.strftime('%B %d, %Y')} by {buyer_name}. This document is a template for a cash "
        "purchase and does not constitute legal advice; the parties are encouraged to have it "
        "reviewed by a Texas real estate attorney or use a TREC promulgated form when applicable.",
        st_small))

    doc.build(story)
    return buf.getvalue()
