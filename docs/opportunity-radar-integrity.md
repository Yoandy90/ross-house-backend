# Opportunity radar integrity

The radar is a Ross House Rentals acquisition feature. It retains property-tax,
struck-off, TDCJ, jail, obituary, disconnected-phone and manual ICE signals.
Candidate records must declare Ross House Rentals as their source; imports from
Ross Tax, Ross Lending or unrelated companies fail before any row is inserted.

List responses remove complete A-numbers and passport numbers, returning only a
masked indicator. Literal admin searches are escaped and limited to 80 characters
before reaching MongoDB, preventing regex operators and unbounded patterns.

Creating a Deal Finder lead uses a deterministic, nonrevealing identifier and
MongoDB setOnInsert. Repeated clicks and concurrent retries return the same lead
instead of creating duplicates. Existing opportunity state is never reset.

Radar scan persistence compares the last-scan value read at the beginning. If an
admin or another scan changed the candidate meanwhile, the stale scan is rejected
instead of overwriting newer signals. These rules do not certify public-record
matches: TDCJ, jail and obituary matches remain leads requiring human verification.
