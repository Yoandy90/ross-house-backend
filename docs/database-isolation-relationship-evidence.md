# Offline relationship evidence: root binding

Migration remains unauthorized. These tools do not connect to a database,
read production documents, build database queries, or execute a migration.

## Required evidence

For each `relationship_closure` entry in a v2 evidence package:

1. `root_collection` is exactly `app_users` or `tenants`.
2. The same package includes that collection as an
   `explicit_root_id_allowlist`, including `root_ids` and `ownership_basis`.
3. Every relationship `root_ids` value is an exact, case-sensitive member of
   that declared root's allowlist. A nonempty subset is allowed. An ID present
   only in the other root does not qualify.
4. Relationship paths are literal dotted field names, such as `owner.root_id`.
   Operators, wildcard paths, array selectors, empty segments, and whitespace
   are rejected. These paths remain metadata; nothing evaluates them.

The envelope's inventory hash, contract hash, collection-content hash,
provenance, declared distinct preparer/approver, and validity window must all
pass the existing validator. All entries and cross-entry bindings are checked
before any output row is updated. Ordering root and relationship entries
differently does not change acceptance.

## Compatibility and workflow

The root-only package produced by `build_root_evidence_package` and the request
produced by `prepare_relationship_evidence_request` remain supported. A request
is a checklist, not approved relationship evidence.

To submit relationship evidence through the existing `--filter-evidence`
input of `python -m scripts.plan_database_isolation`, include the relevant
root entries with the relationship entries in one reviewed v2 package.
The combined content needs its own hashes, evidence ID, and approval review.
Do not reuse a root-only envelope unchanged or extend an expired approval.

Legacy relationship-only packages now fail closed. A prior report's
`offline_evidence_approved` label or an evidence ID string cannot replace
the root evidence. Root-only packages and unrelated partial evidence continue
to leave unsubmitted collections blocked.

## Limits and privacy

This validates consistency of submitted offline evidence, not the existence
of the referenced fields or the ownership of actual documents. It cannot
verify that a human identity or approval assertion is authentic. SHA-256
bindings are not digital signatures. Independent review of the underlying
ownership evidence is still required; migration needs separate authorization.

Keep real IDs and packages outside Git. Only synthetic fixtures belong in
tests. Root allowlists and relationship values are not copied into report rows,
and binding errors do not print rejected IDs.
