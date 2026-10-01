import hashlib
import secrets
from datetime import timedelta

from fastapi import HTTPException, Request
from pwdlib import PasswordHash
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from app.models import Operator, RateLimit, Session, utcnow

password_hasher = PasswordHash.recommended()
_DUMMY_HASH = password_hasher.hash("never-an-operator-password")
COOKIE = "lead_session"


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def throttle(request: Request, scope: str, limit: int):
    now = utcnow()
    ip = request.client.host if request.client else "unknown"
    # Proxy headers are intentionally not trusted. Configure the trusted proxy explicitly at deployment.
    key = digest(f"{scope}:{ip}:{now:%Y%m%d%H%M}")
    with request.app.state.db.session.begin() as db:
        count = db.execute(
            insert(RateLimit)
            .values(key=key, count=1, expires_at=now + timedelta(minutes=2))
            .on_conflict_do_update(index_elements=["key"], set_={"count": RateLimit.count + 1})
            .returning(RateLimit.count)
        ).scalar_one()
        db.execute(delete(RateLimit).where(RateLimit.expires_at < now))
    if count > limit:
        raise HTTPException(
            429, "Слишком много попыток. Подождите минуту.", headers={"Retry-After": "60"}
        )


def check_origin(request: Request):
    origin = request.headers.get("origin")
    if origin and origin not in request.app.state.settings.origins:
        raise HTTPException(403, "Недопустимый источник запроса")


def operator(request: Request) -> Operator:
    token = request.cookies.get(COOKIE, "")
    if not token:
        raise HTTPException(401, "Войдите в кабинет")
    with request.app.state.db.session() as db:
        session = db.scalar(
            select(Session).where(
                Session.token_hash == digest(token), Session.expires_at > utcnow()
            )
        )
        user = db.get(Operator, session.operator_id) if session else None
        if not session or not user or not user.active:
            raise HTTPException(401, "Сессия истекла. Войдите снова")
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            check_origin(request)
            csrf = request.headers.get("x-csrf-token", "")
            if not csrf or not secrets.compare_digest(csrf, session.csrf_token):
                raise HTTPException(403, "Недействительная защита запроса")
        request.state.csrf_token = session.csrf_token
        return user


def service_auth(request: Request):
    expected = request.app.state.settings.internal_token.get_secret_value()
    actual = request.headers.get("x-internal-token", "")
    if len(expected) < 32 or not secrets.compare_digest(expected, actual):
        raise HTTPException(401, "Service authentication required")


def verify_password(password: str, user: Operator | None) -> bool:
    return (
        password_hasher.verify(password, user.password_hash if user else _DUMMY_HASH)
        and user is not None
    )
