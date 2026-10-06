"""Resolve notification recipients using explicit Ross House identity links only."""
from bson import ObjectId


def id_values(value):
    values = [str(value)]
    if ObjectId.is_valid(str(value)):
        values.append(ObjectId(str(value)))
    return values


async def push_recipient(db, user_id):
    """Return the app account, or an unlinked legacy tenant, with its real DB id."""
    query = {"_id": {"$in": id_values(user_id)}}
    app_user = await db.app_users.find_one(query)
    if app_user:
        return (None, None) if app_user.get("status") == "deleted" else ("app_users", app_user)
    tenant = await db.tenants.find_one(query)
    if not tenant:
        return None, None
    if tenant.get("app_user_id"):
        linked = await db.app_users.find_one({"_id": {"$in": id_values(tenant["app_user_id"])}})
        if linked and linked.get("status") != "deleted":
            return "app_users", linked
        return None, None
    return "tenants", tenant


async def notification_audience(db, user):
    """Include legacy tenant-addressed notices without email-based identity guesses."""
    user_id = str(user["_id"])
    ids = id_values(user_id)
    if user.get("role") == "tenant":
        cursor = db.tenants.find({"app_user_id": {"$in": id_values(user_id)}}, {"_id": 1})
        async for tenant in cursor:
            ids.extend(id_values(tenant["_id"]))
    clauses = [{"user_id": {"$in": ids}}, {"target": "all"}]
    if user.get("role"):
        clauses.append({"target": user["role"]})
    return {"$or": clauses}
