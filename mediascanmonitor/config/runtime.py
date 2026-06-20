"""Immutable runtime configuration snapshot, assembled from the DB.

The router and dispatcher (sub-plans 05/06) read this snapshot. Secrets are decrypted
into ``ServerRuntime.secret`` here (in memory only) — adapters receive plaintext tokens.
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from mediascanmonitor.db.models import DebounceMode, ScanMode, ServerType

if TYPE_CHECKING:
    from mediascanmonitor.db.repo import Repo


@dataclass(frozen=True, slots=True)
class ServerRuntime:
    server_id: int
    name: str
    type: ServerType
    base_url: str
    verify_tls: bool
    timeout_seconds: float
    secret: str | None = field(repr=False)   # decrypted plaintext; excluded from repr (invariant 3)
    scan_mode: ScanMode
    debounce_mode: DebounceMode
    debounce_window_seconds: int
    retry_attempts: int
    webhook_method: str | None
    webhook_headers_json: str | None
    webhook_body_template: str | None


@dataclass(frozen=True, slots=True)
class FolderRoute:
    server_id: int
    server_name: str
    path: str                  # watched folder root (normalized, no trailing slash)
    extensions: frozenset[str] # normalized; EMPTY SET MEANS "match all extensions"
    library_id: str | None
    scan_mode: ScanMode


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    watch_paths: frozenset[str]          # dedup union of enabled folder paths
    routes: tuple[FolderRoute, ...]      # one per enabled (server, folder)
    servers: dict[int, ServerRuntime]    # by server_id (enabled only)
    ignore_dirs: frozenset[str]
