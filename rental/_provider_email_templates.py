"""Premium HTML email templates for Service Providers module.

All templates render responsive, brand-aligned HTML for SendGrid.
Brand: Ross House Rentals (amber/gold + navy dark).
"""

from datetime import datetime
from typing import Dict, Any, List, Optional


BRAND = {
    "name": "Ross House Rentals",
    "site": "https://www.rosshouserentals.com",
    "admin_url": "https://www.rosshouserentals.com/admin/proveedores",
    "phone": "(806) 934-2018",
    "address": "Dumas, TX",
    "primary": "#f59e0b",       # amber-500
    "primary_dark": "#d97706",  # amber-600
    "navy": "#0d1a2e",
    "navy_2": "#070B14",
    "text": "#0f172a",
    "muted": "#64748b",
    "card_bg": "#ffffff",
    "bg": "#f1f5f9",
    "border": "#e2e8f0",
    "success": "#10b981",
    "danger": "#ef4444",
}


SERVICE_LABELS_ES = {
    'plumber': 'Plomero', 'electrician': 'Electricista', 'hvac': 'HVAC / Aire',
    'mason': 'Albañil', 'painter': 'Pintor', 'gardener': 'Jardinero',
    'cleaner': 'Limpieza', 'locksmith': 'Cerrajero', 'roofer': 'Techos',
    'appliance_repair': 'Electrodomésticos', 'pest_control': 'Control de plagas',
    'handyman': 'Mantenimiento general', 'flooring': 'Pisos', 'drywall': 'Tablaroca',
    'tile': 'Azulejos', 'concrete': 'Concreto', 'fence': 'Cercas',
    'pool': 'Piscinas', 'security': 'Seguridad', 'other': 'Otro',
}
SERVICE_LABELS_EN = {
    'plumber': 'Plumber', 'electrician': 'Electrician', 'hvac': 'HVAC',
    'mason': 'Mason', 'painter': 'Painter', 'gardener': 'Gardener',
    'cleaner': 'Cleaning', 'locksmith': 'Locksmith', 'roofer': 'Roofer',
    'appliance_repair': 'Appliance repair', 'pest_control': 'Pest control',
    'handyman': 'Handyman', 'flooring': 'Flooring', 'drywall': 'Drywall',
    'tile': 'Tile', 'concrete': 'Concrete', 'fence': 'Fencing',
    'pool': 'Pools', 'security': 'Security', 'other': 'Other',
}

BILLING_LABELS_ES = {'per_hour': 'Por hora', 'per_job': 'Por trabajo terminado', 'both': 'Por hora o por trabajo'}
BILLING_LABELS_EN = {'per_hour': 'Hourly', 'per_job': 'Per finished job', 'both': 'Hourly or per job'}


def _service_labels(lang: str) -> Dict[str, str]:
    return SERVICE_LABELS_ES if lang == 'es' else SERVICE_LABELS_EN


def _wrap(title: str, preheader: str, content_html: str, lang: str = 'es') -> str:
    """Outer layout shared by all emails. Hardened against Gmail Dark Mode."""
    footer_legal = (
        'Recibiste este correo porque hay actividad en tu cuenta de proveedor en Ross House Rentals.'
        if lang == 'es'
        else 'You received this email because there is activity on your provider account at Ross House Rentals.'
    )
    return f"""<!DOCTYPE html>
<html lang="{lang}">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<meta name="color-scheme" content="light only">
<meta name="supported-color-schemes" content="light only">
<title>{title}</title>
<style>
  /* Force light scheme in Apple Mail and Outlook */
  :root {{ color-scheme: light only; supported-color-schemes: light only; }}
  /* Gmail iOS / Android dark mode overrides */
  u + .body .gmail-dark-bg {{ background-color: #0d1a2e !important; }}
  u + .body .gmail-dark-text {{ color: #ffffff !important; }}
  u + .body .gmail-amber {{ color: #fbbf24 !important; }}
  [data-ogsc] .gmail-dark-bg {{ background-color: #0d1a2e !important; }}
  [data-ogsc] .gmail-dark-text {{ color: #ffffff !important; }}
  [data-ogsc] .gmail-amber {{ color: #fbbf24 !important; }}
  [data-ogsb] .gmail-dark-bg {{ background-color: #0d1a2e !important; }}
  /* Responsive — stack the header on narrow screens */
  @media only screen and (max-width: 520px) {{
    .rh-header-cell {{ padding: 22px 20px !important; }}
    .rh-header-table, .rh-header-table tbody, .rh-header-table tr, .rh-header-table td {{ display: block !important; width: 100% !important; }}
    .rh-header-brand-cell {{ padding-bottom: 14px !important; text-align: left !important; }}
    .rh-header-phone-cell {{ text-align: left !important; padding-top: 4px !important; }}
    .rh-header-brand-text {{ font-size: 19px !important; }}
    .rh-content-cell {{ padding: 24px 20px 12px 20px !important; }}
    .rh-footer-cell {{ padding: 22px 20px !important; }}
  }}
</style>
</head>
<body class="body" style="margin:0;padding:0;background:{BRAND['bg']};font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;color:{BRAND['text']};">
<span style="display:none!important;opacity:0;color:transparent;height:0;width:0;overflow:hidden;">{preheader}</span>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="{BRAND['bg']}" style="background:{BRAND['bg']};padding:24px 12px;">
  <tr><td align="center">
    <table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" bgcolor="#ffffff" style="max-width:600px;width:100%;background:{BRAND['card_bg']};border-radius:16px;overflow:hidden;box-shadow:0 4px 24px rgba(15,23,42,0.08);">
      <!-- Header (Gmail dark-mode hardened + responsive) -->
      <tr><td bgcolor="#0d1a2e" class="gmail-dark-bg rh-header-cell" style="background-color:#0d1a2e;padding:28px 32px;">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" class="rh-header-table">
          <tr>
            <td class="rh-header-brand-cell" style="vertical-align:middle;">
              <div style="display:inline-block;background-color:#3b2a06;border:1px solid #b45309;padding:5px 12px;border-radius:999px;">
                <font color="#fbbf24" face="Helvetica,Arial,sans-serif"><span class="gmail-amber" style="font-size:11px;font-weight:700;color:#fbbf24;letter-spacing:0.5px;text-transform:uppercase;">Provider Network</span></font>
              </div>
              <div style="margin-top:10px;line-height:1.2;">
                <font color="#ffffff" face="Helvetica,Arial,sans-serif"><span class="gmail-dark-text rh-header-brand-text" style="font-size:22px;font-weight:800;color:#ffffff;white-space:nowrap;">🛠️ {BRAND['name']}</span></font>
              </div>
            </td>
            <td align="right" class="rh-header-phone-cell" style="vertical-align:middle;white-space:nowrap;">
              <a href="tel:+18069342018" style="text-decoration:none;background-color:#1e3050;padding:8px 12px;border-radius:999px;border:1px solid #334e74;display:inline-block;white-space:nowrap;">
                <font color="#ffffff" face="Helvetica,Arial,sans-serif"><span class="gmail-dark-text" style="color:#ffffff;font-size:13px;font-weight:600;white-space:nowrap;">📞 {BRAND['phone']}</span></font>
              </a>
            </td>
          </tr>
        </table>
      </td></tr>

      <!-- Content -->
      <tr><td bgcolor="#ffffff" class="rh-content-cell" style="padding:32px 32px 16px 32px;background-color:#ffffff;">
        {content_html}
      </td></tr>

      <!-- Footer -->
      <tr><td bgcolor="#f8fafc" class="rh-footer-cell" style="background-color:#f8fafc;padding:24px 32px;border-top:1px solid {BRAND['border']};">
        <div style="font-size:12px;color:{BRAND['muted']};line-height:1.6;text-align:center;">
          <strong style="color:{BRAND['text']};">{BRAND['name']}</strong> · {BRAND['address']} · <a href="tel:+18069342018" style="color:{BRAND['primary_dark']};text-decoration:none;">{BRAND['phone']}</a><br>
          <a href="{BRAND['site']}" style="color:{BRAND['primary_dark']};text-decoration:none;">{BRAND['site']}</a><br>
          <span style="display:inline-block;margin-top:8px;color:#94a3b8;">{footer_legal}</span>
        </div>
      </td></tr>
    </table>
    <div style="margin-top:14px;font-size:11px;color:#94a3b8;">© {datetime.utcnow().year} {BRAND['name']}. All rights reserved.</div>
  </td></tr>
</table>
</body>
</html>"""


def _btn(label: str, url: str, color: Optional[str] = None) -> str:
    bg = color or BRAND['primary']
    return f"""<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:8px 0;">
<tr><td style="border-radius:10px;background:{bg};">
<a href="{url}" style="display:inline-block;padding:13px 26px;font-size:14px;font-weight:700;color:#ffffff;text-decoration:none;border-radius:10px;">{label}</a>
</td></tr></table>"""


def _row(label: str, value: str, icon: str = '') -> str:
    if not value or value == '-':
        return ''
    return f"""<tr>
<td style="padding:10px 0;border-bottom:1px solid {BRAND['border']};font-size:13px;color:{BRAND['muted']};width:42%;vertical-align:top;">{icon} {label}</td>
<td style="padding:10px 0;border-bottom:1px solid {BRAND['border']};font-size:14px;color:{BRAND['text']};font-weight:600;vertical-align:top;">{value}</td>
</tr>"""


# ============================================================
# 1) Admin notification — NEW PROVIDER REGISTERED
# ============================================================

def admin_new_provider_html(provider: Dict[str, Any], lang: str = 'es') -> Dict[str, str]:
    labels = _service_labels(lang)
    services_str = ', '.join([labels.get(s, s) for s in (provider.get('services') or [])])
    billing = provider.get('billing_type') or 'per_hour'
    billing_lbl = (BILLING_LABELS_ES if lang == 'es' else BILLING_LABELS_EN).get(billing, billing)

    rate_lines: List[str] = []
    if billing in ('per_hour', 'both') and provider.get('hourly_rate'):
        rate_lines.append(f"${provider['hourly_rate']:,.0f}/hr")
    if billing in ('per_job', 'both'):
        pmin = provider.get('project_rate_min') or 0
        pmax = provider.get('project_rate_max') or 0
        if pmin or pmax:
            rate_lines.append(f"${pmin:,.0f} – ${pmax:,.0f} / trabajo")
    rate_summary = ' · '.join(rate_lines) or '-'

    license_docs = provider.get('license_documents') or []
    work_photos = provider.get('work_photos') or []

    docs_badge = ''
    if license_docs or work_photos:
        parts = []
        if license_docs:
            parts.append(f"📄 {len(license_docs)} {'documento(s)' if lang=='es' else 'document(s)'}")
        if work_photos:
            parts.append(f"📷 {len(work_photos)} {'foto(s) de trabajo' if lang=='es' else 'work photo(s)'}")
        docs_badge = f"""<div style="margin-top:14px;padding:12px 14px;background:#fef3c7;border:1px solid #fcd34d;border-radius:10px;font-size:13px;color:#78350f;">
<strong>📎 Adjuntos subidos:</strong> {' · '.join(parts)}<br>
<span style="font-size:11px;color:#92400e;">Revisa los archivos en el panel admin.</span>
</div>"""

    if lang == 'es':
        title = f"Nuevo proveedor: {provider.get('name')}"
        preheader = f"{services_str} · {provider.get('phone')}"
        intro = f"""<div style="font-size:13px;font-weight:700;color:{BRAND['primary_dark']};text-transform:uppercase;letter-spacing:1px;">🛠️ Nuevo proveedor registrado</div>
<h1 style="font-size:24px;font-weight:800;color:{BRAND['text']};margin:8px 0 4px 0;line-height:1.25;">{provider.get('name', '')}</h1>
<div style="font-size:14px;color:{BRAND['muted']};margin-bottom:18px;">{provider.get('company_name') or 'Profesional independiente'}</div>"""
        cta_lbl = 'Revisar en panel admin'
        section_title_contact = 'Contacto'
        section_title_services = 'Servicios y zona'
        section_title_pricing = 'Tarifas y experiencia'
        section_title_creds = 'Credenciales y experiencia'
        section_title_bio = 'Acerca del proveedor'
    else:
        title = f"New provider: {provider.get('name')}"
        preheader = f"{services_str} · {provider.get('phone')}"
        intro = f"""<div style="font-size:13px;font-weight:700;color:{BRAND['primary_dark']};text-transform:uppercase;letter-spacing:1px;">🛠️ New provider registered</div>
<h1 style="font-size:24px;font-weight:800;color:{BRAND['text']};margin:8px 0 4px 0;line-height:1.25;">{provider.get('name', '')}</h1>
<div style="font-size:14px;color:{BRAND['muted']};margin-bottom:18px;">{provider.get('company_name') or 'Independent professional'}</div>"""
        cta_lbl = 'Review in admin panel'
        section_title_contact = 'Contact'
        section_title_services = 'Services & area'
        section_title_pricing = 'Rates & experience'
        section_title_creds = 'Credentials & experience'
        section_title_bio = 'About the provider'

    def section(title_: str, rows_html: str) -> str:
        return f"""<div style="margin-top:18px;">
<div style="font-size:11px;font-weight:800;color:{BRAND['primary_dark']};text-transform:uppercase;letter-spacing:1px;margin-bottom:6px;">{title_}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">{rows_html}</table>
</div>"""

    contact_rows = (
        _row('Email', f"<a href=\"mailto:{provider.get('email','')}\" style=\"color:{BRAND['primary_dark']};text-decoration:none;\">{provider.get('email','')}</a>", '✉️') +
        _row('Teléfono' if lang == 'es' else 'Phone', f"<a href=\"tel:{provider.get('phone','')}\" style=\"color:{BRAND['primary_dark']};text-decoration:none;\">{provider.get('phone','')}</a>", '📞') +
        _row('Web', provider.get('website') or '-', '🌐')
    )

    services_rows = (
        _row('Servicios' if lang == 'es' else 'Services', services_str or '-', '🔧') +
        _row('Zonas (ZIP)' if lang == 'es' else 'Areas (ZIP)', ', '.join(provider.get('service_areas') or []) or '-', '📍') +
        _row('Idiomas' if lang == 'es' else 'Languages', ', '.join(provider.get('languages') or []) or '-', '🗣️')
    )

    pricing_rows = (
        _row('Modalidad' if lang == 'es' else 'Billing', billing_lbl, '💼') +
        _row('Tarifas' if lang == 'es' else 'Rates', rate_summary, '💰') +
        _row('Experiencia' if lang == 'es' else 'Experience', f"{provider.get('years_experience') or 0} años", '🏆')
    )

    creds_rows = (
        _row('Seguro' if lang == 'es' else 'Insurance', ('Sí - ' + (provider.get('insurance_provider') or '')) if provider.get('has_insurance') else ('No' if lang == 'es' else 'No'), '🛡️') +
        _row('Licencia' if lang == 'es' else 'License #', provider.get('license_number') or '-', '🪪')
    )

    bio_html = ''
    if provider.get('bio'):
        bio_html = f"""<div style="margin-top:18px;">
<div style="font-size:11px;font-weight:800;color:{BRAND['primary_dark']};text-transform:uppercase;letter-spacing:1px;margin-bottom:8px;">{section_title_bio}</div>
<div style="padding:14px 16px;background:#f8fafc;border:1px solid {BRAND['border']};border-radius:10px;font-size:14px;line-height:1.6;color:{BRAND['text']};white-space:pre-wrap;">{provider.get('bio')}</div>
</div>"""

    refs_html = ''
    if provider.get('references_text'):
        refs_html = f"""<div style="margin-top:14px;padding:14px 16px;background:#eff6ff;border:1px solid #bfdbfe;border-radius:10px;">
<div style="font-size:12px;font-weight:700;color:#1d4ed8;text-transform:uppercase;letter-spacing:1px;margin-bottom:6px;">{'Referencias' if lang == 'es' else 'References'}</div>
<div style="font-size:13px;line-height:1.6;color:{BRAND['text']};white-space:pre-wrap;">{provider.get('references_text')}</div>
</div>"""

    content = (
        intro +
        section(section_title_contact, contact_rows) +
        section(section_title_services, services_rows) +
        section(section_title_pricing, pricing_rows) +
        section(section_title_creds, creds_rows) +
        bio_html +
        refs_html +
        docs_badge +
        f'<div style="margin-top:24px;">{_btn(cta_lbl, BRAND["admin_url"])}</div>'
    )

    html = _wrap(title, preheader, content, lang)
    text = _admin_new_provider_text(provider, lang)
    return {"subject": title, "html": html, "text": text}


def _admin_new_provider_text(p: Dict[str, Any], lang: str = 'es') -> str:
    labels = _service_labels(lang)
    services_str = ', '.join([labels.get(s, s) for s in (p.get('services') or [])])
    return f"""NUEVO PROVEEDOR — Ross House Rentals

Nombre: {p.get('name','')}
Empresa: {p.get('company_name') or '-'}
Email: {p.get('email','')}
Tel: {p.get('phone','')}
Servicios: {services_str}
Zonas: {', '.join(p.get('service_areas') or []) or '-'}
Tarifa/hr: ${p.get('hourly_rate') or 0}
Experiencia: {p.get('years_experience') or 0} años
Seguro: {'Sí' if p.get('has_insurance') else 'No'}
Licencia: {p.get('license_number') or '-'}

Bio: {p.get('bio') or '-'}
Referencias: {p.get('references_text') or '-'}

Revisar: {BRAND['admin_url']}"""


# ============================================================
# 2) Welcome email — sent to NEW PROVIDER
# ============================================================

def welcome_provider_html(provider: Dict[str, Any], lang: str = 'es') -> Dict[str, str]:
    labels = _service_labels(lang)
    services_str = ', '.join([labels.get(s, s) for s in (provider.get('services') or [])])

    if lang == 'es':
        title = f"¡Bienvenido {provider.get('name','')} a Ross House Rentals! 🛠️"
        preheader = "Estás en nuestra red de proveedores. Aquí los próximos pasos."
        content = f"""<div style="font-size:13px;font-weight:700;color:{BRAND['primary_dark']};text-transform:uppercase;letter-spacing:1px;">✅ Registro confirmado</div>
<h1 style="font-size:26px;font-weight:800;color:{BRAND['text']};margin:8px 0 6px 0;line-height:1.2;">¡Bienvenido a la red,<br>{provider.get('name','')}!</h1>
<p style="font-size:15px;line-height:1.65;color:#475569;margin:0 0 22px 0;">
Gracias por unirte a nuestra red de proveedores de servicios en <strong>Dumas, TX</strong>. Nuestro equipo ya recibió tu información y la estamos revisando.
</p>

<div style="padding:18px;background:#fffbeb;border:1px solid #fcd34d;border-radius:12px;margin-bottom:22px;">
  <div style="font-size:12px;font-weight:800;color:#92400e;text-transform:uppercase;letter-spacing:1px;margin-bottom:6px;">📋 Tu perfil</div>
  <div style="font-size:14px;color:{BRAND['text']};line-height:1.7;">
    <strong>Servicios:</strong> {services_str or '-'}<br>
    <strong>Email:</strong> {provider.get('email','')}<br>
    <strong>Tel:</strong> {provider.get('phone','')}
  </div>
</div>

<div style="font-size:11px;font-weight:800;color:{BRAND['primary_dark']};text-transform:uppercase;letter-spacing:1px;margin-bottom:10px;">🚀 Próximos pasos</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:22px;">
<tr><td style="padding:10px 14px;background:#f1f5f9;border-radius:10px;font-size:14px;color:{BRAND['text']};line-height:1.6;">
<strong>1. Revisión de perfil</strong> — Nuestro equipo verificará tu información en las próximas 24-48 horas.
</td></tr>
<tr><td style="height:8px;"></td></tr>
<tr><td style="padding:10px 14px;background:#f1f5f9;border-radius:10px;font-size:14px;color:{BRAND['text']};line-height:1.6;">
<strong>2. Mantente disponible</strong> — Asegúrate de tener tu teléfono y email activos. Te contactaremos por estos medios.
</td></tr>
<tr><td style="height:8px;"></td></tr>
<tr><td style="padding:10px 14px;background:#f1f5f9;border-radius:10px;font-size:14px;color:{BRAND['text']};line-height:1.6;">
<strong>3. Recibirás trabajos</strong> — Cuando surja un trabajo de mantenimiento en tu zona que coincida con tus servicios, te enviaremos los detalles por SMS y correo.
</td></tr>
<tr><td style="height:8px;"></td></tr>
<tr><td style="padding:10px 14px;background:#f1f5f9;border-radius:10px;font-size:14px;color:{BRAND['text']};line-height:1.6;">
<strong>4. Cobra al terminar</strong> — Pagamos al finalizar el trabajo en efectivo, cheque, Zelle, CashApp o transferencia.
</td></tr>
</table>

<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:18px;"><tr><td bgcolor="#0d1a2e" class="gmail-dark-bg" style="padding:18px;background-color:#0d1a2e;border-radius:12px;text-align:center;">
  <font color="#fbbf24" face="Helvetica,Arial,sans-serif"><span class="gmail-amber" style="font-size:13px;color:#fbbf24;font-weight:700;">¿Tienes alguna pregunta?</span></font>
  <div style="margin-top:6px;">
    <font color="#ffffff" face="Helvetica,Arial,sans-serif"><span class="gmail-dark-text" style="font-size:14px;color:#ffffff;line-height:1.5;">
    Llámanos al <a href="tel:+18069342018" style="color:#ffffff;font-weight:700;text-decoration:none;border-bottom:2px solid #fbbf24;">(806) 934-2018</a><br>
    o escríbenos a <a href="mailto:info@rosshouserentals.com" style="color:#ffffff;font-weight:700;text-decoration:none;border-bottom:2px solid #fbbf24;">info@rosshouserentals.com</a>
    </span></font>
  </div>
</td></tr></table>

{_btn('Visitar nuestro sitio', BRAND['site'])}

<p style="font-size:14px;color:{BRAND['muted']};margin:18px 0 0 0;line-height:1.6;">
  ¡Esperamos trabajar contigo pronto!<br>
  <strong style="color:{BRAND['text']};">— Equipo Ross House Rentals</strong>
</p>
"""
    else:
        title = f"Welcome {provider.get('name','')} to Ross House Rentals! 🛠️"
        preheader = "You're in our provider network. Here are the next steps."
        content = f"""<div style="font-size:13px;font-weight:700;color:{BRAND['primary_dark']};text-transform:uppercase;letter-spacing:1px;">✅ Registration confirmed</div>
<h1 style="font-size:26px;font-weight:800;color:{BRAND['text']};margin:8px 0 6px 0;line-height:1.2;">Welcome to the network,<br>{provider.get('name','')}!</h1>
<p style="font-size:15px;line-height:1.65;color:#475569;margin:0 0 22px 0;">
Thanks for joining our service provider network in <strong>Dumas, TX</strong>. Our team has received your info and we're reviewing it.
</p>

<div style="padding:18px;background:#fffbeb;border:1px solid #fcd34d;border-radius:12px;margin-bottom:22px;">
  <div style="font-size:12px;font-weight:800;color:#92400e;text-transform:uppercase;letter-spacing:1px;margin-bottom:6px;">📋 Your profile</div>
  <div style="font-size:14px;color:{BRAND['text']};line-height:1.7;">
    <strong>Services:</strong> {services_str or '-'}<br>
    <strong>Email:</strong> {provider.get('email','')}<br>
    <strong>Phone:</strong> {provider.get('phone','')}
  </div>
</div>

<div style="font-size:11px;font-weight:800;color:{BRAND['primary_dark']};text-transform:uppercase;letter-spacing:1px;margin-bottom:10px;">🚀 Next steps</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:22px;">
<tr><td style="padding:10px 14px;background:#f1f5f9;border-radius:10px;font-size:14px;color:{BRAND['text']};line-height:1.6;">
<strong>1. Profile review</strong> — Our team will verify your info in the next 24-48 hours.
</td></tr>
<tr><td style="height:8px;"></td></tr>
<tr><td style="padding:10px 14px;background:#f1f5f9;border-radius:10px;font-size:14px;color:{BRAND['text']};line-height:1.6;">
<strong>2. Stay reachable</strong> — Keep your phone and email active. We'll contact you through these channels.
</td></tr>
<tr><td style="height:8px;"></td></tr>
<tr><td style="padding:10px 14px;background:#f1f5f9;border-radius:10px;font-size:14px;color:{BRAND['text']};line-height:1.6;">
<strong>3. You'll get jobs</strong> — When a maintenance job in your area matches your services, we'll send the details by SMS and email.
</td></tr>
<tr><td style="height:8px;"></td></tr>
<tr><td style="padding:10px 14px;background:#f1f5f9;border-radius:10px;font-size:14px;color:{BRAND['text']};line-height:1.6;">
<strong>4. Get paid when done</strong> — We pay upon job completion in cash, check, Zelle, CashApp or wire.
</td></tr>
</table>

<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:18px;"><tr><td bgcolor="#0d1a2e" class="gmail-dark-bg" style="padding:18px;background-color:#0d1a2e;border-radius:12px;text-align:center;">
  <font color="#fbbf24" face="Helvetica,Arial,sans-serif"><span class="gmail-amber" style="font-size:13px;color:#fbbf24;font-weight:700;">Got questions?</span></font>
  <div style="margin-top:6px;">
    <font color="#ffffff" face="Helvetica,Arial,sans-serif"><span class="gmail-dark-text" style="font-size:14px;color:#ffffff;line-height:1.5;">
    Call us at <a href="tel:+18069342018" style="color:#ffffff;font-weight:700;text-decoration:none;border-bottom:2px solid #fbbf24;">(806) 934-2018</a><br>
    or email <a href="mailto:info@rosshouserentals.com" style="color:#ffffff;font-weight:700;text-decoration:none;border-bottom:2px solid #fbbf24;">info@rosshouserentals.com</a>
    </span></font>
  </div>
</td></tr></table>

{_btn('Visit our website', BRAND['site'])}

<p style="font-size:14px;color:{BRAND['muted']};margin:18px 0 0 0;line-height:1.6;">
  We look forward to working with you!<br>
  <strong style="color:{BRAND['text']};">— The Ross House Rentals Team</strong>
</p>
"""

    text = (
        f"Hola {provider.get('name','')},\n\n"
        f"Gracias por unirte a la red de proveedores de Ross House Rentals.\n"
        f"Servicios: {services_str}\n\n"
        f"Te contactaremos cuando tengamos trabajos que coincidan con tus servicios.\n"
        f"Web: {BRAND['site']}\n"
        f"Tel: {BRAND['phone']}\n\n"
        f"— Ross House Rentals"
        if lang == 'es' else
        f"Hi {provider.get('name','')},\n\n"
        f"Thanks for joining Ross House Rentals' provider network.\n"
        f"Services: {services_str}\n\n"
        f"We'll contact you when we have jobs matching your services.\n"
        f"Web: {BRAND['site']}\n"
        f"Tel: {BRAND['phone']}\n\n"
        f"— Ross House Rentals"
    )
    return {"subject": title, "html": _wrap(title, preheader, content, lang), "text": text}


# ============================================================
# 3) Maintenance dispatch — sent to PROVIDER for a job
# ============================================================

def dispatch_job_html(provider: Dict[str, Any], job: Dict[str, Any], extra_note: str = '', lang: str = 'es') -> Dict[str, str]:
    title_job = job.get('title') or 'Mantenimiento'
    address = job.get('property_address') or '—'
    description = job.get('description') or ''
    priority = (job.get('priority') or 'medium').lower()
    tenant = job.get('tenant_name') or ''
    phone = job.get('tenant_phone') or ''

    priority_color = {
        'urgent': '#dc2626', 'high': '#ea580c',
        'medium': '#ca8a04', 'low': '#0891b2'
    }.get(priority, '#ca8a04')
    priority_lbl_es = {'urgent': 'Urgente', 'high': 'Alta', 'medium': 'Media', 'low': 'Baja'}
    priority_lbl_en = {'urgent': 'Urgent', 'high': 'High', 'medium': 'Medium', 'low': 'Low'}
    priority_lbl = (priority_lbl_es if lang == 'es' else priority_lbl_en).get(priority, priority.title())

    maps_url = f"https://maps.google.com/?q={address.replace(' ', '+')}"

    if lang == 'es':
        subject = f"🛠️ Trabajo disponible: {title_job} — {address}"
        preheader = f"{priority_lbl} · {address}"
        content = f"""<div style="font-size:13px;font-weight:700;color:{BRAND['primary_dark']};text-transform:uppercase;letter-spacing:1px;">🛠️ Nueva solicitud de trabajo</div>
<h1 style="font-size:24px;font-weight:800;color:{BRAND['text']};margin:8px 0 8px 0;line-height:1.25;">Hola {provider.get('name','')}, tenemos un trabajo para ti</h1>
<p style="font-size:14px;color:#475569;margin:0 0 20px 0;line-height:1.6;">
Hay un trabajo de mantenimiento que coincide con tus servicios. Confirma si puedes tomarlo respondiendo este correo o llamando al <a href="tel:+18069342018" style="color:{BRAND['primary_dark']};text-decoration:none;font-weight:600;">{BRAND['phone']}</a>.
</p>

<div style="padding:18px;border:2px solid {priority_color};border-radius:12px;background:#fefce8;margin-bottom:18px;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
    <tr>
      <td style="vertical-align:top;">
        <div style="font-size:11px;color:{priority_color};font-weight:800;text-transform:uppercase;letter-spacing:1px;">Prioridad</div>
        <div style="font-size:18px;color:{priority_color};font-weight:800;">{priority_lbl}</div>
      </td>
      <td align="right" style="vertical-align:top;">
        <div style="font-size:11px;color:{BRAND['muted']};font-weight:700;text-transform:uppercase;letter-spacing:1px;">Trabajo</div>
        <div style="font-size:16px;color:{BRAND['text']};font-weight:700;">{title_job}</div>
      </td>
    </tr>
  </table>
</div>

<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
{_row('Dirección', f'<a href="{maps_url}" style="color:{BRAND["primary_dark"]};text-decoration:none;">{address}</a>', '🏠')}
{_row('Inquilino', tenant, '👤')}
{_row('Contacto', f'<a href="tel:{phone}" style="color:{BRAND["primary_dark"]};text-decoration:none;">{phone}</a>', '📞')}
</table>

<div style="margin-top:18px;">
  <div style="font-size:11px;font-weight:800;color:{BRAND['primary_dark']};text-transform:uppercase;letter-spacing:1px;margin-bottom:8px;">📋 Descripción del trabajo</div>
  <div style="padding:14px 16px;background:#f8fafc;border:1px solid {BRAND['border']};border-radius:10px;font-size:14px;line-height:1.6;color:{BRAND['text']};white-space:pre-wrap;">{description or '(sin descripción adicional)'}</div>
</div>

{f'<div style="margin-top:14px;padding:14px;background:#eff6ff;border-left:4px solid #3b82f6;border-radius:6px;font-size:14px;color:{BRAND["text"]};"><strong>📝 Nota adicional:</strong><br>{extra_note}</div>' if extra_note else ''}

<div style="margin-top:24px;text-align:center;">
{_btn('✅ Llamar ahora', 'tel:+18069342018', BRAND['primary'])}
<a href="mailto:info@rosshouserentals.com?subject=RE: {title_job} — {address}" style="display:inline-block;padding:13px 26px;font-size:14px;font-weight:700;color:{BRAND['primary_dark']};text-decoration:none;border-radius:10px;border:2px solid {BRAND['primary']};margin-left:8px;">✉️ Responder por email</a>
</div>

<p style="font-size:13px;color:{BRAND['muted']};margin:24px 0 0 0;line-height:1.6;text-align:center;">
  Si no puedes tomar este trabajo, simplemente ignora este correo.<br>
  <strong style="color:{BRAND['text']};">— Ross House Rentals</strong>
</p>
"""
    else:
        subject = f"🛠️ Job available: {title_job} — {address}"
        preheader = f"{priority_lbl} · {address}"
        content = f"""<div style="font-size:13px;font-weight:700;color:{BRAND['primary_dark']};text-transform:uppercase;letter-spacing:1px;">🛠️ New job request</div>
<h1 style="font-size:24px;font-weight:800;color:{BRAND['text']};margin:8px 0 8px 0;line-height:1.25;">Hi {provider.get('name','')}, we have a job for you</h1>
<p style="font-size:14px;color:#475569;margin:0 0 20px 0;line-height:1.6;">
A maintenance job matches your services. Confirm if you can take it by replying to this email or calling <a href="tel:+18069342018" style="color:{BRAND['primary_dark']};text-decoration:none;font-weight:600;">{BRAND['phone']}</a>.
</p>

<div style="padding:18px;border:2px solid {priority_color};border-radius:12px;background:#fefce8;margin-bottom:18px;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
    <tr>
      <td style="vertical-align:top;">
        <div style="font-size:11px;color:{priority_color};font-weight:800;text-transform:uppercase;letter-spacing:1px;">Priority</div>
        <div style="font-size:18px;color:{priority_color};font-weight:800;">{priority_lbl}</div>
      </td>
      <td align="right" style="vertical-align:top;">
        <div style="font-size:11px;color:{BRAND['muted']};font-weight:700;text-transform:uppercase;letter-spacing:1px;">Job</div>
        <div style="font-size:16px;color:{BRAND['text']};font-weight:700;">{title_job}</div>
      </td>
    </tr>
  </table>
</div>

<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
{_row('Address', f'<a href="{maps_url}" style="color:{BRAND["primary_dark"]};text-decoration:none;">{address}</a>', '🏠')}
{_row('Tenant', tenant, '👤')}
{_row('Contact', f'<a href="tel:{phone}" style="color:{BRAND["primary_dark"]};text-decoration:none;">{phone}</a>', '📞')}
</table>

<div style="margin-top:18px;">
  <div style="font-size:11px;font-weight:800;color:{BRAND['primary_dark']};text-transform:uppercase;letter-spacing:1px;margin-bottom:8px;">📋 Job description</div>
  <div style="padding:14px 16px;background:#f8fafc;border:1px solid {BRAND['border']};border-radius:10px;font-size:14px;line-height:1.6;color:{BRAND['text']};white-space:pre-wrap;">{description or '(no extra description)'}</div>
</div>

{f'<div style="margin-top:14px;padding:14px;background:#eff6ff;border-left:4px solid #3b82f6;border-radius:6px;font-size:14px;color:{BRAND["text"]};"><strong>📝 Extra note:</strong><br>{extra_note}</div>' if extra_note else ''}

<div style="margin-top:24px;text-align:center;">
{_btn('✅ Call now', 'tel:+18069342018', BRAND['primary'])}
<a href="mailto:info@rosshouserentals.com?subject=RE: {title_job} — {address}" style="display:inline-block;padding:13px 26px;font-size:14px;font-weight:700;color:{BRAND['primary_dark']};text-decoration:none;border-radius:10px;border:2px solid {BRAND['primary']};margin-left:8px;">✉️ Reply by email</a>
</div>

<p style="font-size:13px;color:{BRAND['muted']};margin:24px 0 0 0;line-height:1.6;text-align:center;">
  If you can't take this job, just ignore this email.<br>
  <strong style="color:{BRAND['text']};">— Ross House Rentals</strong>
</p>
"""

    text = (
        f"Trabajo disponible: {title_job}\n"
        f"Dirección: {address}\n"
        f"Prioridad: {priority_lbl}\n"
        f"Inquilino: {tenant} ({phone})\n\n"
        f"{description}\n\n{extra_note}\n\n"
        f"Llama al {BRAND['phone']} si puedes tomarlo.\n— Ross House Rentals"
    )

    return {"subject": subject, "html": _wrap(subject, preheader, content, lang), "text": text}
