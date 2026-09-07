# Complete offline evidence workflow

The contract covers 82 collections: 2 roots, 30 relationships, 37 source
discriminators, and 13 manual schema reviews. This module completes the final
50 submissions and reports all four groups together. Every result keeps
`migration_authorized: false`. No database is contacted and no query is built.

## Starting point

Use the existing inventory and contract with a valid root/relationship package
from [the relationship workflow](database-isolation-relationship-workflow.md).
Both root entries are required. A partial relationship package is allowed, but
missing relationship evidence remains pending in the unified report; add it
through the relationship workflow before completing the full contract.

The supplied contract's collection assignments must match the repository's
reviewed contract, and all 82 collections must appear in the inventory metadata.
Extra inventory collections are counted as outside this contract. Their ownership
is not inferred or approved. The broader isolation planner and ownership rules
still need to handle these collections and other conflicts separately.

## Prepare, fill, and build

Run from the repository root. `/private` below is a placeholder for a trusted,
access-controlled directory outside Git. Each output filename must be new.

```sh
python -m scripts.build_complete_evidence_package prepare \
  /private/inventory.json /private/relationships.json \
  --output /private/supplemental-submission.json
```

The template binds the inventory, contract, base package hash, and base evidence
ID. It only includes missing source/manual collection rows. It has no usable
approval until an independent review supplies the provenance fields and evidence.

Source evidence requires nonempty `field_paths` and `allowed_values`. Paths must
be literal dotted names. Values must exactly match one or more of:

- `ross_house_rentals`
- `Ross House Rentals`
- `Ross House Rentals LLC`

These are accepted submitted labels, not observed database values. This tool
does not establish that these labels exist or correctly discriminate actual
documents. Generic or other-company labels fail closed. A different actual
marker requires a separately reviewed policy change; do not relabel documents
or invent evidence to satisfy this list.

Manual evidence requires literal `field_paths`, a nonempty `ownership_basis`,
a valid declared `reviewer`, and a UTC `reviewed_at` ending in `Z`. A schema
review may precede preparation of the combined package, allowing valid reviews
to carry forward between batches. It cannot postdate the combined approval or
be more than 30 days older than that approval. Reviewer syntax is validated;
human identity and the underlying review are not authenticated.

The new combined provenance requires distinct preparer and approver identifiers.
Preparation cannot precede base approval, and expiry cannot exceed base expiry.
Every preserved entry and every new entry is revalidated. Previously submitted
rows cannot be replaced or duplicated by an extension. To correct an old entry,
restart from the relevant earlier reviewed package and obtain a new review.

```sh
python -m scripts.build_complete_evidence_package build \
  /private/inventory.json /private/relationships.json \
  --submission /private/supplemental-submission.json \
  --output /private/complete-evidence.json --require-complete
```

For batches, remove unanswered rows and omit `--require-complete`. At least one
new source/manual row must be provided. Use the resulting package as the base
of the next prepare/build cycle. `--require-complete` requires all 82 contract
collections, not merely the final 50. Source and schema evidence remain metadata.

## Inspect and hand off

```sh
python -m scripts.build_complete_evidence_package report \
  /private/inventory.json /private/complete-evidence.json
```

Report revalidates the package, context hashes, and current approval validity.
It prints group totals, validated counts, pending collection names, and a count
of inventory collections outside the contract. It omits root IDs, source values,
field paths, ownership text, and reviewer names. Build also prints base lineage
hashes. `report --output PATH` additionally saves the metadata report privately.
All commands accept `--filter-contract PATH` for the matching contract snapshot.

The private combined v2 file is compatible with the isolation planner's
`--filter-evidence` input. Output reuses the existing complete-file, no-overwrite
writer; see [filesystem limitations](database-isolation-private-output.md).

`contract_evidence_complete` means only that submitted evidence covers this
contract. It is not an authenticated ownership finding, production-readiness
approval, or permission to migrate. Hashes are not signatures. Actual ownership
verification, remaining inventory conflicts, operational gates, and explicit
migration authorization are separate work. The tests use synthetic data only.
