#!/usr/bin/env python3
"""Controlled source-to-proposal cycle against isolated Ross House staging."""
from __future__ import annotations

import json
import os
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
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
    tenant_token = ""
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

        status, simulated = request_json(
            base,
            f"/api/admin/staging-fixtures/renewal-delivery/{quote(marker)}",
            token,
            method="POST",
            body={"confirmation": "SIMULATE_SYNTHETIC_DELIVERY"},
        )
        simulated = require(
            status, simulated, "synthetic delivery simulation failed"
        )
        if (
            simulated.get("synthetic") is not True
            or simulated.get("proposal_id") != proposal_id
            or simulated.get("status") != "sent"
            or simulated.get("attempts") != 1
            or simulated.get("provider") != "staging-simulator"
        ):
            raise CycleFailure("synthetic delivery simulation mismatch")
        checks.append("delivery-simulated")

        status, sent_workflow = request_json(
            base,
            f"/api/admin/lease-renewals/{quote(proposal_id)}/workflow-status",
            token,
        )
        sent_workflow = require(
            status, sent_workflow, "sent workflow read model failed"
        )
        if (
            sent_workflow.get("proposal_id") != proposal_id
            or sent_workflow.get("read_only") is not True
            or (sent_workflow.get("delivery") or {}).get("status") != "sent"
            or (sent_workflow.get("delivery") or {}).get("attempts") != 1
            or sent_workflow.get("next_action") != "await_tenant_response"
        ):
            raise CycleFailure("simulated sent workflow state mismatch")
        checks.append("sent-state-verified")

        status, identity = request_json(
            base,
            f"/api/admin/staging-fixtures/renewal-tenant-session/{quote(marker)}",
            token,
            method="POST",
            body={"confirmation": "CREATE_SYNTHETIC_TENANT_SESSION"},
        )
        identity = require(
            status, identity, "synthetic tenant session creation failed"
        )
        tenant_token = str(identity.get("token") or "")
        if (
            identity.get("synthetic") is not True
            or identity.get("tenant_id") != str(created.get("tenant_id") or "")
            or identity.get("session_bound") is not True
            or not tenant_token
        ):
            raise CycleFailure("synthetic tenant identity mismatch")
        checks.append("tenant-session-created")

        status, offers = request_json(
            base, "/api/tenant/lease-renewals", tenant_token
        )
        offers = require(status, offers, "tenant renewal offer listing failed")
        matching_offers = [
            item
            for item in offers.get("offers", [])
            if isinstance(item, dict)
            and str(item.get("proposal_id") or "") == proposal_id
        ]
        if len(matching_offers) != 1:
            raise CycleFailure("expected exactly one synthetic tenant offer")
        offer = matching_offers[0]
        terms_digest = str(offer.get("terms_digest") or "").lower()
        if (
            len(terms_digest) != 64
            or any(ch not in "0123456789abcdef" for ch in terms_digest)
            or "accept" not in (offer.get("allowed_decisions") or [])
            or offer.get("response") is not None
        ):
            raise CycleFailure("synthetic tenant offer is invalid")
        checks.append("tenant-offer-verified")

        response_path = (
            f"/api/tenant/lease-renewals/{quote(proposal_id)}/respond"
        )
        response_body = {"decision": "accept", "terms_digest": terms_digest}
        status, responded = request_json(
            base,
            response_path,
            tenant_token,
            method="POST",
            body=response_body,
        )
        responded = require(status, responded, "tenant renewal response failed")
        response_view = responded.get("response") or {}
        if (
            responded.get("ok") is not True
            or responded.get("idempotent") is not False
            or response_view.get("decision") != "accept"
            or response_view.get("terms_digest") != terms_digest
        ):
            raise CycleFailure("synthetic tenant response mismatch")
        checks.append("tenant-response-recorded")

        status, repeated = request_json(
            base,
            response_path,
            tenant_token,
            method="POST",
            body=response_body,
        )
        repeated = require(
            status, repeated, "tenant renewal response replay failed"
        )
        if repeated.get("ok") is not True or repeated.get("idempotent") is not True:
            raise CycleFailure("tenant renewal response is not idempotent")
        checks.append("tenant-response-idempotent")

        status, accepted_workflow = request_json(
            base,
            f"/api/admin/lease-renewals/{quote(proposal_id)}/workflow-status",
            token,
        )
        accepted_workflow = require(
            status, accepted_workflow, "accepted workflow read model failed"
        )
        if (
            accepted_workflow.get("proposal_id") != proposal_id
            or accepted_workflow.get("read_only") is not True
            or (accepted_workflow.get("tenant_response") or {}).get("decision")
            != "accept"
            or accepted_workflow.get("next_action") != "generate_contract"
        ):
            raise CycleFailure("accepted workflow state mismatch")
        checks.append("accepted-state-verified")

        status, generated = request_json(
            base,
            f"/api/admin/lease-renewals/{quote(proposal_id)}/generate-contract",
            token,
            method="POST",
            body={},
        )
        generated = require(
            status, generated, "renewal contract generation failed"
        )
        contract_id_new = str(generated.get("contract_id") or "")
        expected_start = date.fromisoformat(
            str(created.get("lease_end_date") or "")
        ) + timedelta(days=1)
        try:
            generated_rent = Decimal(str(generated.get("rent_amount")))
            proposed_rent = Decimal(str(matches[0].get("proposed_rent")))
            generated_start = date.fromisoformat(str(generated.get("start_date") or ""))
            generated_end = date.fromisoformat(str(generated.get("end_date") or ""))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise CycleFailure("generated contract values are invalid") from exc
        if (
            generated.get("ok") is not True
            or generated.get("idempotent") is not False
            or not contract_id_new
            or generated.get("status") != "pending_signatures"
            or generated.get("proposal_id") != proposal_id
            or generated.get("prior_contract_id") != contract_id
            or generated.get("terms_digest") != terms_digest
            or generated.get("tenant_signed") is not False
            or generated.get("landlord_or_admin_signed") is not False
            or generated_start != expected_start
            or generated_end < generated_start
            or generated_rent != proposed_rent
        ):
            raise CycleFailure("generated renewal contract mismatch")
        checks.append("contract-generated-pending-signatures")

        status, repeated_contract = request_json(
            base,
            f"/api/admin/lease-renewals/{quote(proposal_id)}/generate-contract",
            token,
            method="POST",
            body={},
        )
        repeated_contract = require(
            status, repeated_contract, "renewal contract generation replay failed"
        )
        if (
            repeated_contract.get("ok") is not True
            or repeated_contract.get("idempotent") is not True
            or repeated_contract.get("contract_id") != contract_id_new
            or repeated_contract.get("status") != "pending_signatures"
            or repeated_contract.get("start_date") != generated.get("start_date")
            or repeated_contract.get("end_date") != generated.get("end_date")
            or Decimal(str(repeated_contract.get("rent_amount"))) != proposed_rent
        ):
            raise CycleFailure("renewal contract generation is not idempotent")
        checks.append("contract-generation-idempotent")

        status, contract_workflow = request_json(
            base,
            f"/api/admin/lease-renewals/{quote(proposal_id)}/workflow-status",
            token,
        )
        contract_workflow = require(
            status, contract_workflow, "generated contract workflow read model failed"
        )
        contract_view = contract_workflow.get("contract") or {}
        if (
            contract_workflow.get("proposal_id") != proposal_id
            or contract_workflow.get("read_only") is not True
            or contract_view.get("contract_id") != contract_id_new
            or contract_view.get("status") != "pending_signatures"
            or contract_view.get("start_date") != generated.get("start_date")
            or contract_view.get("end_date") != generated.get("end_date")
            or contract_view.get("tenant_signed") is not False
            or contract_view.get("landlord_or_admin_signed") is not False
            or contract_workflow.get("rollover") is not None
            or contract_workflow.get("next_action") != "collect_contract_signatures"
        ):
            raise CycleFailure("generated contract workflow state mismatch")
        checks.append("pending-signatures-state-verified")

        signature_body = {
            "document_id": contract_id_new,
            "document_type": "contract",
            "signature_data": "data:image/png;base64,iVBORw0KGgo=",
            "method": "touch",
            "device_info": "staging-renewal-cycle",
        }
        status, tenant_signed = request_json(
            base, "/api/signatures/sign", tenant_token,
            method="POST", body=signature_body,
        )
        tenant_signed = require(
            status, tenant_signed, "synthetic tenant signature failed"
        )
        if tenant_signed.get("success") is not True or not tenant_signed.get("signature_id"):
            raise CycleFailure("synthetic tenant signature mismatch")
        checks.append("tenant-signature-recorded")

        status, tenant_signed_workflow = request_json(
            base,
            f"/api/admin/lease-renewals/{quote(proposal_id)}/workflow-status",
            token,
        )
        tenant_signed_workflow = require(
            status, tenant_signed_workflow, "tenant-signed workflow read model failed"
        )
        tenant_signed_contract = tenant_signed_workflow.get("contract") or {}
        if (
            tenant_signed_contract.get("contract_id") != contract_id_new
            or tenant_signed_contract.get("status") != "pending_signatures"
            or tenant_signed_contract.get("tenant_signed") is not True
            or tenant_signed_contract.get("landlord_or_admin_signed") is not False
            or tenant_signed_workflow.get("rollover") is not None
            or tenant_signed_workflow.get("next_action") != "collect_contract_signatures"
        ):
            raise CycleFailure("tenant-signed workflow state mismatch")
        checks.append("tenant-signature-state-verified")

        status, admin_signed = request_json(
            base, "/api/signatures/sign", token,
            method="POST", body=signature_body,
        )
        admin_signed = require(
            status, admin_signed, "synthetic admin signature failed"
        )
        if admin_signed.get("success") is not True or not admin_signed.get("signature_id"):
            raise CycleFailure("synthetic admin signature mismatch")
        checks.append("admin-signature-recorded")

        status, fully_signed_workflow = request_json(
            base,
            f"/api/admin/lease-renewals/{quote(proposal_id)}/workflow-status",
            token,
        )
        fully_signed_workflow = require(
            status, fully_signed_workflow, "fully-signed workflow read model failed"
        )
        fully_signed_contract = fully_signed_workflow.get("contract") or {}
        if (
            fully_signed_contract.get("contract_id") != contract_id_new
            or fully_signed_contract.get("status") != "pending_activation"
            or fully_signed_contract.get("tenant_signed") is not True
            or fully_signed_contract.get("landlord_or_admin_signed") is not True
            or fully_signed_workflow.get("rollover") is not None
            or fully_signed_workflow.get("next_action")
            != "await_effective_date_or_rollover"
        ):
            raise CycleFailure("fully-signed workflow state mismatch")
        checks.append("pending-activation-state-verified")
    except Exception as exc:
        primary_error = exc
    finally:
        cleanup_error: Exception | None = None
        if tenant_token:
            try:
                tenant_logout_status, _ = request_json(
                    base,
                    "/api/auth/logout",
                    tenant_token,
                    method="POST",
                    body={},
                )
                if tenant_logout_status != 200:
                    raise CycleFailure("tenant session revocation failed")
                checks.append("tenant-session-revoked")
            except Exception as exc:
                cleanup_error = exc
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
                if cleanup_error is None:
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
