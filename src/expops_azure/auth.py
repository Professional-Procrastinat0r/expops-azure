"""Authentication belongs to this plugin, not to ExpOps or its public plans."""
import logging
import os

from azure.identity import ClientSecretCredential, DefaultAzureCredential


AZURE_ENVIRONMENT = ("AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET", "AZURE_AUTHORITY_HOST")


def identity(credentials):
    values = [credentials.env_get(name) for name in AZURE_ENVIRONMENT[:3]]
    if any(values):
        # A client ID alone is also valid for user-assigned managed identity.
        if values[0] or values[2]:
            if not all(values):
                raise ValueError("Service principal authentication requires AZURE_TENANT_ID, AZURE_CLIENT_ID and AZURE_CLIENT_SECRET")
            options = {}
            if credentials.env_get("AZURE_AUTHORITY_HOST"):
                options["authority"] = credentials.env_get("AZURE_AUTHORITY_HOST")
            return ClientSecretCredential(*values, **options)
    return DefaultAzureCredential(managed_identity_client_id=values[1], exclude_interactive_browser_credential=True)


def client(spec, credentials, service_type):
    """Return service client and any separately owned token credential."""
    # HttpLoggingPolicy emits one INFO request/response dump per storage call.
    # Set this in every process constructing a client, including Dask workers;
    # do not silence SDK warnings/errors or change application logging.
    level = os.environ.get("MLOPS_PROVIDER_LOG_LEVEL", "WARNING").upper()
    if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        level = "WARNING"
    logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(level)
    options = {"max_single_get_size": 4 * 1024 * 1024, "max_chunk_get_size": 4 * 1024 * 1024} if service_type.__name__ == "BlobServiceClient" else {}
    if spec.connection_string_ref is not None:
        secret = credentials.resolve(spec.connection_string_ref)
        if not secret:
            raise ValueError("The Azure connection string reference did not resolve")
        service = service_type.from_connection_string(secret, **options)
        # A spec must describe the same location on drivers and workers, even
        # when authentication is supplied by a private connection string.
        if service.url.rstrip("/").lower() != spec.account_url.rstrip("/").lower():
            service.close()
            raise ValueError("Connection string endpoint does not match account_url")
        return service, None
    credential = identity(credentials)
    try:
        location = {"endpoint" if service_type.__name__ == "TableServiceClient" else "account_url": spec.account_url}
        return service_type(**location, credential=credential, **options), credential
    except BaseException:
        credential.close()
        raise
