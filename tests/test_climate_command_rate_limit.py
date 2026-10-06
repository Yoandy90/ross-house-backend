import asyncio
import hashlib
from unittest.mock import AsyncMock

from rental import climate_router as routes

from pathlib import Path

SOURCE = (Path(__file__).resolve().parents[1] / "rental" / "climate_router.py").read_text(encoding="utf-8")


def test_physical_commands_have_persistent_actor_device_rate_limit():
    assert "check_rate_limit_persistent" in SOURCE
    assert '"climate-physical-command"' in SOURCE
    assert "max_requests=12" in SOURCE
    assert "window_seconds=60" in SOURCE
    assert "await _rate_limit_physical_command(binding, user)" in SOURCE
    # Core setpoint + feature command functions plus admin/tenant run-now routes.
    assert SOURCE.count("await _rate_limit_physical_command(binding, user)") >= 4


def test_physical_command_rate_limit_hashes_actor_and_device(monkeypatch):
    limiter = AsyncMock()
    monkeypatch.setattr(routes, "check_rate_limit_persistent", limiter)

    asyncio.run(routes._rate_limit_physical_command(
        {"_id": "thermostat-123"},
        {"id": "admin@example.com"},
    ))

    limiter.assert_awaited_once()
    args = limiter.await_args.args
    kwargs = limiter.await_args.kwargs
    assert args[0] == "climate-physical-command"
    expected = hashlib.sha256(
        b"admin@example.com:thermostat-123"
    ).hexdigest()
    assert args[1] == expected
    assert "admin@example.com" not in args[1]
    assert "thermostat-123" not in args[1]
    assert kwargs == {"max_requests": 12, "window_seconds": 60}
