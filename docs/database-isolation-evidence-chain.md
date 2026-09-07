# Offline evidence chain verification

Keep the root, relationship and final packages. Verify all three together:

```sh
python -m scripts.verify_database_evidence_chain inventory.json root.json relationship.json complete.json --output /private/review/chain.json
```

The verifier validates every package against the same inventory and contract,
requires exact stage coverage of 2, 32 and 82 contract collections, and proves
that later stages preserved every earlier evidence entry byte-for-byte at the
canonical JSON level. It also requires ordered approvals and non-widening expiry.

Exit 1 means the chain is invalid and no report is published. Exit 2 means the
chain is valid but a reviewer identity was reused across stages or the inventory
still contains collections outside the contract. Exit 0 means only that the
offline chain checks pass without those review blockers.

The summary contains hashes and counts, never collection names, IDs, reviewer
identities, paths, ownership statements or discriminator values. Optional output
uses the private atomic no-overwrite writer outside the repository. Keep all real
packages and reports private and never commit them.

Identity comparison normalizes declared labels but does not authenticate people.
Use six independently controlled identities across the three prepare/approve
steps when feasible. Regardless of the result, ownership is not independently
verified, production readiness is not assessed and migration remains unauthorized.
