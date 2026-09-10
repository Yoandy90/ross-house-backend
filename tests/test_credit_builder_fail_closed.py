"""Credit Builder must not fabricate enrollments, history, or bureau reports."""
import asyncio
from pathlib import Path

import pytest

from rental import credit_builder_router as credit


class _Request:
    headers = {}


def _run(coro):
    return asyncio.run(coro)


def _assert_unavailable(exc):
    assert exc.value.status_code == 503
    assert exc.value.detail == credit.CREDIT_BUILDER_UNAVAILABLE
    assert exc.value.detail["code"] == "credit_builder_unavailable"


def test_tenant_status_fails_closed_before_database_access(monkeypatch):
    async def auth(_request):
        return {"user_id": "tenant-123"}

    monkeypatch.setattr(credit, "get_current_user", auth)
    monkeypatch.setattr(
        credit, "get_db", lambda: pytest.fail("status must not read simulated enrollment data")
    )

    with pytest.raises(credit.HTTPException) as exc:
        _run(credit.get_rent_reporting_status(_Request()))
    _assert_unavailable(exc)


def test_tenant_enrollment_fails_closed_without_database_write(monkeypatch):
    async def auth(_request):
        return {"user_id": "tenant-123"}

    monkeypatch.setattr(credit, "get_current_user", auth)
    monkeypatch.setattr(
        credit, "get_db", lambda: pytest.fail("disabled enrollment must not access the database")
    )

    with pytest.raises(credit.HTTPException) as exc:
        _run(credit.enroll_in_rent_reporting(_Request(), credit.EnrollRequest()))
    _assert_unavailable(exc)


def test_admin_report_fails_closed_without_database_write(monkeypatch):
    async def auth(_request):
        return {"role": "admin"}

    monkeypatch.setattr(credit, "auth_admin", auth)
    monkeypatch.setattr(
        credit, "get_db", lambda: pytest.fail("disabled reporting must not access the database")
    )

    with pytest.raises(credit.HTTPException) as exc:
        _run(credit.admin_report_payment("507f1f77bcf86cd799439011", _Request()))
    _assert_unavailable(exc)


@pytest.mark.parametrize(
    "endpoint,args",
    [
        (credit.admin_list_enrollments, ()),
        (credit.admin_get_enrollment, ("507f1f77bcf86cd799439011",)),
        (credit.admin_update_enrollment, ("507f1f77bcf86cd799439011",)),
    ],
)
def test_admin_management_views_cannot_present_unverified_records(
    monkeypatch, endpoint, args
):
    async def auth(_request):
        return {"role": "admin"}

    monkeypatch.setattr(credit, "auth_admin", auth)
    monkeypatch.setattr(
        credit, "get_db", lambda: pytest.fail("disabled management must not access the database")
    )

    with pytest.raises(credit.HTTPException) as exc:
        _run(endpoint(*args, _Request()))
    _assert_unavailable(exc)


def test_runtime_source_contains_no_simulated_bureau_success():
    text = Path(credit.__file__).read_text(encoding="utf-8")

    assert "generate_payment_history" not in text
    assert "estimated_score_increase" not in text
    assert "confirmation_ids" not in text
    assert "MOCK-" not in text
