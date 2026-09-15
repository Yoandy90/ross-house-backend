import base64
from io import BytesIO
from unittest.mock import AsyncMock
import pytest
from PIL import Image
from fastapi import FastAPI, HTTPException
from httpx import AsyncClient, ASGITransport
from mongomock_motor import AsyncMongoMockClient
from rental.catalog_images import normalize_product_photo
from rental import resident_store as s


def picture(mode='RGBA', size=(1600, 800), color=(255, 0, 0, 128)):
    output=BytesIO(); Image.new(mode,size,color).save(output,format='PNG'); return output.getvalue()


def test_square_contains_product_and_flattens_transparency():
    result=normalize_product_photo(picture())
    image=Image.open(BytesIO(result))
    assert image.size==(1200,1200) and image.format=='WEBP'
    assert len(result)<=250_000 and not image.getexif()
    assert min(image.getpixel((600,0)))>245
    r,g,b=image.getpixel((600,600)); assert r>240 and 110<g<145 and 110<b<145


def test_exif_orientation_before_contain():
    photo=Image.new('RGB',(800,400),'red');exif=Image.Exif();exif[274]=6
    source=BytesIO();photo.save(source,format='JPEG',exif=exif)
    result=Image.open(BytesIO(normalize_product_photo(source.getvalue())))
    assert result.getpixel((600,250))[0]>240
    assert min(result.getpixel((250,600)))>245
    assert not result.getexif()


@pytest.mark.parametrize('raw',[b'',b'not an image',b'x'*3_000_001,b'<svg></svg>'])
def test_reject_invalid_files(raw):
    with pytest.raises(ValueError): normalize_product_photo(raw)


@pytest.mark.asyncio
async def test_upload_roundtrip_dedup_and_auth(monkeypatch):
    monkeypatch.setenv('RAILWAY_PUBLIC_DOMAIN', 'images-staging.example.test')
    db=AsyncMongoMockClient()['images_test']
    monkeypatch.setattr(s,'get_db',lambda:db)
    auth=AsyncMock(return_value={'_id':'admin-1'});monkeypatch.setattr(s,'auth_admin',auth)
    app=FastAPI();app.include_router(s.router)
    body={'data':base64.b64encode(picture()).decode()}
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as client:
        first=await client.post('/admin/store/images',json=body)
        assert first.status_code==200,first.text
        assert first.json()['url']=='https://images-staging.example.test'+first.json()['path']
        second=await client.post('/admin/store/images',json=body)
        assert first.json()==second.json()
        assert await db.resident_store_images.count_documents({})==1
        photo=await client.get(first.json()['path'].removeprefix('/api'))
        assert photo.status_code==200 and photo.headers['content-type']=='image/webp'
        assert len(photo.content)==first.json()['bytes']
        assert (await client.get('/public/store-images/invalid')).status_code==404
        assert (await client.post('/admin/store/images',json={'data':'!'})).status_code==400
        auth.side_effect=HTTPException(403,'denied')
        assert (await client.post('/admin/store/images',json=body)).status_code==403
