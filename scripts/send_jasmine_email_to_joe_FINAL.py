"""Envía el email real a Joe Kuruvila (joe3359@gmail.com).
BCC a yoandyross@gmail.com para tener registro.
"""
import os
from datetime import datetime
from dotenv import load_dotenv
load_dotenv('/app/ross-house-backend/.env')

from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail, Bcc

api_key = os.environ['SENDGRID_API_KEY']
from_email = (os.environ['SENDGRID_FROM_EMAIL'], 'Yoandy Ross — Ross House Rentals')

# ─── EMAIL HTML — limpio, profesional, sin wrapper de preview ───
html = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Interest in Jasmine Apartments</title>
</head>
<body style="margin:0;padding:0;background:#ffffff;font-family:'Helvetica Neue',Helvetica,Arial,sans-serif;font-size:15px;line-height:1.6;color:#1f2937;">
<div style="max-width:640px;margin:0 auto;padding:30px 24px;">

  <p>Dear Mr. Kuruvila,</p>

  <p>
    Good day. My name is <strong>Yoandy Ross</strong>, owner of
    <strong>Ross House Rentals LLC</strong>, a local Dumas, Texas company focused on
    the investment and management of residential rental properties in the Texas Panhandle.
  </p>

  <p>
    I noticed that <strong>Jasmine Apartments</strong> is listed on public platforms
    (LoopNet / Showcase) and I wanted to contact you directly. I am a
    <strong>local and serious buyer</strong> — not an out-of-state fund or a broker
    seeking a commission.
  </p>

  <p>
    To avoid taking up your time with a phone call just yet, I have a few specific
    questions I would appreciate answering by email. Whenever you have a moment,
    even a brief reply would be very helpful. With that, I can move forward internally,
    and if it makes sense for both of us, we can coordinate next steps later.
  </p>

  <p style="margin-top:22px;margin-bottom:6px;"><strong>Questions</strong>
    <span style="color:#6b7280;font-weight:normal;">
      (please answer only what you are comfortable sharing at this stage):
    </span>
  </p>

  <ol style="padding-left:22px;margin:6px 0 18px 0;">
    <li style="margin-bottom:8px;">Is the entire Jasmine Apartments portfolio (the 5 properties / 142 units) still actively available for sale?</li>
    <li style="margin-bottom:8px;">What is your <strong>current asking price</strong>?</li>
    <li style="margin-bottom:8px;">What is the <strong>average occupancy rate</strong> over the last 12 months?</li>
    <li style="margin-bottom:8px;">What is the approximate <strong>NOI</strong> for the last fiscal year (or GPR + operating expenses if you prefer)?</li>
    <li style="margin-bottom:8px;">Year built and approximate date of the most recent major renovations (roofs, HVAC, plumbing)?</li>
    <li style="margin-bottom:8px;">Are you open to partial <strong>seller financing</strong>, or do you prefer a cash sale / agency financing (Fannie/Freddie)?</li>
    <li style="margin-bottom:8px;">What is the <strong>main reason</strong> for selling (retirement, 1031 exchange, portfolio rebalancing)?</li>
    <li style="margin-bottom:8px;">Would you be willing to sign an <strong>NDA</strong> to share the T-12 and full rent roll in a second step?</li>
    <li style="margin-bottom:8px;">How many <strong>offers or LOIs</strong> have you received so far (no details needed)?</li>
    <li style="margin-bottom:8px;">Do you have a <strong>preferred timeline</strong> to close the transaction?</li>
  </ol>

  <p>
    No rush — please reply whenever it is convenient for you. If you prefer to address
    only a few of these questions in this first email, that is perfectly fine. The
    important thing is to open the conversation.
  </p>

  <p>Thank you very much for your time, Mr. Kuruvila. I look forward to hearing back.</p>

  <p style="margin-top:22px;margin-bottom:4px;">Best regards,</p>

  <p style="margin:0;line-height:1.55;">
    <strong style="font-size:16px;color:#0d1a2e;">Yoandy Ross</strong><br/>
    <span style="color:#475569;">Owner · Ross House Rentals LLC</span><br/>
    <a href="mailto:info@rosshouserentals.com" style="color:#1e40af;text-decoration:none;">info@rosshouserentals.com</a><br/>
    (806) 934-2018<br/>
    <a href="https://www.rosshouserentals.com" style="color:#1e40af;text-decoration:none;">www.rosshouserentals.com</a><br/>
    305 Bruce Ave, Dumas, TX 79029
  </p>

</div>
</body>
</html>"""

# Plain text fallback
plain = """Dear Mr. Kuruvila,

Good day. My name is Yoandy Ross, owner of Ross House Rentals LLC, a local Dumas, Texas
company focused on the investment and management of residential rental properties in the
Texas Panhandle.

I noticed that Jasmine Apartments is listed on public platforms (LoopNet / Showcase) and
I wanted to contact you directly. I am a local and serious buyer — not an out-of-state
fund or a broker seeking a commission.

To avoid taking up your time with a phone call just yet, I have a few specific questions
I would appreciate answering by email. Whenever you have a moment, even a brief reply
would be very helpful.

Questions (please answer only what you are comfortable sharing at this stage):

1. Is the entire Jasmine Apartments portfolio (5 properties / 142 units) still actively
   available for sale?
2. What is your current asking price?
3. What is the average occupancy rate over the last 12 months?
4. What is the approximate NOI for the last fiscal year (or GPR + operating expenses)?
5. Year built and approximate date of the most recent major renovations
   (roofs, HVAC, plumbing)?
6. Are you open to partial seller financing, or do you prefer cash / agency financing?
7. What is the main reason for selling (retirement, 1031 exchange, portfolio rebalancing)?
8. Would you be willing to sign an NDA to share the T-12 and full rent roll later?
9. How many offers or LOIs have you received so far (no details needed)?
10. Do you have a preferred timeline to close?

No rush — please reply whenever it is convenient. If you prefer to address only a few of
these questions in this first email, that is perfectly fine.

Thank you very much for your time, Mr. Kuruvila.

Best regards,

Yoandy Ross
Owner · Ross House Rentals LLC
info@rosshouserentals.com
(806) 934-2018
www.rosshouserentals.com
305 Bruce Ave, Dumas, TX 79029
"""

msg = Mail(
    from_email=from_email,
    to_emails='joe3359@gmail.com',
    subject='Interest in Jasmine Apartments — a few questions (Ross House Rentals)',
    plain_text_content=plain,
    html_content=html,
)
# BCC a Yoandy para que tenga copia automática
msg.add_bcc(Bcc('yoandyross@gmail.com'))

# Reply-To explícito a info@rosshouserentals.com
msg.reply_to = 'info@rosshouserentals.com'

resp = SendGridAPIClient(api_key).send(msg)
ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
print(f'✅ ENVIADO A JOE KURUVILA')
print(f'   Para:    joe3359@gmail.com')
print(f'   BCC:     yoandyross@gmail.com')
print(f'   De:      {from_email[0]}')
print(f'   Status:  {resp.status_code}')
print(f'   Hora:    {ts}')
print(f'   Msg ID:  {resp.headers.get("X-Message-Id", "n/a")}')
