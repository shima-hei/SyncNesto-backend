"""Add tenant ownership and preserve existing identities and project resources."""

from alembic import op
import sqlalchemy as sa

revision = "c15a7e92d401"
down_revision = "b94f7d20c315"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """段階的に所有関係を追加し、既存データをDefault Tenantへ移行する。"""
    op.create_table(
        "tenants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(100), nullable=False, unique=True),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_table(
        "tenant_members",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False
        ),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("role_id", sa.Integer(), sa.ForeignKey("roles.id"), nullable=False),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("display_name", sa.String(255)),
        sa.Column("department", sa.String(255)),
        sa.Column("position", sa.String(255)),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "joined_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("tenant_id", "user_id", name="uq_tenant_members"),
    )
    for column in ("tenant_id", "user_id"):
        op.create_index(f"ix_tenant_members_{column}", "tenant_members", [column])
    connection = op.get_bind()
    for key, name in (
        ("tenant_owner", "組織所有者"),
        ("tenant_admin", "組織管理者"),
        ("tenant_member", "組織メンバー"),
    ):
        connection.execute(
            sa.text("INSERT INTO roles (key,name,scope) VALUES (:key,:name,'tenant')"),
            {"key": key, "name": name},
        )
    connection.execute(
        sa.text(
            "INSERT INTO tenants (name,slug,status,version) VALUES ('Default Tenant','default','active',1)"
        )
    )
    for table in ("projects", "notifications", "drafts", "audit_logs"):
        op.add_column(table, sa.Column("tenant_id", sa.Integer(), nullable=True))
    connection.execute(
        sa.text(
            "UPDATE projects SET tenant_id=(SELECT id FROM tenants WHERE slug='default')"
        )
    )
    connection.execute(
        sa.text("""INSERT INTO tenant_members (tenant_id,user_id,role_id,status,display_name,department,position,version)
        SELECT t.id,u.id,r.id,CASE WHEN u.is_active AND u.deleted_at IS NULL THEN 'active' ELSE 'suspended' END,u.name,u.department,u.position,1
        FROM users u CROSS JOIN tenants t CROSS JOIN roles r WHERE t.slug='default' AND r.key='tenant_member' AND r.scope='tenant'""")
    )
    for table in ("notifications", "drafts", "audit_logs"):
        connection.execute(
            sa.text(
                f"UPDATE {table} x SET tenant_id=p.tenant_id FROM projects p WHERE p.id=x.project_id"
            )
        )
    # Projectを失った既存通知も、移行前の単一組織に所属する。
    connection.execute(
        sa.text(
            "UPDATE notifications SET tenant_id=(SELECT id FROM tenants WHERE slug='default') WHERE tenant_id IS NULL"
        )
    )
    # Project作成前の業務下書きも既存組織へ引き継ぐ。
    connection.execute(
        sa.text(
            "UPDATE drafts SET tenant_id=(SELECT id FROM tenants WHERE slug='default') WHERE tenant_id IS NULL"
        )
    )
    for table in ("projects", "notifications", "drafts", "audit_logs"):
        op.create_foreign_key(
            f"fk_{table}_tenant", table, "tenants", ["tenant_id"], ["id"]
        )
        op.create_index(f"ix_{table}_tenant_id", table, ["tenant_id"])
    for table in ("projects", "notifications"):
        op.alter_column(table, "tenant_id", nullable=False)
    op.drop_index("ix_projects_project_code", table_name="projects")
    op.create_index("ix_projects_project_code", "projects", ["project_code"])
    op.create_unique_constraint(
        "uq_projects_tenant_code", "projects", ["tenant_id", "project_code"]
    )
    op.drop_constraint("uq_drafts_owner_scope", "drafts", type_="unique")
    op.create_unique_constraint(
        "uq_drafts_owner_scope", "drafts", ["owner_user_id", "tenant_id", "scope_key"]
    )
    op.create_index(
        "ix_notifications_tenant_recipient_created",
        "notifications",
        ["tenant_id", "recipient_user_id", "created_at", "id"],
    )


def downgrade() -> None:
    """移行後データを破壊する自動downgradeを禁止する。"""
    raise RuntimeError(
        "Tenant migration requires a verified backup and an explicit recovery procedure; automatic downgrade is disabled"
    )
