Fix the remaining offline SQLite migration-safety defect before repeated live-research collection begins.

Do not assign a new stage/S-number in the implementation or final report unless the user provides one.

Do not issue any MDD request.

Do not perform DNS or live-network activity.

Do not start a recurring collector.

Do not use, design, install, or configure PostgreSQL.

Do not activate Tiered50 production.

Do not create a production cutover timestamp.

Do not start Stage 15B.

Do not modify Power BI.

Preserve all accepted RecordedAtTime, timing-history, source-event identity, ServiceSection, backup, raw-retention, and Stage 15 behavior.

The current RecordedAtTime reporting correction is accepted and must not be redesigned.

## Fix the version-dependent row-count validation

The current offline migration utility incorrectly requires these four table counts to remain unchanged for every source schema:

- parsed_snapshots
- timing_observations
- recorded_at_time_evidence
- service_section_observations

That is correct for the schema-4 -> schema-5 reporting-view migration, but it is incorrect for a real schema-2 -> current migration because timing_observations and service_section_observations do not exist in schema 2 and are expected to be created/backfilled.

The defect has been independently reproduced using a database created by the real pre-S79/schema-2 implementation.

The current utility reports FAILED with a message equivalent to:

`required durable row counts changed or were unavailable`

even though the database has already been migrated to schema 5.

Make validation explicitly version-aware.

For schema 4 -> current:

- parsed_snapshots must remain unchanged;
- timing_observations must remain unchanged;
- recorded_at_time_evidence must remain unchanged;
- service_section_observations must remain unchanged.

For schema 2/3 -> current:

- parsed_snapshots must remain unchanged;
- original recorded_at_time_evidence rows must not be lost;
- timing_observations may be created/backfilled;
- service_section_observations may be created/backfilled;
- increases in those projection tables must reconcile with the backfill report rather than being treated as corruption;
- original parsed snapshot JSON must remain unchanged.

Do not treat a table that legitimately did not exist in the older schema as a count-preservation failure.

## Fix failure-state semantics

The migration utility must never report a generic `FAILED` result after silently leaving the main database in a changed state without making that state explicit and safely recoverable.

Use the validated pre-migration SQLite backup as the recovery boundary.

If any migration or post-migration validation fails after the primary database has been modified:

either:

1. restore the primary database atomically from the validated pre-migration safety backup and verify integrity before reporting failure;

or implement an equally safe explicit recovery mechanism.

The preferred behavior is restoration to the exact pre-migration durable state.

After a successful restore:

- report the migration as failed;
- report that the primary database was restored;
- retain enough sanitized diagnostic information to explain the failure;
- remove temporary migration artifacts only when safe.

If restoration itself fails:

- do not claim a clean rollback;
- retain the validated safety backup;
- report an explicit recovery-required/partial-migration state;
- include the safety-backup path;
- never delete the only known-good pre-migration copy.

A plain `FAILED` result must not hide a partially applied migration.

## Delay normal daily-backup refresh until validation succeeds

Currently the normal daily backup can be refreshed before all post-migration validation has finished.

Change the ordering.

The temporary pre-migration safety backup is created first.

Then perform migration/backfill.

Then perform all required validation:

- schema version;
- SQLite integrity/quick check;
- snapshot preservation;
- version-appropriate count/backfill reconciliation;
- RecordedAtTime evidence reconciliation;
- reporting-view validation;
- migration metadata validation.

Only after those checks pass should the normal current-day rotating backup be refreshed.

Only after the refreshed normal backup passes its own integrity check may the temporary migration safety backup be removed.

A failed migration must not overwrite the normal daily backup with an unvalidated intermediate database.

## Harden migration-completion metadata

Do not allow an older database to be considered fully migrated merely because `database_metadata.schema_version` was updated before required projection backfill/reconciliation completed.

Ensure migration completion is recoverable and explicit.

Use the smallest safe design.

Acceptable approaches include:

- updating the final schema version only after required migration/backfill work succeeds; or
- using an explicit durable migration-in-progress/completion marker and automatically resuming/reconciling incomplete work on reopen.

The required invariant is:

A database must not be considered migration-complete while required timing/ServiceSection backfill or reconciliation is incomplete.

Because backfill is already idempotent, preserve that property.

Do not introduce destructive migrations.

## Add an end-to-end utility regression using a genuine schema-2 contract

The existing SQLitePersistence schema-v2 unit regression is good but does not exercise the actual offline migration utility.

Add a regression that creates a genuine pre-S79/schema-2 LIVE_RESEARCH database shape and invokes the same migration orchestration used by the command-line utility.

Verify:

- source schema is 2;
- migration returns success rather than a false row-count failure;
- current schema version is reached;
- parsed snapshot count is unchanged;
- original snapshot JSON is unchanged;
- timing observations are backfilled;
- ServiceSection observations are backfilled where source evidence permits;
- RecordedAtTime evidence is preserved/reconciled;
- integrity check passes;
- backfill rerun is idempotent;
- no duplicate timing or RecordedAtTime evidence is created;
- validated daily backup is created only after successful post-migration validation;
- temporary safety backup is removed only after all success conditions pass.

## Keep the schema-4 -> schema-5 regression

Keep and extend the existing schema-4 migration test.

For schema 4 -> current, prove:

- all four durable row counts remain unchanged;
- old reporting views are replaced;
- exact RecordedAtTime leaf paths are reported;
- latest NULL keeps LatestSourceRecordedAtTimeSourcePath NULL;
- migration timestamp changes once;
- reopen does not modify that timestamp;
- normal backup is refreshed only after validation.

## Add failure-injection migration tests

Add deterministic offline tests for failures after the primary database has begun changing.

At minimum cover:

- backfill failure;
- RecordedAtTime reconciliation failure;
- reporting-view validation failure;
- post-migration integrity/verification failure;
- normal daily-backup failure.

For every failure injected before final success:

- verify the primary database is restored to the original pre-migration state where restoration is possible;
- verify the original schema version and original parsed snapshot data are preserved;
- verify no unvalidated daily backup replaces the previous validated daily backup;
- verify temporary safety-backup cleanup/retention follows the recovery policy;
- verify the returned/reported status accurately describes rollback or recovery-required state.

Do not suppress exceptions merely to make tests pass.

## Preserve RecordedAtTime semantics

Do not change the accepted reporting logic.

Continue to use:

`recorded_at_time_source_path`

for RecordedAtTime lineage.

Continue to guarantee:

- later NULL never erases historical non-NULL RecordedAtTime;
- LatestSourceRecordedAtTime may be NULL;
- LastObservedRecordedAtTime preserves the most recent source-provided non-NULL value;
- FirstObservedRecordedAtTime preserves the first source-provided non-NULL value;
- exact leaf source paths remain attached to first/last values;
- no ActualTime field is created.

## Preserve identity and ServiceSection semantics

Do not change `MDD_SOURCE_EVENT_V2`.

Optional fields remain lineage/disambiguation evidence and never alter the stable identity hash.

Keep explicit ambiguity handling.

Keep ServiceSection at independent source-occurrence grain.

Do not flatten multiple ServiceSections.

Do not reintroduce the original 100 ServiceSection fields as parser gaps.

Unknown future relevant extensions must still be surfaced as gaps.

## Preserve current storage policy

Keep:

- durable LIVE_RESEARCH SQLite outside the repository;
- indefinite parsed/history retention;
- indefinite EstimatedTime revision history;
- indefinite source-provided RecordedAtTime history;
- indefinite parsed ServiceSection/Situation/Attribute evidence;
- raw forensic retention <=24 hours;
- exactly three validated daily SQLite backup versions;
- production `live` blocked;
- PostgreSQL completely deferred.

## Verification

Run the complete repository test suite at least five consecutive times with ResourceWarning promoted to error.

Run the focused migration tests.

Run the Stage 14 validator.

Run the Stage 15 readiness validator.

Run compilation/static checks and whitespace/diff checks.

Perform zero network, MDD, DNS, PostgreSQL, Tiered50, production-cutover, scheduler, or Power BI activity.

Do not run another Heumarkt request in this correction.

## Final report

Report the following without assigning a new stage/S-number:

- migration utility behavior for schema 2 -> current;
- migration utility behavior for schema 4 -> current;
- version-aware row-count/backfill reconciliation;
- migration failure rollback/recovery semantics;
- daily-backup ordering;
- migration-completion metadata behavior;
- schema-2 end-to-end migration regression;
- failure-injection regression results;
- current full-suite test count;
- five consecutive run results;
- validator results;
- zero-network confirmation;
- confirmation PostgreSQL remains deferred;
- confirmation Stage 15B remains NOT STARTED.