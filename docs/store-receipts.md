# Purchase receipt lifecycle

An order is created on confirmation; it is not proof of payment. Only the
authenticated admin recording an actually received payment changes it to paid.
That same atomic store write issues a sequential `RHT-YEAR-NNNNNN` receipt number,
stores its issue time/merchant/version on the immutable order snapshot and records
one `paid` notification intent. Inventory/rental ledgers are unaffected.

ES and EN PDFs are rendered after the commit and persisted once in
`store_receipt_files`, keyed by order/language/version. The file contains the
purchase snapshot, receipt number, payment date in UTC, reference, items, subtotal,
delivery, tax and paid total. Embedded fonts keep viewer rendering consistent.
If rendering/storage fails, payment remains successful; an authenticated download
retries and stores the PDF. Future downloads return those same stored bytes.

Customer: `GET /store/orders/{id}/receipt?language=es|en` (owner only).
Admin: `GET /admin/store/orders/{id}/receipt?language=es|en`.
Both return a private/no-store JSON PDF payload. Unpaid/cancelled purchases do not
have paid receipts. Legacy paid purchases receive one number on first download,
without another payment or a retrospective payment notice. Mobile sharing removes
its temporary cache file after the share sheet closes.

Payment notices use the existing durable store outbox. In-app history is available
in staging; outbound pushes remain suppressed by the existing environment policy.
There is no new email sender and no card/ACH charge path in this change.

The current staging catalog's seven fixture products were updated using a
revision-checked write (5 to 6). Only displayed names/descriptions, pickup label
and slot label changed; prices, stock, images and old purchases were preserved.
Historical fixture labels are cleaned for display without rewriting accounting
snapshots. Actual pickup location and offered hours remain admin configuration.

Validation: isolated receipt/payment/notification and mobile interaction tests;
sample PDF rendered and visually inspected. Do not interpret a passed test or
removal of fixture labels as evidence of live payment processing.
