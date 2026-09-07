# Opportunity evidence integrity

PropertyRadar, obituary and pasted county-record matches remain part of the
Ross House Rentals Oportunidades module. They now use one deterministic evidence
boundary before a motivation signal is attached to a Deal Finder lead.

Person matching is order-independent and accent-safe, but requires both name
anchors as complete tokens. Business entities are rejected. Address matching
requires the same house number and at least one normalized street token. Every
accepted match records confidence and reasons and starts as `needs_review`;
matching never sends outreach or converts a lead automatically.

Evidence receives a stable identifier based on provider and provider record.
Mongo updates use that identifier in one conditional aggregation-pipeline update.
`$setUnion` preserves every signal and `$concatArrays` appends the evidence after
normalizing missing or legacy-null motivation objects. Concurrent scanners cannot
append the same fact twice or overwrite another source's facts.

Configured obituary URLs are restricted to HTTPS endpoints owned by the two
existing providers (Echovita and Morrison Funeral Directors). Credentials,
non-standard ports, local addresses and unrelated hosts are rejected before an
HTTP request. The existing sources, schedules, response fields and UI actions
remain available.

This change does not certify that a person is deceased or that a property is in
probate. Those are potential acquisition signals requiring human verification
against the linked public record. Tests use synthetic records only and perform
no provider, database, email, Railway or production calls.

## Review lifecycle

An authenticated administrator can move an evidence item between `needs_review`,
`confirmed` and `dismissed`. Each transition records the administrator ID, UTC
timestamp and a bounded note in a 50-entry audit history. Transitions are appended
and only the oldest overflow is trimmed; evidence identity and provider provenance
are never deleted by the review action.

Confirming or reopening evidence restores its associated motivation signals.
Dismissal removes a signal only through a conditional write proving there is no
other non-dismissed evidence for that signal. Legacy evidence without explicit
signal metadata is treated conservatively and prevents automatic removal. The
endpoint never sends communications or changes the lead's acquisition stage.
