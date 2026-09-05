import pytest

from rental.lease_renewals_router import ensure_indexes


class Collection:
    def __init__(self, name, calls):
        self.name = name
        self.calls = calls

    async def create_index(self, keys, **options):
        self.calls.append((self.name, keys, options))
        return f"{self.name}_index"


class Database:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        return Collection(name, self.calls)


@pytest.mark.asyncio
async def test_renewal_readiness_creates_every_subsystem_index():
    db = Database()

    await ensure_indexes(db)

    collections = {name for name, _keys, _options in db.calls}
    assert collections == {
        "lease_renewal_proposals",
        "lease_renewal_notification_outbox",
        "lease_renewal_responses",
        "rental_contracts",
        "lease_renewal_rollovers",
        "lease_renewal_rollover_audit",
        "lease_renewal_rollover_recovery_audit",
    }
    assert any(
        name == "lease_renewal_rollover_audit"
        and options.get("unique") is True
        for name, _keys, options in db.calls
    )
    assert any(
        name == "lease_renewal_rollover_recovery_audit"
        and options.get("unique") is True
        for name, _keys, options in db.calls
    )


@pytest.mark.asyncio
async def test_renewal_readiness_propagates_index_failure():
    class BrokenCollection(Collection):
        async def create_index(self, keys, **options):
            if self.name == "lease_renewal_responses":
                raise RuntimeError("index unavailable")
            return await super().create_index(keys, **options)

    class BrokenDatabase(Database):
        def __getattr__(self, name):
            return BrokenCollection(name, self.calls)

    with pytest.raises(RuntimeError, match="index unavailable"):
        await ensure_indexes(BrokenDatabase())
