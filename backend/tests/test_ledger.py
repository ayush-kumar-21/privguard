"""Tests for the hash-chained ledger triggers and verification."""
import pytest
from django.db import connection, IntegrityError


@pytest.mark.django_db(transaction=True)
class TestLedger:
    def test_verify_empty_ledger(self, account):
        """fn_verify_ledger on an empty ledger should return ok=true, entries=0."""
        with connection.cursor() as cur:
            cur.execute("SELECT * FROM fn_verify_ledger(%s)", [account.account_id])
            ok, entries, bad = cur.fetchone()
        assert ok is True
        assert entries == 0
        assert bad is None

    def test_ledger_readonly_blocks_update(self, account):
        """UPDATE on sys_budget_ledger must raise."""
        # First create a ledger entry via RESERVE
        with connection.cursor() as cur:
            from audit.models import SysQuery
            q = SysQuery.objects.create(
                principal=account.principal, db=account.db,
                sql_text="SELECT COUNT(*)", status="pending", rho_charged=0,
            )
            cur.execute(
                "CALL sp_reserve_budget(%s,%s,%s,%s,%s,%s,%s,%s)",
                [account.account_id, q.query_id, 0.01, 'laplace', 1.0, 1.0, 0.1, None],
            )
        # Attempt update
        with pytest.raises(Exception, match="append-only"):
            with connection.cursor() as cur:
                cur.execute(
                    "UPDATE sys_budget_ledger SET rho_delta = 999 WHERE account_id = %s",
                    [account.account_id],
                )

    def test_ledger_readonly_blocks_delete(self, account):
        """DELETE on sys_budget_ledger must raise."""
        with connection.cursor() as cur:
            from audit.models import SysQuery
            q = SysQuery.objects.create(
                principal=account.principal, db=account.db,
                sql_text="SELECT COUNT(*)", status="pending", rho_charged=0,
            )
            cur.execute(
                "CALL sp_reserve_budget(%s,%s,%s,%s,%s,%s,%s,%s)",
                [account.account_id, q.query_id, 0.01, 'laplace', 1.0, 1.0, 0.1, None],
            )
        with pytest.raises(Exception, match="append-only"):
            with connection.cursor() as cur:
                cur.execute(
                    "DELETE FROM sys_budget_ledger WHERE account_id = %s",
                    [account.account_id],
                )
