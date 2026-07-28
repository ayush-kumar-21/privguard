"""
Management command: verify all budget accounts against the ledger.

Reads the v_ledger_reconciliation view (which calls fn_reconcile_account +
fn_verify_ledger for every account) and exits non-zero if any drift is
detected or any hash chain is broken.

Usage:
    python manage.py verify_budgets           # print table
    python manage.py verify_budgets --strict   # exit 1 on any drift
"""
import sys

from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = "Verify budget accounts against the ledger (reconciliation + hash chain)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--strict", action="store_true",
            help="Exit with code 1 if any drift > 1e-9 or any chain is broken.",
        )

    def handle(self, *args, **options):
        with connection.cursor() as cur:
            cur.execute("SELECT * FROM v_ledger_reconciliation")
            columns = [col.name for col in cur.description]
            rows = cur.fetchall()

        if not rows:
            self.stdout.write(self.style.WARNING("No budget accounts found."))
            return

        # Print table header
        header = " | ".join(f"{c:>20s}" for c in columns)
        self.stdout.write(header)
        self.stdout.write("-" * len(header))

        has_errors = False
        for row in rows:
            row_dict = dict(zip(columns, row))
            line = " | ".join(f"{str(v):>20s}" for v in row)
            drift = row_dict.get("drift", 0) or 0
            chain_ok = row_dict.get("chain_ok", True)

            if drift > 1e-9 or not chain_ok:
                self.stdout.write(self.style.ERROR(line))
                has_errors = True
            else:
                self.stdout.write(self.style.SUCCESS(line))

        if has_errors and options["strict"]:
            self.stderr.write(self.style.ERROR(
                "FAILED: drift detected or chain broken. See rows above."
            ))
            sys.exit(1)
        elif not has_errors:
            self.stdout.write(self.style.SUCCESS("\nAll accounts verified OK."))
