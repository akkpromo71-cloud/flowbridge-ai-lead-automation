import secrets
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import delete, select, text

from app.db import Database
from app.deployment import ensure_database_mode
from app.domain.config import load_business_config
from app.intake import accept_lead
from app.logging_privacy import configure_private_logging
from app.models import Operator, Session, utcnow
from app.schemas import LeadInput, Login
from app.security import COOKIE, check_origin, digest, operator, throttle, verify_password
from app.settings import Settings


class RequestGuard:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request_id = secrets.token_hex(12)
        scope.setdefault("state", {})["request_id"] = request_id
        chunks, size = [], 0
        while True:
            event = await receive()
            if event["type"] == "http.disconnect":
                return
            size += len(event.get("body", b""))
            if size > 32768:
                return await JSONResponse(
                    {"detail": "Запрос слишком большой", "request_id": request_id}, status_code=413
                )(scope, receive, send)
            chunks.append(event.get("body", b""))
            if not event.get("more_body", False):
                break
        delivered = False

        async def buffered_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
            return await receive()

        async def headers_send(message):
            if message["type"] == "http.response.start":
                message.setdefault("headers", []).extend(
                    [
                        (b"x-request-id", request_id.encode()),
                        (b"x-content-type-options", b"nosniff"),
                        (b"x-frame-options", b"DENY"),
                        (b"referrer-policy", b"same-origin"),
                        (b"cache-control", b"no-store"),
                    ]
                )
            await send(message)

        await self.app(scope, buffered_receive, headers_send)


def create_app(settings: Settings | None = None, *, analysis_adapter=None) -> FastAPI:
    configure_private_logging()
    settings = settings or Settings()
    if analysis_adapter is not None and settings.mode != "controlled":
        raise RuntimeError("An injected evaluation provider is only allowed in controlled mode")
    database = Database(settings.database_url)
    config = load_business_config(settings.business_config)

    @asynccontextmanager
    async def lifespan(app):
        try:
            ensure_database_mode(database, settings.mode)
            yield
        finally:
            database.engine.dispose()

    app = FastAPI(
        title="AI Lead Automation",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings, app.state.db, app.state.business = settings, database, config
    app.state.analysis_adapter = analysis_adapter
    app.add_middleware(RequestGuard)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Never echo input values (passwords/contact details) in errors or logs.
        errors = [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
        return JSONResponse(
            {"detail": errors, "request_id": request.state.request_id}, status_code=422
        )

    @app.get("/health/live")
    def live():
        return {"status": "ok"}

    @app.get("/health/ready")
    def ready():
        with database.session() as db:
            db.execute(text("SELECT 1"))
        return {"status": "ready"}

    @app.get("/api/v1/public/config")
    def public_config():
        return {
            **config.public_dict(),
            "mode": settings.mode,
            "providers": {
                "analysis": "REAL" if settings.mode in {"controlled", "live"} else "SIMULATED",
                "draft": "REAL"
                if settings.mode in {"controlled", "live"} and settings.real_draft_enabled
                else "SIMULATED",
                "communication": "REAL" if settings.mode == "live" else "SIMULATED",
            },
        }

    @app.post("/api/v1/public/leads", status_code=202)
    def intake(payload: LeadInput, request: Request):
        check_origin(request)
        if settings.mode in {"demo", "controlled"}:
            # Interactive demo uses the same session/CSRF gate as the cabinet.
            operator(request)
        throttle(request, "intake", settings.request_limit_per_minute)
        return accept_lead(database, config, payload, request.headers.get("idempotency-key"))

    @app.post("/api/v1/auth/login")
    def login(payload: Login, request: Request, response: Response):
        check_origin(request)
        throttle(request, "login", 5)
        with database.session() as db:
            user = db.scalar(select(Operator).where(Operator.email == str(payload.email).lower()))
        if not verify_password(payload.password, user) or not user.active:
            raise HTTPException(401, "Неверный email или пароль")
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with database.session.begin() as db:
            db.execute(delete(Session).where(Session.expires_at < utcnow()))
            old_token = request.cookies.get(COOKIE)
            if old_token:
                db.execute(delete(Session).where(Session.token_hash == digest(old_token)))
            db.add(
                Session(
                    operator_id=user.id,
                    token_hash=digest(token),
                    csrf_token=csrf,
                    expires_at=utcnow() + timedelta(hours=settings.session_hours),
                )
            )
        response.set_cookie(
            COOKIE,
            token,
            max_age=settings.session_hours * 3600,
            httponly=True,
            secure=settings.secure_cookies,
            samesite="strict",
            path="/",
        )
        return {
            "operator": {"id": user.id, "email": user.email, "display_name": user.display_name},
            "csrf_token": csrf,
        }

    @app.get("/api/v1/auth/me")
    def me(request: Request, user: Operator = Depends(operator)):
        return {
            "operator": {"id": user.id, "email": user.email, "display_name": user.display_name},
            "csrf_token": request.state.csrf_token,
        }

    @app.post("/api/v1/auth/logout", status_code=204)
    def logout(request: Request, response: Response, user: Operator = Depends(operator)):
        with database.session.begin() as db:
            db.execute(delete(Session).where(Session.token_hash == digest(request.cookies[COOKIE])))
        response.delete_cookie(COOKIE, path="/")

    from app.admin import public_router, router
    from app.processing import router as processing_router

    app.include_router(public_router)
    app.include_router(router)
    app.include_router(processing_router)
    from fastapi.staticfiles import StaticFiles

    from app.settings import ROOT

    static_dir = ROOT / "frontend" / "dist"
    if static_dir.exists():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="frontend")
    return app
