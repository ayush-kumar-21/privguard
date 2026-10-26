"""Report principals who hold `dba` together with a role that can run queries.

    python manage.py audit_separation_of_duty        # exit status 1 if any are found

The trigger and the API refuse to create such an account, but data that predates them, or that was
restored from a backup, can still contain one. The API container runs this at startup and logs a
warning; CI can run it as a gate.
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from accounts.roles import QUERY_ROLES, effective_role_names


class Command(BaseCommand):
    help = "List principals that violate separation of duty (dba plus a query role)."

    def handle(self, *args, **options):
        bad = []
        for p in get_user_model().objects.prefetch_related("roles"):
            assigned = {r.role_name for r in p.roles.all()}
            if "dba" in assigned and effective_role_names(assigned) & QUERY_ROLES:
                bad.append((p.username, sorted(assigned)))
        if not bad:
            self.stdout.write(self.style.SUCCESS("Separation of duty holds: no administrator can query."))
            return
        for name, roles in bad:
            self.stderr.write(f"separation of duty: {name} holds {', '.join(roles)}")
        raise CommandError(f"{len(bad)} account(s) hold dba together with a query role; remove one role from each")
