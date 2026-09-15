"""Notification registration, tenant linking and inbox isolation; no external sends."""
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from bson import ObjectId
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from mongomock_motor import AsyncMongoMockClient

import rental.auth_router as auth
import rental.shared as shared
import push_notification_service as push


@pytest.fixture
def state(monkeypatch):
    db = AsyncMongoMockClient()["notification_test"]
    monkeypatch.setattr(shared, "_db", db)
    monkeypatch.delenv("REQUIRE_SESSION_SID", raising=False)
    sender = AsyncMock(return_value=True)
    monkeypatch.setattr(push, "send_push_notification", sender)
    app = FastAPI()
    app.include_router(auth.router, prefix="/api")
    return db, sender, app


async def account(db, *, role="tenant", **fields):
    uid = ObjectId()
    await db.app_users.insert_one({"_id": uid, "role": role, "email": f"{uid}@example.test", **fields})
    token = shared.create_marketplace_token(str(uid), f"{uid}@example.test", role)
    return uid, {"Authorization": f"Bearer {token}"}


def client(app, headers):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=headers)


@pytest.mark.asyncio
@pytest.mark.parametrize("token", ["ExponentPushToken[test-device]", "ExpoPushToken[test-device]"])
async def test_registration_persists_real_object_id_and_repeated_registration(state, token):
    db, _, app = state
    uid, headers = await account(db)
    async with client(app, headers) as c:
        for _ in range(2):
            response = await c.post("/api/marketplace/register-push-token", json={"push_token": token})
            assert response.status_code == 200
    user = await db.app_users.find_one({"_id": uid})
    assert user["push_token"] == token
    assert user["push_platform"] == "ios"
    assert await db.app_users.count_documents({}) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("token", [None, {}, 1, "", "not-a-token", "ExpoPushToken["])
async def test_invalid_registration_cannot_claim_success(state, token):
    db, _, app = state
    uid, headers = await account(db)
    async with client(app, headers) as c:
        response = await c.post("/api/marketplace/register-push-token", json={"push_token": token})
    assert response.status_code == 400
    assert "push_token" not in await db.app_users.find_one({"_id": uid})


@pytest.mark.asyncio
async def test_no_matching_update_is_not_reported_as_registered(state, monkeypatch):
    db, _, app = state
    uid, headers = await account(db)
    monkeypatch.setattr(auth, "push_recipient", AsyncMock(return_value=("app_users", {"_id": ObjectId()})))
    async with client(app, headers) as c:
        response = await c.post("/api/marketplace/register-push-token", json={"push_token": "ExpoPushToken[test]"})
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_tenant_event_uses_linked_app_device(state):
    db, sender, _ = state
    uid, _ = await account(db, push_token="ExpoPushToken[current]")
    tid = ObjectId()
    await db.tenants.insert_one({"_id": tid, "app_user_id": str(uid), "push_token": "ExpoPushToken[stale]"})
    assert await shared.send_rental_push_to_user(str(tid), "Payment", "Approved", {"type": "manual_confirmation_approved"})
    assert sender.await_args.kwargs["expo_push_token"] == "ExpoPushToken[current]"
    # Manual confirmation's caller already writes its inbox record.
    assert await db.rental_notifications.count_documents({}) == 0


@pytest.mark.asyncio
async def test_maintenance_inbox_exists_without_push_permission(state):
    db, sender, app = state
    uid, headers = await account(db)
    tid = ObjectId()
    await db.tenants.insert_one({"_id": tid, "app_user_id": str(uid)})
    result = await shared.send_rental_push_to_user(str(tid), "Maintenance", "Assigned", {"type": "maintenance_update", "request_id": "ticket1"})
    assert result is False
    sender.assert_not_awaited()
    async with client(app, headers) as c:
        response = (await c.get("/api/marketplace/notifications")).json()
        assert response["unread"] == 1
        notice = response["notifications"][0]
        assert notice["data"]["request_id"] == "ticket1"
        assert (await c.post(f"/api/marketplace/notifications/{notice['id']}/read")).status_code == 200
        assert (await c.get("/api/marketplace/notifications")).json()["unread"] == 0


@pytest.mark.asyncio
async def test_linked_payment_notices_visible_but_other_users_and_admins_are_isolated(state):
    db, _, app = state
    uid, headers = await account(db)
    tid = ObjectId()
    await db.tenants.insert_one({"_id": tid, "app_user_id": str(uid)})
    rows = [
        {"user_id": str(tid)}, {"user_id": str(uid)},
        {"user_id": str(ObjectId())}, {"target": "admin"}, {"target": "all"},
    ]
    for row in rows:
        row.update({"_id": ObjectId(), "type": "manual_confirmation_approved", "created_at": datetime.now(timezone.utc), "read_by": []})
        await db.rental_notifications.insert_one(row)
    async with client(app, headers) as c:
        data = (await c.get("/api/marketplace/notifications?limit=1")).json()
        assert len(data["notifications"]) == 1
        assert data["unread"] == 3
        for foreign in rows[2:4]:
            assert (await c.post(f"/api/marketplace/notifications/{foreign['_id']}/read")).status_code == 404
        assert (await c.post("/api/marketplace/notifications/invalid/read")).status_code == 404
    assert (await db.rental_notifications.find_one({"_id": rows[2]["_id"]}))["read_by"] == []


@pytest.mark.asyncio
async def test_maintenance_admin_notice_persists_with_no_admin_devices(state):
    db, sender, app = state
    await shared.send_rental_push_to_admins("New request", "Created", {"type": "maintenance_new"})
    sender.assert_not_awaited()
    _, headers = await account(db, role="admin")
    async with client(app, headers) as c:
        assert (await c.get("/api/marketplace/notifications")).json()["unread"] == 1
    _, tenant_headers = await account(db)
    async with client(app, tenant_headers) as c:
        assert (await c.get("/api/marketplace/notifications")).json()["unread"] == 0


@pytest.mark.asyncio
async def test_provider_rejection_keeps_inbox_and_reports_false(state):
    db, sender, _ = state
    uid, _ = await account(db, push_token="ExpoPushToken[test]")
    sender.return_value = False
    assert not await shared.send_rental_push_to_user(str(uid), "Maintenance", "Update", {"type": "maintenance_update"})
    assert await db.rental_notifications.count_documents({"user_id": str(uid)}) == 1


@pytest.mark.asyncio
async def test_deleted_linked_account_does_not_receive_legacy_token_fallback(state):
    db, sender, _ = state
    uid, _ = await account(db, status="deleted", push_token="ExpoPushToken[deleted]")
    tid = ObjectId()
    await db.tenants.insert_one({"_id": tid, "app_user_id": str(uid), "push_token": "ExpoPushToken[old]"})
    assert not await shared.send_rental_push_to_user(str(tid), "Payment", "Approved")
    sender.assert_not_awaited()


@pytest.mark.asyncio
async def test_both_expo_token_formats_route_to_expo():
    service = push.PushNotificationService.__new__(push.PushNotificationService)
    service.firebase_service = None
    service._send_via_expo = AsyncMock(return_value={"success": True, "sent_count": 2, "failed_count": 0})
    tokens = ["ExponentPushToken[old-format]", "ExpoPushToken[new-format]"]
    result = await service.send_push_notification(tokens, "Test", "Test")
    assert result["sent_count"] == 2
    assert service._send_via_expo.await_args.args[0] == tokens

@pytest.mark.asyncio
async def test_device_registration_removes_previous_account_token(state):
    db,_,app=state
    old,_=await account(db,push_token='ExpoPushToken[shared-phone]')
    current,headers=await account(db)
    async with client(app,headers) as c:
        r=await c.post('/api/marketplace/register-push-token',json={'push_token':'ExpoPushToken[shared-phone]'})
        assert r.status_code==200
    assert not (await db.app_users.find_one({'_id':old})).get('push_token')
    assert (await db.app_users.find_one({'_id':current}))['push_token']=='ExpoPushToken[shared-phone]'
