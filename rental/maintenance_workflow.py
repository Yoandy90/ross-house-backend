"""Shared admin workflow policy for mutations and available UI actions."""

ALLOWED_STATUSES = {
    "pending", "reviewing", "assigned", "scheduled", "en_route", "in_progress",
    "waiting_parts", "completed", "resolved", "cancelled", "closed",
}
STATUS_TRANSITIONS = {
    "pending": {"reviewing", "assigned", "scheduled", "in_progress", "completed", "resolved", "cancelled"},
    "reviewing": {"pending", "assigned", "scheduled", "in_progress", "completed", "resolved", "cancelled"},
    "assigned": {"reviewing", "scheduled", "en_route", "in_progress", "waiting_parts", "completed", "resolved", "cancelled"},
    "scheduled": {"assigned", "en_route", "in_progress", "waiting_parts", "completed", "resolved", "cancelled"},
    "en_route": {"assigned", "scheduled", "in_progress", "waiting_parts", "completed", "resolved", "cancelled"},
    "in_progress": {"assigned", "scheduled", "en_route", "waiting_parts", "completed", "resolved", "cancelled"},
    "waiting_parts": {"assigned", "scheduled", "en_route", "in_progress", "cancelled"},
    "completed": {"in_progress", "resolved", "closed"},
    "resolved": {"in_progress", "completed", "closed"},
    "cancelled": {"pending", "reviewing"},
    "closed": {"in_progress"},
}

def canonical_status(value) -> str:
    raw = str(value or "pending").strip().lower()
    return "pending" if raw == "open" else raw


def available_statuses(value) -> list[str]:
    return sorted(STATUS_TRANSITIONS.get(canonical_status(value), set()))
