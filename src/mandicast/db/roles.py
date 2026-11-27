"""Least-privilege database role for the API and the Telegram bot (Phase 8, docs/15 §4).

The pipeline, the worker and migrations keep using the owner account. The API and the bot connect as a separate
role that can read every table but write only the five account tables (users and their sessions, sign-in codes,
alerts and alert messages). A bug or an injection in the public-facing services then cannot change prices, models
or forecasts, and cannot alter the schema.

    python -m mandicast db-roles      (idempotent; needs API_DB_PASSWORD; run after db-upgrade)
"""

from __future__ import annotations

import re

from sqlalchemy import Engine, text

ACCOUNT_TABLES = ("users", "login_codes", "user_sessions", "alert_subscriptions", "notification_history")
_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def _names(c, role: str) -> tuple[str, str, str, str]:
    """Role, owner and database as identifiers quoted by the server (quote_ident), plus the owner's raw name.
    Utility statements (CREATE ROLE, GRANT …) cannot take bind parameters, so identifiers are quoted this way."""
    r = c.execute(text("SELECT quote_ident(:r), quote_ident(current_user), quote_ident(current_database()), "
                       "current_user"), {"r": role}).one()
    return r[0], r[1], r[2], r[3]


def ensure_api_role(engine: Engine, role: str, password: str, statement_timeout_s: int = 15,
                    connection_limit: int = 40) -> dict:
    """Create or update `role` (LOGIN, the given password) and reset its privileges in the current database."""
    if not _NAME.fullmatch(role):
        raise ValueError("role name must match [a-z_][a-z0-9_]* (at most 63 characters)")
    if len(password) < 16:
        raise ValueError("the API database password must have at least 16 characters")
    with engine.begin() as c:
        q_role, owner, db, owner_name = _names(c, role)
        if role == owner_name:
            raise ValueError("the API role must differ from the owner account")
        # Utility statements cannot take bind parameters: the password is quoted by the server itself.
        literal = c.execute(text("SELECT quote_literal(:p)"), {"p": password}).scalar_one()
        exists = c.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role}).first() is not None
        verb = "ALTER" if exists else "CREATE"
        c.execute(text(f'{verb} ROLE {q_role} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT '
                       f"CONNECTION LIMIT {int(connection_limit)} PASSWORD {literal}"))
        c.execute(text(f"ALTER ROLE {q_role} SET statement_timeout = '{int(statement_timeout_s)}s'"))
        c.execute(text(f'GRANT CONNECT ON DATABASE {db} TO {q_role}'))
        c.execute(text(f'GRANT USAGE ON SCHEMA public TO {q_role}'))
        c.execute(text(f'REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {q_role}'))
        c.execute(text(f'REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {q_role}'))
        c.execute(text(f'GRANT SELECT ON ALL TABLES IN SCHEMA public TO {q_role}'))
        c.execute(text(f'GRANT INSERT, UPDATE, DELETE ON {", ".join(ACCOUNT_TABLES)} TO {q_role}'))
        sequences = [r[0] for r in c.execute(text(
            "SELECT pg_get_serial_sequence(t, 'id') FROM unnest(CAST(:t AS text[])) AS t"),
            {"t": list(ACCOUNT_TABLES)}) if r[0]]
        for seq in sequences:
            c.execute(text(f'GRANT USAGE, SELECT ON SEQUENCE {seq} TO {q_role}'))
        # Tables added by later migrations become readable (not writable) without re-running this.
        c.execute(text(f'ALTER DEFAULT PRIVILEGES FOR ROLE {owner} IN SCHEMA public GRANT SELECT ON TABLES '
                       f'TO {q_role}'))
    return {"role": role, "database": db, "created": not exists, "writable_tables": list(ACCOUNT_TABLES),
            "sequences": len(sequences)}


def drop_role(engine: Engine, role: str) -> None:
    """Remove the role's privileges in the current database and the role itself (used by tests)."""
    if not _NAME.fullmatch(role):
        raise ValueError("invalid role name")
    with engine.begin() as c:
        if c.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role}).first() is None:
            return
        q_role, owner, db, _ = _names(c, role)
        # Identifiers come from quote_ident above (S608 cannot see that); no user input reaches this statement.
        c.execute(text(f'ALTER DEFAULT PRIVILEGES FOR ROLE {owner} IN SCHEMA public '  # noqa: S608
                       f'REVOKE SELECT ON TABLES FROM {q_role}'))
        c.execute(text(f'REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {q_role}'))
        c.execute(text(f'REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {q_role}'))
        c.execute(text(f'REVOKE ALL ON SCHEMA public FROM {q_role}'))
        c.execute(text(f'REVOKE ALL ON DATABASE {db} FROM {q_role}'))
        c.execute(text(f'DROP ROLE {q_role}'))
