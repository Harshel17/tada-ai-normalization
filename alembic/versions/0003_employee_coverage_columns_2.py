"""Second employee coverage step: documented fields promoted from extras.

Only additive: new NULLABLE columns, so existing rows and existing queries are unaffected.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-16 09:00:00
"""

from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

NEW = [
    ("work_phone_extension", sa.Text()),
    ("employee_type_effective_date", sa.Date()),
    ("union_start_date", sa.Date()),
    ("aca_employment_status_raw", sa.Text()),
    ("comp_standard_hours_per_week", sa.Numeric()),
    ("comp_last_pay_date", sa.Date()),
    ("comp_next_review_date", sa.Date()),
    ("comp_compa_ratio", sa.Numeric()),
    ("comp_last_performance_review_date", sa.Date()),
    ("comp_last_performance_review_rating_raw", sa.Text()),
    ("comp_last_performance_review_job_title", sa.Text()),
    ("comp_next_performance_review_date", sa.Date()),
]


def upgrade() -> None:
    for name, kind in NEW:
        op.add_column("employees", sa.Column(name, kind, nullable=True))


def downgrade() -> None:
    for name, _ in reversed(NEW):
        op.drop_column("employees", name)
