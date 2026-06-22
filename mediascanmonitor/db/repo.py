"""Synchronous repository over the SQLModel tables (contract section 4).

A `SecretBox` is injected so the repo stores Fernet ciphertext and never leaks plaintext
into the DB. Plaintext is returned ONLY by `resolve_secret`. Path/extension normalization
lives at the schema boundary (`FolderCreate` validators, contract section 4), so
`create_folder` trusts the validated model; the only inline normalizer is `set_filetypes`,
which takes a raw `list[str]`.

Threading (contract conventions): each method opens and closes its **own** `Session` from the
factory — no `Session` is shared, stored on the instance, or held across calls — so the sync
methods are safe to run inside `asyncio.to_thread` (sub-plan 06). Error model: missing-id
mutations raise `KeyError`; deletes are idempotent; create surfaces `IntegrityError` (duplicate
name / dangling `server_id`).
"""

from collections.abc import Callable

from sqlmodel import Session, col, select

from mediascanmonitor.db.crypto import SecretBox
from mediascanmonitor.db.models import FileType, Folder, Server, Setting
from mediascanmonitor.db.schemas import FolderCreate, FolderUpdate, ServerCreate, ServerUpdate
from mediascanmonitor.normalize import normalize_extension


class Repo:
    def __init__(self, session_factory: Callable[[], Session], box: SecretBox) -> None:
        self._session_factory = session_factory
        self._box = box

    # servers ----------------------------------------------------------------
    def create_server(self, data: ServerCreate) -> Server:
        with self._session_factory() as session:
            server = Server(
                name=data.name,
                type=data.type,
                base_url=data.base_url,
                verify_tls=data.verify_tls,
                timeout_seconds=data.timeout_seconds,
                secret_encrypted=(
                    self._box.encrypt(data.secret) if data.secret is not None else None
                ),
                scan_mode=data.scan_mode,
                debounce_mode=data.debounce_mode,
                debounce_window_seconds=data.debounce_window_seconds,
                retry_attempts=data.retry_attempts,
                enabled=data.enabled,
                webhook_method=data.webhook_method,
                webhook_headers_json=data.webhook_headers_json,
                webhook_body_template=data.webhook_body_template,
            )
            session.add(server)
            session.commit()
            return server

    def get_server(self, server_id: int) -> Server | None:
        with self._session_factory() as session:
            return session.get(Server, server_id)

    def list_servers(self, *, enabled_only: bool = False) -> list[Server]:
        # Returns Server rows WITHOUT the `folders` relationship loaded — consumers walk
        # children via list_folders() (which force-loads filetypes). See contract section 4
        # "Loading model": accessing `.folders` on these detached rows would raise.
        with self._session_factory() as session:
            statement = select(Server)
            if enabled_only:
                statement = statement.where(col(Server.enabled).is_(True))
            return list(session.exec(statement).all())

    def create_server_with_folders(
        self, server_data: ServerCreate, folders: list[FolderCreate]
    ) -> Server:
        """Create a server and its folders in ONE transaction (all-or-nothing).

        Mirrors create_server + create_folder, but a single session/commit means a
        duplicate-name IntegrityError (or any failure) rolls back the whole thing —
        the UI's combined add-server form never leaves a half-built server behind.
        Schema validators already normalized each FolderCreate, so they're stored as-is.
        """
        with self._session_factory() as session:
            server = Server(
                name=server_data.name,
                type=server_data.type,
                base_url=server_data.base_url,
                verify_tls=server_data.verify_tls,
                timeout_seconds=server_data.timeout_seconds,
                secret_encrypted=(
                    self._box.encrypt(server_data.secret)
                    if server_data.secret is not None
                    else None
                ),
                scan_mode=server_data.scan_mode,
                debounce_mode=server_data.debounce_mode,
                debounce_window_seconds=server_data.debounce_window_seconds,
                retry_attempts=server_data.retry_attempts,
                enabled=server_data.enabled,
                webhook_method=server_data.webhook_method,
                webhook_headers_json=server_data.webhook_headers_json,
                webhook_body_template=server_data.webhook_body_template,
            )
            for folder_data in folders:
                folder = Folder(
                    path=folder_data.path,
                    library_id=folder_data.library_id,
                    enabled=folder_data.enabled,
                )
                for ext in folder_data.extensions:
                    folder.filetypes.append(FileType(extension=ext))
                server.folders.append(folder)
            session.add(server)
            session.commit()
            return server

    def update_server(self, server_id: int, data: ServerUpdate) -> Server:
        with self._session_factory() as session:
            server = session.get(Server, server_id)
            if server is None:
                raise KeyError(f"server {server_id} not found")
            fields = data.model_dump(exclude_unset=True)
            if "secret" in fields:
                secret = fields.pop("secret")
                server.secret_encrypted = self._box.encrypt(secret) if secret is not None else None
            for key, value in fields.items():
                setattr(server, key, value)
            session.add(server)
            session.commit()
            return server

    def delete_server(self, server_id: int) -> None:
        with self._session_factory() as session:
            server = session.get(Server, server_id)
            if server is None:
                return
            session.delete(server)
            session.commit()

    # folders ----------------------------------------------------------------
    def create_folder(self, server_id: int, data: FolderCreate) -> Folder:
        # FolderCreate already normalized the path (absolute) and extensions (deduped) at the
        # schema boundary, so the repo trusts and stores them as-is (contract section 4).
        with self._session_factory() as session:
            folder = Folder(
                server_id=server_id,
                path=data.path,
                library_id=data.library_id,
                enabled=data.enabled,
            )
            for ext in data.extensions:
                folder.filetypes.append(FileType(extension=ext))
            session.add(folder)
            session.commit()
            return folder

    def replace_folders(self, server_id: int, folders: list[FolderCreate]) -> None:
        """Replace ALL of a server's folders with ``folders``, in ONE transaction.

        The detail page edits the whole folder list and saves it wholesale (the same model as
        the new-server form): edited, added, and removed rows are reconciled by clearing the
        current folders (delete-orphan also drops their filetypes) and recreating from the
        submitted set. An empty list clears them. Raises KeyError if the server is gone.
        Folders carry no state beyond what the form captures, so the rebuild loses nothing.
        """
        with self._session_factory() as session:
            server = session.get(Server, server_id)
            if server is None:
                raise KeyError(f"server {server_id} not found")
            # clear() is the safe delete-orphan idiom (see update_folder); the rows appended
            # below are brand-new instances, never the cleared ones.
            server.folders.clear()
            for data in folders:
                folder = Folder(path=data.path, library_id=data.library_id, enabled=data.enabled)
                for ext in data.extensions:
                    folder.filetypes.append(FileType(extension=ext))
                server.folders.append(folder)
            session.commit()

    def list_folders(self, server_id: int) -> list[Folder]:
        with self._session_factory() as session:
            statement = select(Folder).where(col(Folder.server_id) == server_id)
            folders = list(session.exec(statement).all())
            for folder in folders:
                _ = folder.filetypes  # force-load while the session is open
            return folders

    def get_folder(self, folder_id: int) -> Folder | None:
        with self._session_factory() as session:
            folder = session.get(Folder, folder_id)
            if folder is not None:
                _ = folder.filetypes  # force-load while the session is open
            return folder

    def update_folder(self, folder_id: int, data: FolderUpdate) -> Folder:
        # exclude_unset tri-state mirrors update_server: an omitted field is left unchanged;
        # extensions present (a list, incl. []) replaces all FileType rows; explicit
        # None is a no-op.
        with self._session_factory() as session:
            folder = session.get(Folder, folder_id)
            if folder is None:
                raise KeyError(f"folder {folder_id} not found")
            fields = data.model_dump(exclude_unset=True)
            new_exts = fields.pop("extensions", None)
            for key, value in fields.items():
                setattr(folder, key, value)
            if new_exts is not None:
                # delete-orphan cascade deletes the removed rows AND empties the
                # in-memory collection. Do NOT session.delete() each child and append
                # into the same collection — the cascade re-touches deleted instances
                # and SQLAlchemy raises InvalidRequestError. clear() is the safe idiom.
                folder.filetypes.clear()
                # same normalize rule as set_filetypes (raw list[str]).
                normalized: list[str] = []
                for ext in new_exts:
                    norm = normalize_extension(ext)
                    if norm and norm not in normalized:
                        normalized.append(norm)
                for ext in normalized:
                    # folder_id set by the relationship
                    folder.filetypes.append(FileType(extension=ext))
            session.add(folder)
            session.commit()
            _ = folder.filetypes  # force-load while the session is open
            return folder

    def delete_folder(self, folder_id: int) -> None:
        with self._session_factory() as session:
            folder = session.get(Folder, folder_id)
            if folder is None:
                return
            session.delete(folder)
            session.commit()

    # filetypes --------------------------------------------------------------
    def set_filetypes(self, folder_id: int, extensions: list[str]) -> list[FileType]:
        with self._session_factory() as session:
            folder = session.get(Folder, folder_id)
            if folder is None:
                raise KeyError(f"folder {folder_id} not found")
            for existing in list(folder.filetypes):
                session.delete(existing)
            session.flush()
            # raw list[str]: normalize, drop empties, dedupe (order-preserving) — same rule
            # the FolderCreate validator applies on the create path (contract section 4).
            normalized: list[str] = []
            for ext in extensions:
                norm = normalize_extension(ext)
                if norm and norm not in normalized:
                    normalized.append(norm)
            new_types = [FileType(folder_id=folder_id, extension=ext) for ext in normalized]
            for filetype in new_types:
                session.add(filetype)
            session.commit()
            return new_types

    # secrets / settings -----------------------------------------------------
    def resolve_secret(self, server: Server) -> str | None:
        if server.secret_encrypted is None:
            return None
        return self._box.decrypt(server.secret_encrypted)

    def get_setting(self, key: str) -> str | None:
        with self._session_factory() as session:
            setting = session.get(Setting, key)
            return setting.value if setting is not None else None

    def set_setting(self, key: str, value: str) -> None:
        with self._session_factory() as session:
            setting = session.get(Setting, key)
            if setting is None:
                session.add(Setting(key=key, value=value))
            else:
                setting.value = value
                session.add(setting)
            session.commit()
