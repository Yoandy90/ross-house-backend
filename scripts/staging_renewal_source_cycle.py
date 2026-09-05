#!/usr/bin/env python3
"""Controlled source-to-proposal cycle against isolated Ross House staging."""
from __future__ import annotations

import json
import os
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from scripts.staging_renewal_smoke import SmokeFailure, validate_base_url


class CycleFailure(RuntimeError):
    pass


def request_json(base: str, path: str, token: str, method: str = "GET", body: Any = None):
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "ross-house-staging-source-cycle/1",
        "X-Ross-Environment": "staging-source-cycle",
    }
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")
    request = Request(base + path, headers=headers, data=data, method=method)
    try:
        with urlopen(request, timeout=15) as response:
            status = int(response.status)
            raw = response.read()
    except HTTPError as exc:
        status = int(exc.code)
        raw = exc.read()
    except (URLError, TimeoutError, OSError) as exc:
        raise CycleFailure(f"request failed: {type(exc).__name__}") from exc
    try:
        payload = json.loads(raw.decode("utf-8")) if raw else None
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CycleFailure("response was not JSON") from exc
    return status, payload


def require(status: int, payload: Any, detail: str) -> dict:
    if status != 200 or not isinstance(payload, dict):
        raise CycleFailure(detail)
    return payload


def run_cycle(raw_base: str, token: str) -> list[str]:
    try:
        base = validate_base_url(raw_base)
    except SmokeFailure as exc:
        raise CycleFailure(str(exc)) from exc
    if not token:
        raise CycleFailure("STAGING_ADMIN_TOKEN is required")

    marker = ""
    checks: list[str] = []
    primary_error: Exception | None = None
    try:
        status, created = request_json(
            base,
            "/api/admin/staging-fixtures/renewal-source",
            token,
            method="POST",
            body={"confirmation": "CREATE_SYNTHETIC_RENEWAL"},
        )
        created = require(status, created, "synthetic source creation failed")
        marker = str(created.get("marker") or "")
        contract_id = str(created.get("contract_id") or "")
        if not marker or not contract_id or created.get("synthetic") is not True:
            raise CycleFailure("synthetic source identity missing")
        checks.append("source-created")

        status, inspected = request_json(
            base,
            f"/api/admin/staging-fixtures/renewal-source/{quote(marker)}",
            token,
        )
        inspected = require(status, inspected, "synthetic source inspection failed")
        if inspected.get("consistent") is not True or not all(
            (inspected.get("present") or {}).values()
        ):
            raise CycleFailure("synthetic source is inconsistent")
        checks.append("source-consistent")

        status, proposals = request_json(
            base, "/api/admin/lease-renewals/proposals", token
        )
        proposals = require(status, proposals, "proposal generation failed")
        matches = [
            item for item in proposals.get("proposals", [])
            if isinstance(item, dict) and str(item.get("lease_id") or "") == contract_id
        ]
        if len(matches) != 1:
            raise CycleFailure("expected exactly one synthetic proposal")
        proposal_id = str(matches[0].get("_id") or matches[0].get("id") or "")
        if not proposal_id:
            raise CycleFailure("synthetic proposal id missing")
        checks.append("proposal-generated")

        status, workflow = request_json(
            base,
            f"/api/admin/lease-renewals/{quote(proposal_id)}/workflow-status",
            token,
        )
        workflow = require(status, workflow, "workflow read model failed")
        if workflow.get("proposal_id") != proposal_id or workflow.get("read_only") is not True:
            raise CycleFailure("workflow read model identity mismatch")
        checks.append("workflow-read-only")

        status, approved = request_json(
            base,
            f"/api/admin/lease-renewals/{quote(proposal_id)}/approve",
            token,
            method="POST",
            body={},
        )
        approved = require(status, approved, "synthetic proposal approval failed")
        if (
            approved.get("ok") is not True
            or approved.get("status") != "approved"
            or approved.get("notification_queued") is not True
            or approved.get("queued_now") is not True
        ):
            raise CycleFailure("synthetic proposal approval mismatch")
        checks.append("proposal-approved")

        status, outbox = request_json(
            base,
            "/api/admin/lease-renewals/notification-outbox?status=pending&limit=200",
            token,
        )
        outbox = require(status, outbox, "notification outbox inspection failed")
        matching_intents = [
            item
            for item in outbox.get("notifications", [])
            if isinstance(item, dict)
            and str(item.get("proposal_id") or "") == proposal_id
        ]
        if len(matching_intents) != 1:
            raise CycleFailure("expected exactly one synthetic notification intent")
        intent = matching_intents[0]
        if (
            intent.get("status") != "pending"
            or intent.get("attempts") != 0
            or any(
                field in intent
                for field in ("tenant_email", "tenant_phone", "tenant_name")
            )
        ):
            raise CycleFailure("synthetic notification intent is unsafe")
        checks.append("notification-intent-safe")

        status, approved_workflow = request_json(
            base,
            f"/api/admin/lease-renewals/{quote(proposal_id)}/workflow-status",
            token,
        )
        approved_workflow = require(
            status, approved_workflow, "approved workflow read model failed"
        )
        proposal_view = approved_workflow.get("proposal") or {}
        if (
            approved_workflow.get("proposal_id") != proposal_id
            or approved_workflow.get("read_only") is not True
            or proposal_view.get("status") != "approved"
            or (approved_workflow.get("delivery") or {}).get("status") != "pending"
            or (approved_workflow.get("delivery") or {}).get("attempts") != 0
            or approved_workflow.get("next_action") != "send_notification"
        ):
            raise CycleFailure("approved workflow state mismatch")
        checks.append("pending-delivery-verified")
    except Exception as exc:
        primary_error = exc
    finally:
        cleanup_error: Exception | None = None
        if marker:
            query = urlencode({"confirmation": "DELETE_SYNTHETIC_RENEWAL"})
            try:
                status, cleaned = request_json(
                    base,
                    f"/api/admin/staging-fixtures/renewal-lifecycle/{quote(marker)}?{query}",
                    token,
                    method="DELETE",
                )
                cleaned = require(status, cleaned, "synthetic lifecycle cleanup failed")
                if cleaned.get("clean") is not True:
                    raise CycleFailure("synthetic lifecycle cleanup was incomplete")
                checks.append("lifecycle-cleaned")
                status, after = request_json(
                    base,
                    f"/api/admin/staging-fixtures/renewal-source/{quote(marker)}",
                    token,
                )
                after = require(status, after, "post-cleanup inspection failed")
                if any((after.get("present") or {}).values()):
                    raise CycleFailure("synthetic source residuals remain")
                checks.append("no-source-residuals")
            except Exception as exc:
                cleanup_error = exc
        try:
            logout_status, _ = request_json(
                base, "/api/auth/logout", token, method="POST", body={}
            )
            if logout_status != 200:
                raise CycleFailure("session revocation failed")
            checks.append("session-revoked")
        except Exception as exc:
            if primary_error is None and cleanup_error is None:
                primary_error = exc
        if cleanup_error:
            raise cleanup_error
    if primary_error:
        raise primary_error
    return checks


def main() -> int:
    try:
        checks = run_cycle(
            os.environ.get("STAGING_BASE_URL", ""),
            os.environ.get("STAGING_ADMIN_TOKEN", ""),
        )
    except CycleFailure as exc:
        print(f"staging renewal source cycle: FAIL ({exc})", file=sys.stderr)
        return 1
    print("staging renewal source cycle: PASS (" + ", ".join(checks) + ")")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
