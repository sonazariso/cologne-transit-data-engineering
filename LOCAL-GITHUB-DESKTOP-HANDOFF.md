# GitHub Desktop handoff — do not overwrite `.git`

This folder is the updated **source tree**; it contains no `.git` directory and has **not** been pushed.

1. In GitHub Desktop, **clone/open the existing** `sonazariso/cologne-transit-intelligence` repository and make a backup of that working folder. Confirm any uncommitted changes are saved or committed before importing anything.
2. Compare the included sources with the clone. If the clone is based on the supplied 19-Oct v98 source, copy the contents of this folder **into the clone's working directory**, excluding this handoff file if preferred; do **not** copy, replace or delete its `.git` directory.
3. For a complete migration, explicitly delete obsolete tracked files/folders listed in the separate `FILES-TO-REMOVE.txt` included beside the release ZIP. Copying files alone does **not** delete old tracked files.
4. Inspect every addition/change/deletion in GitHub Desktop. Keep local-only and untracked personal files out of the commit; check `.gitignore`, `.env.example`, raw diagnostics and backup files. Verify your default branch and remote are correct.
5. Run the offline checks in `docs/REPRODUCIBILITY.md`, then **Commit** and **Push** manually through GitHub Desktop.
6. Confirm that the new title, 50-station guide and links render on the **existing GitHub URL**. Only at that point rename the GitHub repository to `cologne-transit-data-engineering` (Settings → General), update local origin and any CV/bookmarks/integrations. See `docs/GITHUB_RELEASE_CHECKLIST.md`.

If your local clone diverges from v98, don't blindly replace source files: stop and reconcile differences first. `docs/RELEASE_NOTES.md` describes the intentional no-runtime-code-change boundary.
