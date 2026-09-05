"""Bounded autonomous worker for the inspection delivery outbox."""
from __future__ import annotations

import asyncio
import logging
import os
import socket
from collections import Counter
from typing import Any, Awaitable, Callable, Dict

from .inspection_delivery_router import claim_next, process_claimed, send_via_provider

logger = logging.getLogger(__name__)
DEFAULT_INTERVAL_SECONDS = 30
DEFAULT_BATCH_SIZE = 10
MAX_BATCH_SIZE = 50


def _true(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def worker_enabled() -> bool:
    """Require an explicit opt-in in addition to the server-wide job policy."""
    return _true("INSPECTION_DELIVERY_WORKER_ENABLED")


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


async def process_available(
    db,
    *,
    worker_id: str,
    batch_size: int = DEFAULT_BATCH_SIZE,
    sender: Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]] = send_via_provider,
) -> Dict[str, int]:
    """Drain at most one bounded batch so a bad queue cannot starve the API."""
    outcomes: Counter[str] = Counter()
    limit = max(1, min(int(batch_size), MAX_BATCH_SIZE))
    for _ in range(limit):
        intent = await claim_next(db, worker_id)
        if not intent:
            outcomes["idle"] += 1
            break
        outcomes[await process_claimed(db, intent, sender)] += 1
    return dict(outcomes)


async def inspection_delivery_loop(db) -> None:
    interval = _bounded_int("INSPECTION_DELIVERY_WORKER_INTERVAL_SECONDS", DEFAULT_INTERVAL_SECONDS, 10, 3600)
    batch_size = _bounded_int("INSPECTION_DELIVERY_WORKER_BATCH_SIZE", DEFAULT_BATCH_SIZE, 1, MAX_BATCH_SIZE)
    worker_id = f"inspection-delivery:{socket.gethostname()}:{os.getpid()}"
    logger.info("Inspection delivery worker started (interval=%ss, batch=%s)", interval, batch_size)
    while True:
        try:
            outcomes = await process_available(db, worker_id=worker_id, batch_size=batch_size)
            if outcomes != {"idle": 1}:
                logger.info("Inspection delivery pass complete: %s", outcomes)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Inspection delivery pass failed")
        await asyncio.sleep(interval)
