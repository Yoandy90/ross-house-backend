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


def test_history_separates_obligations_from_checkout_and_future_rent(monkeypatch):
    import asyncio
    from datetime import datetime, timezone
    from mongomock_motor import AsyncMongoMockClient
    from rental import tenant_invoices_router as router

    async def scenario():
        db = AsyncMongoMockClient()['invoice_history']
        current = datetime.now(timezone.utc).strftime('%Y-%m')
        future = '2099-10'
        await db.rental_payments.insert_many([
            {'tenant_id': 'tenant', 'period': current, 'status': 'pending',
             'amount': 1200, 'late_fee': 50, 'total_due': 1250},
            {'tenant_id': 'tenant', 'period': current, 'status': 'pending_checkout',
             'invoice_id': 'legacy-invoice', 'amount': 1200, 'total_paid': 1200},
            {'tenant_id': 'tenant', 'period': future, 'status': 'pending',
             'record_type': 'invoice', 'amount': 1200, 'charge_attempt': {'status': 'processing'}},
            {'tenant_id': 'tenant', 'period': current, 'status': 'partial',
             'amount': 500, 'total_paid': 200},
            {'tenant_id': 'tenant', 'period': current, 'status': 'cancelled', 'amount': 900},
        ])
        async def auth(_): return {'_id': 'tenant'}
        async def ids(_): return ['tenant']
        monkeypatch.setattr(router, 'get_db', lambda: db)
        monkeypatch.setattr(router, 'auth_marketplace', auth)
        monkeypatch.setattr(router, '_resolve_tenant_ids_for_user', ids)
        result = await router.tenant_invoices_history(None)
        assert len(result['items']) == 4
        assert result['summary']['total_pending'] == 1550
        assert result['summary']['pending_count'] == 2
        assert result['summary']['total_future'] == 1200
        assert result['summary']['future_count'] == 1
    asyncio.run(scenario())
