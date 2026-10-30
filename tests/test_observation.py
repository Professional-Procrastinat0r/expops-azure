"""Azure Table read-only factory, shared contract, scope and subscription lifetime."""
import pytest
from azure.core.exceptions import ResourceNotFoundError

from expops.storage import MetadataKey, MetadataStore, MetadataObservationStore, open_metadata_reader
from expops.storage.errors import StorageCapabilityError, StorageConfigurationError, StorageSessionError
from expops.storage.secrets import StorageCredentialContext
from expops_azure.plugins import TABLE_PLUGIN
from expops_azure.specs import AzureTableSpec


class Entity(dict):
    metadata = {'etag': 'test-etag'}


class Table:
    def __init__(self):
        self.rows = {}
        self.writes = 0
    def get_entity(self, partition, row):
        try: return Entity(self.rows[(partition, row)])
        except KeyError:
            error = ResourceNotFoundError('Missing entity')
            error.error_code = 'ResourceNotFound'
            raise error from None
    def upsert_entity(self, entity, **kwargs):
        self.writes += 1
        self.rows[(entity['PartitionKey'], entity['RowKey'])] = dict(entity)
    create_entity = upsert_entity
    update_entity = upsert_entity
    def query_entities(self, query, *, parameters):
        return [Entity(row) for (partition, key), row in self.rows.items()
                if partition == parameters['partition'] and parameters['lower'] <= key < parameters['upper']]


@pytest.fixture
def backend(monkeypatch):
    import expops_azure.table as module
    table, clients = Table(), []
    class Service:
        closed = False
        def get_table_client(self, name): return table
        def close(self): self.closed = True
    def client(*args):
        service = Service()
        clients.append(service)
        return service, None
    monkeypatch.setattr(module, 'client', client)
    return table, clients


def test_azure_reader_supports_store_observation_without_writes_or_schema_creation(backend):
    table, clients = backend
    spec = AzureTableSpec('https://test.table.core.windows.net', 'Metadata')
    credentials = StorageCredentialContext.from_environment({})
    writer = TABLE_PLUGIN.create_store('p', spec, credentials)
    other = TABLE_PLUGIN.create_store('other', spec, credentials)
    assert isinstance(writer, MetadataStore) and isinstance(writer, MetadataObservationStore)
    writer.mark_pipeline_started('r')
    writes = table.writes
    with open_metadata_reader('p', spec) as reader:
        key = MetadataKey('run_status', 'r')
        with reader.listen(keys=[key], poll_interval=.005) as listener:
            assert listener.mode == 'polling' and not listener.native
            assert listener.get(1).value(key) == 'running'
            assert table.writes == writes
            other.mark_pipeline_completed('r', True)
            assert listener.get(.02) is None
            writer.mark_pipeline_completed('r', True)
            assert listener.get(1).value(key) == 'completed'
            with pytest.raises(StorageConfigurationError): reader._store.mark_pipeline_started('blocked')
            with pytest.raises(StorageConfigurationError): reader._store.save_probe_metrics_by_path('r', 'loss', {})
            with pytest.raises(StorageConfigurationError): reader._store.increment_stat('r', 'count')
    assert clients[-1].closed
    assert writer.get_run_status('r') == 'completed'
    writer.close()
    other.close()


def test_azure_store_cancels_listeners_and_explicit_native_mode_fails(backend):
    spec = AzureTableSpec('https://test.table.core.windows.net', 'Metadata')
    store = TABLE_PLUGIN.create_store('p', spec, StorageCredentialContext.from_environment({}))
    key = MetadataKey('runs')
    with pytest.raises(StorageCapabilityError): store.listen(keys=[key], mode='native')
    listener = store.listen(keys=[key], poll_interval=.005)
    assert listener.get(1).value(key) == []
    store.close()
    with pytest.raises(StorageSessionError): listener.get(.01)
    with pytest.raises(StorageSessionError): store.listen(keys=[key])
