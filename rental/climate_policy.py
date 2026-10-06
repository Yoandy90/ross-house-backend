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
    settings = raw.get('settings') or {}
    fan = raw.get('fan') or settings.get('fan') or {}
    fan_values = fan.get('changeableValues') or {}
    fan_modes = [x for x in fan.get('allowedModes', []) if x in ('Auto', 'On', 'Circulate', 'FollowSchedule')]
    operation = raw.get('operationStatus') or {}
    activity = raw.get('activity')
    if activity not in ('heating', 'cooling', 'idle'):
        activity = {
            'Heating': 'heating',
            'Cooling': 'cooling',
            'EquipmentOff': 'idle',
        }.get(operation.get('mode'))
    hold = raw.get('holdStatus') or values.get('thermostatSetpointStatus')
    if hold in ('NoHold', 'Schedule'):
        hold = 'schedule'
    elif hold in ('PermanentHold', 'Permanent'):
        hold = 'permanent'
    elif hold in ('TemporaryHold', 'HoldUntil', 'Temporary'):
        hold = 'temporary'
    schedule_capabilities = raw.get('scheduleCapabilities') or {}
    available_schedules = schedule_capabilities.get('availableScheduleTypes') or []
    current_period = raw.get('currentSchedulePeriod')
    return {
        'online': raw.get('isAlive') is True and not raw.get('isUpgrading', False),
        'units': raw.get('units'), 'temperature': raw.get('indoorTemperature'),
        'humidity': raw.get('indoorHumidity'),
        'outdoorTemperature': raw.get('outdoorTemperature'),
        'outdoorHumidity': raw.get('displayedOutdoorHumidity'),
        'mode': values.get('mode'), 'heatSetpoint': values.get('heatSetpoint'),
        'coolSetpoint': values.get('coolSetpoint'),
        'activity': activity,
        'modes': [x for x in raw.get('allowedModes', []) if x in ('Off', 'Heat', 'Cool', 'Auto', 'EmergencyHeat')],
        'fanModes': fan_modes,
        'fanMode': fan.get('mode') or fan_values.get('mode'),
        'fanRunning': fan.get('running') if 'running' in fan else (
            operation.get('fanRequest') or operation.get('circulationFanRequest')
            if operation else None),
        'holdStatus': hold,
        'scheduleStatus': raw.get('scheduleStatus'),
        'currentSchedulePeriod': current_period if isinstance(current_period, dict) else None,
        'capabilities': {
            'fan': bool(fan.get('supported', bool(fan_modes))),
            'hold': hold is not None,
            'schedule': any(x not in ('None', None) for x in available_schedules),
            'scheduleFan': schedule_capabilities.get('schedulableFan') is True,
            'outdoor': raw.get('outdoorTemperature') is not None or raw.get('displayedOutdoorHumidity') is not None,
        },
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
