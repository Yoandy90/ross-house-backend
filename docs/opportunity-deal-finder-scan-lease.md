# Deal Finder singleton scan lease

Manual searches, run-now batches and scheduled county sweeps share one
Mongo-backed renewable lease.
Acquisition is a single atomic find-and-update against a fixed app-settings
document. A second process or replica receives a stable busy result and does not
fetch properties, move the cursor, upsert leads or send duplicate alerts.

The lease has a monotonically increasing generation. Renewal and release require
the exact owner and generation, so a delayed worker cannot renew or release a
newer's lease. It expires after four hours by default and is renewed before every
page-cursor write. Losing renewal aborts the batch without moving that cursor.

Every manual worker renews the lease while fetching result pages and while
enriching properties. Both supported county-provider engines release the exact
owner and generation in a finally block on success or error; process crashes
recover by expiry. The previous non-atomic scan-record check is no longer used as
a concurrency lock.

The cron-config response reports authoritative `running`, `lease_expires_at` and
`lease_generation` fields without exposing the random owner secret. The existing
scan cursor, idempotent property upserts, strong-opportunity classification and
email alerts remain unchanged.

This mechanism coordinates runtime scans only. It does not authorize deployment,
access production during tests, change Railway, or certify external county data.
