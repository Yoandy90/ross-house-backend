# Declared reviewer identities in offline evidence

The v2 evidence validator compares preparer and approver identifiers after
Unicode NFKC normalization, case folding, and collapsing internal whitespace.
Equivalent spellings cannot satisfy the requirement for distinct reviewers.
This includes composed/decomposed accents and full-width character variants.

Both identifiers must be nonempty strings without surrounding whitespace,
Unicode control/format characters, wildcard characters, or colons. Wildcards
and colons are also rejected after normalization. Colons are reserved by the
existing evidence-ID serialization and cannot be part of reviewer identifiers.
Invalid identifiers produce field-specific errors without echoing their values.

Normalization is used only for comparison. Submitted provenance is not rewritten;
the evidence ID remains bound to the original strings. Existing valid packages
with distinct reviewer identifiers retain their IDs. Previously accepted
ambiguous identifiers now fail closed and require a corrected, reviewed package.
The root package builder uses this same validation before returning its output.

These checks establish consistency of declared identifiers only. They cannot
verify human identity, prevent one person from using unrelated aliases, or detect
every visually similar character across writing systems. Evidence hashes are
not signatures. Independent ownership review and separate migration authorization
remain required; no database access or migration is performed by these checks.
