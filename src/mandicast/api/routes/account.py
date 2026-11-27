"""Authenticated endpoints (docs/07 §6): dashboard sign-in with a one-time code from the bot, alert management,
notification history, account deletion.

Security:
- Bearer session tokens (random, stored as SHA-256, 30 days); 401 `auth_required` without a valid one.
- Every query is filtered by the caller's user id; another user's alert id behaves exactly like a missing one (404).
- These responses are never cached (the gateway skips them and sends `Cache-Control: no-store`).
- Request bodies reject unknown fields; values are validated as in the bot.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from mandicast.api.deps import market_or_404, series_or_404
from mandicast.api.errors import ApiError
from mandicast.bot import service

router = APIRouter(tags=["account"])
AUTH_HEADERS = {"WWW-Authenticate": "Bearer"}


def get_rw_db(request: Request) -> Iterator[Session]:
    """A writable session (the read endpoints use read-only connections); committed when the request succeeds."""
    session = request.app.state.rw_session_factory()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


def current_user(request: Request, db: Annotated[Session, Depends(get_rw_db)]) -> int:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    user_id = service.user_for_token(db, token.strip()) if scheme.lower() == "bearer" else None
    if user_id is None:
        raise ApiError(401, "auth_required", "Sign in with a link from the MandiCast Telegram bot (/login).",
                       headers=AUTH_HEADERS)
    return user_id


class CodeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=10, max_length=200)


class SessionOut(BaseModel):
    token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105 - the OAuth token type name, not a secret
    expires_at: datetime


class Condition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["price_above", "price_below", "forecast_change_pct"]
    value: float = Field(gt=0, le=10_000_000)


class AlertIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    commodity_id: int = Field(ge=1, le=2_147_483_647)
    market_id: int = Field(ge=1, le=2_147_483_647)
    condition: Condition


class AlertOut(BaseModel):
    id: int
    commodity_id: int
    commodity: str
    market_id: int
    market: str
    condition: Condition
    active: bool
    created_at: datetime
    description: str


class NotificationOut(BaseModel):
    id: int
    subscription_id: int
    status: str
    message: str
    created_at: datetime
    sent_at: datetime | None


def _alert_out(sub: dict) -> dict:
    return {"id": sub["id"], "commodity_id": sub["commodity_id"], "commodity": sub["commodity"],
            "market_id": sub["market_id"], "market": sub["market"],
            "condition": {"type": sub["condition_type"], "value": float(sub["threshold"])},
            "active": sub["active"], "created_at": sub["created_at"], "description": service.describe(sub)}


@router.post("/auth/session", response_model=SessionOut, responses={401: {"description": "invalid or used code"}})
def create_session(body: CodeIn, request: Request, db: Annotated[Session, Depends(get_rw_db)]):
    """Exchange the one-time code from the bot's /login link for a session token (the code works once)."""
    out = service.exchange_code(db, body.code, request.app.state.settings.session_days)
    if out is None:
        raise ApiError(401, "invalid_code", "The sign-in link is invalid, expired or already used. Send /login to the "
                       "bot for a new one.")
    return {"token": out[0], "expires_at": out[1]}


@router.post("/auth/logout", status_code=204)
def logout(request: Request, user_id: Annotated[int, Depends(current_user)],
           db: Annotated[Session, Depends(get_rw_db)]) -> Response:
    service.end_session(db, request.headers.get("authorization", "").partition(" ")[2].strip())
    return Response(status_code=204)


@router.get("/alerts", response_model=list[AlertOut])
def list_alerts(user_id: Annotated[int, Depends(current_user)], db: Annotated[Session, Depends(get_rw_db)]):
    """My alert subscriptions."""
    return [_alert_out(x) for x in service.list_subscriptions(db, user_id)]


@router.post("/alerts", response_model=AlertOut, status_code=201,
             responses={400: {"description": "invalid"}, 404: {"description": "unknown series"},
                        409: {"description": "duplicate"}})
def create_alert(body: AlertIn, request: Request, user_id: Annotated[int, Depends(current_user)],
                 db: Annotated[Session, Depends(get_rw_db)]):
    """Create an alert: price at or above / at or below a value (₹ per quintal), or the 7-day forecast moving by at
    least a percentage from the last reported price."""
    market_or_404(db, body.market_id)
    series = series_or_404(db, body.commodity_id, body.market_id)
    try:
        new_id = service.add_subscription(db, user_id, series["id"], body.condition.type, body.condition.value,
                                          request.app.state.settings.max_alerts_per_user)
    except service.SubscriptionError as exc:
        status = 409 if exc.code == "subscription_exists" else 400
        raise ApiError(status, exc.code, str(exc)) from None
    return _alert_out(next(x for x in service.list_subscriptions(db, user_id) if x["id"] == new_id))


@router.delete("/alerts/{alert_id}", status_code=204, responses={404: {"description": "not found"}})
def delete_alert(alert_id: Annotated[int, Path(ge=1, le=2_147_483_647)],
                 user_id: Annotated[int, Depends(current_user)],
                 db: Annotated[Session, Depends(get_rw_db)]) -> Response:
    if not service.delete_subscription(db, user_id, alert_id):
        raise ApiError(404, "alert_not_found", f"You have no alert {alert_id}.")
    return Response(status_code=204)


@router.get("/alerts/history", response_model=list[NotificationOut])
def alert_history(user_id: Annotated[int, Depends(current_user)], db: Annotated[Session, Depends(get_rw_db)]):
    """Alert messages sent (or attempted) to me, newest first (kept 12 months)."""
    return service.history(db, user_id)


@router.delete("/me", status_code=204)
def delete_me(user_id: Annotated[int, Depends(current_user)], db: Annotated[Session, Depends(get_rw_db)]) -> Response:
    """Delete my account, sessions, alerts and alert history, immediately."""
    service.delete_user(db, user_id)
    return Response(status_code=204)
