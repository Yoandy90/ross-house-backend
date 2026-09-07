# Private evidence file output

`scripts.build_root_evidence_package` writes non-executable evidence offline.
This does not authorize a migration or authenticate ownership approvals.

The writer stages JSON in a randomly named file beside the requested output,
with owner-only permissions (`0600` on POSIX). It completes short writes and
syncs the file before publishing the final name through a hard link. Publication
refuses an existing destination, including a dangling symlink or a file created
by a competing writer. The output must remain outside the repository, including
when a parent path resolves through a symlink.

Handled write, sync, or publication errors remove the temporary file and do not
publish a partial package. An existing destination is never replaced. A process
kill, machine failure, or cleanup failure can leave a private temporary file;
directory metadata is not synced, so this is not a power-loss durability guarantee.

Use a trusted private directory on a filesystem that supports hard links.
Unsupported filesystems fail instead of falling back to a non-atomic copy.
POSIX mode bits do not substitute for Windows ACLs: protect the destination
directory using the operating system's access controls. This helper does not
defend against another user who can replace its parent directories while it runs.

Regression tests inject short writes, zero-progress writes, storage errors,
sync errors, publication errors, a competing destination, and a dangling
destination symlink using synthetic data only.
