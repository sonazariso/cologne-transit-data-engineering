# Contributing / Project Discipline

This is currently a controlled portfolio project. Changes should preserve the stage-gated review model.

Before implementing a new architectural or semantic decision:

1. identify the roadmap stage;
2. document evidence and assumptions;
3. define tests/validation;
4. obtain approval;
5. implement narrowly;
6. update status/decision documentation.

Avoid “cleanup” changes that silently alter semantics, especially around timing, cancellation, matching, scope, sampling, and NULL handling.
