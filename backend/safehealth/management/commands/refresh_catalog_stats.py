"""
Management command: refresh catalog statistics from PostgreSQL.

Populates the vestigial catalog columns (est_row_count, n_pages, height)
from PostgreSQL's own statistics (pg_class, pg_relation_size), making them
honest instead of always-zero.

Usage:
    python manage.py refresh_catalog_stats
"""
from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = "Refresh sys_table and sys_index statistics from PostgreSQL pg_class."

    def handle(self, *args, **options):
        with connection.cursor() as cur:
            # First run ANALYZE to ensure statistics are fresh
            cur.execute("ANALYZE;")

            # Update table statistics
            cur.execute("""
                UPDATE sys_table t
                   SET est_row_count = c.reltuples::bigint,
                       n_pages = c.relpages
                  FROM pg_class c
                 WHERE c.relname = t.table_name
                   AND c.relkind = 'r';
            """)
            table_count = cur.rowcount

            # Update index statistics
            cur.execute("""
                UPDATE sys_index i
                   SET height = COALESCE(pg_relation_size(c.oid) / 8192, 1)
                  FROM pg_class c
                 WHERE c.relname = i.index_name;
            """)
            index_count = cur.rowcount

        self.stdout.write(self.style.SUCCESS(
            f"Updated {table_count} table(s), {index_count} index(es)."
        ))
