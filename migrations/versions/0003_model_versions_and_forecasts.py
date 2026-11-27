"""model versions, forecast runs and published forecasts (Phase 4)

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-27

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0003'
down_revision: str | Sequence[str] | None = '0002'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'model_versions',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('version', sa.Text(), nullable=False, unique=True),
        sa.Column('kind', sa.String(32), nullable=False),
        sa.Column('commodity_id', sa.Integer(), sa.ForeignKey('commodities.id'), nullable=False),
        sa.Column('horizon_days', sa.SmallInteger(), nullable=False),
        sa.Column('training_cutoff', sa.Date(), nullable=False),
        sa.Column('valid_from', sa.Date(), nullable=False),
        sa.Column('valid_to', sa.Date(), nullable=False),
        sa.Column('artifact_path', sa.Text(), nullable=False),
        sa.Column('artifact_sha256', sa.String(64), nullable=False),
        sa.Column('code_sha256', sa.String(64), nullable=False),
        sa.Column('params', postgresql.JSONB(), nullable=False),
        sa.Column('training', postgresql.JSONB(), nullable=False),
        sa.Column('evaluation', postgresql.JSONB(), nullable=False),
        sa.Column('interval_method', sa.String(16), nullable=False),
        sa.Column('status', sa.String(16), nullable=False, server_default='candidate'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('promoted_at', sa.DateTime(timezone=True)),
        sa.Column('replaced_version_id', sa.Integer(), sa.ForeignKey('model_versions.id')),
        sa.CheckConstraint("status in ('candidate', 'production', 'archived', 'rejected')", name='ck_model_status'),
        sa.CheckConstraint("interval_method in ('conditional', 'unconditional')", name='ck_model_interval_method'),
        sa.CheckConstraint('horizon_days between 1 and 14', name='ck_model_horizon'),
        sa.CheckConstraint('valid_from <= valid_to', name='ck_model_validity'),
    )
    op.create_index('uq_model_production', 'model_versions', ['commodity_id'], unique=True,
                    postgresql_where=sa.text("status = 'production'"))

    op.create_table(
        'forecast_runs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('job_id', sa.BigInteger(), sa.ForeignKey('ingestion_jobs.id')),
        sa.Column('model_version_id', sa.Integer(), sa.ForeignKey('model_versions.id'), nullable=False),
        sa.Column('origin', sa.Date(), nullable=False),
        sa.Column('data_complete_through', sa.Date(), nullable=False),
        sa.Column('series_eligible', sa.Integer(), nullable=False),
        sa.Column('series_forecast', sa.Integer(), nullable=False),
        sa.Column('calibration', postgresql.JSONB(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint('model_version_id', 'origin', name='uq_forecast_run_model_origin'),
    )

    op.create_table(
        'forecasts',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('run_id', sa.Integer(), sa.ForeignKey('forecast_runs.id'), nullable=False),
        sa.Column('series_id', sa.Integer(), sa.ForeignKey('price_series.id'), nullable=False),
        sa.Column('model_version_id', sa.Integer(), sa.ForeignKey('model_versions.id'), nullable=False),
        sa.Column('issued_on', sa.Date(), nullable=False),
        sa.Column('target_date', sa.Date(), nullable=False),
        sa.Column('horizon_days', sa.SmallInteger(), nullable=False),
        sa.Column('last_date', sa.Date(), nullable=False),
        sa.Column('last_price', sa.Numeric(14, 2), nullable=False),
        sa.Column('point', sa.Numeric(14, 2), nullable=False),
        sa.Column('lower80', sa.Numeric(14, 2), nullable=False),
        sa.Column('upper80', sa.Numeric(14, 2), nullable=False),
        sa.Column('lower90', sa.Numeric(14, 2), nullable=False),
        sa.Column('upper90', sa.Numeric(14, 2), nullable=False),
        sa.Column('volatility_tercile', sa.SmallInteger()),
        sa.UniqueConstraint('series_id', 'model_version_id', 'issued_on', 'target_date', name='uq_forecast_point'),
        sa.CheckConstraint('horizon_days between 1 and 14', name='ck_forecast_horizon'),
        sa.CheckConstraint('target_date = issued_on + horizon_days', name='ck_forecast_target'),
        # Conformal intervals need not contain the point forecast, so only the nesting of the levels is checked.
        sa.CheckConstraint('lower90 <= lower80 AND lower80 <= upper80 AND upper80 <= upper90',
                           name='ck_forecast_interval_order'),
    )
    op.create_index('ix_forecasts_series_issued', 'forecasts', ['series_id', 'issued_on'])
    op.create_index('ix_forecasts_run', 'forecasts', ['run_id'])


def downgrade() -> None:
    op.drop_index('ix_forecasts_run', table_name='forecasts')
    op.drop_index('ix_forecasts_series_issued', table_name='forecasts')
    op.drop_table('forecasts')
    op.drop_table('forecast_runs')
    op.drop_index('uq_model_production', table_name='model_versions')
    op.drop_table('model_versions')
