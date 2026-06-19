"""
HUD Fair Market Rent (FMR) lookup table for Texas
==================================================
Data: HUD FY 2024 Final FMRs.
Use: comparar la renta actual de una propiedad vs. el voucher Section 8 máximo
para ver el "uplift" potencial.

Updated: October 2024 (HUD publishes new values every October).
"""

# ─────────────────────────────────────────────────────────────────────
# Texas FMR by Metropolitan Statistical Area (MSA) / County
# Bedrooms: 0 (efficiency) → 4
# ─────────────────────────────────────────────────────────────────────

TX_FMR = {
    # Major MSAs
    "houston":     {"0br": 1100, "1br": 1290, "2br": 1540, "3br": 2030, "4br": 2420},
    "dallas":      {"0br": 1250, "1br": 1440, "2br": 1710, "3br": 2260, "4br": 2690},
    "austin":      {"0br": 1500, "1br": 1710, "2br": 2070, "3br": 2710, "4br": 3200},
    "fort_worth":  {"0br": 1180, "1br": 1310, "2br": 1580, "3br": 2090, "4br": 2490},
    "arlington":   {"0br": 1180, "1br": 1310, "2br": 1580, "3br": 2090, "4br": 2490},
    "san_antonio": {"0br": 1030, "1br": 1150, "2br": 1400, "3br": 1860, "4br": 2220},
    "el_paso":     {"0br":  720, "1br":  830, "2br": 1030, "3br": 1450, "4br": 1730},
    "corpus_christi":{"0br": 820, "1br": 930, "2br": 1180, "3br": 1580, "4br": 1890},
    "mcallen":     {"0br":  680, "1br":  800, "2br": 1000, "3br": 1400, "4br": 1700},
    "lubbock":     {"0br":  730, "1br":  850, "2br": 1080, "3br": 1500, "4br": 1810},
    "amarillo":    {"0br":  750, "1br":  840, "2br": 1070, "3br": 1470, "4br": 1800},
    "waco":        {"0br":  790, "1br":  900, "2br": 1130, "3br": 1530, "4br": 1850},
    "killeen":     {"0br":  790, "1br":  900, "2br": 1130, "3br": 1530, "4br": 1850},
    "tyler":       {"0br":  770, "1br":  870, "2br": 1100, "3br": 1490, "4br": 1810},
    "beaumont":    {"0br":  790, "1br":  900, "2br": 1130, "3br": 1530, "4br": 1850},
    "abilene":     {"0br":  730, "1br":  830, "2br": 1050, "3br": 1450, "4br": 1750},
    "midland":     {"0br":  900, "1br": 1020, "2br": 1290, "3br": 1730, "4br": 2090},
    "odessa":      {"0br":  830, "1br":  950, "2br": 1190, "3br": 1620, "4br": 1950},
    # Default: TX non-metro average (HUD county FMR fallback)
    "default":     {"0br":  720, "1br":  820, "2br": 1030, "3br": 1400, "4br": 1680},
}

# City → MSA bucket. Add small towns to their MSA so we can find FMR.
CITY_TO_MSA = {
    # Houston MSA
    "houston": "houston", "sugar land": "houston", "baytown": "houston",
    "pasadena": "houston", "katy": "houston", "spring": "houston", "humble": "houston",
    "pearland": "houston", "the woodlands": "houston", "conroe": "houston",
    "league city": "houston", "missouri city": "houston", "rosenberg": "houston",
    "galveston": "houston",
    # Dallas / Fort Worth MSA
    "dallas": "dallas", "plano": "dallas", "irving": "dallas", "garland": "dallas",
    "grand prairie": "dallas", "mesquite": "dallas", "richardson": "dallas",
    "carrollton": "dallas", "frisco": "dallas", "mckinney": "dallas", "allen": "dallas",
    "rowlett": "dallas", "denton": "dallas", "lewisville": "dallas", "flower mound": "dallas",
    "fort worth": "fort_worth", "arlington": "arlington", "mansfield": "fort_worth",
    "euless": "fort_worth", "bedford": "fort_worth", "north richland hills": "fort_worth",
    "burleson": "fort_worth",
    # Austin MSA
    "austin": "austin", "round rock": "austin", "georgetown": "austin", "cedar park": "austin",
    "pflugerville": "austin", "leander": "austin", "kyle": "austin", "buda": "austin",
    "san marcos": "austin", "hutto": "austin",
    # San Antonio
    "san antonio": "san_antonio", "new braunfels": "san_antonio", "schertz": "san_antonio",
    "cibolo": "san_antonio", "seguin": "san_antonio",
    # West Texas
    "el paso": "el_paso", "horizon city": "el_paso",
    "midland": "midland", "odessa": "odessa",
    "lubbock": "lubbock", "abilene": "abilene",
    # Panhandle (CRITICAL for Jasmine in Dumas)
    "amarillo": "amarillo", "dumas": "amarillo", "canyon": "amarillo",
    "borger": "amarillo", "pampa": "amarillo", "hereford": "amarillo",
    # South Texas / RGV
    "mcallen": "mcallen", "edinburg": "mcallen", "mission": "mcallen",
    "harlingen": "mcallen", "brownsville": "mcallen", "pharr": "mcallen",
    # East Texas / Gulf
    "corpus christi": "corpus_christi", "tyler": "tyler", "longview": "tyler",
    "beaumont": "beaumont", "port arthur": "beaumont", "orange": "beaumont",
    "waco": "waco", "temple": "killeen", "killeen": "killeen", "harker heights": "killeen",
}


def get_msa(city: str) -> str:
    """Normalize city name → MSA bucket key. Returns 'default' if unknown."""
    if not city:
        return "default"
    key = city.strip().lower()
    return CITY_TO_MSA.get(key, "default")


def get_fmr(city: str, bedrooms: int) -> dict:
    """Return FMR breakdown for a city + bedroom count.

    Returns: {msa, fmr_amount, all_bedrooms: {0br..4br}}
    """
    msa = get_msa(city)
    table = TX_FMR.get(msa, TX_FMR["default"])
    br = max(0, min(4, int(bedrooms or 0)))
    fmr_amount = table.get(f"{br}br", 0)
    return {
        "msa": msa,
        "msa_display": msa.replace("_", " ").title(),
        "fmr_amount": fmr_amount,
        "bedrooms_used": br,
        "all_bedrooms": dict(table),
    }


def compute_s8_impact(current_rent: float, fmr_amount: float) -> dict:
    """Calculate the financial impact of switching to Section 8.

    Returns: {monthly_uplift, annual_uplift, pct_uplift, recommendation}
    """
    current_rent = float(current_rent or 0)
    fmr_amount = float(fmr_amount or 0)
    monthly_uplift = max(0, fmr_amount - current_rent)
    annual_uplift = monthly_uplift * 12
    pct_uplift = (monthly_uplift / current_rent * 100) if current_rent > 0 else 0

    if fmr_amount <= 0:
        rec = "no_data"
        rec_text = "Sin datos FMR para este mercado"
    elif current_rent <= 0:
        rec = "set_rent_first"
        rec_text = "Define la renta actual primero para calcular el uplift"
    elif fmr_amount <= current_rent:
        diff = current_rent - fmr_amount
        rec = "no_upside"
        rec_text = (
            f"Tu renta actual (${current_rent:,.0f}) ya está ${diff:,.0f}/mes "
            f"por encima del FMR. S8 limitaría tu ingreso. Mejor mantener market-rate."
        )
    elif monthly_uplift < 50:
        rec = "marginal"
        rec_text = (
            f"Uplift marginal (+${monthly_uplift:,.0f}/mes). S8 puede valer la pena "
            f"por estabilidad de pago, pero no por upside de renta."
        )
    elif monthly_uplift < 200:
        rec = "good"
        rec_text = (
            f"Uplift positivo (+${monthly_uplift:,.0f}/mes = +${annual_uplift:,.0f}/año). "
            f"Aceptar S8 mejora NOI y reduce riesgo de impago."
        )
    else:
        rec = "excellent"
        rec_text = (
            f"⭐ Uplift fuerte (+${monthly_uplift:,.0f}/mes = +${annual_uplift:,.0f}/año, "
            f"+{pct_uplift:.0f}%). Activa S8 ASAP — estás dejando dinero en la mesa."
        )

    return {
        "current_rent": round(current_rent, 2),
        "fmr_amount": round(fmr_amount, 2),
        "monthly_uplift": round(monthly_uplift, 2),
        "annual_uplift": round(annual_uplift, 2),
        "pct_uplift": round(pct_uplift, 1),
        "recommendation": rec,
        "recommendation_text": rec_text,
    }
