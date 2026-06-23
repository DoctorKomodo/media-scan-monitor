"""Library-discovery endpoints: unsaved-config + stored, success / not-supported / error."""

import httpx
import respx

from mediascanmonitor.db.models import ServerType
from mediascanmonitor.db.schemas import ServerCreate

ABS_BASE = "http://abs:13378"


def _abs_form() -> dict[str, str]:
    return {
        "type": "audiobookshelf",
        "base_url": ABS_BASE,
        "secret": "tok",
        "timeout_seconds": "10",
    }


@respx.mock
def test_libraries_unsaved_renders_options(auth_client: httpx.Client) -> None:
    respx.get(f"{ABS_BASE}/api/libraries").mock(
        return_value=httpx.Response(
            200, json={"libraries": [{"id": "lib_abc", "name": "Audiobooks"}]}
        )
    )
    resp = auth_client.post("/ui/servers/libraries", data=_abs_form())
    assert resp.status_code == 200
    assert "Audiobooks" in resp.text
    assert "lib_abc" in resp.text


def test_libraries_unsaved_not_supported_for_webhook(auth_client: httpx.Client) -> None:
    resp = auth_client.post(
        "/ui/servers/libraries", data={"type": "webhook", "base_url": "", "secret": ""}
    )
    assert resp.status_code == 200
    assert "no libraries" in resp.text.lower()


@respx.mock
def test_libraries_unsaved_renders_error_on_401(auth_client: httpx.Client) -> None:
    respx.get(f"{ABS_BASE}/api/libraries").mock(return_value=httpx.Response(401))
    resp = auth_client.post("/ui/servers/libraries", data=_abs_form())
    assert resp.status_code == 200
    assert "HTTP 401" in resp.text


@respx.mock
def test_libraries_stored_uses_saved_secret(auth_client: httpx.Client, repo) -> None:  # type: ignore[no-untyped-def]
    server = repo.create_server(
        ServerCreate(
            name="ABS", type=ServerType.audiobookshelf, base_url=ABS_BASE, secret="stored-tok"
        )
    )
    route = respx.get(f"{ABS_BASE}/api/libraries").mock(
        return_value=httpx.Response(
            200, json={"libraries": [{"id": "lib_z", "name": "Stored Lib"}]}
        )
    )
    resp = auth_client.post(f"/ui/servers/{server.id}/libraries", data={"secret": ""})
    assert resp.status_code == 200
    assert "Stored Lib" in resp.text
    assert route.calls.last.request.headers["Authorization"] == "Bearer stored-tok"


@respx.mock
def test_libraries_stored_typed_token_overrides_stored(auth_client: httpx.Client, repo) -> None:  # type: ignore[no-untyped-def]
    # The deliberate enhancement over ui_test_server: a freshly-typed token in the form wins.
    server = repo.create_server(
        ServerCreate(
            name="ABS", type=ServerType.audiobookshelf, base_url=ABS_BASE, secret="stored-tok"
        )
    )
    route = respx.get(f"{ABS_BASE}/api/libraries").mock(
        return_value=httpx.Response(200, json={"libraries": []})
    )
    auth_client.post(f"/ui/servers/{server.id}/libraries", data={"secret": "typed-override"})
    assert route.calls.last.request.headers["Authorization"] == "Bearer typed-override"


def test_libraries_stored_404_for_missing(auth_client: httpx.Client) -> None:
    resp = auth_client.post("/ui/servers/9999/libraries", data={"secret": ""})
    assert resp.status_code == 200
    assert "not found" in resp.text.lower()


def test_parse_folder_rows_reads_library_name() -> None:
    # The hidden folder-<i>-library_name field round-trips into FolderCreate; empty → None.
    from starlette.datastructures import FormData

    from mediascanmonitor.web.pages import _parse_folder_rows

    named = _parse_folder_rows(
        FormData(
            [
                ("folder-0-path", "/data/abs"),
                ("folder-0-library_id", "lib_x"),
                ("folder-0-library_name", "Audiobooks"),
            ]
        )
    )
    assert (named[0].library_id, named[0].library_name) == ("lib_x", "Audiobooks")

    blank = _parse_folder_rows(
        FormData(
            [
                ("folder-0-path", "/data/tv"),
                ("folder-0-library_id", "2"),
                ("folder-0-library_name", ""),
            ]
        )
    )
    assert blank[0].library_name is None
