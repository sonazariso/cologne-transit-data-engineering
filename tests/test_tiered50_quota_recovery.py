from __future__ import annotations

import copy
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cologne_transit_collector.mssql_persistence import (  # noqa: E402
    MssqlConnectionSettings,
    MssqlPersistence,
)
from cologne_transit_collector.quota import (  # noqa: E402
    CONSERVATIVE_LIVE_ATTEMPT,
    HTTP_PROVIDER_ATTEMPT_CONFIRMED,
    PENDING_AUTHORIZATION,
    RELEASED_NO_NETWORK,
)


UTC = timezone.utc
NOVEMBER = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


class _QuotaCursor:
    def __init__(self, state: "_QuotaState", connection: "_QuotaConnection") -> None:
        self.state = state
        self.connection = connection
        self.result: list[dict[str, object]] = []
        self.rowcount = -1

    def execute(self, statement: str, params=()) -> None:  # type: ignore[no-untyped-def]
        sql = " ".join(str(statement).split())
        parameters = tuple(params or ())
        self.state.statements.append((sql, parameters))
        self.result = []
        self.rowcount = -1

        if "BEGIN TRANSACTION" in sql:
            self.connection.snapshot = copy.deepcopy(self.state.rows), copy.deepcopy(self.state.baselines)
            return
        if "SELECT SYSUTCDATETIME()" in sql:
            self.result = [{"AuthoritativeUtc": self.state.now}]
            return
        if "sp_getapplock" in sql:
            self.result = [{"LockResult": 0}]
            return
        if "SELECT PermitId, MonthKey, Amount, ReservedAtUtc, Status" in sql:
            cutoff = parameters[-1]
            self.result = [
                dict(row)
                for row in self.state.rows
                if row["Status"] == PENDING_AUTHORIZATION and row["ReservedAtUtc"] < cutoff
            ]
            return
        if "UPDATE ctl.MddMacQuotaPermit" in sql:
            status = parameters[0]
            finalized_at = parameters[1]
            reason = parameters[2]
            pending_status = parameters[3]
            cutoff = parameters[4]
            changed = 0
            for row in self.state.rows:
                if row["Status"] == pending_status and row["ReservedAtUtc"] < cutoff:
                    row["Status"] = status
                    row["FinalizedAtUtc"] = finalized_at
                    row["ReleasedAtUtc"] = None
                    row["FinalizationReason"] = reason
                    row["LiveClientInvocation"] = 1
                    changed += 1
            self.rowcount = changed
            return
        if "FROM ctl.MddMacQuotaResetContract" in sql:
            self.result = [dict(self.state.contract)]
            return
        if "EXEC ctl.uspEnsureMddMacQuotaPeriod" in sql:
            month = str(parameters[1])
            start = parameters[3]
            if self.state.fail_rollover:
                self.state.fail_rollover = False
                raise RuntimeError("simulated crash between recovery and new-period creation")
            if any(
                row["Status"] == PENDING_AUTHORIZATION and row["ReservedAtUtc"] < start
                for row in self.state.rows
            ):
                raise RuntimeError("a prior-period quota permit is still pending at rollover")
            if not any(str(row["MonthKey"]) == month for row in self.state.baselines):
                self.state.baselines.append(
                    {
                        "MonthKey": month,
                        "ProviderPeriodStartUtc": parameters[3],
                        "ProviderPeriodEndUtc": parameters[4],
                    }
                )
            return
        if "FROM ctl.MddMacSharedQuotaAccounting" in sql:
            at = parameters[0]
            self.result = [
                dict(row)
                for row in self.state.baselines
                if row["ProviderPeriodStartUtc"] <= at < row["ProviderPeriodEndUtc"]
            ]
            return
        if "FROM ctl.MddMacConservativeBootstrapAuthorization" in sql:
            self.result = []
            return
        if sql == "COMMIT TRANSACTION;":
            self.connection.snapshot = None
            return
        raise AssertionError(f"unexpected SQL in quota recovery fixture: {sql}")

    def fetchone(self):  # type: ignore[no-untyped-def]
        return self.result[0] if self.result else None

    def fetchall(self):  # type: ignore[no-untyped-def]
        return list(self.result)


class _QuotaConnection:
    def __init__(self, state: "_QuotaState") -> None:
        self.state = state
        self.cursor_value = _QuotaCursor(state, self)
        self.snapshot: tuple[list[dict[str, object]], list[dict[str, object]]] | None = None

    def cursor(self):  # type: ignore[no-untyped-def]
        return self.cursor_value

    def commit(self) -> None:
        self.snapshot = None

    def rollback(self) -> None:
        if self.snapshot is not None:
            self.state.rows, self.state.baselines = copy.deepcopy(self.snapshot[0]), copy.deepcopy(self.snapshot[1])
            self.snapshot = None

    def close(self) -> None:
        return None


class _QuotaState:
    def __init__(self, *, now: datetime = NOVEMBER, rows: list[dict[str, object]] | None = None) -> None:
        self.now = now
        self.rows = rows or []
        self.baselines: list[dict[str, object]] = []
        self.statements: list[tuple[str, tuple[object, ...]]] = []
        self.contract = {
            "ContractKey": "MDD-RESET-CONTRACT-VERIFIED-1",
            "ContractStatus": "ACTIVE",
            "ResetModel": "CALENDAR_MONTH_PROVIDER_TIMEZONE",
            "ResetTimezone": "Europe/Berlin",
            "EffectiveFromUtc": datetime(2026, 10, 31, 23, 0, tzinfo=UTC),
            "ActivationDelaySeconds": 1800,
            "ProviderQuotaCeiling": 250000,
            "CredentialExclusivityConfirmed": True,
            "ProviderResetBoundaryVerified": True,
            "CalendarMonthResetModelEstablished": True,
            "OperatorAssumptionApproved": False,
            "SourceEvidenceHash": "a" * 64,
        }
        self.fail_recovery = False
        self.fail_rollover = False
        self.fail_connections = 0

    def connect(self) -> _QuotaConnection:
        if self.fail_connections:
            self.fail_connections -= 1
            raise RuntimeError("simulated SQL outage")
        if self.fail_recovery:
            self.fail_recovery = False
            raise RuntimeError("simulated SQL recovery outage")
        return _QuotaConnection(self)


class QuotaRecoverySequenceTests(unittest.TestCase):
    def _persistence(self, state: _QuotaState) -> MssqlPersistence:
        return MssqlPersistence(
            settings=MssqlConnectionSettings(host="sql", user="runtime", password="not-used"),
            connection_factory=state.connect,
        )

    @staticmethod
    def _permit(
        permit_id: str,
        *,
        status: str = PENDING_AUTHORIZATION,
        reserved_at: datetime = NOVEMBER - timedelta(minutes=10),
        month: str = "2026-10",
        amount: int = 1,
    ) -> dict[str, object]:
        return {
            "PermitId": permit_id,
            "MonthKey": month,
            "Amount": amount,
            "ReservedAtUtc": reserved_at,
            "Status": status,
            "FinalizedAtUtc": None,
            "ReleasedAtUtc": None,
            "FinalizationReason": None,
            "LiveClientInvocation": 0,
        }

    def test_stale_previous_period_permits_recover_before_rollover_and_keep_month_identity(self) -> None:
        boundary_now = datetime(2026, 11, 1, 0, 1, tzinfo=UTC)
        old_previous_period = datetime(2026, 10, 31, 20, 0, tzinfo=UTC)
        recent_previous_period = datetime(2026, 10, 31, 22, 59, 50, tzinfo=UTC)
        state = _QuotaState(
            now=boundary_now,
            rows=[
                self._permit("stale-1", amount=2, reserved_at=old_previous_period),
                self._permit("stale-2", amount=3, reserved_at=old_previous_period - timedelta(minutes=1)),
                self._permit(
                    "recent",
                    reserved_at=recent_previous_period,
                ),
                self._permit("confirmed", status=HTTP_PROVIDER_ATTEMPT_CONFIRMED, reserved_at=old_previous_period),
                self._permit("uncertain", status=CONSERVATIVE_LIVE_ATTEMPT, reserved_at=old_previous_period),
                self._permit("released", status=RELEASED_NO_NETWORK, reserved_at=old_previous_period),
            ]
        )
        persistence = self._persistence(state)

        blocked = persistence.recover_stale_quota_permits(
            at_utc=datetime(2026, 10, 31, 23, 0, tzinfo=UTC),
            stale_after_seconds=7200,
        )

        self.assertEqual(blocked["recovered_permits"], 2)
        self.assertEqual(blocked["recovered_by_month"], {"2026-10": 2})
        self.assertEqual(blocked["automatic_rollover"]["status"], "BLOCKED_PENDING_PRIOR_PERIOD_PERMITS")
        self.assertEqual(next(row for row in state.rows if row["PermitId"] == "stale-1")["MonthKey"], "2026-10")
        self.assertEqual(next(row for row in state.rows if row["PermitId"] == "recent")["Status"], PENDING_AUTHORIZATION)
        self.assertEqual(next(row for row in state.rows if row["PermitId"] == "confirmed")["Status"], HTTP_PROVIDER_ATTEMPT_CONFIRMED)
        self.assertEqual(next(row for row in state.rows if row["PermitId"] == "uncertain")["Status"], CONSERVATIVE_LIVE_ATTEMPT)
        self.assertEqual(next(row for row in state.rows if row["PermitId"] == "released")["Status"], RELEASED_NO_NETWORK)

        state.now = datetime(2026, 11, 1, 1, 5, tzinfo=UTC)
        progressed = persistence.recover_stale_quota_permits(
            at_utc=datetime(2026, 10, 31, 23, 1, tzinfo=UTC),
            stale_after_seconds=7200,
        )
        self.assertEqual(progressed["status"], "PASS")
        self.assertEqual(progressed["automatic_rollover"]["status"], "APPLIED_OR_IDEMPOTENT")
        self.assertEqual(progressed["month_key"], "2026-11")
        self.assertEqual(len(state.baselines), 1)

    def test_no_pending_repeated_rollover_and_october_clock_skew_are_idempotent(self) -> None:
        state = _QuotaState()
        persistence = self._persistence(state)

        first = persistence.recover_stale_quota_permits(at_utc=datetime(2026, 10, 31, 23, 0, tzinfo=UTC))
        second = persistence.recover_stale_quota_permits(at_utc=datetime(2026, 10, 31, 23, 1, tzinfo=UTC))
        self.assertEqual(first["month_key"], "2026-11")
        self.assertEqual(first["recovered_permits"], 0)
        self.assertEqual(second["recovered_permits"], 0)
        self.assertEqual(len(state.baselines), 1)
        self.assertTrue(
            any("SYSUTCDATETIME()" in statement for statement, _params in state.statements),
            "recovery must use SQL Server time rather than the Mac caller clock",
        )

    def test_recovery_and_rollover_failures_are_retryable_without_losing_conservative_state(self) -> None:
        state = _QuotaState(rows=[self._permit("crashed-recovery")])
        state.fail_recovery = True
        persistence = self._persistence(state)
        with self.assertRaisesRegex(RuntimeError, "recovery outage"):
            persistence.recover_stale_quota_permits()
        self.assertEqual(state.rows[0]["Status"], PENDING_AUTHORIZATION)

        state.fail_rollover = True
        recovered_then_crashed = self._persistence(state)
        with self.assertRaisesRegex(RuntimeError, "new-period creation"):
            recovered_then_crashed.recover_stale_quota_permits()
        self.assertEqual(state.rows[0]["Status"], CONSERVATIVE_LIVE_ATTEMPT)
        self.assertEqual(state.baselines, [])

        retried = recovered_then_crashed.recover_stale_quota_permits()
        self.assertEqual(retried["status"], "PASS")
        self.assertEqual(len(state.baselines), 1)

    def test_sql_outage_then_recovery_and_december_january_progression(self) -> None:
        state = _QuotaState()
        state.fail_connections = 1
        persistence = self._persistence(state)
        with self.assertRaisesRegex(RuntimeError, "SQL outage"):
            persistence.recover_stale_quota_permits()

        self.assertEqual(persistence.recover_stale_quota_permits()["month_key"], "2026-11")
        state.now = datetime(2026, 12, 1, 12, 0, tzinfo=UTC)
        december = persistence.recover_stale_quota_permits()
        self.assertEqual(december["month_key"], "2026-12")
        state.now = datetime(2027, 1, 1, 12, 0, tzinfo=UTC)
        january = persistence.recover_stale_quota_permits()
        self.assertEqual(january["month_key"], "2027-01")
        self.assertEqual({row["MonthKey"] for row in state.baselines}, {"2026-11", "2026-12", "2027-01"})


if __name__ == "__main__":
    unittest.main()
