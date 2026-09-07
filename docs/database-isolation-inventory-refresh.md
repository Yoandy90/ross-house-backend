# Reviewing a refreshed offline inventory

Run this command on two already available local metadata inventories:

```sh
python -m scripts.compare_database_inventories previous.json current.json --output /private/review/inventory-comparison.json
```

It does not obtain an inventory, connect to a database, read evidence packages,
or create migration queries. Both snapshots must identify the historical source
`taxportal`; the destination remains exclusively `ross_house_production`.

The report lists added and removed collection names and names whose metadata
changed. It does not copy metadata values, document IDs, paths, or evidence
values into the report. Optional output uses the existing private atomic writer
outside the repository, refusing existing files. Stdout includes collection
names: keep the report private and do not commit real inventory reports.

Exit codes: 0 means identical inventory bytes; 2 means changed bytes requiring
evidence revalidation; 1 means invalid inputs or failed output publication.
Errors do not print submitted values or file paths.

Any changed byte invalidates the old inventory binding, including whitespace,
BOM, collection ordering, and snapshot timestamp changes. The semantic breakdown
helps explain the change; it never exempts a formatting-only change from this
rule. Do not rewrite old hashes or carry approvals forward automatically.
Rebuild the evidence against the new snapshot and independently review it.

An unchanged snapshot does not prove ownership, freshness, or migration readiness.
Validate the evidence contract, provenance, expiration and current scope
separately. New collections remain subject to classification and independent
ownership review; this comparison never marks any collection safe to migrate.
