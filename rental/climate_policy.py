"""Pure, fail-closed thermostat validation shared by all climate endpoints."""
import math
from datetime import date, datetime
from fastapi import HTTPException


def scope_for_contract(contract):
    if not contract or contract.get('status') != 'active' or not contract.get('property_id'):
        raise HTTPException(403, 'climate_no_active_home')
    today = date.today()
    for field, future in [('start_date', True), ('end_date', False)]:
        value = contract.get(field)
        try:
            day = value.date() if isinstance(value, datetime) else date.fromisoformat(str(value)[:10])
        except (TypeError, ValueError):
            raise HTTPException(403, 'climate_contract_dates_invalid')
        if (future and day > today) or (not future and day < today):
            raise HTTPException(403, 'climate_no_active_home')
    return {'property_id': str(contract['property_id']), 'unit_id': str(contract.get('unit_id') or '')}


def finite(value):
    return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value)


def snapshot(raw):
    values = raw.get('changeableValues') or {}
    return {
        'online': raw.get('isAlive') is True and not raw.get('isUpgrading', False),
        'units': raw.get('units'), 'temperature': raw.get('indoorTemperature'),
        'humidity': raw.get('indoorHumidity'),
        'mode': values.get('mode'), 'heatSetpoint': values.get('heatSetpoint'),
        'coolSetpoint': values.get('coolSetpoint'),
        'activity': raw.get('activity') if raw.get('activity') in ('heating', 'cooling', 'idle') else None,
        'modes': [x for x in raw.get('allowedModes', []) if x in ('Off', 'Heat', 'Cool', 'Auto')],
        **{k: raw.get(k) for k in ('minHeatSetpoint', 'maxHeatSetpoint', 'minCoolSetpoint', 'maxCoolSetpoint', 'deadband')},
    }


def validate_change(raw, command):
    if not snapshot(raw)['online']:
        raise HTTPException(409, 'climate_device_offline')
    if raw.get('units') not in ('Fahrenheit', 'Celsius'):
        raise HTTPException(409, 'climate_units_unknown')
    values = dict(raw.get('changeableValues') or {})
    if not command or set(command) - {'mode', 'heatSetpoint', 'coolSetpoint'}:
        raise HTTPException(422, 'climate_invalid_command')
    if 'mode' in command and command['mode'] not in snapshot(raw)['modes']:
        raise HTTPException(422, 'climate_mode_unsupported')
    for key, kind in [('heatSetpoint', 'Heat'), ('coolSetpoint', 'Cool')]:
        if key in command:
            value = command[key]
            low, high = raw.get('min' + kind + 'Setpoint'), raw.get('max' + kind + 'Setpoint')
            if not all(finite(x) for x in (value, low, high)) or not low <= value <= high:
                raise HTTPException(422, 'climate_temperature_out_of_range')
    values.update(command)
    heat, cool = values.get('heatSetpoint'), values.get('coolSetpoint')
    if values.get('mode') == 'Auto':
        band = raw.get('deadband')
        if not all(finite(x) for x in (heat, cool, band)) or cool - heat < band:
            raise HTTPException(422, 'climate_deadband_invalid')
    if 'heatSetpoint' in command or 'coolSetpoint' in command:
        values['thermostatSetpointStatus'] = 'PermanentHold'
    return values
