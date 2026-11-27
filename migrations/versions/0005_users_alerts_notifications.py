"""users, sessions, one-time login codes, alert subscriptions, notification history (Phase 6)

Personal data is limited to the Telegram chat id (methodology/requirements: no names or usernames are stored).
Deleting a user removes their sessions, login codes, subscriptions and notification history (ON DELETE CASCADE).

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-27

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0005'
down_revision: str | Sequence[str] | None = '0004'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'users',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('telegram_chat_id', sa.BigInteger(), nullable=False, unique=True),
        sa.Column('language', sa.String(8), nullable=False, server_default='en'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        'login_codes',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('code_sha256', sa.String(64), nullable=False, unique=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('used_at', sa.DateTime(timezone=True)),
    )
    op.create_table(
        'user_sessions',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('token_sha256', sa.String(64), nullable=False, unique=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_used_at', sa.DateTime(timezone=True)),
    )
    op.create_table(
        'alert_subscriptions',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('series_id', sa.Integer(), sa.ForeignKey('price_series.id'), nullable=False),
        sa.Column('condition_type', sa.String(24), nullable=False),
        sa.Column('threshold', sa.Numeric(14, 2), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('last_evaluated_date', sa.Date()),
        sa.Column('last_condition', sa.Boolean()),
        sa.Column('last_forecast_run_id', sa.Integer()),
        sa.UniqueConstraint('user_id', 'series_id', 'condition_type', 'threshold', name='uq_alert_subscription'),
        sa.CheckConstraint("condition_type in ('price_above', 'price_below', 'forecast_change_pct')",
                           name='ck_alert_condition'),
        sa.CheckConstraint('threshold > 0', name='ck_alert_threshold_positive'),
    )
    op.create_index('ix_alert_subscriptions_user', 'alert_subscriptions', ['user_id'])
    op.create_table(
        'notification_history',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('subscription_id', sa.Integer(),
                  sa.ForeignKey('alert_subscriptions.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('idempotency_key', sa.String(128), nullable=False, unique=True),
        sa.Column('status', sa.String(16), nullable=False, server_default='pending'),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('error', sa.Text()),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('sent_at', sa.DateTime(timezone=True)),
        sa.CheckConstraint("status in ('pending', 'sent', 'failed')", name='ck_notification_status'),
    )
    op.create_index('ix_notification_user_created', 'notification_history', ['user_id', 'created_at'])
    op.create_index('ix_notification_status', 'notification_history', ['status'])


def downgrade() -> None:
    op.drop_table('notification_history')
    op.drop_index('ix_alert_subscriptions_user', table_name='alert_subscriptions')
    op.drop_table('alert_subscriptions')
    op.drop_table('user_sessions')
    op.drop_table('login_codes')
    op.drop_table('users')
