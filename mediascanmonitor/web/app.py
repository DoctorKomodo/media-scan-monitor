"""FastAPI application factory (contract §A).

PURE: takes the already-built Repo / Engine / EventsBus, stores them on app.state, mounts
SessionMiddleware (signs the cookie with itsdangerous; same_site="lax" is the deliberate
CSRF defense — see contract §C), Jinja2 templates, the LoginRateLimiter, and every router,
then returns the app. No I/O, no env reads, no password bootstrap (serve_web does that,
sub-plan 03), so each test builds its own app cheaply.

The session stores exactly one key: "authed" (True once logged in). Later sub-plans append
their include_router(...) lines below — keep every line on merge (the one shared merge
point across Phase 3 sub-plans).
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from mediascanmonitor.db.repo import Repo
from mediascanmonitor.engine import Engine
from mediascanmonitor.observ.events_bus import EventsBus
from mediascanmonitor.web import auth
from mediascanmonitor.web.api import events as api_events
from mediascanmonitor.web.api import folders as api_folders
from mediascanmonitor.web.api import servers as api_servers
from mediascanmonitor.web.ratelimit import LoginRateLimiter

_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


def create_app(
    repo: Repo,
    engine: Engine,
    events_bus: EventsBus,
    *,
    session_secret: str,
) -> FastAPI:
    app = FastAPI(title="media-scan-monitor")

    app.state.repo = repo
    app.state.engine = engine
    app.state.events_bus = events_bus
    app.state.templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
    app.state.limiter = LoginRateLimiter()

    app.add_middleware(
        SessionMiddleware,
        secret_key=session_secret,
        same_site="lax",
        https_only=False,
    )

    app.include_router(auth.router)
    app.include_router(api_servers.router)
    app.include_router(api_folders.router)
    app.include_router(api_events.router)
    # sub-plan 03: app.include_router(system.router)
    # sub-plan 04: app.include_router(pages.router); app.mount("/static", StaticFiles(...))

    return app
