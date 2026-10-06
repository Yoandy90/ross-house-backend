from rental import climate_intelligence, climate_notifications


def test_schedule_missed_alert_requires_automatic_write_worker(monkeypatch):
    monkeypatch.setenv("CLIMATE_ENABLED", "true")
    monkeypatch.setenv("CLIMATE_CONTROL_ENABLED", "true")
    monkeypatch.setenv("CLIMATE_MONITOR_ENABLED", "true")
    monkeypatch.delenv("CLIMATE_SCHEDULE_WORKER_ENABLED", raising=False)

    assert climate_intelligence.schedule_alerts_enabled() is False

    monkeypatch.setenv("CLIMATE_SCHEDULE_WORKER_ENABLED", "true")
    assert climate_intelligence.schedule_alerts_enabled() is True

    monkeypatch.setenv("CLIMATE_CONTROL_ENABLED", "false")
    assert climate_intelligence.schedule_alerts_enabled() is False


def test_legacy_monitor_flag_cannot_enable_schedule_missed_alert(monkeypatch):
    monkeypatch.setenv("CLIMATE_ENABLED", "true")
    monkeypatch.setenv("CLIMATE_MONITOR_ENABLED", "true")
    monkeypatch.delenv("CLIMATE_CONTROL_ENABLED", raising=False)
    monkeypatch.delenv("CLIMATE_SCHEDULE_WORKER_ENABLED", raising=False)

    assert climate_intelligence.schedule_alerts_enabled() is False


def test_basic_humidity_alerts_have_specific_bilingual_copy():
    for alert_type in ("humidity_low", "humidity_high"):
        assert alert_type in climate_notifications.COPY
        assert climate_notifications.COPY[alert_type]["es"][0]
        assert climate_notifications.COPY[alert_type]["en"][0]
