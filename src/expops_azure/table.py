"""Independent implementation of the complete ExpOps metadata contract.

One project partition, typed row-key prefixes, JSON identities and binary JSON
chunks. Each record is a single entity (atomic replace). Counter updates use
ETag compare-and-swap, including retry of the concurrent initial-create case.
"""
from __future__ import annotations

import hashlib
import json
import random
import time

from azure.core import MatchConditions
from azure.core.exceptions import HttpResponseError, ResourceNotFoundError
from azure.data.tables import TableServiceClient, UpdateMode
from expops.storage.interfaces.metadata_store import MetadataStore
from expops.storage.metadata_observation import MetadataObservationMixin
from expops.storage.errors import StorageConfigurationError

from .auth import client


def _json(value):
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), allow_nan=False)


def _hash(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


class AzureTableMetadataStore(MetadataObservationMixin, MetadataStore):
    """Small/medium experiment store, not a secondary-index analytics engine.

    Table/container creation is an explicit setup step. Logical cache expiry is
    handled by ExpOps; index rows are retained until explicitly maintained.
    Records above 900 KiB are rejected rather than silently truncated.
    """
    def __init__(self, project_id, spec, credentials, *, read_only=False):
        if not isinstance(project_id, str) or not project_id:
            raise ValueError("project_id is required")
        self.project_id, self._spec = project_id, spec
        self._partition = "p" + _hash(project_id)
        self._closed = False
        self.read_only = read_only
        self._service, self._credential = client(spec, credentials, TableServiceClient)
        try:
            self._table = self._service.get_table_client(spec.table)
            self._initialize_observation()
        except BaseException:
            self.close()
            raise

    @property
    def spec(self):
        return self._spec

    def _check(self):
        if self._closed:
            raise ValueError("Azure Table metadata store is closed")

    def _row(self, kind, identity):
        scope = _hash(identity[0]) if identity else "all"
        return f"{kind}:{scope}:{_hash(identity)}"

    def _entity(self, kind, identity, value):
        data = _json({"identity": identity, "value": value}).encode("utf-8")
        if len(data) > 900 * 1024:
            raise ValueError("Azure metadata record exceeds the plugin's 900 KiB limit; store large payloads as files")
        chunks = [data[i:i + 60000] for i in range(0, len(data), 60000)]
        return {"PartitionKey": self._partition, "RowKey": self._row(kind, identity),
                "chunks": len(chunks), **{f"data{i:02}": part for i, part in enumerate(chunks)}}

    @staticmethod
    def _decode(entity):
        return json.loads(b"".join(entity[f"data{i:02}"] for i in range(entity["chunks"])).decode("utf-8"))

    def _get_entity(self, kind, identity):
        self._check()
        try:
            return self._table.get_entity(self._partition, self._row(kind, identity))
        except ResourceNotFoundError as exc:
            if exc.error_code == "TableNotFound":
                raise
            return None

    def _get(self, kind, identity):
        entity = self._get_entity(kind, identity)
        if entity is None:
            return None
        decoded = self._decode(entity)
        if decoded["identity"] != identity:
            raise ValueError("Azure metadata record identity mismatch")
        return decoded["value"]

    def _put(self, kind, identity, value):
        self._check()
        self._check_writable()
        self._table.upsert_entity(self._entity(kind, identity, value), mode=UpdateMode.REPLACE)

    def _list(self, kind, scope=None):
        self._check()
        prefix = kind + ":" + (_hash(scope) + ":" if scope is not None else "")
        # Prefix characters are generated ASCII. Parameters keep project/run
        # identifiers out of OData syntax (including quotes and slashes).
        query = "PartitionKey eq @partition and RowKey ge @lower and RowKey lt @upper"
        entities = self._table.query_entities(query, parameters={
            "partition": self._partition, "lower": prefix, "upper": prefix + "~",
        })
        for entity in entities:
            decoded = self._decode(entity)
            if scope is None or decoded["identity"][0] == scope:
                yield decoded["identity"], decoded["value"]

    def _change(self, kind, identity, transform):
        self._check_writable()
        for attempt in range(32):
            entity = self._get_entity(kind, identity)
            value = self._decode(entity)["value"] if entity is not None else None
            replacement = self._entity(kind, identity, transform(value))
            try:
                if entity is None:
                    self._table.create_entity(replacement)
                else:
                    self._table.update_entity(replacement, mode=UpdateMode.REPLACE,
                                              etag=entity.metadata["etag"],
                                              match_condition=MatchConditions.IfNotModified)
                return
            except HttpResponseError as exc:
                if exc.status_code not in {409, 412} or attempt == 31:
                    raise
                time.sleep(random.uniform(0, min(0.1, 0.002 * 2 ** attempt)))

    @staticmethod
    def _cache_identity(parts, input_hash, config_hash, function_hash):
        if not input_hash or not config_hash:
            return None
        return [*parts, input_hash, config_hash, function_hash or "none"]

    @staticmethod
    def _cache_path(record):
        if record and record.get("status") in {"completed", "cached"}:
            return record.get("cache_path") or None
        return None

    def set_step_cache_record(self, process_name, step_name, input_hash, config_hash, function_hash, record, ttl_seconds=None):
        identity = self._cache_identity([process_name, step_name], input_hash, config_hash, function_hash)
        if identity is None:
            raise ValueError("Cache publication requires input and config hashes")
        self._put("stepcache", identity, record)

    def get_step_cache_record(self, process_name, step_name, input_hash, config_hash, function_hash):
        identity = self._cache_identity([process_name, step_name], input_hash, config_hash, function_hash)
        return self._get("stepcache", identity) if identity is not None else None

    def get_step_cache_path(self, process_name, step_name, input_hash, config_hash, function_hash):
        return self._cache_path(self.get_step_cache_record(process_name, step_name, input_hash, config_hash, function_hash))

    def set_process_cache_record(self, process_name, input_hash, config_hash, function_hash, record, ttl_seconds=None):
        identity = self._cache_identity([process_name], input_hash, config_hash, function_hash)
        if identity is None:
            raise ValueError("Cache publication requires input and config hashes")
        self._put("processcache", identity, record)

    def get_process_cache_record(self, process_name, input_hash, config_hash, function_hash):
        identity = self._cache_identity([process_name], input_hash, config_hash, function_hash)
        return self._get("processcache", identity) if identity is not None else None

    def get_process_cache_path(self, process_name, input_hash, config_hash, function_hash):
        return self._cache_path(self.get_process_cache_record(process_name, input_hash, config_hash, function_hash))

    def get_process_cache_paths_batch(self, lookups):
        return {f"{name}|{ih}|{ch}|{fh or 'none'}": self.get_process_cache_path(name, ih, ch, fh)
                for name, ih, ch, fh in lookups}

    def mark_pipeline_started(self, run_id):
        now = time.time()
        self._change("run", [run_id], lambda old: {
            "status": "running", "start": now, "end": None,
        })

    def mark_pipeline_completed(self, run_id, success):
        now = time.time()
        self._change("run", [run_id], lambda old: {
            "status": "completed" if success else "failed",
            "start": old.get("start") if old else now, "end": now,
        })

    def get_run_status(self, run_id):
        record = self._get("run", [run_id])
        return record["status"] if record else None

    def list_runs(self, limit=100):
        if limit <= 0:
            return []
        rows = list(self._list("run"))
        rows.sort(key=lambda item: (item[1].get("start") or 0, item[0][0]), reverse=True)
        return [identity[0] for identity, record in rows[:limit]]

    def record_run_step(self, run_id, process_name, step_name, record):
        self._put("runstep", [run_id, process_name, step_name], record)

    def get_run_step(self, run_id, process_name, step_name):
        return self._get("runstep", [run_id, process_name, step_name])

    def list_run_steps(self, run_id):
        return {f"{identity[1]}.{identity[2]}": record for identity, record in self._list("runstep", run_id)}

    def save_probe_metrics_by_path(self, run_id, probe_path, metrics):
        self._put("metric", [run_id, probe_path], metrics)

    def get_probe_metrics_by_path(self, run_id, probe_path):
        return self._get("metric", [run_id, probe_path]) or {}

    def list_probe_paths(self, run_id):
        return sorted(identity[1] for identity, record in self._list("metric", run_id))

    def increment_stat(self, run_id, name, amount=1):
        if type(amount) is not int:
            raise ValueError("Counter increment must be an integer")
        self._change("stat", [run_id, name], lambda old: (old or 0) + amount)

    def get_pipeline_stats(self, run_id):
        return {identity[1]: value for identity, value in self._list("stat", run_id)}

    def record_run_chart_artifacts(self, run_id, chart_name, artifacts):
        chart_type = artifacts[0].get("chart_type", "static") if artifacts else "static"
        self._put("chart", [run_id, chart_name], {"type": chart_type, "items": artifacts})

    def list_run_charts(self, run_id):
        return {identity[1]: value for identity, value in self._list("chart", run_id)}

    def copy_run_chart_artifacts(self, from_run_id, to_run_id, chart_name):
        record = self._get("chart", [from_run_id, chart_name])
        if record is None:
            return False
        self._put("chart", [to_run_id, chart_name], record)
        return True

    def close(self):
        def release():
            self._closed = True
            try:
                self._service.close()
            finally:
                if self._credential is not None:
                    self._credential.close()
        self._close_observation(release)

    def _check_writable(self):
        if self.read_only:
            raise StorageConfigurationError('Metadata reader cannot write records')

    def __getstate__(self):
        raise TypeError("Transport AzureTableSpec, not a live metadata store")
