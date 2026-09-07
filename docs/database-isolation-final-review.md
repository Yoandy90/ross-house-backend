# Final offline evidence review

Use already available local files; this command never collects production data:

```sh
python -m scripts.review_database_evidence inventory.json package.json --output /private/review/evidence-review.json
```

The command reuses the full shared scope, hash, provenance, relationship and
manual-review validators before calculating a summary. Invalid or expired
evidence fails with exit 1 and no success report. An output error also returns 1.
Existing outputs are never overwritten; optional files use the private atomic
writer outside the repository.

Exit 2 means validated evidence still needs review because the contract is
incomplete, inventory collections remain outside the contract, or evidence
expires within 24 hours (inclusive). The fixed 24-hour threshold is an offline
review policy, not an extension of validity. At expiration, validation fails.

Exit 0 means only that the offline contract checks passed without these blockers.
It does not independently verify ownership or reviewer identity, assess
production readiness, or authorize migration. The CLI always uses the current
UTC clock and offers no option to bypass expiry or other blockers.

The summary contains counts, timestamps, fixed reason codes and input hashes.
It excludes collection names, root IDs, evidence IDs, reviewer identities,
submitted paths, ownership statements and discriminator values. Hashes support
artifact matching; they are not digital signatures. Use the existing detailed
coverage report locally to identify individual pending collections.

Any changed inventory requires evidence bound to that new snapshot; see the
inventory refresh workflow. Never replace the old hash to bypass validation.
