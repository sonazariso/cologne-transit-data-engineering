# Documentation and source-review workflow

The current project is a closed Data Engineering case study. Update only reviewed source-contract or offline documentation, prove claims with observed dated evidence, and keep old experimental stages labelled historical rather than silently rewriting their results.

1. Classify a change as code, schema, source contract, sample/retention, or historical narrative.
2. Record evidence date, environment, expected grain and scope; for SQL or provider behavior prefer read-only proof.
3. Keep run/audit/snapshot attribution and original source semantics; no accidental active-collector changes.
4. Run applicable offline checks and inspect final diff/secret scan before publication.
5. Push only via the repository owner's local GitHub Desktop workflow; rename repository *after* the updated commit is verified online.

See [`GITHUB_RELEASE_CHECKLIST.md`](GITHUB_RELEASE_CHECKLIST.md).
