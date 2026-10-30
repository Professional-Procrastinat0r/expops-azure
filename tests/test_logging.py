"""Exercise real SDK HTTP logging without making a network request."""
import logging

import pytest
from azure.core.pipeline import Pipeline
from azure.core.pipeline.policies import HttpLoggingPolicy
from azure.core.pipeline.transport import HttpResponse, HttpTransport
from azure.core.rest import HttpRequest

from expops.storage.secrets import StorageCredentialContext
from expops_azure.auth import client
from expops_azure.specs import parse_table


class Transport(HttpTransport):
    def open(self):
        pass

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def send(self, request, **kwargs):
        response = HttpResponse(request, None)
        response.status_code = 204
        response.headers = {"ETag": "test-etag"}
        return response


@pytest.mark.parametrize("level,visible", [(None, False), ("INFO", True), ("DEBUG", True),
                                          ("ERROR", False), ("invalid", False)])
def test_http_headers_follow_provider_level(caplog, monkeypatch, level, visible):
    name = "azure.core.pipeline.policies.http_logging_policy"
    logger = logging.getLogger(name)
    previous = logger.level
    if level is None:
        monkeypatch.delenv("MLOPS_PROVIDER_LOG_LEVEL", raising=False)
    else:
        monkeypatch.setenv("MLOPS_PROVIDER_LOG_LEVEL", level)
    try:
        from azure.data.tables import TableServiceClient
        spec = parse_table({"type": "azure-table", "account_url": "https://example.table.core.windows.net",
                            "connection_string_ref": {"source": "environment", "name": "TEST_CONNECTION"}})
        credentials = StorageCredentialContext.from_environment({"TEST_CONNECTION":
            "DefaultEndpointsProtocol=https;AccountName=example;AccountKey=YWJjZA==;EndpointSuffix=core.windows.net"})
        with caplog.at_level(logging.INFO):
            service, credential = client(spec, credentials, TableServiceClient)
            assert credential is None
            service.close()
            with Pipeline(transport=Transport(), policies=[HttpLoggingPolicy()]) as pipeline:
                pipeline.run(HttpRequest("GET", "https://example.table.core.windows.net/Test"))
            if level in (None, "invalid"):
                logger.warning("storage warning remains visible")
        assert ("Response headers:" in caplog.text) is visible
        if level in (None, "invalid"):
            assert "storage warning remains visible" in caplog.text
    finally:
        logger.setLevel(previous)
