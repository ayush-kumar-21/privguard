from django.db import migrations


def add_roles(apps, schema_editor):
    """Give the four base roles a description and add the enterprise job roles."""
    from accounts.roles import BASE_ROLES, JOB_ROLES

    Role = apps.get_model("accounts", "Role")
    for name, text in BASE_ROLES.items():
        role, _ = Role.objects.get_or_create(role_name=name, defaults={"description": text})
        if not role.description:
            role.description = text
            role.save(update_fields=["description"])
    for name, (carries, text) in JOB_ROLES.items():
        Role.objects.get_or_create(role_name=name, defaults={"description": text})


def remove_unused_job_roles(apps, schema_editor):
    from accounts.roles import JOB_ROLES

    Role = apps.get_model("accounts", "Role")
    Role.objects.filter(role_name__in=JOB_ROLES, principals__isnull=True).delete()


class Migration(migrations.Migration):
    dependencies = [("accounts", "0002_grant_constraints")]
    operations = [migrations.RunPython(add_roles, remove_unused_job_roles)]
