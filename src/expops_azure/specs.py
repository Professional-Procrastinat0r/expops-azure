"""Pure, secret-free configuration and wire specifications."""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Mapping, Any
from urllib.parse import urlsplit

from expops.storage.secrets import SecretRef, secret_ref_from_dict


def key(value: str, *, empty: bool = True) -> str:
    if not isinstance(value, str) or (not value and not empty):
        raise ValueError("Expected a relative file key")
    if value and (value.startswith("/") or "\\" in value or "\x00" in value
                  or any(part in {"", ".", ".."} for part in value.split("/"))):
        raise ValueError("File key must stay within its mount")
    return value


def endpoint(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("account_url must be a URL without credentials")
    parts = urlsplit(value)
    if (not parts.hostname or parts.username or parts.password or parts.query or parts.fragment
        or (parts.scheme != "https" and not (
            parts.scheme == "http" and parts.hostname in {"localhost", "127.0.0.1", "::1"}))):
        raise ValueError("account_url requires HTTPS (HTTP only for loopback Azurite), without credentials or query")
    return value.rstrip("/")


def _parse(raw: Mapping[str, Any], backend: str, fields: set[str]):
    if not isinstance(raw, Mapping) or set(raw) - fields - {"type", "account_url", "connection_string_ref"}:
        raise ValueError(f"Unknown {backend} configuration fields")
    if raw.get("type") != backend:
        raise ValueError("Unexpected Azure backend type")
    auth = raw.get("connection_string_ref")
    if auth is not None and not isinstance(auth, Mapping):
        raise ValueError("connection_string_ref must be a secret reference")
    return endpoint(raw.get("account_url")), secret_ref_from_dict(auth) if auth is not None else None


@dataclass(frozen=True)
class AzureBlobSpec:
    account_url: str
    container: str
    prefix: str = ""
    connection_string_ref: SecretRef | None = None

    def __post_init__(self):
        endpoint(self.account_url)
        if not isinstance(self.container, str) or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])", self.container) or "--" in self.container:
            raise ValueError("container must be a valid Azure container name")
        key(self.prefix)

    def to_dict(self):
        result = {"type": "azure-blob", "account_url": self.account_url,
                  "container": self.container, "prefix": self.prefix}
        if self.connection_string_ref is not None:
            result["connection_string_ref"] = self.connection_string_ref.to_dict()
        return result


def parse_blob(raw):
    url, reference = _parse(raw, "azure-blob", {"container", "prefix"})
    return AzureBlobSpec(url, raw.get("container"), raw.get("prefix", ""), reference)


@dataclass(frozen=True)
class AzureTableSpec:
    account_url: str
    table: str = "ExpOpsMetadata"
    connection_string_ref: SecretRef | None = None

    def __post_init__(self):
        endpoint(self.account_url)
        if not isinstance(self.table, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9]{2,62}", self.table):
            raise ValueError("table must be 3-63 alphanumeric characters starting with a letter")

    def to_dict(self):
        result = {"type": "azure-table", "account_url": self.account_url, "table": self.table}
        if self.connection_string_ref is not None:
            result["connection_string_ref"] = self.connection_string_ref.to_dict()
        return result


def parse_table(raw):
    url, reference = _parse(raw, "azure-table", {"table"})
    return AzureTableSpec(url, raw.get("table", "ExpOpsMetadata"), reference)
