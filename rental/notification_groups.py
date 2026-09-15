"""Audience resolution for Ross House only. Explicit identity links, no email joins."""
from datetime import datetime, timezone
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict
from fastapi import HTTPException

ROLES = ['tenant', 'maintenance', 'landlord', 'buyer', 'admin', 'guest']

class Audience(BaseModel):
    model_config = ConfigDict(extra='forbid')
    mode: Literal['filters', 'manual', 'groups', 'all'] = 'filters'
    roles: list[str] = Field(default_factory=lambda: ['tenant'], max_length=10)
    worker_types: list[Literal['employee', 'contractor']] = Field(default_factory=list)
    cities: list[str] = Field(default_factory=list, max_length=100)
    property_types: list[str] = Field(default_factory=list, max_length=30)
    property_ids: list[str] = Field(default_factory=list, max_length=500)
    active_contract_only: bool = True
    user_ids: list[str] = Field(default_factory=list, max_length=10000)
    group_ids: list[str] = Field(default_factory=list, max_length=100)
    exclude_ids: list[str] = Field(default_factory=list, max_length=10000)


def norm(v):
    return str(v or '').strip().casefold()


def current_contract(c):
    if c.get('status') != 'active':
        return False
    today = datetime.now(timezone.utc).date().isoformat()
    # Status alone can lag the expiration date. Inclusive ISO dates.
    for key, cmp in [('start_date', 'start'), ('end_date', 'end')]:
        value = c.get(key) or (c.get('lease_end_date') if key == 'end_date' else c.get('lease_start_date'))
        if isinstance(value, datetime):
            value = value.date().isoformat()
        if value:
            day = str(value)[:10]
            if len(day) == 10 and ((cmp == 'start' and day > today) or (cmp == 'end' and day < today)):
                return False
    return True


async def directory(db):
    # Projections deliberately exclude credentials, payment data and tax identifiers.
    properties = {str(p['_id']): p async for p in db.properties.find({}, {'address':1,'city':1,'type':1,'property_type':1})}
    tenants = {str(t['_id']): str(t.get('app_user_id') or '') async for t in db.tenants.find({}, {'app_user_id':1})}
    homes = {}
    async for contract in db.rental_contracts.find({'status':'active'}, {'tenant_id':1,'property_id':1,'status':1,'start_date':1,'end_date':1,'lease_start_date':1,'lease_end_date':1}):
        if current_contract(contract):
            uid = tenants.get(str(contract.get('tenant_id')))
            prop = properties.get(str(contract.get('property_id')))
            if uid:
                homes.setdefault(uid, []).append(prop or {'_id':contract.get('property_id')})
    providers = {str(p['_id']): p async for p in db.service_providers.find({}, {'status':1,'worker_type':1,'app_user_id':1})}
    result = []
    async for u in db.app_users.find({'status':{'$nin':['deleted','inactive','disabled','suspended']}, 'is_active':{'$ne':False}}, {'name':1,'email':1,'role':1,'service_provider_id':1,'push_token':1,'expo_push_token':1,'language':1,'preferred_language':1,'notification_preferences':1}):
        uid, role = str(u['_id']), u.get('role')
        if role not in ROLES:
            continue
        worker_type = ''
        if role == 'maintenance':
            p = providers.get(str(u.get('service_provider_id')))
            if not p or p.get('status') != 'active' or str(p.get('app_user_id') or '') != uid:
                continue
            worker_type = p.get('worker_type') or 'contractor'
        from push_notification_service import is_expo_token
        token = u.get('push_token') or u.get('expo_push_token') or ''
        result.append({'id':uid, 'name':u.get('name') or u.get('email') or uid, 'role':role, 'worker_type':worker_type,
            'homes':[{'id':str(p['_id']), 'address':p.get('address',''), 'city':p.get('city',''), 'type':p.get('type') or p.get('property_type') or ''} for p in homes.get(uid,[])],
            'push_available':is_expo_token(token), 'language':'en' if norm(u.get('preferred_language') or u.get('language')).startswith('en') else 'es',
            'news_enabled':(u.get('notification_preferences') or {}).get('news',True)})
    return result


def matches(u, a):
    if a.roles and u['role'] not in a.roles:
        return False
    if a.worker_types and u['worker_type'] not in a.worker_types:
        return False
    if a.active_contract_only and u['role'] == 'tenant' and not u['homes']:
        return False
    if a.cities or a.property_types or a.property_ids:
        # All location conditions must match the SAME home.
        return any((not a.cities or norm(h['city']) in {norm(c) for c in a.cities}) and
                   (not a.property_types or norm(h['type']) in {norm(t) for t in a.property_types}) and
                   (not a.property_ids or h['id'] in a.property_ids) for h in u['homes'])
    return True


async def resolve(db, audience, *, category='operational', rows=None):
    a = audience if isinstance(audience, Audience) else Audience.model_validate(audience)
    if any(r not in ROLES for r in a.roles):
        raise HTTPException(400, 'Rol no válido')
    rows = await directory(db) if rows is None else rows
    selected = set()
    if a.mode == 'manual':
        selected.update(a.user_ids)
    elif a.mode == 'groups':
        if not a.group_ids:
            raise HTTPException(400, 'Selecciona al menos un grupo')
        async for group in db.notification_groups.find({'_id':{'$in':a.group_ids}, 'archived':{'$ne':True}}):
            ga = Audience.model_validate(group['audience'])
            if ga.mode not in ('manual','filters'):
                raise HTTPException(400, 'No se permiten grupos anidados')
            selected.update(u['id'] for u in await resolve(db, ga, category=category, rows=rows))
    elif a.mode == 'all':
        selected.update(u['id'] for u in rows)
    else:
        if not a.roles:
            raise HTTPException(400, 'Selecciona un rol o Todos los usuarios')
        selected.update(u['id'] for u in rows if matches(u,a))
    return [u for u in rows if u['id'] in selected and u['id'] not in a.exclude_ids and (category != 'news' or u['news_enabled'])]
