# History note

On 2026-10-07 the history of branch `ccr-87bfef72-ew9p6m` was rewritten (while the branch had four commits and no pull request) to remove an identifier that should not be associated with this repository. Commit SHAs created before that date are obsolete; anything that referenced them should use the current history.

Verification performed after the rewrite, on the rewritten branch and on a fresh mirror clone of the remote: no reachable commit contains the identifier in any file, path, commit message or author field; no tags exist; the remote has a single branch ref. The identifier is intentionally not recorded here.

Limit: rewriting a branch on the hosting service does not by itself delete the old commit objects there. At the time of the rewrite the host still served the pre-rewrite commits when asked for them by their full SHA. Removing them from the host requires a sensitive-data removal request to the host's support, or deleting and recreating the repository and pushing the rewritten history.
