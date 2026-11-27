"""MLOps: promotion gate results, model events, fallback-aware forecasts, monitoring snapshots (Phase 5)

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-27

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0004'
down_revision: str | Sequence[str] | None = '0003'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EVENTS = "('registered', 'promoted', 'rejected', 'rolled_back', 'archived', 'fallback')"


def upgrade() -> None:
    op.add_column('model_versions', sa.Column('gate', postgresql.JSONB()))
    op.add_column('model_versions', sa.Column('status_reason', sa.Text()))
    op.add_column('model_versions', sa.Column('mlflow_registry_version', sa.String(32)))

    op.create_table(
        'model_events',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('commodity_id', sa.Integer(), sa.ForeignKey('commodities.id'), nullable=False),
        sa.Column('model_version_id', sa.Integer(), sa.ForeignKey('model_versions.id')),
        sa.Column('event', sa.String(16), nullable=False),
        sa.Column('reason', sa.Text()),
        sa.Column('details', postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(f"event in {EVENTS}", name='ck_model_event'),
    )
    op.create_index('ix_model_events_commodity', 'model_events', ['commodity_id', 'created_at'])

    # forecast runs: one publication per crop and origin, possibly without a model (last-value fallback)
    op.add_column('forecast_runs', sa.Column('commodity_id', sa.Integer(), sa.ForeignKey('commodities.id')))
    op.execute("UPDATE forecast_runs fr SET commodity_id = mv.commodity_id FROM model_versions mv "
               "WHERE mv.id = fr.model_version_id")
    op.alter_column('forecast_runs', 'commodity_id', nullable=False)
    op.alter_column('forecast_runs', 'model_version_id', nullable=True)
    op.add_column('forecast_runs', sa.Column('decisions', postgresql.JSONB(), nullable=False,
                                             server_default=sa.text("'{}'::jsonb")))
    op.add_column('forecast_runs', sa.Column('drift', postgresql.JSONB(), nullable=False,
                                             server_default=sa.text("'{}'::jsonb")))
    op.create_unique_constraint('uq_forecast_run_commodity_origin', 'forecast_runs', ['commodity_id', 'origin'])

    # forecasts: what was served, and the model's own value (kept for monitoring even when last value is served)
    op.add_column('forecasts', sa.Column('model_point', sa.Numeric(14, 2)))
    op.add_column('forecasts', sa.Column('served_by', sa.String(16), nullable=False, server_default='model'))
    op.execute("UPDATE forecasts SET model_point = point")
    op.alter_column('forecasts', 'model_version_id', nullable=True)
    op.create_check_constraint('ck_forecast_served_by', 'forecasts', "served_by in ('model', 'last_value')")
    op.create_unique_constraint('uq_forecast_run_series_target', 'forecasts', ['run_id', 'series_id', 'target_date'])

    op.create_table(
        'monitoring_snapshots',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('as_of', sa.Date(), nullable=False),
        sa.Column('commodity_id', sa.Integer(), sa.ForeignKey('commodities.id'), nullable=False),
        sa.Column('day_range', sa.String(8), nullable=False),
        sa.Column('origins', sa.Integer(), nullable=False),
        sa.Column('points', sa.Integer(), nullable=False),
        sa.Column('wape_model', sa.Float()),
        sa.Column('wape_last_value', sa.Float()),
        sa.Column('wape_served', sa.Float()),
        sa.Column('coverage80', sa.Float()),
        sa.Column('coverage90', sa.Float()),
        sa.Column('direction_moves_ge_2pct', sa.Float()),
        sa.Column('details', postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column('computed_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint('as_of', 'commodity_id', 'day_range', name='uq_monitoring_day'),
        sa.CheckConstraint("day_range in ('1-7', '8-14')", name='ck_monitoring_day_range'),
    )


def downgrade() -> None:
    op.drop_table('monitoring_snapshots')
    op.drop_constraint('uq_forecast_run_series_target', 'forecasts', type_='unique')
    op.drop_constraint('ck_forecast_served_by', 'forecasts', type_='check')
    op.execute("DELETE FROM forecasts WHERE model_version_id IS NULL")
    op.alter_column('forecasts', 'model_version_id', nullable=False)
    op.drop_column('forecasts', 'served_by')
    op.drop_column('forecasts', 'model_point')
    op.drop_constraint('uq_forecast_run_commodity_origin', 'forecast_runs', type_='unique')
    op.drop_column('forecast_runs', 'drift')
    op.drop_column('forecast_runs', 'decisions')
    op.execute("DELETE FROM forecast_runs WHERE model_version_id IS NULL")
    op.alter_column('forecast_runs', 'model_version_id', nullable=False)
    op.drop_column('forecast_runs', 'commodity_id')
    op.drop_index('ix_model_events_commodity', table_name='model_events')
    op.drop_table('model_events')
    op.drop_column('model_versions', 'mlflow_registry_version')
    op.drop_column('model_versions', 'status_reason')
    op.drop_column('model_versions', 'gate')
