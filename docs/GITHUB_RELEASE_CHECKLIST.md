# GitHub release checklist — Data Engineering completed pilot

Target named in local project docs: `sonazariso/cologne-transit-intelligence`. Confirm the actual remote with `git remote -v`; do not guess or overwrite unrelated repositories. The old `sonazariso/cologne-public-transport-intelligence` is historical and not the active target.

**Do not publish until the operator confirms GitHub visibility and sharing rights.** The currently linked GitHub connection did not expose the named target repository during this review, so automatic publication has not occurred.

## Recommended public repository content

- New concise portfolio README and source-to-SQL walkthrough.
- The source-controlled Python collector, SQL schema/stored procedures, relevant offline tests and reproducible non-secret environment template.
- An approved sanitized sample drawn from the exactly 10 retained snapshots, or a synthetic substitute; a snapshot ID manifest and matching row counts, never the entire SQL Server database.
- Screenshots of *actual* historically validated ingestion/evidence only; label date/era. Do not imply deployment or sustained collection from an isolated historical screenshot.
- Closed technical evidence and clear limitations; put older plans and storage experiments under a visibly historical category rather than advertising them as current instructions.

## Safety checks before push

- `git status --short` and `git remote -v` match the intended repo; review the exact commit file list.
- Scan commits as well as working tree for secrets, SQL logins, local paths, usernames, personal data, provider payloads, large SQLite, MDF/LDF/BAK, and any third-party data without redistribution permission.
- Ensure `.gitignore` excludes caches, runtime/log directories, database/backup files, environment secrets and source-data archives.
- Mark PostgreSQL Stage 10 and unfinished Tiered50 as design/blocked, not delivered services.
- Confirm source links in README resolve and tests can be run with documented environment without network or production credentials.
- The repository owner will commit and push manually after reviewing the complete diff, Git remote, visibility, and publication rights. No automated push or release is authorized.

## Current publication status

**NOT PUBLISHED BY THIS REVIEW.** The author/editor has supplied a documentation package; it is not proof of a remote Git commit or public release.

