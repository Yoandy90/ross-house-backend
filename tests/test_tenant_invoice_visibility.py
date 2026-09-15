from rental.tenant_invoices_router import _is_unissued_future_rent


def test_untouched_future_rent_is_not_counted_as_pending_debt():
    assert _is_unissued_future_rent(
        {"status": "pending", "period": "2026-10", "auto_generated": True},
        "2026-09",
    )


def test_current_due_paid_and_attempted_future_rent_remain_visible():
    assert not _is_unissued_future_rent(
        {"status": "pending", "period": "2026-09"}, "2026-09"
    )
    assert not _is_unissued_future_rent(
        {"status": "completed", "period": "2026-10"}, "2026-09"
    )
    assert not _is_unissued_future_rent(
        {"status": "pending", "period": "2026-10",
         "charge_attempt": {"status": "processing"}},
        "2026-09",
    )
