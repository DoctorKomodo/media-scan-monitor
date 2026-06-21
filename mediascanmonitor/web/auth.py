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
import contextlib
import os
import secrets
from pathlib import Path

import structlog
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from mediascanmonitor.db.repo import Repo
from mediascanmonitor.web.deps import get_repo, get_templates, require_page_auth
from mediascanmonitor.web.ratelimit import LoginRateLimiter

PASSWORD_HASH_KEY = "password_hash"
MUST_CHANGE_KEY = "must_change_password"

_hasher = PasswordHasher()  # library defaults
log = structlog.get_logger("web.auth")


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


def generate_password() -> str:
    """A strong, URL-safe random password (~24 chars) for first-run bootstrap."""
    return secrets.token_urlsafe(18)


def is_must_change(repo: Repo) -> bool:
    """True while the auto-generated bootstrap password still needs rotating."""
    return repo.get_setting(MUST_CHANGE_KEY) == "1"


def _resolve_initial_password_path() -> Path:
    """Where the auto-generated password file lives.

    Precedence: ``MSM_INITIAL_PASSWORD_FILE`` > ``<dir of MSM_DB_PATH>/initial_password.txt``
    > ``/config/initial_password.txt``.
    """
    explicit = os.environ.get("MSM_INITIAL_PASSWORD_FILE")
    if explicit:
        return Path(explicit)
    db_path = os.environ.get("MSM_DB_PATH")
    if db_path:
        return Path(db_path).parent / "initial_password.txt"
    return Path("/config/initial_password.txt")


def _write_initial_password_file(path: Path, value: str) -> None:
    """Write ``value`` to ``path`` owner-only (0600), creating the parent dir if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(value + "\n")
    path.chmod(0o600)  # enforce 0600 even if the file pre-existed with looser perms


def _generate_and_store(repo: Repo, path: Path) -> None:
    """Generate a strong password, write the retrievable 0600 file, then persist it as the
    live must-change credential. The file is written BEFORE the hash + flag so a write
    failure leaves any existing password intact (no lockout). NEVER logs the value (rule 5).
    """
    generated = generate_password()
    _write_initial_password_file(path, generated)
    repo.set_setting(MUST_CHANGE_KEY, "1")
    set_password(repo, generated)


def reset_to_generated_password(repo: Repo, path: Path) -> None:
    """Unconditionally regenerate the admin password (forgot-password recovery).

    Unlike ``bootstrap_password`` this does NOT check ``is_password_set`` — it overwrites
    whatever is currently set, writes the new value to ``path`` (0600), sets the must-change
    flag, and logs a distinct ``auth.password.reset`` event (path only, never the value).
    """
    _generate_and_store(repo, path)
    log.info("auth.password.reset", path=str(path))


def clear_initial_password(repo: Repo, path: Path) -> None:
    """Clear the must-change flag and best-effort delete the generated-password file."""
    repo.set_setting(MUST_CHANGE_KEY, "0")
    # best-effort: missing file is not an error
    with contextlib.suppress(OSError):
        path.unlink()


def bootstrap_password(repo: Repo, *, initial_password_path: Path | None = None) -> None:
    """Seed a first-run password (idempotent; runs at every web startup).

    Precedence: an existing password (no-op) > ``MSM_PASSWORD_FILE`` (path; contents
    whitespace-stripped) > ``MSM_PASSWORD`` > auto-generate. Env-supplied passwords are
    taken as final. When nothing is supplied, generate a strong random password, set the
    ``must_change_password`` flag, and write the value to ``initial_password_path`` (default
    ``_resolve_initial_password_path()``) at mode 0600. NEVER logs the value (rule 5).
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
        return
    # Nothing supplied → auto-generate and force a change on first login.
    target = (
        initial_password_path
        if initial_password_path is not None
        else _resolve_initial_password_path()
    )
    _generate_and_store(repo, target)
    log.info("auth.bootstrap.generated", path=str(target))


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


@router.get("/account/password", dependencies=[Depends(require_page_auth)])
async def change_password_page(
    request: Request,
    repo: Repo = Depends(get_repo),
    templates: Jinja2Templates = Depends(get_templates),
) -> Response:
    must_change = await asyncio.to_thread(is_must_change, repo)
    return templates.TemplateResponse(
        request, "change_password.html", {"error": None, "must_change": must_change}
    )


@router.post("/account/password", dependencies=[Depends(require_page_auth)])
async def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    repo: Repo = Depends(get_repo),
    templates: Jinja2Templates = Depends(get_templates),
) -> Response:
    must_change = await asyncio.to_thread(is_must_change, repo)

    def _render_error(message: str) -> Response:
        return templates.TemplateResponse(
            request,
            "change_password.html",
            {"error": message, "must_change": must_change},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if not await asyncio.to_thread(check_password, repo, current_password):
        return _render_error("Current password is incorrect.")
    if not new_password.strip():
        return _render_error("New password must not be empty.")
    if new_password != confirm_password:
        return _render_error("New password and confirmation do not match.")
    # no rebuild: auth is not engine config
    await asyncio.to_thread(set_password, repo, new_password)
    await asyncio.to_thread(clear_initial_password, repo, _resolve_initial_password_path())
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
