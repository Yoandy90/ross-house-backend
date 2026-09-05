import scripts.staging_renewal_source_cycle as cycle


BASE = "https://ross-house-staging.example.com"
TOKEN = "synthetic-token"


def success_responses():
    return {
        ("POST", "/api/admin/staging-fixtures/renewal-source"): (
            200, {"synthetic": True, "marker": "staging-renewal-" + "a" * 32, "contract_id": "c1"}
        ),
        ("GET", "/api/admin/staging-fixtures/renewal-source/staging-renewal-" + "a" * 32): (
            200, {"consistent": True, "present": {"property": True, "tenant": True, "contract": True}}
        ),
        ("GET", "/api/admin/lease-renewals/proposals"): (
            200, {"proposals": [{"_id": "p1", "lease_id": "c1"}]}
        ),
        ("GET", "/api/admin/lease-renewals/p1/workflow-status"): (
            200, {"proposal_id": "p1", "read_only": True}
        ),
        ("POST", "/api/admin/lease-renewals/p1/approve"): (
            200, {
                "ok": True,
                "status": "approved",
                "notification_queued": True,
                "queued_now": True,
            }
        ),
        (
            "GET",
            "/api/admin/lease-renewals/notification-outbox?status=pending&limit=200",
        ): (
            200,
            {
                "notifications": [
                    {
                        "proposal_id": "p1",
                        "tenant_id": "t1",
                        "status": "pending",
                        "attempts": 0,
                    }
                ],
                "total": 1,
            },
        ),
        ("DELETE", "/api/admin/staging-fixtures/renewal-lifecycle/staging-renewal-" + "a" * 32 + "?confirmation=DELETE_SYNTHETIC_RENEWAL"): (
            200, {"clean": True}
        ),
        ("POST", "/api/auth/logout"): (200, {"success": True}),
    }


def test_source_cycle_passes_and_cleans(monkeypatch):
    responses = success_responses()
    calls = []

    def fake(base, path, token, method="GET", body=None):
        calls.append((method, path))
        if (
            method == "GET"
            and path.endswith("staging-renewal-" + "a" * 32)
            and calls.count((method, path)) == 2
        ):
            return 200, {"consistent": False, "present": {"property": False, "tenant": False, "contract": False}}
        if (
            method == "GET"
            and path == "/api/admin/lease-renewals/p1/workflow-status"
            and calls.count((method, path)) == 2
        ):
            return 200, {
                "proposal_id": "p1",
                "read_only": True,
                "proposal": {"status": "approved"},
                "delivery": None,
                "next_action": "repair_notification_intent",
            }
        return responses[(method, path)]

    monkeypatch.setattr(cycle, "request_json", fake)
    checks = cycle.run_cycle(BASE, TOKEN)
    assert "proposal-generated" in checks
    assert "proposal-approved" in checks
    assert "notification-intent-safe" in checks
    assert "pending-delivery-verified" in checks
    assert "lifecycle-cleaned" in checks
    assert "no-source-residuals" in checks
    assert calls[-1] == ("POST", "/api/auth/logout")


def test_failure_after_creation_still_cleans_and_revokes(monkeypatch):
    responses = success_responses()
    responses[("GET", "/api/admin/lease-renewals/proposals")] = (200, {"proposals": []})
    calls = []
    inspection_count = 0

    def fake(base, path, token, method="GET", body=None):
        nonlocal inspection_count
        calls.append((method, path))
        if method == "GET" and "/renewal-source/" in path:
            inspection_count += 1
            if inspection_count == 2:
                return 200, {"present": {"property": False, "tenant": False, "contract": False}}
        return responses[(method, path)]

    monkeypatch.setattr(cycle, "request_json", fake)
    try:
        cycle.run_cycle(BASE, TOKEN)
        assert False, "cycle must fail"
    except cycle.CycleFailure as exc:
        assert "exactly one synthetic proposal" in str(exc)
    assert any(method == "DELETE" for method, _ in calls)
    assert calls[-1] == ("POST", "/api/auth/logout")


def test_refuses_missing_token():
    try:
        cycle.run_cycle(BASE, "")
        assert False, "missing token must fail"
    except cycle.CycleFailure as exc:
        assert "STAGING_ADMIN_TOKEN is required" in str(exc)


def test_logout_failure_fails_cycle_after_cleanup(monkeypatch):
    responses = success_responses()
    responses[("POST", "/api/auth/logout")] = (500, {"detail": "failed"})

    inspection_count = 0
    workflow_count = 0

    def fake(base, path, token, method="GET", body=None):
        nonlocal inspection_count, workflow_count
        if method == "GET" and "/renewal-source/" in path:
            inspection_count += 1
            if inspection_count == 2:
                return 200, {
                    "present": {
                        "property": False,
                        "tenant": False,
                        "contract": False,
                    }
                }
        if method == "GET" and path.endswith("/workflow-status"):
            workflow_count += 1
            if workflow_count == 2:
                return 200, {
                    "proposal_id": "p1",
                    "read_only": True,
                    "proposal": {"status": "approved"},
                    "delivery": {"status": "pending", "attempts": 0},
                    "next_action": "send_notification",
                }
        return responses[(method, path)]

    monkeypatch.setattr(cycle, "request_json", fake)
    try:
        cycle.run_cycle(BASE, TOKEN)
        assert False, "logout failure must fail the cycle"
    except cycle.CycleFailure as exc:
        assert "session revocation failed" in str(exc)
