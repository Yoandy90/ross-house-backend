# Shared validation at every evidence entry point

Source and manual-review policy is enforced by `apply_offline_filter_evidence`,
the common validator used by the isolation planner, evidence builders and the
coverage report. Passing a v2 package directly to the planner must not bypass
the stricter supplemental checks introduced by the complete-package workflow.

Source and manual `field_paths` must be literal dotted names. Source values are
case-sensitive and must exactly match `ross_house_rentals`, `Ross House Rentals`,
or `Ross House Rentals LLC`. A different actual marker requires a reviewed policy
change. These are accepted assertions, not observed database values.

Manual reviewer identifiers use the same syntax validation as provenance
identifiers. The review timestamp must be UTC, no later than the package's
approval, and at most 30 days older. A valid earlier review can carry forward in
a batch; it does not need to follow the latest combined preparation timestamp.
Boundary timestamps are inclusive. Every entry is checked before any report row
is marked as having approved offline evidence.

The complete-package builder delegates these rules instead of maintaining a
second implementation. Existing compliant v2 packages keep their hash bindings.
Legacy packages with generic source labels, operator paths, invalid reviewer
identifiers or invalid review dates now fail closed even through the planner.

Hashes do not authenticate reviewers or establish ownership. A successful
validation never authorizes migration, contacts a database, or evaluates paths
as queries. Only synthetic values are used in the regression tests.
