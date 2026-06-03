# 🏠 Ross House Rentals — Backend API

FastAPI backend for Ross House Rentals LLC property management platform.

## Quick Start

```bash
pip install -r requirements.txt
cp .env.example .env
python server.py
```

## API Endpoints

- `GET /api/health` — Health check
- `POST /api/rental/phone/send-otp` — Send OTP via SMS
- `POST /api/rental/phone/verify-otp` — Verify OTP
- `POST /api/public/marketplace-login` — Email+Phone login
- `GET /api/public/properties` — List properties
- `POST /api/chat/send` — Send chat message
- `GET /api/tenant/payment-methods` — List saved payment methods
- `DELETE /api/marketplace/delete-account` — Delete account

## Deploy to Railway

1. Push to GitHub
2. Connect repo in Railway
3. Add environment variables from `.env.example`
4. Railway auto-detects Python and deploys

## Tech Stack

- **FastAPI** + **Uvicorn**
- **MongoDB Atlas** (Motor async driver)
- **Stripe** (Payments)
- **Twilio** (SMS/OTP)
- **SendGrid** (Email)
