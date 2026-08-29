# Managed unit topology serialization

This phase replaces the temporary fail-closed unit create/delete freeze with a serialized workflow.

Invariants:

- The property document owns a short-lived `mutation_lock` CAS claim.
- Canonical lease creation, property updates, and all unit writes share that claim.
- Unit creation requires an available, unclaimed property and rejects non-terminal whole-property leases.
- Unit deletion rejects occupied units and any unit referenced by contract history.
- Unit operational `rented` remains lifecycle-only.
- The background property projection sync skips live or malformed mutation claims.
- Lock release matches the exact token and is non-throwing after a committed write; stale claims expire automatically.
- No provider, payment, deployment, or production data behavior is touched.
