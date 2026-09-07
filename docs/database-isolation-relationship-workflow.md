# Offline relationship evidence workflow

This workflow completes the path from reviewed root evidence to a combined v2
package accepted by `scripts.plan_database_isolation --filter-evidence`.
It supports all 30 configured relationship collections and both `app_users`
and `tenants` roots. It never connects to a database, constructs an executable
query, or authorizes a migration.

## 1. Prepare the private submission

Use an existing metadata inventory, the matching filter contract, and a valid
root-only package produced by `scripts.build_root_evidence_package`. Root
approval must still be effective. Run from the repository root:

```sh
python -m scripts.build_relationship_evidence_package prepare \
  /private/inventory.json /private/roots.json \
  --output /private/relationship-submission.json
```

`/private` is a placeholder for an access-controlled directory outside the
repository. The output must not already exist. The template includes the
inventory and contract hashes, the request hash, the full root-package hash,
and its evidence ID. Do not replace these bindings manually to reuse a stale
submission: generate a fresh template when the inputs change.

Each collection has placeholders for:

- `root_collection`: exactly `app_users` or `tenants`.
- `relationship_paths`: literal dotted field paths, reviewed offline.
- `exact_root_ids`: a nonempty subset of that root's approved IDs.

Fill the five provenance fields with the combined review's preparer, independent
approver, preparation time, approval time, and expiry, using UTC timestamps
ending in `Z`. Preparation cannot precede the root approval; combined expiry
cannot exceed root expiry. The existing 30-day maximum approval window applies.
The template intentionally has no usable approval and cannot build unchanged.

To review in batches, remove unanswered collection rows. At least one complete
relationship row is required. Do not put real IDs in Git or into support logs.

## 2. Build and validate

```sh
python -m scripts.build_relationship_evidence_package build \
  /private/inventory.json /private/roots.json \
  --submission /private/relationship-submission.json \
  --output /private/combined-evidence.json --require-complete
```

`--require-complete` requires all 30 relationship collections. Omit it for a
partial package: unsubmitted collections remain pending. Duplicate, unknown,
external, or malformed collection rows fail before output publication. A root
from the other collection cannot supply an ID. No input package is mutated.

Both commands accept `--filter-contract PATH` when using a matching reviewed
contract. The output uses the same private atomic writer as root evidence:
no overwrite, complete-file publication, and POSIX owner-only permissions.
See [private output limitations](database-isolation-private-output.md) for
filesystem requirements, Windows ACLs, and interrupted-process behavior.

## 3. Review the result

Build prints a metadata-only JSON summary to stdout after successful output
publication: package and source hashes, evidence IDs, validated counts, and
pending collection names. It does not print submitted root IDs, field paths,
or reviewer names. The combined private file includes the two validated root
entries and supplied relationships, bound to the combined review's evidence ID.
It is directly usable as the planner's offline evidence input.

`relationship_evidence_complete` refers only to these 30 collections. It is not
a production-readiness result: source discriminators, manual schema reviews,
other inventory conflicts, and separate migration approval may remain pending.
Even a complete result has `migration_authorized: false`.

These tools check submitted consistency, not actual document ownership or the
authenticity of human approvals. Hashes are not digital signatures. Collection
paths are not evaluated. No database credentials, production calls, migration,
deployment, or real ownership data are needed to run the synthetic tests.
