"""
Replace every stored noise seed by a one-way commitment.

A seed regenerates a query's noise exactly (answer - noise = true value), so it
must not sit in a table or in a pg_dump. Nothing reads the column back, so
existing rows are rewritten in place. This is irreversible by design: once a
seed is overwritten it cannot be recovered, and that is the point.
"""
import hashlib

from django.db import migrations

PREFIX = b"privguard/seed-commitment/v1"  # keep identical to privacy.service.seed_commitment


def commit(apps, schema_editor):
    Cache = apps.get_model("audit", "SysNoiseCache")
    batch = []
    for row in Cache.objects.all().iterator(chunk_size=500):
        row.noise_seed = hashlib.sha256(PREFIX + bytes(row.noise_seed)).digest()
        batch.append(row)
        if len(batch) >= 500:
            Cache.objects.bulk_update(batch, ["noise_seed"])
            batch = []
    if batch:
        Cache.objects.bulk_update(batch, ["noise_seed"])


class Migration(migrations.Migration):
    dependencies = [("audit", "0005_drop_exact_counts")]

    operations = [migrations.RunPython(commit, migrations.RunPython.noop)]
