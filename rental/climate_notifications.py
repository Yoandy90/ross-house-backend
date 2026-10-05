"""Climate alert inbox/push delivery using Ross House's existing notification stack."""
from __future__ import annotations

import hashlib
import os
from datetime import datetime

from bson import ObjectId

from .notification_groups import Audience, resolve
from .notification_identity import id_values, push_recipient


COPY = {
    "offline": {
        "es": ("Termostato sin conexión", "Ross House no está recibiendo lecturas del termostato."),
        "en": ("Thermostat offline", "Ross House is not receiving thermostat readings."),
    },
    "temperature_low": {
        "es": ("Temperatura interior baja", "La propiedad cayó por debajo del umbral de protección."),
        "en": ("Low indoor temperature", "The property dropped below the protection threshold."),
    },
    "temperature_high": {
        "es": ("Temperatura interior alta", "La propiedad superó el umbral de protección."),
        "en": ("High indoor temperature", "The property exceeded the protection threshold."),
    },
    "humidity_high_sustained": {
        "es": ("Humedad alta prolongada", "La humedad interior se mantiene elevada y debe revisarse."),
        "en": ("Sustained high humidity", "Indoor humidity has remained elevated and should be reviewed."),
    },
    "humidity_low_sustained": {
        "es": ("Humedad baja prolongada", "La humedad interior se mantiene demasiado baja."),
        "en": ("Sustained low humidity", "Indoor humidity has remained unusually low."),
    },
    "humidity_rising_fast": {
        "es": ("Humedad subiendo rápido", "La humedad interior aumentó rápidamente. Revisa la propiedad si continúa."),
        "en": ("Humidity rising quickly", "Indoor humidity increased quickly. Review the property if it continues."),
    },
    "no_progress": {
        "es": ("HVAC trabajando sin progreso", "El sistema está funcionando pero la temperatura no avanza hacia el objetivo como se esperaba."),
        "en": ("HVAC running without progress", "The system is running but temperature is not moving toward target as expected."),
    },
    "short_cycling": {
        "es": ("Demasiados ciclos HVAC", "El equipo está arrancando con demasiada frecuencia en poco tiempo."),
        "en": ("Frequent HVAC cycling", "The equipment is starting too frequently within a short period."),
    },
    "excessive_runtime": {
        "es": ("Funcionamiento HVAC elevado", "El sistema lleva un porcentaje inusualmente alto del tiempo trabajando y sigue lejos del objetivo."),
        "en": ("High HVAC runtime", "The system has been running unusually often and remains away from target."),
    },
    "efficiency_degradation": {
        "es": ("Posible pérdida de eficiencia", "El control de temperatura reciente es peor que la referencia histórica de esta propiedad."),
        "en": ("Possible efficiency degradation", "Recent temperature control is worse than this property's historical baseline."),
    },
    "fan_extended": {
        "es": ("Ventilador funcionando por mucho tiempo", "El ventilador lleva un período prolongado funcionando."),
        "en": ("Fan running for a long time", "The fan has been running for an extended period."),
    },
    "emergency_heat_extended": {
        "es": ("Emergency Heat prolongado", "La calefacción de emergencia lleva un período prolongado activa. Revisa el sistema y el consumo."),
        "en": ("Extended emergency heat", "Emergency heat has been active for an extended period. Review the system and energy use."),
    },
    "command_unconfirmed": {
        "es": ("Cambio de clima sin confirmar", "Un comando al termostato no pudo confirmarse. Actualiza antes de repetirlo."),
        "en": ("Climate change unconfirmed", "A thermostat command could not be confirmed. Refresh before retrying it."),
    },
    "schedule_unconfirmed": {
        "es": ("Schedule sin confirmar", "Un cambio programado no llegó a un estado final confirmado."),
        "en": ("Schedule unconfirmed", "A scheduled change did not reach a confirmed final state."),
    },
    "filter_runtime": {
        "es": ("Revisar filtro HVAC", "El tiempo estimado de funcionamiento alcanzó el intervalo configurado para revisar/cambiar el filtro."),
        "en": ("Check HVAC filter", "Estimated runtime reached the configured filter service interval."),
    },
    "stale_telemetry": {
        "es": ("Telemetría climática atrasada", "Las lecturas del termostato están más antiguas de lo esperado."),
        "en": ("Climate telemetry stale", "Thermostat readings are older than expected."),
    },
    "cooling_in_cold_weather": {
        "es": ("Enfriamiento con frío exterior", "El aire acondicionado está enfriando mientras la temperatura oficial exterior es inusualmente baja."),
        "en": ("Cooling in cold weather", "Cooling is active while the official outdoor temperature is unusually low."),
    },
    "heating_in_hot_weather": {
        "es": ("Calefacción con calor exterior", "La calefacción está activa mientras la temperatura oficial exterior es inusualmente alta."),
        "en": ("Heating in warm weather", "Heating is active while the official outdoor temperature is unusually warm."),
    },
    "forecast_freeze_risk": {
        "es": ("Riesgo de congelación", "Se pronostica congelación y la temperatura interior está acercándose al rango de protección."),
        "en": ("Freeze risk", "Freezing weather is forecast and indoor temperature is approaching the protection range."),
    },
    "nws_severe_weather": {
        "es": ("Alerta meteorológica oficial", "El National Weather Service tiene una alerta severa o extrema activa para esta propiedad."),
        "en": ("Official severe weather alert", "The National Weather Service has an active severe or extreme alert for this property."),
    },
    "schedule_missed": {
        "es": ("Schedule no ejecutado", "Un horario de Ross House debía ejecutarse y no encontramos una ejecución exitosa."),
        "en": ("Schedule missed", "A Ross House climate schedule was due and no successful execution was recorded."),
    },
    "unexpected_change": {
        "es": ("Cambio fuera del horario", "Detectamos un cambio de modo o temperatura fuera de una ejecución del Schedule de Ross House."),
        "en": ("Change outside schedule", "A mode or temperature change was detected outside a Ross House schedule execution."),
    },
    "thermal_envelope_degradation": {
        "es": ("Posible pérdida térmica mayor", "La temperatura interior está variando más rápido con el HVAC en espera que en el historial de esta propiedad."),
        "en": ("Possible thermal drift increase", "Indoor temperature is drifting faster while HVAC is idle than this property's historical baseline."),
    },
}

RESOLVED = {
    "es": ("Alerta climática resuelta", "La condición climática detectada ya no está activa."),
    "en": ("Climate alert resolved", "The detected climate condition is no longer active."),
}


def enabled():
    return os.getenv("CLIMATE_ALERT_NOTIFICATIONS_ENABLED") == "true"


def push_enabled():
    return enabled() and os.getenv("CLIMATE_ALERT_PUSH_ENABLED") == "true"


def _public_id(value):
    return str(value) if not isinstance(value, ObjectId) else str(value)


async def _recipients(db, binding):
    tenant_audience = Audience(
        mode="filters",
        roles=["tenant"],
        property_ids=[str(binding.get("property_id", ""))],
        active_contract_only=True,
    )
    rows = await resolve(db, tenant_audience, category="operational")
    if os.getenv("CLIMATE_ALERT_NOTIFY_ADMINS") == "true":
        admins = await resolve(
            db,
            Audience(mode="filters", roles=["admin"], active_contract_only=False),
            category="operational",
        )
        known = {row["id"] for row in rows}
        rows.extend(row for row in admins if row["id"] not in known)
    return rows


def alert_category(alert_type: str) -> str:
    if alert_type == "offline":
        return "offline"
    if alert_type.startswith("temperature_"):
        return "temperature"
    if alert_type.startswith("humidity_"):
        return "humidity"
    if alert_type in {
        "cooling_in_cold_weather",
        "heating_in_hot_weather",
        "forecast_freeze_risk",
        "nws_severe_weather",
    }:
        return "weather"
    return "predictive"


async def notify_transition(
    db,
    binding: dict,
    alert_type: str,
    severity: str,
    active: bool,
    transition_at: datetime,
):
    if not enabled():
        return {"status": "disabled"}

    transition_key = hashlib.sha256(
        f"{binding['_id']}:{alert_type}:{active}:{transition_at.isoformat()}".encode()
    ).hexdigest()
    existing = await db.climate_alert_deliveries.find_one({"_id": transition_key})
    if existing:
        return {"status": existing.get("status", "existing")}

    await db.climate_alert_deliveries.insert_one({
        "_id": transition_key,
        "device_id": binding["_id"],
        "property_id": binding.get("property_id", ""),
        "alert_type": alert_type,
        "severity": severity,
        "active": active,
        "created_at": transition_at,
        "status": "sending",
    })

    rows = await _recipients(db, binding)
    delivered = 0
    pushed = 0
    for row in rows:
        table, account = await push_recipient(db, row["id"])
        if not account:
            continue
        prefs = account.get("notification_preferences") or {}
        if prefs.get("climate") is False:
            continue
        climate_prefs = account.get("climate_alert_preferences") or {}
        if climate_prefs.get(alert_category(alert_type)) is False:
            continue

        if active:
            copy = COPY.get(alert_type) or {
                "es": ("Alerta de climatización", "Ross House detectó una condición que debe revisarse."),
                "en": ("Climate alert", "Ross House detected a condition that should be reviewed."),
            }
        else:
            copy = {"es": RESOLVED["es"], "en": RESOLVED["en"]}

        title_es, body_es = copy["es"]
        title_en, body_en = copy["en"]
        user_id = str(account["_id"])
        notification_id = ObjectId()
        data = {
            "type": "climate_alert" if active else "climate_alert_resolved",
            "destination": "notifications",
            "notification_id": str(notification_id),
            "device_id": binding["_id"],
            "property_id": str(binding.get("property_id", "")),
            "alert_type": alert_type,
            "severity": severity,
        }
        await db.rental_notifications.insert_one({
            "_id": notification_id,
            "user_id": user_id,
            "title": title_es,
            "body": body_es,
            "title_en": title_en,
            "body_en": body_en,
            "type": data["type"],
            "data": data,
            "read_by": [],
            "created_at": transition_at,
        })
        delivered += 1

        token = account.get("push_token") or account.get("expo_push_token")
        if push_enabled() and token:
            from push_notification_service import is_expo_token
            if is_expo_token(token):
                language = str(account.get("preferred_language") or account.get("language") or "es")
                english = language.lower().startswith("en")
                try:
                    from .notification_center import expo_send
                    outcome = await expo_send(
                        token,
                        title_en if english else title_es,
                        body_en if english else body_es,
                        data,
                    )
                    if outcome.get("status") == "accepted":
                        pushed += 1
                    if outcome.get("error") == "DeviceNotRegistered":
                        await db[table].update_one(
                            {"_id": {"$in": id_values(account["_id"])}},
                            {"$unset": {"push_token": "", "expo_push_token": ""}},
                        )
                except Exception:
                    pass

    await db.climate_alert_deliveries.update_one(
        {"_id": transition_key},
        {"$set": {
            "status": "finished",
            "recipient_count": delivered,
            "push_count": pushed,
            "finished_at": datetime.utcnow(),
        }},
    )
    return {"status": "finished", "recipients": delivered, "push": pushed}
