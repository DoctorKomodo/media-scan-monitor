"""SQLModel persistence models and enums (frozen interface contract, sections 1-2).

`FileType` is its own table so the `Server >- Folder >- FileType` cascade delete can be
tested explicitly. Secrets live only as Fernet ciphertext in `Server.secret_encrypted`;
plaintext never touches a model field.

Forward references (``list[Folder]``, ``list[FileType]``) are left UNQUOTED and this module —
like the rest of the package — uses no ``from __future__ import annotations``: on Python 3.14
PEP 649 defers annotation evaluation, so SQLModel/SQLAlchemy resolve relationship targets to the
real classes when the mappers configure. The PEP 563 future import would instead stringize the
annotation to ``"list['Folder']"`` and break mapper configuration.
"""

from enum import StrEnum

from sqlmodel import Field, Relationship, SQLModel


class ServerType(StrEnum):
    webhook = "webhook"
    plex = "plex"
    emby = "emby"
    jellyfin = "jellyfin"
    audiobookshelf = "audiobookshelf"


class ScanMode(StrEnum):
    targeted = "targeted"  # backend scans a specific folder path (Plex ?path=)
    library = "library"  # backend refreshes a whole library id


class DebounceMode(StrEnum):
    off = "off"  # dispatch every matching event
    trailing = "trailing"  # collapse a burst per (server_id, scan_key) after a window


class Server(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    type: ServerType
    base_url: str = ""  # "" for pure webhook with full URL in template
    verify_tls: bool = True
    timeout_seconds: float = 10.0
    secret_encrypted: str | None = None  # Fernet token; never the plaintext
    scan_mode: ScanMode = ScanMode.targeted
    debounce_mode: DebounceMode = DebounceMode.trailing
    debounce_window_seconds: int = 30
    retry_attempts: int = 3  # total tries (1 = no retry)
    enabled: bool = True
    # webhook-only (unused until Phase 2, defined now to avoid a Phase 2 migration):
    webhook_method: str | None = None
    webhook_headers_json: str | None = None
    webhook_body_template: str | None = None
    folders: list[Folder] = Relationship(
        back_populates="server",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class Folder(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    server_id: int = Field(foreign_key="server.id", ondelete="CASCADE", index=True)
    path: str  # host path watched, e.g. /data/media/tvseries
    library_id: str | None = None  # backend section/library id; None for webhook
    enabled: bool = True
    server: Server = Relationship(back_populates="folders")
    filetypes: list[FileType] = Relationship(
        back_populates="folder",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class FileType(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    folder_id: int = Field(foreign_key="folder.id", ondelete="CASCADE", index=True)
    extension: str  # normalized: lowercase, no leading dot
    folder: Folder = Relationship(back_populates="filetypes")


class Setting(SQLModel, table=True):
    key: str = Field(primary_key=True)  # e.g. "password_hash", "inotify_gate"
    value: str
