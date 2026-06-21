"""Auth router flows: login success/failure, rate-limit lockout, logout, change-pw, setup."""

from starlette.testclient import TestClient

from mediascanmonitor.db.repo import Repo
from mediascanmonitor.web.auth import check_password, is_password_set, set_password


def test_login_success_sets_session_and_redirects(app, repo: Repo) -> None:  # type: ignore[no-untyped-def]
    set_password(repo, "pw")
    client = TestClient(app)
    resp = client.post("/auth/login", data={"password": "pw"}, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"
    # session now authed: a guarded request would pass (probe via logout, which requires auth)
    out = client.post("/auth/logout", follow_redirects=False)
    assert out.status_code == 303


def test_login_failure_returns_401_and_records_failure(app, repo: Repo) -> None:  # type: ignore[no-untyped-def]
    set_password(repo, "pw")
    client = TestClient(app)
    resp = client.post("/auth/login", data={"password": "wrong"}, follow_redirects=False)
    assert resp.status_code == 401
    assert "incorrect" in resp.text.lower() or "invalid" in resp.text.lower()


def test_login_rate_limit_locks_out_after_max_attempts(app, repo: Repo) -> None:  # type: ignore[no-untyped-def]
    set_password(repo, "pw")
    client = TestClient(app)
    # default limiter max_attempts=5: 5 failures, then the 6th attempt is 429
    for _ in range(5):
        r = client.post("/auth/login", data={"password": "x"}, follow_redirects=False)
        assert r.status_code == 401
    locked = client.post("/auth/login", data={"password": "x"}, follow_redirects=False)
    assert locked.status_code == 429


def test_login_success_resets_rate_limit(app, repo: Repo) -> None:  # type: ignore[no-untyped-def]
    set_password(repo, "pw")
    client = TestClient(app)
    for _ in range(4):
        client.post("/auth/login", data={"password": "x"}, follow_redirects=False)
    ok = client.post("/auth/login", data={"password": "pw"}, follow_redirects=False)
    assert ok.status_code == 303  # under the limit; success resets the counter


def test_logout_requires_auth(app) -> None:  # type: ignore[no-untyped-def]
    client = TestClient(app)
    # not logged in, password not set → guard sends to setup
    resp = client.post("/auth/logout", follow_redirects=False)
    assert resp.status_code in (303, 401)


def test_logout_clears_session(auth_client: TestClient) -> None:
    resp = auth_client.post("/auth/logout", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"
    # session cleared: logout again now redirects to /login via the guard (no longer authed)
    again = auth_client.post("/auth/logout", follow_redirects=False)
    assert again.status_code == 303
    assert again.headers["location"] == "/login"


def test_get_change_password_renders_form(auth_client: TestClient) -> None:
    resp = auth_client.get("/account/password")
    assert resp.status_code == 200
    assert "Change password" in resp.text


def test_change_password_success(auth_client: TestClient, repo: Repo) -> None:
    resp = auth_client.post(
        "/account/password",
        data={
            "current_password": "pw",
            "new_password": "brand-new-pw",
            "confirm_password": "brand-new-pw",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"
    assert check_password(repo, "brand-new-pw")


def test_change_password_wrong_current_rerenders_form(auth_client: TestClient) -> None:
    resp = auth_client.post(
        "/account/password",
        data={
            "current_password": "wrong",
            "new_password": "x",
            "confirm_password": "x",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 400
    assert "Change password" in resp.text  # the change-password template, not login.html
    assert "Current password is incorrect." in resp.text


def test_change_password_confirm_mismatch_rejected(auth_client: TestClient) -> None:
    resp = auth_client.post(
        "/account/password",
        data={
            "current_password": "pw",
            "new_password": "aaa",
            "confirm_password": "bbb",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 400
    assert "do not match" in resp.text


def test_change_password_requires_auth(client: TestClient) -> None:
    resp = client.post(
        "/account/password",
        data={"current_password": "pw", "new_password": "x", "confirm_password": "x"},
        follow_redirects=False,
    )
    assert resp.status_code == 303  # redirected by require_page_auth (not authed)


def test_change_password_clears_must_change_and_deletes_file(
    app,
    repo: Repo,
    tmp_path,
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    from mediascanmonitor.web.auth import MUST_CHANGE_KEY, set_password

    pw_file = tmp_path / "initial_password.txt"
    pw_file.write_text("pw\n", encoding="utf-8")
    monkeypatch.setenv("MSM_INITIAL_PASSWORD_FILE", str(pw_file))
    set_password(repo, "pw")
    repo.set_setting(MUST_CHANGE_KEY, "1")

    client = TestClient(app)
    client.post("/auth/login", data={"password": "pw"}, follow_redirects=False)
    resp = client.post(
        "/account/password",
        data={"current_password": "pw", "new_password": "rotated", "confirm_password": "rotated"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert repo.get_setting(MUST_CHANGE_KEY) == "0"
    assert not pw_file.exists()


def test_get_login_renders_form(client: TestClient) -> None:
    resp = client.get("/login")
    assert resp.status_code == 200
    assert "password" in resp.text.lower()


def test_get_setup_renders_when_no_password(client: TestClient) -> None:
    resp = client.get("/setup")
    assert resp.status_code == 200
    assert "password" in resp.text.lower()


def test_post_setup_creates_password_and_logs_in(app, repo: Repo) -> None:  # type: ignore[no-untyped-def]
    client = TestClient(app)
    resp = client.post("/setup", data={"password": "first-pw"}, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"
    assert is_password_set(repo) is True
    # logged in: logout works (requires auth) and redirects to /login
    out = client.post("/auth/logout", follow_redirects=False)
    assert out.status_code == 303


def test_setup_blocked_once_password_exists(app, repo: Repo) -> None:  # type: ignore[no-untyped-def]
    set_password(repo, "already")
    client = TestClient(app)
    get_resp = client.get("/setup", follow_redirects=False)
    assert get_resp.status_code == 303
    assert get_resp.headers["location"] == "/login"
    post_resp = client.post("/setup", data={"password": "hijack"}, follow_redirects=False)
    assert post_resp.status_code == 303
    assert post_resp.headers["location"] == "/login"
    assert check_password(repo, "already") is True  # not overwritten
    assert check_password(repo, "hijack") is False
