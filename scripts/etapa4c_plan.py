"""ETAPA 4C — Plan de escritura para 812 NE 2nd (Buyer's Statement Chicago Title, 2026-05-08).

ÚNICA FUENTE del plan: compartida por dry-run, apply y tests.
ZERO writes en este módulo.
"""
INV_812 = "6a277696a8489d364620984f"
PROP_812 = "69e40ae6268db576b07cafd0"
INV_OAK = "6a2761e4a8489d364620984e"
PROP_OAK = "69dbabdf5347719e9849b402"
SETTLEMENT_DATE = "2026-05-08"

# CREATE — cargos de cierre documentados (Buyer's Statement) → ACQUISITION_COST canónico
# Las 4 líneas comparten source_document para reconocer el statement de origen.
SOURCE_STATEMENT = "CHICAGO-TITLE-BUYERS-STATEMENT-812-2026-05-08"
CLOSING_CHARGES_812 = [
    {"description": "Escrow Fee — Chicago Title (Buyer's Statement 812 NE 2nd)", "amount": 350.00},
    {"description": "Overnight Delivery Fee — Chicago Title (Buyer's Statement 812 NE 2nd)", "amount": 15.00},
    {"description": "Recording Fees — Chicago Title (Buyer's Statement 812 NE 2nd)", "amount": 29.00},
    {"description": "Survey — Chicago Title (Buyer's Statement 812 NE 2nd)", "amount": 649.50},
]
for _c in CLOSING_CHARGES_812:
    _c["source_document"] = SOURCE_STATEMENT

# CREATE (APROBADO) — inspección pre-compra PAGADA como ACQUISITION_COST (due diligence).
# ESTRATEGIA APROBADA: 3 líneas que comparten source_document (misma factura $785)
# para que nunca se cuenten simultáneamente el total y las líneas.
SOURCE_INSPECTION = "INSPECTION-INVOICE-812-785-PAID"
INSPECTION_CHARGES_812 = [
    {"description": "Residential Home Inspection — due diligence pre-compra 812 NE 2nd (factura $785)", "amount": 500.00},
    {"description": "Sewer Scope — due diligence pre-compra 812 NE 2nd (factura $785)", "amount": 150.00},
    {"description": "WDI Inspection — due diligence pre-compra 812 NE 2nd (factura $785)", "amount": 135.00},
]
for _c in INSPECTION_CHARGES_812:
    _c["source_document"] = SOURCE_INSPECTION

COMMON_FIELDS = {
    "property_id": PROP_812,
    "category": "other",
    "expense_scope": "PROPERTY",
    "accounting_treatment": "ACQUISITION_COST",
    "expense_date": SETTLEMENT_DATE,
    "vendor": "Chicago Title",
    "status": "paid",
}

# UPDATE — DECISIÓN HUMANA CONFIRMADA (checkpoint 1 aprobado): ambas compradas CASH,
# sin deuda bancaria ⇒ loan_balance = 0 EXPLÍCITO (valor REAL, no null)
INVESTMENT_UPDATES_812 = {"loan_balance": 0.0}
INVESTMENT_UPDATES_OAK = {"loan_balance": 0.0}

# KEEP — no tocar (ya correctos o deben permanecer null = Not recorded)
KEEP_812 = {
    "purchase_price": 108000.0,          # ya en DB, coincide con settlement
    "closing_costs": None,               # manual NO se escribe: canónico gana (anti-double-count)
    "current_estimated_value": None,     # MANUAL, aún no registrado
    "arv": None,                         # aún no registrado
    "address_snapshot": "812 ND 2da ",   # snapshot legacy del investment se preserva
    "canonical_address": "812 NE 2nd ",  # en properties, ya correcta
}

# REVIEW — requieren decisión del usuario, NO escribir
REVIEW = []

# DO_NOT_IMPORT — documentados pero NO son costos registrables
DO_NOT_IMPORT = [
    {"item": "Option Fee $100", "reason": "DECISIÓN HUMANA: previamente pagado/acreditado dentro de la transacción; "
     "purchase price final ya es $108,000. Se conserva solo como metadata de auditoría; NO expense, NO cost basis."},
    {"item": "Earnest Money $1,000", "reason": "ya incluido dentro del purchase price (forma de pago, no costo)"},
    {"item": "Tax proration/credit $932.06", "reason": "crédito prorrateado, no capital improvement ni costo"},
    {"item": "Balance Due From Buyer $107,011.44", "reason": "cash neto al cierre, NO purchase price ni cost basis"},
    {"item": "Facturas a nombre del vendedor / trabajos pre-closing sin evidencia de pago RHR",
     "reason": "sin evidencia de pago por Ross House Rentals"},
]
