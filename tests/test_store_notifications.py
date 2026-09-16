"""Isolated Mongo/provider tests. Never deliver a real push."""
import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient, ASGITransport

from test_resident_store import shop, setup, order, checkout
from rental import resident_store as s, store_notifications as n, notification_center as center
from rental.notification_identity import notification_audience


@pytest.fixture(autouse=True)
def transport(monkeypatch):
    monkeypatch.setenv('ENVIRONMENT', 'production')
    monkeypatch.setenv('DISABLE_BACKGROUND_JOBS', 'false')
    sender = AsyncMock(return_value={'status': 'accepted', 'ticket_id': 'fake-ticket'})
    monkeypatch.setattr(center, 'expo_send', sender)
    return sender


async def users(db):
    await db.app_users.insert_many([
        {'_id': 'resident-1', 'role': 'tenant', 'push_token': 'ExpoPushToken[resident]', 'language': 'en'},
        {'_id': 'admin-1', 'role': 'admin', 'push_token': 'ExpoPushToken[admin]'},
        {'_id': 'other', 'role': 'tenant', 'push_token': 'ExpoPushToken[other]'},
    ])


async def create(shop):
    db, app = shop
    await setup(db)
    await users(db)
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        result = await order(client)
        assert result.status_code == 200
        return result.json()['id']


@pytest.mark.asyncio
async def test_retry_and_concurrent_workers_have_one_notice_and_push_per_recipient(shop, transport):
    db, app = shop
    oid = await create(shop)
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        assert (await order(client)).json()['id'] == oid
    assert not transport.called, 'HTTP order requests must not send pushes'
    await asyncio.gather(n.drain(db), n.drain(db), n.drain(db))
    assert transport.await_count == 2
    assert await db.rental_notifications.count_documents({}) == 2
    assert await db.push_deliveries.count_documents({'status': 'accepted'}) == 2
    owner_call = next(c for c in transport.call_args_list if c.args[0] == 'ExpoPushToken[resident]')
    assert owner_call.args[1] == 'Order received'
    assert set(owner_call.args[3]) == {'type', 'order_id', 'status', 'notification_id'}
    assert 'address' not in str(owner_call) and '432' not in str(owner_call)


@pytest.mark.asyncio
async def test_inbox_failure_does_not_fail_purchase_and_recovers_after_restart(shop, monkeypatch):
    db, app = shop
    real = n.sync_inbox
    monkeypatch.setattr(n, 'sync_inbox', AsyncMock(side_effect=RuntimeError('database unavailable')))
    await create(shop)
    assert len((await s.read_state())['orders']) == 1
    assert await db.rental_notifications.count_documents({}) == 0
    monkeypatch.setattr(n, 'sync_inbox', real)
    await n.sync_inbox(db)
    assert await db.rental_notifications.count_documents({}) == 2


@pytest.mark.asyncio
async def test_partial_materialization_replay_keeps_read_state(shop, monkeypatch):
    db, _ = shop
    await create(shop)
    notice = await db.rental_notifications.find_one({'user_id': 'resident-1'})
    await db.rental_notifications.update_one({'_id': notice['_id']}, {'$set': {'read_by': ['resident-1']}})
    await db.store_notification_events.update_many({'status': 'materialized'}, {'$set': {'status': 'pending'}})
    await db.store_notification_events.delete_one({'_id': n.CURSOR_KEY})
    await asyncio.gather(n.sync_inbox(db), n.sync_inbox(db))
    assert await db.rental_notifications.count_documents({}) == 2
    assert (await db.rental_notifications.find_one({'_id': notice['_id']}))['read_by'] == ['resident-1']


@pytest.mark.asyncio
async def test_legacy_orders_are_not_backfilled(shop):
    db, _ = shop
    state = await setup(db)
    state['audit'] = [{'action': 'order_created', 'target': 'old-order', 'at': s.now()}]
    await db.resident_store.replace_one({'_id': s.KEY}, state)
    await n.drain(db)
    assert await db.rental_notifications.count_documents({}) == 0


@pytest.mark.asyncio
async def test_staging_never_sends_or_queues_a_later_real_push(shop, monkeypatch, transport):
    monkeypatch.setenv('ENVIRONMENT', 'staging')
    db, _ = shop
    await create(shop)
    await n.drain(db)
    assert await db.rental_notifications.count_documents({}) == 2
    assert await db.push_deliveries.count_documents({'status': 'suppressed_environment'}) == 2
    monkeypatch.setenv('ENVIRONMENT', 'production')
    await n.drain(db)
    assert not transport.called


@pytest.mark.asyncio
async def test_kill_switch_rechecked_for_already_queued_deliveries(shop, monkeypatch, transport):
    db, _ = shop
    await create(shop)
    monkeypatch.setenv('DISABLE_BACKGROUND_JOBS', 'true')
    await n.drain(db)
    assert not transport.called
    assert await db.push_deliveries.count_documents({'status': 'suppressed_environment'}) == 2


@pytest.mark.asyncio
async def test_ambiguous_provider_and_crashed_claim_are_not_retried(shop, transport):
    db, _ = shop
    await create(shop)
    transport.side_effect = TimeoutError('unknown acceptance')
    await db.push_deliveries.update_one({'user_id': 'admin-1'}, {'$set': {
        'status': 'sending', 'started_at': n.now() - timedelta(hours=2)}})
    await n.drain(db)
    await n.drain(db)
    assert transport.await_count == 1
    assert await db.push_deliveries.count_documents({'status': 'uncertain'}) == 2


@pytest.mark.asyncio
async def test_no_device_has_inbox_and_suspended_account_gets_no_push(shop, transport):
    db, _ = shop
    await create(shop)
    await db.app_users.update_one({'_id': 'resident-1'}, {'$unset': {'push_token': ''}})
    await db.app_users.update_one({'_id': 'admin-1'}, {'$set': {'status': 'suspended'}})
    await n.drain(db)
    assert not transport.called
    assert await db.rental_notifications.count_documents({}) == 2
    assert await db.push_deliveries.count_documents({'status': 'no_device'}) == 1
    assert await db.push_deliveries.count_documents({'status': 'ineligible'}) == 1


@pytest.mark.asyncio
async def test_quick_cancellation_preserves_history_and_only_pushes_latest_state(shop, transport):
    db, app = shop
    oid = await create(shop)
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        for _ in range(2):
            assert (await client.post(f'/store/orders/{oid}/cancel')).status_code == 200
    await n.drain(db)
    assert transport.await_count == 1
    assert transport.call_args.args[3]['status'] == 'cancelled'
    assert await db.rental_notifications.count_documents({}) == 3
    assert await db.push_deliveries.count_documents({'status': 'superseded'}) == 2


@pytest.mark.asyncio
async def test_failed_transaction_does_not_create_a_notification_event(shop):
    db, app = shop
    await setup(db)
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        result = await client.post('/store/orders', json={**checkout(), 'quote_hash': '0'*64,
                                                         'idempotency_key': 'bad-quote-key-12345'})
        assert result.status_code == 409
    await n.sync_inbox(db)
    assert await db.rental_notifications.count_documents({}) == 0
    assert not (await s.read_state())['audit']


@pytest.mark.asyncio
async def test_inbox_audience_is_owner_or_admin_never_another_resident(shop):
    db, _ = shop
    await create(shop)
    for uid, role, expected in [('resident-1', 'tenant', 1), ('other', 'tenant', 0), ('admin-1', 'admin', 1)]:
        audience = await notification_audience(db, {'_id': uid, 'role': role})
        assert await db.rental_notifications.count_documents(audience) == expected


@pytest.mark.asyncio
async def test_status_events_and_admin_count_follow_order_lifecycle(shop, monkeypatch):
    from rental import admin_nav_router as nav
    db, app = shop
    oid = await create(shop)
    monkeypatch.setattr(nav, 'get_db', lambda: db)
    monkeypatch.setattr(nav, 'auth_admin', AsyncMock(return_value={'_id': 'admin-1'}))
    assert (await nav.nav_summary(None))['store_orders'] == 1
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        for status in ['preparing', 'ready']:
            for _ in range(2):
                assert (await client.post(f'/admin/store/orders/{oid}/status', json={'status': status})).status_code == 200
        # Rejected delivery cannot generate a delivered notice.
        assert (await client.post(f'/admin/store/orders/{oid}/status', json={'status': 'delivered'})).status_code == 409
        await client.post(f'/admin/store/orders/{oid}/payment', json={'reference': 'test-receipt'})
        assert (await client.post(f'/admin/store/orders/{oid}/status', json={'status': 'delivered'})).status_code == 200
    assert await db.rental_notifications.count_documents({'user_id': 'resident-1'}) == 4
    assert (await nav.nav_summary(None))['store_orders'] == 0


@pytest.mark.asyncio
async def test_invalid_token_is_removed_only_if_it_matches_attempt(shop, transport):
    db, _ = shop
    await create(shop)
    transport.return_value = {'status': 'failed', 'error': 'DeviceNotRegistered'}
    await n.drain(db)
    assert 'push_token' not in await db.app_users.find_one({'_id': 'resident-1'})
    assert await db.push_deliveries.count_documents({'status': 'failed'}) == 2
