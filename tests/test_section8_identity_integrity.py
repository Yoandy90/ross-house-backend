from fastapi import FastAPI

from rental.auth_metrics import router as security_router
from rental.tenant_router import router as historical_tenant_router


def test_secure_section8_routes_are_first_runtime_match():
    app = FastAPI()
    app.include_router(security_router, prefix="/api")
    app.include_router(historical_tenant_router, prefix="/api")

    post_matches = [r for r in app.routes
                    if getattr(r, "path", None) == "/api/tenant/section8/declare"
                    and "POST" in getattr(r, "methods", set())]
    get_matches = [r for r in app.routes
                   if getattr(r, "path", None) == "/api/tenant/section8/status"
                   and "GET" in getattr(r, "methods", set())]

    assert len(post_matches) == 2
    assert post_matches[0].name == "secure_tenant_declare_section8"
    assert post_matches[1].name == "tenant_declare_section8"
    assert len(get_matches) == 2
    assert get_matches[0].name == "secure_tenant_section8_status"
    assert get_matches[1].name == "tenant_section8_status"


def test_section8_tenant_write_uses_exact_resolved_id_only():
    source = open("rental/section8_security_router.py", encoding="utf-8").read()
    assert "resolve_authenticated_tenant(user)" in source
    assert 'db.tenants.update_one({"_id": tenant["_id"]}' in source
    assert '"email": {"$regex"' not in source
    assert '"phone": {"$regex"' not in source
    assert "section8_tenant_concurrent_missing" in source


def test_section8_pretenant_write_remains_self_owned():
    source = open("rental/section8_security_router.py", encoding="utf-8").read()
    assert "user_filter = _user_filter(user)" in source
    assert "db.app_users.update_one(user_filter" in source
    assert "section8_app_user_missing" in source
    assert "email" not in source.split("def _user_filter", 1)[1].split("def _bounded_text", 1)[0]
