# Offline evidence JSON input

All JSON file inputs to the offline isolation planner and evidence preparation
and package builders use one strict reader. It rejects repeated object keys at
any nesting level, including keys made identical by JSON Unicode escapes.
It also rejects NaN, Infinity, numeric overflow to infinity, malformed UTF-8,
trailing content, and top-level values other than objects.

Errors use fixed codes and do not include submitted keys or values. Invalid
inputs fail before package generation. Correct the source document and review
it again; do not automatically pick one of two conflicting values.

Valid UTF-8 files, including files with a BOM, retain existing behavior.
Inventory and contract hashes still cover the exact original bytes, including
whitespace and BOM. Distinct nested objects may use the same property name.

This boundary validates file syntax, not ownership or reviewer identity. The
existing scope, provenance, relationship, and manual-review validators still
apply. No database access or migration authorization is introduced.
