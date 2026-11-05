"""Row-level security helper applied to every tenant table (ADR-0003, doc 07 §2).

The `NULLIF(…, '')` form is required: after a `SET LOCAL` transaction ends, `current_setting('app.tenant_id',
true)` returns '' rather than NULL on PostgreSQL 18, and a bare `::uuid` cast would error (verified 2026-11-04).
"""

from __future__ import annotations

from alembic import op

RUNTIME_ROLE = "app_rls"
TENANT_PREDICATE = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"
POLICY_NAME = "tenant_isolation"


def apply_tenant_rls(schema: str, table: str, *, grants: str = "SELECT, INSERT, UPDATE, DELETE") -> None:
    qualified = f"{schema}.{table}"
    op.execute(f"ALTER TABLE {qualified} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {qualified} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {POLICY_NAME} ON {qualified} USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE})"
    )
    op.execute(f"GRANT {grants} ON {qualified} TO {RUNTIME_ROLE}")


def drop_tenant_rls(schema: str, table: str) -> None:
    op.execute(f"DROP POLICY IF EXISTS {POLICY_NAME} ON {schema}.{table}")
