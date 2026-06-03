"""
Rental Communications Router
==============================
Send SMS/Email messages to tenants, bulk messaging, message history.
"""
import logging
import os
from datetime import datetime
from typing import Optional
from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request

from rental.shared import get_db, auth_admin, serialize

router = APIRouter()


@router.post('/admin/send-message')
async def admin_send_message(request: Request):
    """Send SMS/Email to one or multiple tenants."""
    await auth_admin(request)
    db = get_db()
    data = await request.json()

    recipients = data.get('recipients', [])  # list of {name, phone, email}
    channel = data.get('channel', 'sms')  # sms | email | both
    subject = data.get('subject', '')
    message = data.get('message', '')
    template = data.get('template', '')

    if not message and not template:
        raise HTTPException(status_code=400, detail="Mensaje requerido")
    if not recipients:
        raise HTTPException(status_code=400, detail="Al menos un destinatario requerido")

    results = {"sent": 0, "failed": 0, "errors": []}

    for r in recipients:
        # Send SMS
        if channel in ('sms', 'both') and r.get('phone'):
            try:
                twilio_sid = os.getenv('TWILIO_ACCOUNT_SID')
                twilio_token = os.getenv('TWILIO_AUTH_TOKEN')
                twilio_phone = os.getenv('TWILIO_PHONE_NUMBER')

                if not twilio_sid:
                    config_doc = await db.api_config.find_one({'_id': 'main'})
                    if config_doc:
                        twilio_sid = config_doc.get('twilio_account_sid') or config_doc.get('TWILIO_ACCOUNT_SID')
                        twilio_token = config_doc.get('twilio_auth_token') or config_doc.get('TWILIO_AUTH_TOKEN')
                        twilio_phone = config_doc.get('twilio_phone_number') or config_doc.get('TWILIO_PHONE_NUMBER')

                if twilio_sid and twilio_token and twilio_phone:
                    from twilio.rest import Client
                    client = Client(twilio_sid, twilio_token)
                    phone = r['phone']
                    digits = ''.join(filter(str.isdigit, phone))
                    if len(digits) == 10:
                        phone = f'+1{digits}'
                    elif not phone.startswith('+'):
                        phone = f'+1{digits}'

                    client.messages.create(body=message, from_=twilio_phone, to=phone)
                    results["sent"] += 1
                    logging.info(f"📱 SMS sent to {r.get('name', '')} ({phone[-4:]})")
                else:
                    results["failed"] += 1
                    results["errors"].append(f"Twilio no configurado para {r.get('name', '')}")
            except Exception as e:
                results["failed"] += 1
                results["errors"].append(f"SMS a {r.get('name', '')}: {str(e)[:80]}")

        # Send Email
        if channel in ('email', 'both') and r.get('email'):
            try:
                sendgrid_key = os.getenv('SENDGRID_API_KEY')
                from_email = os.getenv('SENDGRID_FROM_EMAIL', 'info@rosshouserentals.com')

                if not sendgrid_key:
                    config_doc = await db.api_config.find_one({'_id': 'main'})
                    if config_doc:
                        sendgrid_key = config_doc.get('sendgrid_api_key') or config_doc.get('SENDGRID_API_KEY')
                        from_email = config_doc.get('sendgrid_from_email', from_email)

                if sendgrid_key:
                    import sendgrid
                    from sendgrid.helpers.mail import Mail, Email, To, Content
                    sg = sendgrid.SendGridAPIClient(api_key=sendgrid_key)
                    mail = Mail(
                        from_email=Email(from_email, "Ross House Rentals"),
                        to_emails=To(r['email']),
                        subject=subject or "Ross House Rentals",
                        plain_text_content=Content("text/plain", message),
                    )
                    sg.client.mail.send.post(request_body=mail.get())
                    results["sent"] += 1
                    logging.info(f"📧 Email sent to {r.get('name', '')} ({r['email']})")
                else:
                    results["failed"] += 1
                    results["errors"].append(f"SendGrid no configurado para {r.get('name', '')}")
            except Exception as e:
                results["failed"] += 1
                results["errors"].append(f"Email a {r.get('name', '')}: {str(e)[:80]}")

    # Save to message history
    await db.message_history.insert_one({
        "channel": channel,
        "subject": subject,
        "message": message,
        "recipient_count": len(recipients),
        "sent": results["sent"],
        "failed": results["failed"],
        "created_at": datetime.utcnow(),
    })

    return {"success": True, **results}


@router.get('/admin/message-history')
async def admin_message_history(request: Request):
    """Get sent message history."""
    await auth_admin(request)
    db = get_db()
    messages = await db.message_history.find().sort("created_at", -1).to_list(100)
    return {"success": True, "messages": [serialize(m) for m in messages], "count": len(messages)}


@router.get('/admin/message-templates')
async def admin_get_templates(request: Request):
    """Get message templates."""
    await auth_admin(request)
    db = get_db()
    templates = await db.message_templates.find().sort("created_at", -1).to_list(50)
    if not templates:
        # Seed defaults
        defaults = [
            {
                "name": "Recordatorio de Pago",
                "subject": "Recordatorio — Su renta vence pronto",
                "message": "Estimado inquilino, le recordamos que su pago de renta vence el día 1 del mes. Por favor realice su pago a tiempo para evitar cargos por retraso. Puede pagar mediante la app o en nuestra oficina. Gracias — Ross House Rentals LLC, (806) 934-2018",
                "channel": "both",
                "category": "pagos",
            },
            {
                "name": "Renta Atrasada",
                "subject": "Aviso — Pago de renta atrasado",
                "message": "Estimado inquilino, nuestros registros indican que su pago de renta aún no ha sido recibido. Le pedimos ponerse al día lo antes posible para evitar acciones adicionales. Contáctenos al (806) 934-2018 si tiene preguntas. — Ross House Rentals LLC",
                "channel": "both",
                "category": "pagos",
            },
            {
                "name": "Mantenimiento Programado",
                "subject": "Aviso de Mantenimiento Programado",
                "message": "Le informamos que se realizará mantenimiento programado en su propiedad. Por favor asegúrese de que haya acceso al área necesaria. Le notificaremos la fecha y hora exacta. Gracias por su comprensión. — Ross House Rentals LLC",
                "channel": "sms",
                "category": "mantenimiento",
            },
            {
                "name": "Mantenimiento Completado",
                "subject": "Mantenimiento Completado",
                "message": "Le informamos que el trabajo de mantenimiento solicitado en su propiedad ha sido completado. Si nota algún problema, no dude en reportarlo a través de la app o llamando al (806) 934-2018. — Ross House Rentals LLC",
                "channel": "sms",
                "category": "mantenimiento",
            },
            {
                "name": "Bienvenida Nuevo Inquilino",
                "subject": "¡Bienvenido a Ross House Rentals!",
                "message": "¡Bienvenido a su nuevo hogar! Estamos encantados de tenerle como inquilino de Ross House Rentals. Descargue nuestra app para reportar mantenimiento, hacer pagos y más. Para cualquier necesidad, contáctenos al (806) 934-2018. ¡Le deseamos lo mejor!",
                "channel": "both",
                "category": "general",
            },
            {
                "name": "Renovación de Contrato",
                "subject": "Su contrato de arrendamiento está por vencer",
                "message": "Estimado inquilino, su contrato de arrendamiento está próximo a vencer. Nos gustaría discutir las opciones de renovación con usted. Por favor contáctenos al (806) 934-2018 o visite nuestra oficina para programar una cita. — Ross House Rentals LLC",
                "channel": "email",
                "category": "contratos",
            },
            {
                "name": "Inspección Programada",
                "subject": "Inspección de Propiedad Programada",
                "message": "Le informamos que se ha programado una inspección de su propiedad. Le confirmaremos la fecha y hora con anticipación. Por favor asegúrese de que la propiedad esté accesible. Gracias. — Ross House Rentals LLC, (806) 934-2018",
                "channel": "both",
                "category": "inspecciones",
            },
            {
                "name": "Aviso de Emergencia",
                "subject": "AVISO URGENTE — Ross House Rentals",
                "message": "AVISO IMPORTANTE: Se ha detectado una situación que requiere su atención inmediata en su propiedad. Por favor comuníquese con nuestra oficina de inmediato al (806) 934-2018. — Ross House Rentals LLC",
                "channel": "both",
                "category": "emergencia",
            },
            {
                "name": "Felices Fiestas",
                "subject": "¡Felices Fiestas de parte de Ross House Rentals!",
                "message": "De parte de todo el equipo de Ross House Rentals, le deseamos unas felices fiestas y un próspero año nuevo. Agradecemos su confianza como inquilino. ¡Que tenga una excelente temporada! — Ross House Rentals LLC",
                "channel": "email",
                "category": "general",
            },
            {
                "name": "Confirmación de Pago",
                "subject": "Pago Recibido — Gracias",
                "message": "Hemos recibido su pago de renta exitosamente. Gracias por mantenerse al día. Si necesita un recibo, puede descargarlo desde la app o solicitarlo en nuestra oficina. — Ross House Rentals LLC, (806) 934-2018",
                "channel": "both",
                "category": "pagos",
            },
        ]
        for t in defaults:
            t["created_at"] = datetime.utcnow()
        await db.message_templates.insert_many(defaults)
        templates = await db.message_templates.find().sort("created_at", -1).to_list(50)

    return {"success": True, "templates": [serialize(t) for t in templates]}


@router.post('/admin/message-templates')
async def admin_create_template(request: Request):
    """Create a new message template."""
    await auth_admin(request)
    db = get_db()
    data = await request.json()

    template = {
        "name": data.get("name", ""),
        "subject": data.get("subject", ""),
        "message": data.get("message", ""),
        "channel": data.get("channel", "both"),
        "category": data.get("category", "general"),
        "created_at": datetime.utcnow(),
    }
    result = await db.message_templates.insert_one(template)
    template["_id"] = str(result.inserted_id)
    return {"success": True, "template": template}


@router.put('/admin/message-templates/{template_id}')
async def admin_update_template(template_id: str, request: Request):
    """Update a message template."""
    await auth_admin(request)
    db = get_db()
    data = await request.json()

    update = {}
    for field in ["name", "subject", "message", "channel", "category"]:
        if field in data:
            update[field] = data[field]
    update["updated_at"] = datetime.utcnow()

    await db.message_templates.update_one({"_id": ObjectId(template_id)}, {"$set": update})
    updated = await db.message_templates.find_one({"_id": ObjectId(template_id)})
    return {"success": True, "template": serialize(updated)}


@router.delete('/admin/message-templates/{template_id}')
async def admin_delete_template(template_id: str, request: Request):
    """Delete a message template."""
    await auth_admin(request)
    db = get_db()
    await db.message_templates.delete_one({"_id": ObjectId(template_id)})
    return {"success": True}


# ── Inspections ──────────────────────────────────────────────────────────────

INSPECTION_ROOMS = [
    "Sala/Living Room", "Cocina/Kitchen", "Baño Principal/Main Bathroom",
    "Dormitorio 1/Bedroom 1", "Dormitorio 2/Bedroom 2", "Dormitorio 3/Bedroom 3",
    "Baño 2/Bathroom 2", "Garaje/Garage", "Patio/Yard", "Lavandería/Laundry",
]

INSPECTION_ITEMS = [
    "Paredes/Walls", "Pisos/Floors", "Techo/Ceiling", "Ventanas/Windows",
    "Puertas/Doors", "Luces/Lights", "Enchufes/Outlets", "Plomería/Plumbing",
    "Electrodomésticos/Appliances", "Closets", "Pintura/Paint", "Limpieza/Cleanliness",
]


@router.get('/admin/inspections')
async def admin_list_inspections(request: Request):
    """List all property inspections."""
    await auth_admin(request)
    db = get_db()
    inspections = await db.inspections.find().sort("created_at", -1).to_list(200)
    return {
        "success": True,
        "inspections": [serialize(i) for i in inspections],
        "count": len(inspections),
        "rooms": INSPECTION_ROOMS,
        "items": INSPECTION_ITEMS,
    }


@router.post('/admin/inspections')
async def admin_create_inspection(request: Request):
    """Create a new property inspection."""
    await auth_admin(request)
    db = get_db()
    data = await request.json()

    inspection = {
        "property_id": data.get("property_id", ""),
        "property_name": data.get("property_name", ""),
        "tenant_name": data.get("tenant_name", ""),
        "type": data.get("type", "move_in"),  # move_in | move_out | routine
        "status": "pending",  # pending | in_progress | completed
        "scheduled_date": data.get("scheduled_date", ""),
        "rooms": {},  # {room_name: {items: {item: {condition, notes, photos}}}}
        "general_notes": data.get("general_notes", ""),
        "inspector": data.get("inspector", "Admin"),
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
    }

    result = await db.inspections.insert_one(inspection)
    inspection["_id"] = str(result.inserted_id)

    return {"success": True, "inspection": inspection}


@router.put('/admin/inspections/{inspection_id}')
async def admin_update_inspection(inspection_id: str, request: Request):
    """Update an inspection (add room data, photos, change status)."""
    await auth_admin(request)
    db = get_db()
    data = await request.json()

    update = {"updated_at": datetime.utcnow()}
    for field in ["status", "rooms", "general_notes", "scheduled_date", "inspector"]:
        if field in data:
            update[field] = data[field]
    if "completed_at" not in update and data.get("status") == "completed":
        update["completed_at"] = datetime.utcnow()

    await db.inspections.update_one({"_id": ObjectId(inspection_id)}, {"$set": update})

    updated = await db.inspections.find_one({"_id": ObjectId(inspection_id)})
    return {"success": True, "inspection": serialize(updated)}


@router.get('/admin/inspections/{inspection_id}')
async def admin_get_inspection(inspection_id: str, request: Request):
    """Get a single inspection detail."""
    await auth_admin(request)
    db = get_db()
    inspection = await db.inspections.find_one({"_id": ObjectId(inspection_id)})
    if not inspection:
        raise HTTPException(status_code=404, detail="Inspección no encontrada")
    return {
        "success": True,
        "inspection": serialize(inspection),
        "rooms": INSPECTION_ROOMS,
        "items": INSPECTION_ITEMS,
    }
