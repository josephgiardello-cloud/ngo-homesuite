import hmac
import logging
import sqlite3
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from ngo_homesuite.db.schema import SCHEMA_VERSION
from ngo_homesuite.domain.identity import Identity, Session
from ngo_homesuite.domain.services import Allocation, Entity, Services
from ngo_homesuite.domain.store import DomainError, TenantStore

log = logging.getLogger(__name__)


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginInput(Input):
    tenant: str = Field(min_length=1, max_length=40, pattern=r"^[a-z0-9_-]+$")
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=1024)


class DonorInput(Input):
    name: str
    email: str = ""
    phone: str = ""
    address: str = ""


class DonationInput(Input):
    donor_id: int = Field(gt=0)
    amount: str
    idempotency_key: str
    purpose: str = ""
    campaign_id: int | None = Field(default=None, gt=0)
    pledge_id: int | None = Field(default=None, gt=0)
    donated_on: str | None = None


class AllocationInput(Input):
    category: Literal["project", "payroll", "general"]
    project_id: int | None = Field(default=None, gt=0)
    amount: str


class AllocationsInput(Input):
    allocations: list[AllocationInput] = Field(max_length=100)


class ProjectInput(Input):
    name: str
    budget: str
    description: str = ""


class GrantInput(Input):
    project_id: int = Field(gt=0)
    name: str
    amount: str


class GrantActionInput(Input):
    action: Literal["approve", "pay", "cancel"]


class SpentInput(Input):
    amount: str


class UserInput(Input):
    username: str
    password: str = Field(min_length=10, max_length=1024)
    role: Literal["admin", "fundraiser", "viewer"]


class IDOutput(BaseModel):
    id: int


class OKOutput(BaseModel):
    ok: bool = True


def create_app(
    store: TenantStore, *, secure_cookies: bool = True, allowed_hosts: list[str] | None = None,
) -> FastAPI:
    services, identity = Services(store), Identity(Services(store))
    app = FastAPI(title="NGO HomeSuite", version="1.0.0")
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=allowed_hosts or ["localhost", "127.0.0.1", "testserver"],
    )
    app.state.services = services
    app.state.identity = identity
    static = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.middleware("http")
    async def browser_boundary(
        request: Request, call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        origin = request.headers.get("origin")
        expected = str(request.base_url).rstrip("/")
        if request.method not in {"GET", "HEAD", "OPTIONS"} and origin and origin != expected:
            return JSONResponse({"detail": "Cross-origin request denied"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; "
            "base-uri 'none'; form-action 'self'"
        )
        return response

    @app.exception_handler(DomainError)
    async def domain_error(_request: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=exc.status)

    @app.exception_handler(sqlite3.Error)
    async def database_error(_request: Request, exc: sqlite3.Error) -> JSONResponse:
        log.error("Database operation failed", exc_info=exc)
        return JSONResponse({"detail": "Database operation failed; no success has been confirmed"}, status_code=503)

    def current(request: Request) -> Session:
        session = identity.resolve(request.cookies.get("ngohs_session", ""))
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            if not hmac.compare_digest(request.headers.get("x-csrf-token", ""), session.csrf):
                raise DomainError("CSRF token required", 403)
        return session

    @app.get("/", include_in_schema=False)
    def home() -> FileResponse:
        return FileResponse(static / "index.html")

    @app.get("/health", response_model=OKOutput)
    def health() -> OKOutput:
        return OKOutput()

    @app.post("/api/v1/login")
    def login(body: LoginInput, response: Response) -> dict[str, str]:
        session = identity.login(body.tenant, body.username, body.password)
        response.set_cookie(
            "ngohs_session", session.token, httponly=True, secure=secure_cookies,
            samesite="strict", max_age=8 * 3600,
        )
        return {"csrf_token": session.csrf, "tenant": session.actor.tenant}

    @app.get("/api/v1/session")
    def session_info(session: Annotated[Session, Depends(current)]) -> dict[str, str | int]:
        with store.transaction(session.actor.tenant) as conn:
            user = services.authorize(conn, session.actor, "admin", "fundraiser", "viewer")
            row = conn.execute("SELECT value FROM app_meta WHERE key = 'schema_version'").fetchone()
            if row is None or int(row[0]) != SCHEMA_VERSION:
                raise DomainError("Database migration required", 503)
            return {"user_id": session.actor.user_id, "username": user["username"], "role": user["role"],
                    "tenant": session.actor.tenant, "csrf_token": session.csrf}

    @app.post("/api/v1/logout", response_model=OKOutput)
    def logout(response: Response, session: Annotated[Session, Depends(current)]) -> OKOutput:
        identity.logout(session)
        response.delete_cookie("ngohs_session")
        return OKOutput()

    @app.get("/api/v1/{entity}", response_model=list[dict[str, object]])
    def list_entities(
        entity: Literal["donors", "donations", "projects", "grants", "audit_log"],
        session: Annotated[Session, Depends(current)],
    ) -> list[Entity]:
        return services.list_entities(session.actor, entity)

    @app.post("/api/v1/donors", response_model=IDOutput, status_code=201)
    def donor(body: DonorInput, session: Annotated[Session, Depends(current)]) -> IDOutput:
        return IDOutput(id=services.create_donor(session.actor, **body.model_dump()))

    @app.post("/api/v1/donations", response_model=IDOutput, status_code=201)
    def donation(body: DonationInput, session: Annotated[Session, Depends(current)]) -> IDOutput:
        return IDOutput(id=services.record_donation(session.actor, **body.model_dump()))

    @app.put("/api/v1/donations/{donation_id}/allocations", response_model=OKOutput)
    def allocate(
        donation_id: int, body: AllocationsInput, session: Annotated[Session, Depends(current)],
    ) -> OKOutput:
        services.allocate_donation(session.actor, donation_id, [
            Allocation(a.category, a.project_id, a.amount) for a in body.allocations
        ])
        return OKOutput()

    @app.post("/api/v1/projects", response_model=IDOutput, status_code=201)
    def project(body: ProjectInput, session: Annotated[Session, Depends(current)]) -> IDOutput:
        return IDOutput(id=services.create_project(session.actor, **body.model_dump()))

    @app.put("/api/v1/projects/{project_id}/spent", response_model=OKOutput)
    def spent(project_id: int, body: SpentInput, session: Annotated[Session, Depends(current)]) -> OKOutput:
        services.set_project_spent(session.actor, project_id, body.amount)
        return OKOutput()

    @app.post("/api/v1/grants", response_model=IDOutput, status_code=201)
    def grant(body: GrantInput, session: Annotated[Session, Depends(current)]) -> IDOutput:
        return IDOutput(id=services.create_grant(session.actor, **body.model_dump()))

    @app.post("/api/v1/grants/{grant_id}/transition", response_model=OKOutput)
    def transition(
        grant_id: int, body: GrantActionInput, session: Annotated[Session, Depends(current)],
    ) -> OKOutput:
        services.transition_grant(session.actor, grant_id, body.action)
        return OKOutput()

    @app.post("/api/v1/users", response_model=IDOutput, status_code=201)
    def user(body: UserInput, session: Annotated[Session, Depends(current)]) -> IDOutput:
        return IDOutput(id=identity.create_user(session.actor.tenant, **body.model_dump(), actor=session.actor))

    return app
