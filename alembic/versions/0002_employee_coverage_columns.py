"""Employee coverage: structured columns promoted from extras (reviewed autogenerate).

Only additive: new nullable columns, and NOT NULL JSONB lists that default to [] for existing rows.


Revision ID: 0002
Revises: 0001
Create Date: 2026-10-13 11:12:26.611507
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("employees", sa.Column("preferred_pronouns", sa.Text(), nullable=True))
    op.add_column("employees", sa.Column("preferred_language", sa.Text(), nullable=True))
    op.add_column("employees", sa.Column("deceased_date", sa.Date(), nullable=True))
    op.add_column("employees", sa.Column("secondary_email", sa.Text(), nullable=True))
    op.add_column("employees", sa.Column("fax", sa.Text(), nullable=True))
    op.add_column("employees", sa.Column("preferred_phone", sa.Text(), nullable=True))
    op.add_column(
        "employees",
        sa.Column(
            "emergency_contacts",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
    )
    op.add_column(
        "employees",
        sa.Column("tax_form_address", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column("employees", sa.Column("tax_geo_code", sa.Text(), nullable=True))
    op.add_column("employees", sa.Column("school_district_code", sa.Text(), nullable=True))
    op.add_column("employees", sa.Column("termination_type_raw", sa.Text(), nullable=True))
    op.add_column("employees", sa.Column("termination_explanation", sa.Text(), nullable=True))
    op.add_column("employees", sa.Column("last_worked_date", sa.Date(), nullable=True))
    op.add_column("employees", sa.Column("leave_return_date", sa.Date(), nullable=True))
    op.add_column("employees", sa.Column("eligible_for_rehire", sa.Boolean(), nullable=True))
    op.add_column("employees", sa.Column("peo_start_date", sa.Date(), nullable=True))
    op.add_column("employees", sa.Column("retirement_date", sa.Date(), nullable=True))
    op.add_column("employees", sa.Column("job_group", sa.Text(), nullable=True))
    op.add_column("employees", sa.Column("job_effective_date", sa.Date(), nullable=True))
    op.add_column("employees", sa.Column("soc_code", sa.Text(), nullable=True))
    op.add_column("employees", sa.Column("eeo_job_category_raw", sa.Text(), nullable=True))
    op.add_column("employees", sa.Column("full_time_equivalent", sa.Numeric(), nullable=True))
    op.add_column("employees", sa.Column("is_contractor", sa.Boolean(), nullable=True))
    op.add_column("employees", sa.Column("is_officer", sa.Boolean(), nullable=True))
    op.add_column("employees", sa.Column("is_business_owner", sa.Boolean(), nullable=True))
    op.add_column("employees", sa.Column("is_manager", sa.Boolean(), nullable=True))
    op.add_column("employees", sa.Column("is_supervisor", sa.Boolean(), nullable=True))
    op.add_column("employees", sa.Column("is_remote_worker", sa.Boolean(), nullable=True))
    op.add_column("employees", sa.Column("is_seasonal", sa.Boolean(), nullable=True))
    op.add_column("employees", sa.Column("is_union_member", sa.Boolean(), nullable=True))
    op.add_column("employees", sa.Column("union_code_raw", sa.Text(), nullable=True))
    op.add_column("employees", sa.Column("workers_comp_code", sa.Text(), nullable=True))
    op.add_column("employees", sa.Column("timeclock_id", sa.Text(), nullable=True))
    op.add_column(
        "employees",
        sa.Column(
            "labor_allocations",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
    )
    op.add_column(
        "employees",
        sa.Column(
            "tax_withholdings",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
    )
    op.add_column("employees", sa.Column("fica_exempt", sa.Boolean(), nullable=True))
    op.add_column("employees", sa.Column("non_resident_alien", sa.Boolean(), nullable=True))
    op.add_column(
        "employees", sa.Column("benefits", postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )
    op.add_column(
        "employees", sa.Column("comp_standard_hours_per_day", sa.Numeric(), nullable=True)
    )
    op.add_column("employees", sa.Column("comp_pay_frequency_raw", sa.Text(), nullable=True))
    op.add_column("employees", sa.Column("comp_periods_per_year", sa.Integer(), nullable=True))
    op.add_column("employees", sa.Column("comp_last_change_date", sa.Date(), nullable=True))
    op.add_column("employees", sa.Column("comp_last_change_amount", sa.Numeric(), nullable=True))
    op.add_column("employees", sa.Column("comp_last_change_percent", sa.Numeric(), nullable=True))
    op.add_column("employees", sa.Column("comp_per_diem_amount", sa.Numeric(), nullable=True))
    op.add_column(
        "employees",
        sa.Column(
            "comp_alternate_rates",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("employees", "comp_alternate_rates")
    op.drop_column("employees", "comp_per_diem_amount")
    op.drop_column("employees", "comp_last_change_percent")
    op.drop_column("employees", "comp_last_change_amount")
    op.drop_column("employees", "comp_last_change_date")
    op.drop_column("employees", "comp_periods_per_year")
    op.drop_column("employees", "comp_pay_frequency_raw")
    op.drop_column("employees", "comp_standard_hours_per_day")
    op.drop_column("employees", "benefits")
    op.drop_column("employees", "non_resident_alien")
    op.drop_column("employees", "fica_exempt")
    op.drop_column("employees", "tax_withholdings")
    op.drop_column("employees", "labor_allocations")
    op.drop_column("employees", "timeclock_id")
    op.drop_column("employees", "workers_comp_code")
    op.drop_column("employees", "union_code_raw")
    op.drop_column("employees", "is_union_member")
    op.drop_column("employees", "is_seasonal")
    op.drop_column("employees", "is_remote_worker")
    op.drop_column("employees", "is_supervisor")
    op.drop_column("employees", "is_manager")
    op.drop_column("employees", "is_business_owner")
    op.drop_column("employees", "is_officer")
    op.drop_column("employees", "is_contractor")
    op.drop_column("employees", "full_time_equivalent")
    op.drop_column("employees", "eeo_job_category_raw")
    op.drop_column("employees", "soc_code")
    op.drop_column("employees", "job_effective_date")
    op.drop_column("employees", "job_group")
    op.drop_column("employees", "retirement_date")
    op.drop_column("employees", "peo_start_date")
    op.drop_column("employees", "eligible_for_rehire")
    op.drop_column("employees", "leave_return_date")
    op.drop_column("employees", "last_worked_date")
    op.drop_column("employees", "termination_explanation")
    op.drop_column("employees", "termination_type_raw")
    op.drop_column("employees", "school_district_code")
    op.drop_column("employees", "tax_geo_code")
    op.drop_column("employees", "tax_form_address")
    op.drop_column("employees", "emergency_contacts")
    op.drop_column("employees", "preferred_phone")
    op.drop_column("employees", "fax")
    op.drop_column("employees", "secondary_email")
    op.drop_column("employees", "deceased_date")
    op.drop_column("employees", "preferred_language")
    op.drop_column("employees", "preferred_pronouns")
