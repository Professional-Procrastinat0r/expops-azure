"""Installed entry-point descriptors; no manual registration is required."""
from expops.storage.registry import (
    BackendRuntimeRequirements, MetadataStorePlugin, MountPlugin, MountCapabilities,
)
from .auth import AZURE_ENVIRONMENT
from .specs import AzureBlobSpec, AzureTableSpec, parse_blob, parse_table


def create_blob(spec, credentials):
    from .blob import AzureBlobFileSystem
    return AzureBlobFileSystem(spec, credentials)


def create_table(project_id, spec, credentials):
    from .table import AzureTableMetadataStore
    return AzureTableMetadataStore(project_id, spec, credentials)


def create_table_reader(project_id, spec, credentials):
    from .table import AzureTableMetadataStore
    return AzureTableMetadataStore(project_id, spec, credentials, read_only=True)


# Include our own distribution: workers must discover its entry points before
# reconstructing Azure specs. Supply its wheel to workers until it is published.
RUNTIME = BackendRuntimeRequirements(
    dependencies=(("expops_azure", "expops-azure==0.1.0"),),
    worker_environment=AZURE_ENVIRONMENT,
)

BLOB_PLUGIN = MountPlugin(
    name="azure-blob", spec_type=AzureBlobSpec, parse_spec=parse_blob,
    resolve_spec=lambda context: parse_blob(context.config),
    create_filesystem=create_blob,
    capabilities=MountCapabilities(writable=True, complete_writes=True, remove_file=True),
    runtime=RUNTIME,
    namespace_path=lambda spec, key: (spec.account_url.lower(), spec.container,
                                     *filter(None, (spec.prefix + "/" + key).split("/"))),
)

TABLE_PLUGIN = MetadataStorePlugin(
    name="azure-table", spec_type=AzureTableSpec, parse_spec=parse_table,
    resolve_spec=lambda context: parse_table(context.config),
    create_store=create_table, create_reader=create_table_reader, runtime=RUNTIME,
)
