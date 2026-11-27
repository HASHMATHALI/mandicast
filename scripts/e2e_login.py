"""Development and test helper, not part of the service: a dashboard sign-in link for a TEST user, without Telegram.

The real flow is: the bot's /login command issues a one-time code and sends DASHBOARD_URL/login#code=…. This script
calls the same service function for a fixed test chat id, so the dashboard's end-to-end test (dashboard/e2e) can sign
in on a local database. The chat id is negative: Telegram private chats (the only ones the bot serves) have positive
ids, so it can never belong to a real user.

    python scripts/e2e_login.py link      # prints DASHBOARD_URL/login#code=… (valid 15 minutes, once)
    python scripts/e2e_login.py delete    # deletes the test user with its sessions, alerts and history
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from mandicast.bot import service
from mandicast.config import get_settings

TEST_CHAT_ID = -990_000_001


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=["link", "delete"])
    args = parser.parse_args(argv)
    settings = get_settings()
    engine = create_engine(settings.database_url.get_secret_value(), hide_parameters=True,
                           connect_args={"connect_timeout": settings.db_connect_timeout_s})
    with Session(engine) as s, s.begin():
        if args.action == "link":
            user_id = service.get_or_create_user(s, TEST_CHAT_ID)
            code = service.issue_login_code(s, user_id, settings.login_code_minutes)
            print(f"{settings.dashboard_url}/login#code={code}")
        else:
            user_id = service.find_user(s, TEST_CHAT_ID)
            if user_id is not None:
                service.delete_user(s, user_id)
            print("deleted" if user_id is not None else "no test user")
    engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
