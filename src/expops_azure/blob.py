"""Azure block blobs adapted to ExpOps' filesystem protocol.

Reads stream in bounded chunks. Writes spool locally (8 MiB memory threshold)
and upload only on successful context exit. Azure's block-blob commit publishes
the complete replacement; an aborted Python context never uploads its spool.
"""
from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache
import fnmatch
import io
import tempfile

from azure.core.exceptions import ResourceNotFoundError, ResourceExistsError
from azure.storage.blob import BlobServiceClient, ContentSettings
from expops.storage.interfaces.filesystem import MountFileSystem, CompleteWriteMount, RemovableMount

from .auth import client
from .specs import key


class _Reader(io.RawIOBase):
    def __init__(self, chunks):
        super().__init__()
        self._chunks = iter(chunks)
        self._pending = memoryview(b"")

    def readable(self):
        return True

    def readinto(self, buffer):
        if self.closed:
            raise ValueError("I/O on closed blob")
        while not self._pending:
            try:
                self._pending = memoryview(next(self._chunks))
            except StopIteration:
                return 0
        count = min(len(buffer), len(self._pending))
        buffer[:count] = self._pending[:count]
        self._pending = self._pending[count:]
        return count

    def close(self):
        close = getattr(self._chunks, "close", None)
        try:
            if close is not None:
                close()
        finally:
            self._pending = memoryview(b"")
            super().close()


def _matches(path, pattern):
    parts, patterns = path.split("/"), pattern.split("/")

    @lru_cache(None)
    def match(i, j):
        if j == len(patterns):
            return i == len(parts)
        if patterns[j] == "**":
            return match(i, j + 1) or (i < len(parts) and match(i + 1, j))
        return i < len(parts) and fnmatch.fnmatchcase(parts[i], patterns[j]) and match(i + 1, j + 1)
    return match(0, 0)


class AzureBlobFileSystem(MountFileSystem, CompleteWriteMount, RemovableMount):
    def __init__(self, spec, credentials):
        self.spec = spec
        self._closed = False
        self._service, self._credential = client(spec, credentials, BlobServiceClient)
        try:
            self._container = self._service.get_container_client(spec.container)
        except BaseException:
            self.close()
            raise

    def _check(self, value):
        if self._closed:
            raise ValueError("Azure Blob filesystem is closed")
        return key(value)

    def _name(self, value):
        self._check(value)
        return "/".join(filter(None, (self.spec.prefix, value)))

    def _blob(self, value):
        return self._container.get_blob_client(self._name(value))

    def _properties(self, value):
        try:
            return self._blob(value).get_blob_properties()
        except ResourceNotFoundError as exc:
            if exc.error_code in {"BlobNotFound", "ResourceNotFound"}:
                return None
            raise

    def _parents(self, value):
        parts = value.split("/")
        for i in range(1, len(parts)):
            if self._properties("/".join(parts[:i])) is not None:
                raise NotADirectoryError("A parent key is a blob")

    def _descendants(self, value):
        prefix = self._name(value).rstrip("/")
        prefix = prefix + "/" if prefix else ""
        return self._container.list_blobs(name_starts_with=prefix)

    def stat(self, value):
        self._check(value)
        self._parents(value)
        if value:
            properties = self._properties(value)
            if properties is not None:
                return {"type": "file", "size": properties.size,
                        "mtime": properties.last_modified.timestamp(), "etag": properties.etag}
        # Root means the virtual prefix directory; verify the container exists.
        if not value:
            self._container.get_container_properties()
            return {"type": "directory"}
        if next(iter(self._descendants(value)), None) is not None:
            return {"type": "directory"}
        raise FileNotFoundError(value)

    def _directory(self, value):
        if self.stat(value)["type"] != "directory":
            raise NotADirectoryError(value)

    def iterdir(self, value):
        self._directory(value)
        physical = self._name(value).rstrip("/")
        physical = physical + "/" if physical else ""
        children = set()
        for item in self._descendants(value):
            relative = item.name[len(physical):]
            first = relative.split("/", 1)[0]
            if first:
                child = "/".join(filter(None, (value, first)))
                key(child, empty=False)
                children.add(child)
        return tuple(sorted(children))

    def glob(self, value, pattern):
        self._directory(value)
        key(pattern, empty=False)
        physical = self._name(value).rstrip("/")
        physical = physical + "/" if physical else ""
        candidates = set()
        for item in self._descendants(value):
            relative = item.name[len(physical):]
            key(relative, empty=False)
            segments = relative.split("/")
            candidates.update("/".join(segments[:i]) for i in range(1, len(segments) + 1))
        return tuple("/".join(filter(None, (value, entry))) for entry in sorted(candidates) if _matches(entry, pattern))

    @contextmanager
    def open(self, value, mode="rb", *, encoding=None, errors=None, newline=None, content_type=None):
        self._check(value)
        key(value, empty=False)
        if mode not in {"r", "rt", "rb", "w", "wt", "wb", "x", "xt", "xb"}:
            raise ValueError("Azure Blob supports r/w/x modes, without append or update")
        binary = "b" in mode
        if binary and any(option is not None for option in (encoding, errors, newline)):
            raise ValueError("Binary streams do not accept text options")
        self._parents(value)
        if mode.startswith("r"):
            if self.stat(value)["type"] != "file":
                raise IsADirectoryError(value)
            try:
                downloader = self._blob(value).download_blob(max_concurrency=1)
                stream = io.BufferedReader(_Reader(downloader.chunks()))
                if not binary:
                    stream = io.TextIOWrapper(stream, encoding=encoding or "utf-8", errors=errors, newline=newline)
                with stream:
                    yield stream
            except ResourceNotFoundError as exc:
                if exc.error_code == "BlobNotFound":
                    raise FileNotFoundError(value) from exc
                raise
            return
        try:
            existing = self.stat(value)
        except FileNotFoundError:
            existing = None
        if existing and existing["type"] == "directory":
            raise IsADirectoryError(value)
        if existing and mode.startswith("x"):
            raise FileExistsError(value)
        with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b") as spool:
            stream = spool if binary else io.TextIOWrapper(spool, encoding=encoding or "utf-8", errors=errors, newline=newline)
            try:
                yield stream
                stream.flush()
                spool.seek(0)
                options = {"overwrite": not mode.startswith("x"), "max_concurrency": 1}
                if content_type is not None:
                    options["content_settings"] = ContentSettings(content_type=content_type)
                try:
                    self._blob(value).upload_blob(spool, **options)
                except ResourceExistsError as exc:
                    if mode.startswith("x"):
                        raise FileExistsError(value) from exc
                    raise
            finally:
                if not binary:
                    if not stream.closed:
                        stream.detach()

    def publish(self, value, mode, **options):
        if mode not in {"w", "wb", "wt", "x", "xb", "xt"}:
            raise ValueError("Publication requires write or exclusive mode")
        return self.open(value, mode, **options)

    def remove_file(self, value):
        key(value, empty=False)
        if self.stat(value)["type"] != "file":
            raise IsADirectoryError(value)
        self._blob(value).delete_blob()

    def close(self):
        if not self._closed:
            self._closed = True
            try:
                self._service.close()
            finally:
                if self._credential is not None:
                    self._credential.close()

    def __getstate__(self):
        raise TypeError("Transport AzureBlobSpec, not a live filesystem")
