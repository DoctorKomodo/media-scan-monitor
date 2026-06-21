"""Auth guards: API → 401, HTML page → 303 /login; no-password → setup redirect/401."""

import pytest
from fastapi import Depends, FastAPI, Request
from starlette.middleware.sessions import SessionMiddleware
from starlette.testclient import TestClient

from mediascanmonitor.db.repo import Repo
from mediascanmonitor.web.auth import MUST_CHANGE_KEY, set_password
from mediascanmonitor.web.deps import require_api_auth, require_page_auth


def _probe_app(repo: Repo) -> FastAPI:
    app = FastAPI()
    app.state.repo = repo
    app.add_middleware(SessionMiddleware, secret_key="probe-secret", same_site="lax")

    @app.get("/probe/api", dependencies=[Depends(require_api_auth)])
    async def api_route() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/probe/page", dependencies=[Depends(require_page_auth)])
    async def page_route() -> dict[str, bool]:
        return {"ok": True}

    return app


def test_api_guard_401_when_unauthenticated(repo: Repo) -> None:
    set_password(repo, "pw")  # password IS set, just not logged in
    client = TestClient(_probe_app(repo))
    resp = client.get("/probe/api")
    assert resp.status_code == 401


def test_page_guard_303_to_login_when_unauthenticated(repo: Repo) -> None:
    set_password(repo, "pw")
    client = TestClient(_probe_app(repo))
    resp = client.get("/probe/page", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_api_guard_setup_required_when_no_password(repo: Repo) -> None:
    client = TestClient(_probe_app(repo))  # no password set
    resp = client.get("/probe/api")
    assert resp.status_code == 401
    assert resp.json()["detail"] == "setup required"


def test_page_guard_redirects_to_setup_when_no_password(repo: Repo) -> None:
    client = TestClient(_probe_app(repo))  # no password set
    resp = client.get("/probe/page", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/setup"


@pytest.mark.parametrize("path", ["/probe/api", "/probe/page"])
def test_guards_allow_when_authenticated(repo: Repo, path: str) -> None:
    set_password(repo, "pw")
    app = _probe_app(repo)

    # add a tiny login endpoint that sets the session, to authenticate the probe client
    @app.post("/login-probe")
    async def login_probe(request: Request) -> dict[str, bool]:
        request.session["authed"] = True
        return {"ok": True}

    client = TestClient(app)
    client.post("/login-probe")
    resp = client.get(path)
    assert resp.status_code == 200


def _must_change_client(app, repo) -> TestClient:  # type: ignore[no-untyped-def]
    set_password(repo, "pw")
    repo.set_setting(MUST_CHANGE_KEY, "1")
    client = TestClient(app)
    resp = client.post("/auth/login", data={"password": "pw"}, follow_redirects=False)
    assert resp.status_code == 303
    return client


def test_must_change_redirects_pages_to_account_password(app, repo) -> None:  # type: ignore[no-untyped-def]
    client = _must_change_client(app, repo)
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/account/password"


def test_must_change_allows_the_change_page_itself(app, repo) -> None:  # type: ignore[no-untyped-def]
    client = _must_change_client(app, repo)
    resp = client.get("/account/password", follow_redirects=False)
    assert resp.status_code == 200  # not redirected — it is on the allowlist
    # the full nav is hidden during forced change, but a logout escape is offered
    assert 'action="/auth/logout"' in resp.text


def test_must_change_allows_account_password_subpath_not_sibling(app, repo) -> None:  # type: ignore[no-untyped-def]
    # The allowlist matches whole paths / sub-paths, never a bare string prefix: a
    # sibling like /account/password-reset must still be gated (redirected), not allowed.
    from mediascanmonitor.web.deps import _on_must_change_allowlist

    assert _on_must_change_allowlist("/account/password") is True
    assert _on_must_change_allowlist("/account/password/confirm") is True
    assert _on_must_change_allowlist("/auth/logout") is True
    assert _on_must_change_allowlist("/account/password-reset") is False
    assert _on_must_change_allowlist("/auth/logout-all") is False


def test_must_change_allows_logout(app, repo) -> None:  # type: ignore[no-untyped-def]
    client = _must_change_client(app, repo)
    resp = client.post("/auth/logout", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"  # logout, not the change page


def test_must_change_returns_403_on_api(app, repo) -> None:  # type: ignore[no-untyped-def]
    client = _must_change_client(app, repo)
    resp = client.get("/api/status")
    assert resp.status_code == 403


def test_cleared_flag_restores_normal_access(app, repo) -> None:  # type: ignore[no-untyped-def]
    client = _must_change_client(app, repo)
    repo.set_setting(MUST_CHANGE_KEY, "0")
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 200
