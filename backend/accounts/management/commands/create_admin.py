"""Create a real administrator (the `dba` role plus Django-admin access).

    python manage.py create_admin alice_admin
    PRIVGUARD_ADMIN_PASSWORD=... python manage.py create_admin alice_admin

The password comes from the environment or a prompt, never from the command line (which other
users of the machine can read). Re-running for an existing user makes them an administrator and
resets nothing else.
"""
import getpass
import os

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.models import PrincipalRole, Role


class Command(BaseCommand):
    help = "Create (or promote) an administrator with the dba role."

    def add_arguments(self, parser):
        parser.add_argument("username")

    @transaction.atomic
    def handle(self, *args, username, **options):
        Principal = get_user_model()
        user = Principal.objects.filter(username=username).first()
        if user is None:
            password = os.environ.get("PRIVGUARD_ADMIN_PASSWORD") or getpass.getpass("Password: ")
            user = Principal(username=username, kind="human")
            try:
                validate_password(password, user=user)
            except ValidationError as e:
                raise CommandError(" ".join(e.messages))
            user.set_password(password)
        user.is_staff = user.is_superuser = True
        user.save()
        role, _ = Role.objects.get_or_create(role_name="dba")
        PrincipalRole.objects.get_or_create(principal=user, role=role)
        self.stdout.write(self.style.SUCCESS(f"{username} is an administrator."))
