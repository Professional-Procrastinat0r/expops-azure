"""Setup and end-to-end checks using installed ExpOps entry points."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
import tempfile
import uuid

from expops.storage.config import load_storage_config
from expops.storage.factory import create_storage_session_from_plan
from expops.storage.plan import resolve_storage_plan, ResolvedStoragePlan


def load_plan(filename):
    path = Path(filename).resolve()
    return resolve_storage_plan(
        "azure-plugin-demo", config_path=path, project_root=path.parent / "demo",
        storage_config=load_storage_config(path), env=os.environ,
    )


def initialize(plan, sample):
    from azure.core.exceptions import ResourceExistsError
    from azure.storage.blob import BlobServiceClient
    from azure.data.tables import TableServiceClient
    from expops.storage.secrets import StorageCredentialContext
    from .auth import client
    from .specs import AzureBlobSpec, AzureTableSpec

    credentials = StorageCredentialContext.from_environment()
    specs = {("blob", item.backend.account_url, item.backend.container): item.backend
             for item in plan.mounts.values() if isinstance(item.backend, AzureBlobSpec)}
    if isinstance(plan.metadata_spec, AzureTableSpec):
        specs[("table", plan.metadata_spec.account_url, plan.metadata_spec.table)] = plan.metadata_spec
    for (kind, url, name), spec in specs.items():
        with ExitStack() as resources:
            service, credential = client(spec, credentials, BlobServiceClient if kind == "blob" else TableServiceClient)
            if credential is not None:
                resources.callback(credential.close)
            resources.callback(service.close)
            try:
                if kind == "blob":
                    service.create_container(name)
                else:
                    service.create_table(name)
            except ResourceExistsError:
                pass
        print(f"Ready: {kind} {name}")
    if sample is not None:
        with create_storage_session_from_plan(plan) as storage:
            storage.vfs.put_file("input/samples.csv", Path(sample).resolve())
        print("Uploaded demo input/samples.csv")


def worker_check(snapshot, run_id, index):
    from expops.storage.managed_layout import ManagedStorageLayout
    plan = ResolvedStoragePlan.from_json(snapshot)
    with create_storage_session_from_plan(plan) as storage:
        storage.metadata_store.increment_stat(run_id, "worker_count")
        ref = storage.files.publish_bytes(
            "artefact", ManagedStorageLayout("smoke-v1", plan.project_id).artifact_key(f"worker-{index}.txt"),
            f"worker {index}".encode(),
        )
        assert storage.files.read(ref) == f"worker {index}".encode()
    return {"pid": os.getpid(), "artefact": ref.uri}


def smoke(plan, workers=0):
    from expops.storage.managed_layout import ManagedStorageLayout
    from expops.core.runtime.step_state_manager import ProcessExecutionResult

    run_id = "smoke-" + uuid.uuid4().hex
    with create_storage_session_from_plan(plan) as storage, tempfile.TemporaryDirectory(prefix="expops-azure-") as scratch:
        metadata = storage.metadata_store
        metadata.mark_pipeline_started(run_id)
        address = "cache/smoke/" + run_id + ".txt"
        storage.vfs.write_text(address, "original")
        try:
            with storage.vfs.open(address, "w", atomic=True) as stream:
                stream.write("must not publish")
                raise RuntimeError("intentional aborted publication")
        except RuntimeError:
            pass
        assert storage.vfs.read_text(address) == "original"
        storage.vfs.remove_file(address)
        with StepManagerScope(storage, scratch) as manager:
            manager.record_process_completion(
                run_id, ProcessExecutionResult("plugin-check", True, result={"answer": 42}),
                input_hash="input-v1", config_hash="config-v1", function_hash="code-v1",
            )
            cached = manager.get_cached_process_result(
                run_id=run_id, process_name="plugin-check", input_hash="input-v1",
                config_hash="config-v1", function_hash="code-v1",
            )
            assert cached and cached["answer"] == 42
        ref = storage.files.publish_bytes(
            "artefact", ManagedStorageLayout("smoke-v1", plan.project_id).artifact_key(run_id + ".txt"), b"verified",
        )
        metadata.save_probe_metrics_by_path(run_id, "plugin-check", {"answer": 42})
        assert metadata.get_probe_metrics_by_path(run_id, "plugin-check") == {"answer": 42}
        metadata.record_run_chart_artifacts(run_id, "proof", [{"object_path": ref.uri, "chart_type": "static"}])
        assert metadata.list_run_charts(run_id)["proof"]["items"][0]["object_path"] == ref.uri
        results = []
        if workers:
            from distributed import Client, LocalCluster
            with LocalCluster(n_workers=workers, threads_per_worker=1, processes=True,
                              dashboard_address=None, memory_limit=0) as cluster, Client(cluster) as client:
                results = client.gather([client.submit(worker_check, plan.to_json(), run_id, i, pure=False)
                                         for i in range(workers * 2)])
            assert metadata.get_pipeline_stats(run_id)["worker_count"] == workers * 2
        metadata.mark_pipeline_completed(run_id, True)
        assert metadata.get_run_status(run_id) == "completed"
        print(json.dumps({"run_id": run_id, "status": "passed", "cache_reused": True,
                          "artefact": ref.uri, "workers": results}, indent=2))


class StepManagerScope:
    """Borrow storage through the public manager constructor."""
    def __init__(self, storage, scratch):
        from expops.core.runtime.step_state_manager import StepStateManager
        self.manager = StepStateManager(Path(scratch) / "steps", storage_session=storage,
                                        storage_binding_id=storage.fs.plan.cache_binding_id)

    def __enter__(self):
        return self.manager

    def __exit__(self, *error):
        self.manager.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "smoke"):
        command = commands.add_parser(name)
        command.add_argument("--compute", required=True)
        if name == "init":
            command.add_argument("--sample", help="Upload a sample CSV to input/samples.csv")
        else:
            command.add_argument("--workers", type=int, default=0, choices=range(0, 9))
    args = parser.parse_args()
    plan = load_plan(args.compute)
    if args.command == "init":
        initialize(plan, args.sample)
    else:
        smoke(plan, args.workers)


if __name__ == "__main__":
    main()
