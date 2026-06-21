"""Authentication surface: Argon2 password hashing, Setting-backed storage, bootstrap.

All functions here are SYNCHRONOUS (Argon2 is CPU-bound, the Repo is sync SQLModel);
route handlers call them off the event loop via ``asyncio.to_thread`` (contract §C).
The password is stored as an Argon2 PHC string in the ``Setting`` table under
``password_hash`` — never in the clear. ``bootstrap_password`` seeds a first-run
password from the environment but NEVER logs the value (rule 5) and never overwrites a
password already set in the UI.

CSRF (contract §C): these POSTs are authenticated by the ``same_site="lax"`` session
cookie, which the browser withholds on cross-site POSTs — that is the deliberate CSRF
defense, so no CSRF token is used. The omission is intentional, not an oversight.
"""

import asyncio
import os
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from mediascanmonitor.db.repo import Repo
from mediascanmonitor.web.deps import get_repo, get_templates, require_page_auth
from mediascanmonitor.web.ratelimit import LoginRateLimiter

PASSWORD_HASH_KEY = "password_hash"

_hasher = PasswordHasher()  # library defaults


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(stored_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(stored_hash, password)
    except VerifyMismatchError, InvalidHashError, VerificationError:
        return False


def is_password_set(repo: Repo) -> bool:
    return repo.get_setting(PASSWORD_HASH_KEY) is not None


def set_password(repo: Repo, password: str) -> None:
    repo.set_setting(PASSWORD_HASH_KEY, hash_password(password))


def check_password(repo: Repo, password: str) -> bool:
    stored = repo.get_setting(PASSWORD_HASH_KEY)
    return stored is not None and verify_password(stored, password)


def bootstrap_password(repo: Repo) -> None:
    """Seed a first-run password from the environment (idempotent).

    Precedence: ``MSM_PASSWORD_FILE`` (a path; file contents, whitespace-stripped) then
    ``MSM_PASSWORD``. If a password is already set, return without touching it. If neither
    env source yields a non-empty value, do nothing — the setup screen handles first run.
    Never logs the value.
    """
    if is_password_set(repo):
        return
    value = ""
    file_path = os.environ.get("MSM_PASSWORD_FILE")
    if file_path:
        try:
            value = _read_secret_file(file_path)
        except OSError:
            value = ""
    if not value:
        value = (os.environ.get("MSM_PASSWORD") or "").strip()
    if value:
        set_password(repo, value)


def _read_secret_file(path: str) -> str:
    return Path(path).read_text(encoding="utf-8").strip()


router = APIRouter()


def _client_ip(request: Request) -> str:
    return request.client.host if request.client is not None else "unknown"


def _limiter(request: Request) -> LoginRateLimiter:
    limiter: LoginRateLimiter = request.app.state.limiter
    return limiter


@router.get("/login")
async def login_page(
    request: Request,
    templates: Jinja2Templates = Depends(get_templates),
) -> Response:
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/auth/login")
async def login(
    request: Request,
    password: str = Form(...),
    repo: Repo = Depends(get_repo),
    templates: Jinja2Templates = Depends(get_templates),
) -> Response:
    limiter = _limiter(request)
    key = _client_ip(request)
    if not limiter.allowed(key):
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "Too many attempts. Try again later."},
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )
    if await asyncio.to_thread(check_password, repo, password):
        limiter.reset(key)
        request.session["authed"] = True
        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    limiter.record_failure(key)
    return templates.TemplateResponse(
        request,
        "login.html",
        {"error": "Incorrect password."},
        status_code=status.HTTP_401_UNAUTHORIZED,
    )


@router.post("/auth/logout", dependencies=[Depends(require_page_auth)])
async def logout(request: Request) -> Response:
    request.session.clear()
    return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/auth/password", dependencies=[Depends(require_page_auth)])
async def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    repo: Repo = Depends(get_repo),
    templates: Jinja2Templates = Depends(get_templates),
) -> Response:
    if not await asyncio.to_thread(check_password, repo, current_password):
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "Current password is incorrect."},
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    # no rebuild: auth is not engine config
    await asyncio.to_thread(set_password, repo, new_password)
    return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/setup")
async def setup_page(
    request: Request,
    repo: Repo = Depends(get_repo),
    templates: Jinja2Templates = Depends(get_templates),
) -> Response:
    if await asyncio.to_thread(is_password_set, repo):
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(request, "setup.html", {"error": None})


@router.post("/setup")
async def setup(
    request: Request,
    password: str = Form(...),
    repo: Repo = Depends(get_repo),
    templates: Jinja2Templates = Depends(get_templates),
) -> Response:
    if await asyncio.to_thread(is_password_set, repo):
        # setup can never reset an existing password
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    if not password.strip():
        return templates.TemplateResponse(
            request,
            "setup.html",
            {"error": "Password must not be empty."},
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    await asyncio.to_thread(set_password, repo, password)
    request.session["authed"] = True
    return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
