# Synthetic evidence rehearsal

Run the complete offline evidence workflow without providing any real data:

```sh
python -m scripts.rehearse_database_evidence
```

The command generates its own synthetic inventory and temporary values, loads
the shipped contract, builds root evidence, relationship evidence, a partial
supplemental batch, and a complete contract package. Its checks include:

| Stage or check | Expected result |
| --- | --- |
| Roots | 2 synthetic collections validated |
| Relationships | 32 synthetic collections validated |
| Partial supplemental batch | 33 validated; remaining entries pending |
| Complete contract | 82 synthetic collections validated |
| Unclassified inventory entry | Ownership still unresolved |
| Unrelated root ID or other-company marker | Rejected |
| Self-approval or stale base hash | Rejected |
| Altered package or expired approval | Rejected |
| Incomplete final package | Rejected when completeness is required |
| Migration flag | Always false |

The fixed simulation time is **2030-01-15 12:00 UTC**. It is deliberately separate
from the real clock so results are repeatable and do not expire in CI. This does
not change the real-clock validation used by the actual evidence commands.

## Output and exit status

JSON on stdout contains check results, stage counts, the contract hash, and the
remaining categories of real work. It includes `synthetic_only: true`,
`real_evidence_validated: false`, `production_readiness_assessed: false`, and
`migration_authorized: false`. It does not include generated evidence packages,
record IDs, reviewer names, or evidence IDs. It cannot be used as a v2 evidence
package. The temporary synthetic inventory is removed automatically.

Exit code **0** means every expected outcome passed. Exit code **1** means a
check failed; unexpected programming or storage errors also produce a nonzero
exit. Merely raising an unrelated validation error does not satisfy a rejection
check. CI runs the command directly so these failures stop the gate.

Optionally save the metadata report to a new private path outside the repository:

```sh
python -m scripts.rehearse_database_evidence --output /private/rehearsal-report.json
```

The path is illustrative. Output uses the existing private no-overwrite writer;
its [filesystem limitations](database-isolation-private-output.md) still apply.
The runner has no arguments for real inventories, evidence packages, credentials,
database connections, or migration execution. It is not a deployment command.

## What follows a passing rehearsal

A pass verifies the software against these synthetic scenarios. It does not
prove ownership, validate real evidence, or approve production. Independently
reviewed ownership evidence, review of inventory outside the contract,
operational readiness checks, and separate migration authorization remain
required. Follow the [complete workflow](database-isolation-complete-workflow.md)
when those real inputs are available and their handling is authorized.
