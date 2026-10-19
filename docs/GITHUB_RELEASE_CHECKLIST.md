# GitHub Desktop release and repository-rename runbook

**Chosen project title:** Cologne Transit Data Engineering
**Existing repository:** `sonazariso/cologne-transit-intelligence`
**Proposed new repository slug (after successful local commit + push):** `cologne-transit-data-engineering`

## Before the first push

1. Back up/clone your existing **local** Git repository before merging; compare this deliverable to its tracked branch instead of replacing `.git` or silently overwriting uncommitted changes.
2. Copy the released changed/new content into the **existing GitHub Desktop working folder** (not the `.git` folder). Review the file additions/removals and confirm paths that used to be local-only (raw admin evidence/logs/ZIPs) are not reintroduced.
3. Run `git status --short`, `git remote -v` (must resolve to the intended repository), a secret scan and the documented offline checks. GitHub Desktop must show only intended staged changes.
4. Confirm provider licensing/sharing and ensure no MDD key, SQL password, `.env`, production payload, host path, private username, backup, `*.mdf`, `*.ldf`, raw discovery record or unexpected generated ZIP is staged.
5. Commit as, for example, `docs: reposition Cologne Transit as a Data Engineering case study`; then **push via GitHub Desktop**. Verify the README, links and status on the real GitHub page.

## The moment to change the remote repository name

**Rename only after the new commit is confirmed visible on the old GitHub URL and all cross-links pass.** Then GitHub → repository **Settings → General → Repository name** → `cologne-transit-data-engineering`. GitHub normally redirects many old links, but it is not a substitute for updating remotes, workflows, badges and hardcoded absolute URLs.

After renaming, in the local repo:

```bash
git remote -v
git remote set-url origin https://github.com/sonazariso/cologne-transit-data-engineering.git
git remote -v
```

Update bookmarks, repository description/topics, external CV references and any repo-specific automation. **Do not rename** `CologneTransitIntelligence` database, SQL schema, LaunchAgent label or Python package solely to match the README; these identifiers preserve operational history and require independent migration decisions.

## External validations not performed by a source ZIP

- Existing GitHub target/repo access and default branch (GitHub connector returned 404 in this chat).
- A valid *new* full SQL Server backup after retention, verification and isolated test restore.
- Fresh post-retention ten-snapshot row/hash and FK reconciliation.
- Provider data/license review and final GitHub diff / remote visibility.

The ZIP is a **local change package**. It is not a Git commit and never pushes by itself.
