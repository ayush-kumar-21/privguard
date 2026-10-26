"""
Management command: reap leaked reservations.

Finds sys_query rows still 'pending' past the cutoff whose query_id has a
RESERVE and no settling row, ABORTs them via sp_settle_budget, and marks
the query as 'error'.

Usage:
    python manage.py reap_reservations                  # default 300s
    python manage.py reap_reservations --older-than 600
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import connection
from django.utils import timezone


class Command(BaseCommand):
    help = "Reap leaked budget reservations older than a threshold."

    def add_arguments(self, parser):
        parser.add_argument(
            "--older-than", type=int, default=300,
            help="Age in seconds beyond which a pending query is considered leaked (default: 300).",
        )

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(seconds=options["older_than"])

        with connection.cursor() as cur:
            # Find pending queries with a RESERVE but no COMMIT/ABORT
            cur.execute("""
                SELECT q.query_id, q.principal_id, l.account_id, l.rho_delta
                FROM sys_query q
                JOIN sys_budget_ledger l ON l.query_id = q.query_id AND l.op = 'RESERVE'
                WHERE q.status = 'pending'
                  AND q.submitted_at < %s
                  AND NOT EXISTS (
                      SELECT 1 FROM sys_budget_ledger s
                       WHERE s.query_id = q.query_id AND s.op IN ('COMMIT', 'ABORT')
                  )
            """, [cutoff])
            leaked = cur.fetchall()

        if not leaked:
            self.stdout.write(self.style.SUCCESS("No leaked reservations found."))
            return

        self.stdout.write(f"Found {len(leaked)} leaked reservation(s):")
        for query_id, principal_id, account_id, rho_delta in leaked:
            with connection.cursor() as cur:
                cur.execute("SELECT fn_refund_patients(%s)", [query_id])  # patients charged for it
                cur.execute(
                    "CALL sp_settle_budget(%s, %s, %s, %s, %s, %s, %s)",
                    [account_id, query_id, rho_delta, False, 'reaper', 0.0, None],
                )
                status = cur.fetchone()[-1]

            if status == 'already_settled':
                self.stdout.write(f"  query {query_id}: already settled (race)")
            else:
                # Mark query as error
                with connection.cursor() as cur:
                    cur.execute(
                        "UPDATE sys_query SET status = 'error' WHERE query_id = %s",
                        [query_id],
                    )
                self.stdout.write(f"  query {query_id}: ABORTed, rho={rho_delta:.6f}")

        self.stdout.write(self.style.SUCCESS(f"Reaped {len(leaked)} reservation(s)."))
