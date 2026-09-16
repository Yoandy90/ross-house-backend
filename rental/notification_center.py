"""Admin-authenticated campaigns, reusable audiences, durable inbox and Expo delivery."""
import asyncio
import html
import hashlib
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
from bson import ObjectId
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal
from pymongo import ReturnDocument

from rental.shared import get_db, auth_admin, auth_marketplace
from rental.notification_groups import Audience, directory, resolve, ROLES
from rental.notification_identity import id_values

router = APIRouter(tags=['Notification Center'])
BASE = '/admin/notification-center'
log = logging.getLogger(__name__)

def now():
    return datetime.now(timezone.utc)

def actor_id(actor):
    return str(actor.get('_id') or actor.get('id') or actor.get('email'))

def public(doc):
    if not doc:
        return None
    return {('id' if k == '_id' else k): (v.isoformat() if isinstance(v,datetime) else str(v) if isinstance(v,ObjectId) else v) for k,v in doc.items()}

class Message(BaseModel):
    model_config = ConfigDict(extra='forbid')
    title: str = Field(min_length=1,max_length=120)
    body: str = Field(min_length=1,max_length=1000)
    title_en: str = Field(default='',max_length=120)
    body_en: str = Field(default='',max_length=1000)
    category: Literal['operational','news'] = 'operational'
    destination: Literal['notifications','payments','maintenance','contracts','renewals','news'] = 'notifications'
    news_slug: str = Field(default='',max_length=180)

class Preview(BaseModel):
    model_config = ConfigDict(extra='forbid')
    audience: Audience
    message: Message

class CampaignCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    preview_id: str
    scheduled_at: datetime | None = None

class Group(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1,max_length=100)
    audience: Audience

class Template(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1,max_length=100)
    message: Message

BUILTINS = [
 {'id':'rent','name':'Recordatorio de renta','message':{'title':'Tu próximo pago de renta','body':'La renta de {mes} vence el {fecha}. Consulta tu cuenta.','title_en':'Your next rent payment','body_en':'Rent for {mes} is due on {fecha}. Check your account.','destination':'payments'}},
 {'id':'paid','name':'Pago confirmado','message':{'title':'Recibimos tu pago','body':'Tu recibo de {mes} ya está disponible.','title_en':'Payment received','body_en':'Your receipt for {mes} is available.','destination':'payments'}},
 {'id':'visit','name':'Visita programada','message':{'title':'Visita de mantenimiento','body':'Tu visita está programada para el {fecha}.','title_en':'Maintenance visit','body_en':'Your visit is scheduled for {fecha}.','destination':'maintenance'}},
 {'id':'maintenance','name':'Actualización de mantenimiento','message':{'title':'Solicitud #{numero}: {estado}','body':'Consulta los detalles de tu solicitud.','title_en':'Request #{numero}: {estado}','body_en':'View the details of your request.','destination':'maintenance'}},
 {'id':'office','name':'Aviso de oficina','message':{'title':'{asunto}','body':'{mensaje}','title_en':'','body_en':'','destination':'notifications'}},
 {'id':'news','name':'Nueva noticia','message':{'title':'{titulo}','body':'{fragmento}','title_en':'','body_en':'','destination':'news','category':'news'}},
 {'id':'renewal','name':'Renovación disponible','message':{'title':'Tu renovación está disponible','body':'Revisa la propuesta de renovación de tu contrato.','title_en':'Your renewal is available','body_en':'Review your lease renewal proposal.','destination':'renewals'}},
]

async def validate_message(db, message):
    m = message.model_dump() if isinstance(message,Message) else dict(message)
    if not m['title'].strip() or not m['body'].strip():
        raise HTTPException(400,'Escribe el título y mensaje')
    if bool(m.get('title_en')) != bool(m.get('body_en')):
        raise HTTPException(400,'Completa título y mensaje en inglés, o deja ambos vacíos')
    if any(re.search(r'\{[^}]+\}',m.get(k,'')) for k in ['title','body','title_en','body_en']):
        raise HTTPException(400,'Completa las variables de la plantilla antes de enviar')
    if m['destination'] == 'news':
        if not re.fullmatch(r'[A-Za-z0-9_-]+', m.get('news_slug','')):
            raise HTTPException(400,'Selecciona una noticia válida')
        if not await db.email_templates.find_one({'slug':m['news_slug'],'published_to_blog':True}):
            raise HTTPException(400,'La noticia no está publicada')
        m['category'] = 'news'
    return m

@router.get(BASE+'/directory')
async def get_directory(request:Request):
    await auth_admin(request)
    rows = await directory(get_db())
    return {'users':rows,'roles':ROLES}

@router.get(BASE+'/groups')
async def get_groups(request:Request):
    await auth_admin(request)
    return {'groups':[public(g) async for g in get_db().notification_groups.find({'archived':{'$ne':True}}).sort('name',1)]}

@router.post(BASE+'/groups')
async def create_group(body:Group, request:Request):
    actor = await auth_admin(request)
    if body.audience.mode not in ('manual','filters'):
        raise HTTPException(400,'Un grupo debe tener selección manual o filtros')
    await resolve(get_db(),body.audience)
    doc={'_id':str(uuid4()),**body.model_dump(),'created_by':actor_id(actor),'created_at':now()}
    await get_db().notification_groups.insert_one(doc)
    return public(doc)

@router.put(BASE+'/groups/{gid}')
async def edit_group(gid:str,body:Group,request:Request):
    actor=await auth_admin(request)
    if body.audience.mode not in ('manual','filters'):
        raise HTTPException(400,'No se permiten grupos anidados')
    await resolve(get_db(),body.audience)
    result=await get_db().notification_groups.update_one({'_id':gid,'archived':{'$ne':True}}, {'$set':{**body.model_dump(),'updated_by':actor_id(actor),'updated_at':now()}})
    if not result.matched_count: raise HTTPException(404,'Grupo no encontrado')
    return {'success':True}

@router.delete(BASE+'/groups/{gid}')
async def archive_group(gid:str,request:Request):
    actor=await auth_admin(request)
    await get_db().notification_groups.update_one({'_id':gid},{'$set':{'archived':True,'updated_by':actor_id(actor)}})
    return {'success':True}

@router.get(BASE+'/templates')
async def get_templates(request:Request):
    await auth_admin(request)
    return {'templates':BUILTINS+[public(t) async for t in get_db().push_templates.find({})]}

@router.post(BASE+'/templates')
async def save_template(body:Template,request:Request):
    actor=await auth_admin(request)
    doc={'_id':str(uuid4()),**body.model_dump(),'created_by':actor_id(actor),'created_at':now()}
    await get_db().push_templates.insert_one(doc)
    return public(doc)

@router.post(BASE+'/preview')
async def preview(body:Preview,request:Request):
    actor=await auth_admin(request)
    db=get_db()
    message=await validate_message(db,body.message)
    rows=await resolve(db,body.audience,category=message['category'])
    if not rows: raise HTTPException(400,'No hay destinatarios elegibles')
    doc={'_id':str(uuid4()),'actor':actor_id(actor),'audience':body.audience.model_dump(),'message':message,'recipients':[u['id'] for u in rows],'expires_at':now()+timedelta(minutes=15)}
    await db.push_previews.insert_one(doc)
    return {'preview_id':doc['_id'],'total':len(rows),'push_available':sum(u['push_available'] for u in rows),'recipients':rows}

@router.post(BASE+'/campaigns')
async def create_campaign(body:CampaignCreate,request:Request,tasks:BackgroundTasks):
    actor=await auth_admin(request)
    db=get_db()
    p=await db.push_previews.find_one({'_id':body.preview_id,'actor':actor_id(actor),'expires_at':{'$gt':now()}})
    if not p: raise HTTPException(409,'La vista previa expiró. Revísala nuevamente')
    scheduled=body.scheduled_at
    if scheduled and scheduled.tzinfo is None:
        raise HTTPException(400,'Selecciona una fecha futura con zona horaria')
    if scheduled:
        # MongoDB stores UTC milliseconds. Compare retries at the same precision.
        scheduled=scheduled.astimezone(timezone.utc)
        scheduled=scheduled.replace(microsecond=scheduled.microsecond//1000*1000)
    cid=p['_id']
    campaign=await db.push_campaigns.find_one({'_id':cid})
    if not campaign:
        if scheduled and scheduled<=now():
            raise HTTPException(400,'Selecciona una fecha futura con zona horaria')
        campaign=await db.push_campaigns.find_one_and_update({'_id':cid},{'$setOnInsert':{'audience':p['audience'],'message':p['message'],'preview_recipients':p['recipients'],'created_by':actor_id(actor),'created_at':now(),'due_at':scheduled or now(),'dynamic_at_send':bool(scheduled),'status':'queued'}},upsert=True,return_document=ReturnDocument.AFTER)
    was_scheduled=bool(campaign.get('dynamic_at_send'))
    saved_due=campaign['due_at']
    if saved_due.tzinfo is None: saved_due=saved_due.replace(tzinfo=timezone.utc)
    if was_scheduled!=bool(scheduled) or (scheduled and saved_due!=scheduled):
        raise HTTPException(409,'Esta vista previa ya fue confirmada con otra fecha. Revisa el aviso nuevamente')
    if not was_scheduled and campaign['status']=='queued': tasks.add_task(deliver_campaign,db,cid)
    return {'id':cid,'status':campaign['status'],'scheduled':was_scheduled}

@router.get(BASE+'/campaigns')
async def get_campaigns(request:Request):
    await auth_admin(request)
    rows=[]
    async for c in get_db().push_campaigns.find({}).sort('created_at',-1).limit(100):
        item=public(c)
        item.pop('preview_recipients',None)
        item['counts']={}
        async for d in get_db().push_deliveries.find({'campaign_id':str(c['_id'])},{'status':1}):
            s=d['status']; item['counts'][s]=item['counts'].get(s,0)+1
        item['opened']=await get_db().rental_notifications.count_documents({'campaign_id':str(c['_id']),'read_by.0':{'$exists':True}})
        rows.append(item)
    return {'campaigns':rows}

@router.get(BASE+'/campaigns/{cid}')
async def campaign_detail(cid:str,request:Request):
    await auth_admin(request)
    c=await get_db().push_campaigns.find_one({'_id':cid})
    if not c: raise HTTPException(404,'Campaña no encontrada')
    deliveries=[]
    async for d in get_db().push_deliveries.find({'campaign_id':cid}):
        item=public(d)
        n=await get_db().rental_notifications.find_one({'campaign_id':cid,'user_id':d['user_id']},{'read_by':1})
        item['opened']=d['user_id'] in (n or {}).get('read_by',[])
        deliveries.append(item)
    return {'campaign':public(c),'deliveries':deliveries}

@router.post(BASE+'/campaigns/{cid}/cancel')
async def cancel_campaign(cid:str,request:Request):
    actor=await auth_admin(request)
    r=await get_db().push_campaigns.update_one({'_id':cid,'status':'queued'},{'$set':{'status':'cancelled','cancelled_by':actor_id(actor)}})
    if not r.matched_count: raise HTTPException(409,'El envío ya comenzó o no está disponible')
    return {'success':True}

async def expo_send(token,title,body,data):
    headers={}
    if os.getenv('EXPO_ACCESS_TOKEN'): headers['Authorization']='Bearer '+os.environ['EXPO_ACCESS_TOKEN']
    async with httpx.AsyncClient(timeout=15) as client:
        response=await client.post('https://exp.host/--/api/v2/push/send',headers=headers,json={'to':token,'title':title,'body':body,'data':data,'sound':'default'})
    if response.status_code!=200: return {'status':'failed','error':'provider_http_'+str(response.status_code)}
    d=response.json().get('data',{})
    if isinstance(d,list): d=d[0] if d else {}
    return {'status':'accepted','ticket_id':d['id']} if d.get('status')=='ok' and d.get('id') else {'status':'failed','error':d.get('details',{}).get('error','provider_rejected')}

async def deliver_campaign(db,cid):
    c=await db.push_campaigns.find_one_and_update({'_id':cid,'status':'queued','due_at':{'$lte':now()}},{'$set':{'status':'sending','started_at':now()}},return_document=ReturnDocument.AFTER)
    if not c: return
    try:
        m=await validate_message(db,Message.model_validate(c['message']))
        rows=await resolve(db,c['audience'],category=m['category'])
        if not c.get('dynamic_at_send'):
            rows=[u for u in rows if u['id'] in c['preview_recipients']]
        for u in rows:
            uid=u['id']; did=cid+':'+uid
            await db.push_deliveries.update_one({'_id':did},{'$setOnInsert':{'campaign_id':cid,'user_id':uid,'name':u['name'],'status':'pending'}},upsert=True)
            claimed=await db.push_deliveries.find_one_and_update({'_id':did,'status':'pending'},{'$set':{'status':'sending','started_at':now()}},return_document=ReturnDocument.AFTER)
            if not claimed: continue
            data={'type':'news_published' if m['destination']=='news' else 'admin_broadcast','destination':m['destination'],'news_slug':m['news_slug'],'campaign_id':cid}
            nid=ObjectId()
            await db.rental_notifications.update_one({'campaign_id':cid,'user_id':uid},{'$setOnInsert':{'_id':nid,'title':m['title'],'body':m['body'],'title_en':m['title_en'],'body_en':m['body_en'],'type':data['type'],'data':data,'read_by':[],'created_at':now()}},upsert=True)
            notice=await db.rental_notifications.find_one({'campaign_id':cid,'user_id':uid},{'_id':1})
            data['notification_id']=str(notice['_id'])
            user=await db.app_users.find_one({'_id':{'$in':id_values(uid)}},{'push_token':1,'expo_push_token':1})
            token=(user or {}).get('push_token') or (user or {}).get('expo_push_token')
            from push_notification_service import is_expo_token
            outcome={'status':'inbox_only'}
            if is_expo_token(token):
                await db.push_deliveries.update_one({'_id':did},{'$set':{'token_hash':hashlib.sha256(token.encode()).hexdigest()}})
                en=u['language']=='en' and m['title_en'] and m['body_en']
                try:
                    outcome=await expo_send(token,m['title_en'] if en else m['title'],m['body_en'] if en else m['body'],data)
                except Exception:
                    # A timeout can occur AFTER acceptance. Never blindly resend it.
                    outcome={'status':'uncertain','error':'provider_result_unknown'}
            if outcome.get('error') == 'DeviceNotRegistered':
                await db.app_users.update_one({'_id':{'$in':id_values(uid)},'push_token':token},{'$unset':{'push_token':''}})
                await db.app_users.update_one({'_id':{'$in':id_values(uid)},'expo_push_token':token},{'$unset':{'expo_push_token':''}})
            await db.push_deliveries.update_one({'_id':did},{'$set':{**outcome,'updated_at':now()}})
        await db.push_campaigns.update_one({'_id':cid},{'$set':{'status':'finished','finished_at':now(),'recipient_count':len(rows)}})
    except Exception as exc:
        log.warning('Campaign stopped (%s)',type(exc).__name__)
        await db.push_campaigns.update_one({'_id':cid},{'$set':{'status':'requires_review','error':type(exc).__name__}})

@router.post(BASE+'/run-due')
async def run_due(request:Request,tasks:BackgroundTasks):
    await auth_admin(request)
    tasks.add_task(drain,get_db())
    return {'success':True}

async def drain(db):
    # A worker crash is ambiguous: surface it, never resend a potentially accepted push.
    await db.push_campaigns.update_many({'status':'sending','started_at':{'$lt':now()-timedelta(hours=1)}},{'$set':{'status':'requires_review'}})
    async for c in db.push_campaigns.find({'status':'queued','due_at':{'$lte':now()}}).limit(20):
        await deliver_campaign(db,c['_id'])
    await check_receipts(db)

async def check_receipts(db):
    await db.push_deliveries.update_many({'status':'accepted','updated_at':{'$lt':now()-timedelta(hours=24)}},{'$set':{'status':'uncertain','error':'receipt_not_available'}})
    docs=await db.push_deliveries.find({'status':'accepted','updated_at':{'$lt':now()-timedelta(minutes=15)}}).limit(300).to_list(300)
    if not docs: return
    headers={}
    if os.getenv('EXPO_ACCESS_TOKEN'): headers['Authorization']='Bearer '+os.environ['EXPO_ACCESS_TOKEN']
    async with httpx.AsyncClient(timeout=15) as client:
        r=await client.post('https://exp.host/--/api/v2/push/getReceipts',headers=headers,json={'ids':[d['ticket_id'] for d in docs]})
    r.raise_for_status(); receipts=r.json().get('data',{})
    for d in docs:
        rec=receipts.get(d['ticket_id'])
        if rec:
            if rec.get('details',{}).get('error') == 'DeviceNotRegistered':
                user = await db.app_users.find_one({'_id':{'$in':id_values(d['user_id'])}},{'push_token':1,'expo_push_token':1})
                for field in ['push_token','expo_push_token']:
                    token = (user or {}).get(field)
                    if token and hashlib.sha256(token.encode()).hexdigest() == d.get('token_hash'):
                        await db.app_users.update_one({'_id':{'$in':id_values(d['user_id'])},field:token},{'$unset':{field:''}})
            await db.push_deliveries.update_one({'_id':d['_id'],'status':'accepted'},{'$set':{'status':'provider_delivered' if rec.get('status')=='ok' else 'failed','error':rec.get('details',{}).get('error',''),'receipt_at':now()}})

async def scheduler():
    while True:
        try:
            from rental.store_notifications import drain as drain_store
            await drain_store(get_db())
        except Exception as exc: log.warning('Store notification worker (%s)',type(exc).__name__)
        try: await drain(get_db())
        except Exception as exc: log.warning('Notification worker (%s)',type(exc).__name__)
        await asyncio.sleep(30)

class NewsRule(BaseModel):
    model_config=ConfigDict(extra='forbid')
    enabled:bool=False
    audience:Audience=Field(default_factory=Audience)

@router.get(BASE+'/news-rule')
async def get_news_rule(request:Request):
    await auth_admin(request)
    return public(await get_db().push_rules.find_one({'_id':'news'})) or {'enabled':False,'audience':Audience().model_dump()}

@router.put(BASE+'/news-rule')
async def set_news_rule(body:NewsRule,request:Request):
    actor=await auth_admin(request)
    await resolve(get_db(),body.audience,category='news')
    await get_db().push_rules.update_one({'_id':'news'},{'$set':{**body.model_dump(),'updated_by':actor_id(actor),'updated_at':now()}},upsert=True)
    return {'success':True}

async def queue_news(db,doc):
    rule=await db.push_rules.find_one({'_id':'news','enabled':True})
    if not rule: return
    def plain(value): return re.sub(r'\s+',' ',html.unescape(re.sub('<[^>]+>',' ',str(value or '')))).strip()
    message=Message(title=plain(doc.get('subject_es'))[:120] or 'Nueva noticia',body=plain(doc.get('body_es'))[:200] or 'Lee la noticia completa.',title_en=plain(doc.get('subject_en'))[:120],body_en=plain(doc.get('body_en'))[:200],category='news',destination='news',news_slug=doc.get('slug') or '')
    if not message.title_en or not message.body_en: message.title_en=''; message.body_en=''
    m=await validate_message(db,message)
    cid='news:'+str(doc['_id'])
    await db.push_campaigns.update_one({'_id':cid},{'$setOnInsert':{'audience':rule['audience'],'message':m,'created_by':'news_publication','created_at':now(),'due_at':now(),'dynamic_at_send':True,'status':'queued'}},upsert=True)
    return cid

@router.get('/marketplace/notification-preferences')
async def preferences(request:Request):
    user=await auth_marketplace(request)
    return {'news':(user.get('notification_preferences') or {}).get('news',True)}

@router.put('/marketplace/notification-preferences')
async def update_preferences(request:Request):
    user=await auth_marketplace(request); data=await request.json()
    if set(data)!={'news'} or type(data['news']) is not bool: raise HTTPException(400,'Preferencia no válida')
    await get_db().app_users.update_one({'_id':{'$in':id_values(user['_id'])}},{'$set':{'notification_preferences.news':data['news']}})
    return {'success':True}

async def ensure_indexes(db):
    await db.push_previews.create_index('expires_at',expireAfterSeconds=0)
    await db.push_campaigns.create_index([('status',1),('due_at',1)])
    await db.push_deliveries.create_index([('campaign_id',1),('user_id',1)],unique=True)
    await db.rental_notifications.create_index([('campaign_id',1),('user_id',1)],unique=True,partialFilterExpression={'campaign_id':{'$exists':True}})

@router.get('/marketplace/notification-detail/{nid}')
async def notification_detail(nid:str,request:Request):
    user=await auth_marketplace(request)
    if not ObjectId.is_valid(nid): raise HTTPException(404,'Aviso no encontrado')
    from rental.notification_identity import notification_audience
    audience=await notification_audience(get_db(),user)
    doc=await get_db().rental_notifications.find_one({'_id':ObjectId(nid),**audience},{'title':1,'body':1,'title_en':1,'body_en':1,'created_at':1})
    if not doc: raise HTTPException(404,'Aviso no encontrado')
    return {'notification':public(doc)}
