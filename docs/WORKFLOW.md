# Development and Approval Workflow

## Git repositories

Legacy reference repository:

`sonazariso/cologne-public-transport-intelligence`

New authoritative repository:

`sonazariso/cologne-transit-intelligence`

The legacy repository is not deleted, rewritten, or merged wholesale into the new repository.

## Working copy

Authoritative local working tree on macOS:

`/Users/admin/Documents/cologne-transit-intelligence/`

Windows accesses the same files via VMware Shared Folders:

`\\vmware-host\Shared Folders\cologne-transit-intelligence`

Do not create a second Windows clone unless this workflow is explicitly changed later.

## Branches

- `main` — approved milestones
- `develop` — integration branch for current approved-stage work
- short-lived stage/feature branches may be created from `develop` when useful

The exact branching model can be simplified later if it creates unnecessary overhead; Git history quality is more important than branch count.

## Stage workflow

1. Open a stage/branch discussion.
2. Document goal and current evidence.
3. Identify assumptions and unknowns.
4. Compare options and risks.
5. Define validation/tests before implementation.
6. Obtain explicit approval.
7. Implement only the approved scope.
8. Run validation and reconcile results.
9. Update authoritative documentation/status.
10. Commit and merge the approved milestone.

## Commit guidance

Prefer focused commits with intent-revealing messages, for example:

- `docs: define approved project scope`
- `docs: record source inventory findings`
- `sql: add approved GTFS staging schema`
- `collector: persist approved TRIAS timing fields`
- `powerbi: add approved system performance model`

## GitHub Desktop initial publication

After copying this foundation into `/Users/admin/Documents/cologne-transit-intelligence/`:

1. Add the local repository in GitHub Desktop.
2. Initialize Git if the folder is not already a Git repository.
3. Create the initial commit for the documentation foundation.
4. Publish as `sonazariso/cologne-transit-intelligence`.
5. Keep the repository visibility according to the user's preference; visibility is not decided by these documents.
6. Create/use `develop` after the foundation is safely published.

Do not commit secrets or legacy runtime data during publication.
