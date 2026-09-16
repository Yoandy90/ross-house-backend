import asyncio
import pytest
from httpx import AsyncClient, ASGITransport
from test_resident_store import shop, setup, order, checkout
from rental import resident_store as s

@pytest.mark.asyncio
async def test_sku_barcode_variants_stock_reason_and_movements(shop):
    db, app = shop; state = await setup(db)
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as c:
        product = {**state['products']['water'], 'sku': 'shirt-m-red', 'barcode': '123456789', 'family_id': 'shirt', 'size': 'M', 'color': 'Red'}
        r = await c.put('/admin/store/products/shirt-m', json={'revision': 0, 'product': product}); assert r.status_code == 200
        for field in ('sku','barcode'):
            duplicate = {**product, 'sku': 'shirt-l-red', 'barcode': '999', field: product[field]}
            r = await c.put('/admin/store/products/shirt-l', json={'revision': (await s.read_state())['revision'], 'product': duplicate})
            assert r.status_code == 409 and r.json()['detail'] == f'store_{field}_exists'
        product['stock'] = 6
        assert (await c.put('/admin/store/products/shirt-m', json={'revision': (await s.read_state())['revision'], 'product': product})).status_code == 400
        assert (await c.put('/admin/store/products/shirt-m', json={'revision': (await s.read_state())['revision'], 'product': product, 'stock_reason': 'Supplier delivery'})).status_code == 200
        oid = (await order(c)).json()['id']
        for _ in range(2): await c.post(f'/store/orders/{oid}/cancel')
        moves = (await c.get('/admin/store')).json()['inventory_movements']
        assert [m['target']['quantity_delta'] for m in moves if m['target']['product_id']=='water'] == [-1,1]
        assert [m['target']['quantity_delta'] for m in moves if m['target']['product_id']=='shirt-m'] == [3,3]
        assert (await s.read_state())['products']['shirt-m']['sku'] == 'SHIRT-M-RED'

@pytest.mark.asyncio
async def test_tax_profile_changes_requote_not_history_and_fixed_weight(shop):
    db, app = shop; state = await setup(db)
    cfg = {**state['settings'], 'tax_classes': [{'id':'standard','name':'Standard','rate_bps':825}]}
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as c:
        assert (await c.put('/admin/store/settings', json={'revision':0,'settings':cfg})).status_code == 200
        product = {**state['products']['water'], 'tax_class':'standard','sale_unit':'kg','pack_size':'0.5'}
        assert (await c.put('/admin/store/products/water', json={'revision':1,'product':product})).status_code == 200
        oid = (await order(c)).json()['id']
        line = (await s.read_state())['orders'][oid]['items'][0]
        assert line['sku'] == 'RH-WATER' and line['pack_size'] == '0.5' and line['tax_bps']==825
        cfg['tax_classes'][0]['rate_bps']=1000
        assert (await c.put('/admin/store/settings', json={'revision': (await s.read_state())['revision'],'settings':cfg})).status_code == 200
        q = (await c.post('/store/quote', json=checkout())).json()
        assert q['items'][0]['tax_bps']==1000
        assert (await s.read_state())['orders'][oid]['items'][0]['tax_bps']==825
        cfg['tax_classes']=[]
        assert (await c.put('/admin/store/settings', json={'revision': (await s.read_state())['revision'],'settings':cfg})).status_code == 409
        assert (await c.put('/admin/store/products/invalid', json={'revision': (await s.read_state())['revision'],'product':{**product,'pack_size':'NaN'}})).status_code == 400
