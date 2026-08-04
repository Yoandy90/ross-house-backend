"""Jasmine Apartments — Follow-up #1 a Joe Kuruvila (34 dias despues del soft inquiry).
Corto, 3 preguntas decisivas: portafolio vs separado, precio, disponibilidad.
BCC a Yoandy + reply-to info@. Tracker: jasmine_kuruvila_followup_2026_08_03.
"""
import os
from datetime import datetime
from dotenv import load_dotenv
load_dotenv('/app/ross-house-backend/.env')
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail, Bcc

from_email = (os.environ['SENDGRID_FROM_EMAIL'], 'Yoandy Ross — Ross House Rentals')

html = """
<div style="font-family:Georgia,'Times New Roman',serif;font-size:15px;line-height:1.7;color:#1a1a1a;max-width:640px;">
<p>Joe,</p>

<p>Following up on my note from June 30th about <b>Jasmine Apartments</b>. I know you're busy running the property, so I'll keep this to three questions you can answer in a single reply:</p>

<ol style="margin:14px 0;padding-left:22px;">
  <li style="margin-bottom:8px;"><b>Whole or separate?</b> Are you selling the portfolio as one package, or would you consider selling properties/phases separately?</li>
  <li style="margin-bottom:8px;"><b>Price:</b> What is your current asking price — for the full package, or per property if you'd split it?</li>
  <li style="margin-bottom:8px;"><b>Status:</b> Is it still actively for sale?</li>
</ol>

<p>For context: I'm a local Dumas owner-operator (Ross House Rentals), my financing is arranged, and I can move quickly and discreetly — no brokers involved.</p>

<p>If the timing isn't right or it's spoken for, a one-line reply saves us both time. Otherwise, just hit reply — or I'm at <b>(806) 934-2018</b>.</p>

<p>Best regards,<br>
<b>Yoandy Ross</b><br>
Ross House Rentals LLC — Dumas, TX<br>
<a href="https://www.rosshouserentals.com" style="color:#1d4ed8;">rosshouserentals.com</a> · (806) 934-2018</p>
</div>
"""

msg = Mail(
    from_email=from_email,
    to_emails='joe3359@gmail.com',
    subject='Re: Interest in Jasmine Apartments — 3 quick questions',
    html_content=html,
)
msg.add_bcc(Bcc('yoandyross@gmail.com'))
msg.reply_to = 'info@rosshouserentals.com'

resp = SendGridAPIClient(os.environ['SENDGRID_API_KEY']).send(msg)
ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
print('✅ FOLLOW-UP ENVIADO A JOE KURUVILA')
print(f'   Para:    joe3359@gmail.com  |  BCC: yoandyross@gmail.com')
print(f'   Status:  {resp.status_code}  |  Hora: {ts}')
print(f'   Msg ID:  {resp.headers.get("X-Message-Id", "n/a")}')
