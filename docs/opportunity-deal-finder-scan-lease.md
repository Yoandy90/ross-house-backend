# Deal Finder singleton scan lease

Manual and scheduled county sweeps share one Mongo-backed renewable lease.
Acquisition is a single atomic find-and-update against a fixed app-settings
document. A second process or replica receives a stable busy result and does not
fetch properties, move the cursor, upsert leads or send duplicate alerts.

The lease has a monotonically increasing generation. Renewal and release require
the exact owner and generation, so a delayed worker cannot renew or release a
newer's lease. It expires after four hours by default and is renewed before every
page-cursor write. Losing renewal aborts the batch without moving that cursor.

The manual endpoint acquires the same lease before returning success. The worker
releases it in a finally block on success or error; process crashes recover by
expiry. The existing scan cursor, idempotent property upserts, strong-opportunity
classification and email alerts remain unchanged.

This mechanism coordinates runtime scans only. It does not authorize deployment,
access production during tests, change Railway, or certify external county data.
