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
