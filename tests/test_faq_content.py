import json
from pathlib import Path
from unittest.mock import AsyncMock
import pytest
from fastapi import FastAPI
from httpx import AsyncClient,ASGITransport
from mongomock_motor import AsyncMongoMockClient
from rental import faq_router as f, shared

@pytest.mark.asyncio
async def test_seed_bilingual_public_and_repeat(monkeypatch):
 db=AsyncMongoMockClient()['faq'];monkeypatch.setattr(shared,'get_db',lambda:db);monkeypatch.setattr(f,'auth_admin',AsyncMock())
 app=FastAPI();app.include_router(f.router);app.include_router(f.public_router)
 async with AsyncClient(transport=ASGITransport(app),base_url='http://test') as c:
  assert (await c.post('/admin/faqs/seed')).json()['seeded']==17
  assert (await c.post('/admin/faqs/seed')).json()['seeded']==0
  es=(await c.get('/public/faqs?lang=es')).json();en=(await c.get('/public/faqs?lang=en-US')).json()
  assert es['total']==en['total']==17 and en['lang']=='en'
  assert all(x['question'] and x['answer'] for x in en['faqs'])
  assert es['faqs'][0]['question']!=en['faqs'][0]['question']
  assert 'Stripe' not in json.dumps(es)+json.dumps(en)
  assert len([x for x in es['faqs'] if x['category']=='store'])==6

@pytest.mark.asyncio
async def test_create_serializes_and_hidden_not_public(monkeypatch):
 db=AsyncMongoMockClient()['faq'];monkeypatch.setattr(shared,'get_db',lambda:db);monkeypatch.setattr(f,'auth_admin',AsyncMock())
 app=FastAPI();app.include_router(f.router);app.include_router(f.public_router)
 async with AsyncClient(transport=ASGITransport(app),base_url='http://test') as c:
  r=await c.post('/admin/faqs',json={'question_es':'Pregunta','answer_es':'Respuesta','active':False})
  assert r.status_code==200 and r.json()['faq']['id']
  assert (await c.get('/public/faqs')).json()['total']==0
  await c.put('/admin/faqs/'+r.json()['faq']['id'],json={'active':True})
  assert (await c.get('/public/faqs?lang=en')).json()['faqs'][0]['answer']=='Respuesta'

@pytest.mark.asyncio
async def test_seed_requires_admin(monkeypatch):
 from fastapi import HTTPException
 monkeypatch.setattr(f,'auth_admin',AsyncMock(side_effect=HTTPException(403,'Denied')))
 app=FastAPI();app.include_router(f.router)
 async with AsyncClient(transport=ASGITransport(app),base_url='http://test') as c:
  assert (await c.post('/admin/faqs/seed')).status_code==403
