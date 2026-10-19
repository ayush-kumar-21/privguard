"""
Replay a realistic analyst workload so the demo has a story to tell.

`seed_demo` builds the structural data — catalog, policies, roles, principals,
patients and visits. It leaves the *behavioural* tables empty, so a freshly
seeded instance shows a budget burn-down that is a flat line at zero, a
cache-hit rate of 0%, and a one-row audit log. Every screen is correct and
every screen looks broken.

This command fixes that by running queries through `run_private_query` — the
same entry point the API uses. Nothing here writes a fabricated row: the noise
is really sampled, the budget is really reserved and settled through the
PL/pgSQL procedures, and the ledger hashes really chain. The demo is a
recording of the system working, not a mock of it.

    python manage.py seed_activity            # skip if history already exists
    python manage.py seed_activity --force    # replay anyway
    python manage.py seed_activity --days 30  # spread over a longer window

Timestamps: `sys_query.submitted_at` is backdated afterwards so the burn-down
has a real time axis. The ledger's `logged_at` is deliberately left alone --
`sys_budget_ledger` carries an append-only trigger, and rewriting history in an
append-only audit structure to make a chart look nicer is exactly the thing the
ledger exists to prevent.
"""
from __future__ import annotations

import random
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from accounts.models import Principal
from audit.models import SysQuery
from budget.models import BudgetAccount
from catalog.models import SysDatabase
from privacy.service import run_private_query
from privacy.sql import DPQueryError

DB_NAME = "safehealth"

# A realistic analyst session: broad questions first, then narrowing filters.
# Repeats are intentional and load-bearing -- re-asking a question must return
# the memoised answer at rho = 0, which is what defeats repeat-and-average.
ALICE_WORKLOAD = [
    ("SELECT COUNT(*) FROM visits", 0.05),
    ("SELECT admission_type, COUNT(*) FROM visits GROUP BY admission_type", 0.10),
    ("SELECT COUNT(*) FROM visits WHERE admission_type = 'EMERGENCY'", 0.05),
    ("SELECT AVG(total_charge) FROM visits", 0.08),
    ("SELECT dept_id, COUNT(*) FROM visits GROUP BY dept_id", 0.10),
    # Repeat of query 1 -> cache hit, rho 0.
    ("SELECT COUNT(*) FROM visits", 0.05),
    ("SELECT SUM(total_charge) FROM visits WHERE admission_type = 'ELECTIVE'", 0.08),
    ("SELECT COUNT(*) FROM visits WHERE total_charge > 2000", 0.05),
    ("SELECT AVG(total_charge) FROM visits WHERE admission_type = 'URGENT'", 0.08),
    # Repeat of the grouped query -> cache hit.
    ("SELECT admission_type, COUNT(*) FROM visits GROUP BY admission_type", 0.10),
    ("SELECT COUNT(*) FROM visits WHERE dept_id = 3", 0.05),
    ("SELECT SUM(total_charge) FROM visits", 0.10),
    ("SELECT COUNT(*) FROM visits WHERE total_charge <= 500", 0.05),
    ("SELECT dept_id, SUM(total_charge) FROM visits GROUP BY dept_id", 0.12),
    # A third ask of the very first question -> still cached, still free.
    ("SELECT COUNT(*) FROM visits", 0.05),
    ("SELECT COUNT(*) FROM visits WHERE admission_type != 'EMERGENCY'", 0.05),
    ("SELECT AVG(total_charge) FROM visits WHERE dept_id = 2", 0.08),
    ("SELECT COUNT(*) FROM visits WHERE total_charge >= 1500", 0.05),
]

# Frank's account is advertised as nearly exhausted (rho_total 0.20). Spend it
# down for real, then let the next query be refused -- the rejection is the
# demonstration, so it must be genuine rather than a hand-written row.
FRANK_WORKLOAD = [
    ("SELECT COUNT(*) FROM visits", 0.06),
    ("SELECT COUNT(*) FROM visits WHERE admission_type = 'URGENT'", 0.06),
    ("SELECT admission_type, COUNT(*) FROM visits GROUP BY admission_type", 0.06),
    # Budget is now ~0.18/0.20. This one cannot be afforded and is refused.
    ("SELECT SUM(total_charge) FROM visits", 0.10),
]

ERIN_WORKLOAD = [
    ("SELECT COUNT(*) FROM visits", 0.05),
    ("SELECT admission_type, COUNT(*) FROM visits GROUP BY admission_type", 0.08),
]

WORKLOADS = [("alice", ALICE_WORKLOAD), ("frank", FRANK_WORKLOAD), ("erin", ERIN_WORKLOAD)]

# Below this many queries the instance is considered "fresh" and worth seeding.
ALREADY_SEEDED_THRESHOLD = 10


class Command(BaseCommand):
    help = "Replay a realistic DP query workload so the demo screens have data."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Replay even if a query history already exists.",
        )
        parser.add_argument(
            "--days",
            type=int,
            default=14,
            help="Spread the backdated history over this many days (default 14).",
        )
        parser.add_argument(
            "--seed", type=int, default=7, help="RNG seed for timestamp jitter."
        )

    def handle(self, *args, **opts):
        # The seeder is not an adversary timing the API, so skip the response-time
        # floor (it would add ~150 ms to every replayed query).
        settings.PRIVGUARD_MIN_RESPONSE_MS = 0
        existing = SysQuery.objects.count()
        if existing >= ALREADY_SEEDED_THRESHOLD and not opts["force"]:
            self.stdout.write(
                f"{existing} queries already logged - skipping. Use --force to replay."
            )
            return

        db = SysDatabase.objects.filter(db_name=DB_NAME).first()
        if db is None:
            self.stderr.write(
                self.style.ERROR(f"database '{DB_NAME}' not in the catalog - run seed_demo first")
            )
            return

        executed = cached = rejected = failed = 0

        for username, workload in WORKLOADS:
            principal = Principal.objects.filter(username=username).first()
            if principal is None:
                self.stdout.write(f"  {username}: no such principal, skipping")
                continue
            if not BudgetAccount.objects.filter(principal=principal, db=db).exists():
                self.stdout.write(f"  {username}: no budget account, skipping")
                continue

            for sql, rho in workload:
                try:
                    result = run_private_query(principal, DB_NAME, sql, rho_req=rho)
                except DPQueryError as exc:
                    # A policy refusal is a legitimate outcome to record, but an
                    # unexpected one means the workload references something the
                    # catalog does not have -- surface it rather than hiding it.
                    failed += 1
                    self.stdout.write(f"  {username}: refused {sql[:52]}... ({exc})")
                    continue

                status = result["status"]
                if status == "executed":
                    executed += 1
                elif status == "cache_hit":
                    cached += 1
                elif status == "rejected_budget":
                    rejected += 1

        self._backdate(opts["days"], opts["seed"])

        total = executed + cached + rejected
        self.stdout.write(
            self.style.SUCCESS(
                f"Replayed {total} queries: {executed} executed, {cached} served from "
                f"cache (rho 0), {rejected} refused for budget."
            )
        )
        if failed:
            self.stdout.write(self.style.WARNING(f"{failed} query(ies) hit a policy error."))

        for username, _ in WORKLOADS:
            account = BudgetAccount.objects.filter(
                principal__username=username, db=db
            ).first()
            if account:
                pct = 100.0 * account.rho_spent / account.rho_total if account.rho_total else 0.0
                self.stdout.write(
                    f"  {username:<6} rho {account.rho_spent:.3f} / {account.rho_total:.2f}"
                    f"  ({pct:.0f}% spent)"
                )

    def _backdate(self, days: int, seed: int) -> None:
        """Spread query timestamps backwards so the burn-down has a time axis.

        Only `sys_query` is touched. Ordering is preserved -- the oldest query
        keeps the oldest timestamp -- because the burn-down is a cumulative
        curve and shuffling the order would make it descend, which zCDP
        composition never does.
        """
        rng = random.Random(seed)
        rows = list(SysQuery.objects.order_by("query_id").values_list("query_id", flat=True))
        if not rows:
            return

        now = timezone.now()
        span = timedelta(days=days)
        step = span / max(len(rows), 1)

        with transaction.atomic():
            for i, query_id in enumerate(rows):
                # Walk forward from `days` ago to roughly an hour ago, with a
                # little jitter so the points do not look mechanically spaced.
                offset = step * i + timedelta(minutes=rng.randint(0, 180))
                stamp = now - span + offset
                if stamp > now:
                    stamp = now - timedelta(minutes=rng.randint(1, 30))
                # .update() bypasses auto_now_add, which would otherwise pin
                # every row to insertion time.
                SysQuery.objects.filter(query_id=query_id).update(submitted_at=stamp)

        self.stdout.write(f"Backdated {len(rows)} queries across {days} days.")
