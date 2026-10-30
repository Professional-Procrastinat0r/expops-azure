from concurrent.futures import ThreadPoolExecutor
import os
import pickle
import uuid

import pytest
from expops.storage.factory import create_storage_session_from_plan, create_metadata_store_from_spec
from expops.storage.interfaces.metadata_store import MetadataStore
from expops.storage.plan import ResolvedStoragePlan
from expops_azure.cli import load_plan, smoke

pytestmark = pytest.mark.integration


@pytest.fixture
def plan():
    filename = os.environ.get("EXPOPS_AZURE_TEST_COMPUTE")
    if not filename:
        pytest.skip("Set EXPOPS_AZURE_TEST_COMPUTE to a dedicated initialized test deployment")
    return load_plan(filename)


def test_public_vfs_text_binary_listing_and_aborted_publication(plan):
    prefix = "tests/" + uuid.uuid4().hex
    with create_storage_session_from_plan(plan) as storage:
        fs = storage.vfs
        root = f"cache/{prefix}"
        with fs.open(root + "/one.txt", "w", newline="\n", atomic=True) as stream:
            stream.write("héllo\n")
        fs.write_bytes(root + "/nested/two.bin", b"\x00\xff")
        assert fs.path(root + "/one.txt").read_text() == "héllo\n"
        assert fs.read_bytes(root + "/nested/two.bin") == b"\x00\xff"
        assert fs.stat(root + "/one.txt").size_bytes == len("héllo\n".encode())
        assert sorted(path.name for path in fs.iterdir(root)) == ["nested", "one.txt"]
        assert [p.name for p in fs.glob(root, "*.txt")] == ["one.txt"]
        assert [p.name for p in fs.glob(root, "**/*.bin")] == ["two.bin"]
        with pytest.raises(RuntimeError):
            with fs.open(root + "/one.txt", "w", atomic=True) as stream:
                stream.write("aborted")
                raise RuntimeError("abort")
        assert fs.read_text(root + "/one.txt") == "héllo\n"
        with pytest.raises(RuntimeError):
            with fs.open(root + "/absent.txt", "w", atomic=True) as stream:
                stream.write("abort new")
                raise RuntimeError("abort")
        assert not fs.exists(root + "/absent.txt")
        with pytest.raises(FileExistsError):
            with fs.open(root + "/one.txt", "x"):
                pass
        with pytest.raises(NotADirectoryError):
            fs.stat(root + "/one.txt/child")
        with pytest.raises(IsADirectoryError):
            fs.remove_file(root + "/nested")
        with pytest.raises(TypeError):
            os.fspath(fs.path(root + "/one.txt"))
        with pytest.raises(ValueError):
            with fs.open(root + "/one.txt", "a"):
                pass
        fs.remove_file(root + "/one.txt")
        fs.remove_file(root + "/nested/two.bin")
        assert not fs.exists(root)


def test_all_metadata_domains_isolation_and_strict_hashes(plan):
    run = "test:'/%_" + uuid.uuid4().hex
    process, step = "train:with/slash", "fit:step"
    with create_storage_session_from_plan(plan) as storage:
        metadata = storage.metadata_store
        assert isinstance(metadata, MetadataStore)
        metadata.mark_pipeline_started(run)
        assert metadata.get_run_status(run) == "running"
        metadata.record_run_step(run, process, step, {"status": "completed"})
        metadata.record_run_step(run + "other", process, step, {"status": "failed"})
        assert metadata.list_run_steps(run) == {f"{process}.{step}": {"status": "completed"}}
        assert metadata.get_run_step(run, process, "absent") is None
        record = {"status": "completed", "cache_path": "vfs://cache/result", "run_id": run}
        metadata.set_process_cache_record(process, run, "config", None, record)
        metadata.set_step_cache_record(process, step, run, "config", None, record)
        assert metadata.get_process_cache_path(process, run, "config", None) == record["cache_path"]
        assert metadata.get_step_cache_path(process, step, run, "config", None) == record["cache_path"]
        assert metadata.get_process_cache_path(process, None, "config", None) is None
        assert metadata.get_process_cache_path(process, run, "different", None) is None
        metadata.set_process_cache_record(process, run, "failed", None, {**record, "status": "failed"})
        assert metadata.get_process_cache_path(process, run, "failed", None) is None
        assert metadata.get_process_cache_record(process, run, "failed", None)["status"] == "failed"
        assert metadata.get_process_cache_paths_batch([(process, run, "config", None)]) == {f"{process}|{run}|config|none": record["cache_path"]}
        metadata.save_probe_metrics_by_path(run, "//process[@name='train']", {"long": "測試" * 12000})
        assert metadata.get_probe_metrics_by_path(run, "//process[@name='train']")["long"] == "測試" * 12000
        assert metadata.list_probe_paths(run) == ["//process[@name='train']"]
        metadata.record_run_chart_artifacts(run, "chart", [{"object_path": "vfs://artefact/a", "chart_type": "static"}])
        assert metadata.copy_run_chart_artifacts(run, run + "copy", "chart")
        assert metadata.list_run_charts(run + "copy") == metadata.list_run_charts(run)
        assert not metadata.copy_run_chart_artifacts(run, run + "copy", "missing")
        metadata.mark_pipeline_completed(run, False)
        assert metadata.get_run_status(run) == "failed"
        assert run in metadata.list_runs(limit=10000)
        assert metadata.list_runs(0) == []
        other = create_metadata_store_from_spec(plan.project_id.upper(), plan.metadata_spec)
        try:
            assert other.get_run_status(run) is None
        finally:
            other.close()
        rebuilt = create_metadata_store_from_spec(plan.project_id, metadata.spec.to_dict())
        try:
            assert rebuilt.get_run_status(run) == "failed"
        finally:
            rebuilt.close()
        with pytest.raises(TypeError):
            pickle.dumps(storage.fs)


def test_counter_contention_across_independent_clients(plan):
    run = "concurrency-" + uuid.uuid4().hex
    stores = [create_metadata_store_from_spec(plan.project_id, plan.metadata_spec) for _ in range(4)]
    try:
        def increment(store):
            for _ in range(12):
                store.increment_stat(run, "hits")
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(increment, stores))
        assert stores[0].get_pipeline_stats(run) == {"hits": 48}
    finally:
        for store in stores:
            store.close()


def test_session_close_aborts_open_publication_and_reconstruction(plan):
    plan = ResolvedStoragePlan.from_json(plan.to_json())
    storage = create_storage_session_from_plan(plan)
    path = "cache/tests/" + uuid.uuid4().hex
    context = storage.fs.open("/" + path, "wb", atomic=True)
    stream = context.__enter__()
    stream.write(b"unfinished")
    storage.fs.close()
    from expops.storage.errors import StorageSessionError
    with pytest.raises(StorageSessionError):
        context.__exit__(None, None, None)
    storage.close()
    with create_storage_session_from_plan(plan) as rebuilt:
        assert not rebuilt.vfs.exists(path)


def test_real_managed_cache_and_dask_process_workers(plan):
    smoke(plan, workers=2)


def test_large_blob_spills_to_disk_and_streams_back(plan):
    import hashlib
    path = "cache/tests/" + uuid.uuid4().hex + ".bin"
    block = bytes(range(256)) * 4096
    expected = hashlib.sha256(block * 10).hexdigest()
    with create_storage_session_from_plan(plan) as storage:
        with storage.vfs.open(path, "wb", atomic=True) as stream:
            for _ in range(10):
                stream.write(block)
        assert storage.vfs.stat(path).size_bytes == 10 * len(block)
        digest = hashlib.sha256()
        with storage.vfs.open(path, "rb") as stream:
            for chunk in iter(lambda: stream.read(70000), b""):
                digest.update(chunk)
        assert digest.hexdigest() == expected
        storage.vfs.remove_file(path)


def test_missing_container_or_table_is_an_error_not_cache_miss(plan):
    from dataclasses import replace
    from azure.core.exceptions import ResourceNotFoundError
    from expops.storage.secrets import StorageCredentialContext
    from expops_azure.blob import AzureBlobFileSystem
    from expops_azure.table import AzureTableMetadataStore
    creds = StorageCredentialContext.from_environment()
    blob = AzureBlobFileSystem(replace(plan.mounts["cache"].backend, container="missing-" + uuid.uuid4().hex), creds)
    table = AzureTableMetadataStore(plan.project_id, replace(plan.metadata_spec, table="Missing" + uuid.uuid4().hex), creds)
    try:
        with pytest.raises(ResourceNotFoundError):
            blob.stat("")
        with pytest.raises(ResourceNotFoundError):
            table.get_process_cache_record("train", "i", "c", None)
    finally:
        blob.close()
        table.close()
