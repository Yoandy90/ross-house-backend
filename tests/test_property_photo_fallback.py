import base64
from unittest.mock import AsyncMock
import pytest
from mongomock_motor import AsyncMongoMockClient
import rental_storage_service as storage
from rental import properties_router as routes


def test_upload_preserves_mongo_payload(monkeypatch):
    monkeypatch.setattr(storage,'_use_mongo_fallback',True)
    photo=storage.upload_property_photo('demo','photo'.encode(),'test.jpg','image/jpeg')
    assert photo['storage_type']=='mongodb'
    assert base64.b64decode(photo['base64_data'].split(',')[1])==b'photo'


@pytest.mark.asyncio
async def test_public_photo_reads_exact_non_deleted_fallback(monkeypatch):
    db=AsyncMongoMockClient()['photo_test'];monkeypatch.setattr(routes,'get_db',lambda:db)
    path='properties/demo/photo.jpg'
    await db.property_photos.insert_one({'storage_path':'ross-rentals/'+path,'storage_type':'mongodb','is_deleted':False,'base64_data':'data:image/jpeg;base64,'+base64.b64encode(b'photo').decode(),'content_type':'image/jpeg'})
    result=await routes.public_serve_property_file(path)
    assert result.body==b'photo' and result.media_type=='image/jpeg'
    await db.property_photos.update_one({'storage_path':'ross-rentals/'+path},{'$set':{'is_deleted':True}})
    monkeypatch.setattr(storage,'get_object',lambda _: (_ for _ in ()).throw(Exception('missing')))
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc: await routes.public_serve_property_file(path)
    assert exc.value.status_code==404
