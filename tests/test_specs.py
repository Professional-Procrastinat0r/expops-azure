import json
from importlib.metadata import entry_points
import pytest

from expops.storage.registry import get_mount_registry, get_metadata_store_registry
from expops.storage.secrets import StorageCredentialContext
from expops_azure.specs import parse_blob, parse_table, key


def test_installed_entry_points_and_wire_roundtrip():
    for group, name in (("expops.mounts", "azure-blob"), ("expops.metadata_stores", "azure-table")):
        assert len(list(entry_points(group=group, name=name))) == 1
    mount = parse_blob({"type": "azure-blob", "account_url": "https://example.blob.core.windows.net",
                        "container": "datasets", "prefix": "a/b"})
    assert get_mount_registry().parse_spec(json.loads(json.dumps(mount.to_dict()))) == mount
    metadata = parse_table({"type": "azure-table", "account_url": "https://example.table.core.windows.net",
                            "connection_string_ref": {"source": "environment", "name": "TEST_SECRET", "required": True}})
    assert get_metadata_store_registry().parse_spec(metadata.to_dict()) == metadata
    assert "do-not-serialize" not in json.dumps(metadata.to_dict())


@pytest.mark.parametrize("value", ["../escape", "/absolute", "a/../../b", "a\\b", "a//b", "a/./b", "a/\x00"])
def test_unsafe_blob_keys(value):
    with pytest.raises(ValueError):
        key(value)


@pytest.mark.parametrize("url", ["https://user:secret@host", "https://host?sig=secret", "http://remote", "https://host/#secret"])
def test_endpoints_cannot_hide_credentials(url):
    with pytest.raises(ValueError):
        parse_table({"type": "azure-table", "account_url": url})


def test_unknown_fields_and_bad_names_fail_offline():
    for raw in (
        {"type": "azure-blob", "account_url": "https://example", "container": "ab"},
        {"type": "azure-blob", "account_url": "https://example", "container": "valid", "secret": "hidden"},
    ):
        with pytest.raises(ValueError):
            parse_blob(raw)
    with pytest.raises(ValueError):
        parse_table({"type": "azure-table", "account_url": "https://example", "table": "bad-name"})


def test_plugin_contributes_distribution_and_secret_environment():
    from expops_azure.plugins import BLOB_PLUGIN, TABLE_PLUGIN
    assert ("expops_azure", "expops-azure==0.1.0") in BLOB_PLUGIN.runtime.dependencies
    assert "AZURE_CLIENT_SECRET" in TABLE_PLUGIN.runtime.worker_environment


def test_large_metadata_is_chunked_without_data_loss():
    from expops_azure.table import AzureTableMetadataStore
    store = object.__new__(AzureTableMetadataStore)
    store._partition = "test"
    record = {"long": "測試" * 30000}
    entity = store._entity("metric", ["run", "path"], record)
    assert entity["chunks"] > 1
    assert all(len(value) <= 60000 for key, value in entity.items() if key.startswith("data"))
    assert store._decode(entity)["value"] == record
    with pytest.raises(ValueError, match="900 KiB"):
        store._entity("metric", ["run", "path"], {"long": "x" * (950 * 1024)})


def test_explicit_service_principal_uses_supplied_environment(monkeypatch):
    import expops_azure.auth as auth
    captured = {}
    def credential(*args, **kwargs):
        captured.update(args=args, options=kwargs)
        return object()
    monkeypatch.setattr(auth, "ClientSecretCredential", credential)
    auth.identity(StorageCredentialContext.from_environment({
        "AZURE_TENANT_ID": "tenant", "AZURE_CLIENT_ID": "client", "AZURE_CLIENT_SECRET": "secret",
    }))
    assert captured["args"] == ("tenant", "client", "secret")
