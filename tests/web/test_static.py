"""Static assets are served from /static."""

import httpx


def test_app_css_served(client: httpx.Client) -> None:
    resp = client.get("/static/app.css")  # static is on the allow-list — no auth needed
    assert resp.status_code == 200
    assert "text/css" in resp.headers["content-type"]


def test_htmx_served(client: httpx.Client) -> None:
    resp = client.get("/static/htmx.min.js")
    assert resp.status_code == 200
    assert resp.content  # non-empty vendored bundle
