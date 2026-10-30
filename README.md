# ExpOps Azure user plugins

This is an independent Python distribution providing `azure-blob` filesystem
mounts and an `azure-table` metadata backend. It registers installed entry points;
there are no modifications, monkeypatches or manual registrations in ExpOps.

The current source additionally implements the expanded
`MetadataStore`/`MetadataObservationStore` contract. Azure Table exposes
`store.listen(...)` with polling, and its registered `create_reader` supports
`open_metadata_reader(...).listen(...)` without table creation or write access.
This needs the matching current platform source exposing
`MetadataObservationMixin`; the historical wheel snapshot described below does
not include this API. Observation rejects metadata mutations; provider read-only
credentials remain the deployment permission boundary.

From the FYP workspace root, the prepared Azure development environment was
updated to editable source installs with:

```powershell
.\expops-azure\.venv\Scripts\python.exe -m pip install --no-deps --no-build-isolation -e ./expops-platform -e ./expops-azure
```

That command reuses already installed dependencies. On a fresh environment,
install the matching platform and plugin with dependencies first. Source edits
are picked up by newly started processes; entry-point/dependency changes still
require reinstalling in the target environment. Restart long-running observers
after updating either package.

Requires ExpOps >= `0.1.28.dev37`, exposing `MountPlugin`, `MetadataStorePlugin` and
schema-3 storage plans. See `VALIDATION.md` for the exact tested platform snapshot.
An older PyPI release without those interfaces is not compatible. At validation
time PyPI's latest stable release was 0.1.27, so use the supplied compatible
platform wheel first. A plugin cannot add interfaces missing from its host.

## Install

Use the same Python environment that runs ExpOps:

```powershell
python -m pip install .
expops-azure --help
```

The original validation prepared `.venv` with both distributions installed as
wheels; the development update above now uses editable sources. You can use its executables directly without
PowerShell activation:

```powershell
.\.venv\Scripts\expops-azure.exe --help
```

To reproduce that environment elsewhere, install the compatible platform wheel
provided by the platform maintainer, then this plugin wheel. For the prepared
local trial, the platform wheel is `.build/wheels/expops-0.1.28.dev37-py3-none-any.whl`.
It was built from an unchanged exported snapshot, not by patching the repository.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install .build/wheels/expops-0.1.28.dev37-py3-none-any.whl dist/expops_azure-0.1.0-py3-none-any.whl
```

For development, `python -m pip install -e '.[dev]'` is convenient. For the actual
extension test, build and install a wheel:

```powershell
python -m pip install build
python -m build
python -m pip install --force-reinstall --no-deps dist/expops_azure-0.1.0-py3-none-any.whl
```

The distribution depends on `expops`, `azure-storage-blob`, `azure-data-tables`,
and `azure-identity`. It uses public ExpOps protocols, specifications, credential
services and entry-point descriptors, and its own Azure adapters. It does not
inherit a built-in ExpOps database adapter or reach into its private state.

## Azure setup, step by step (PowerShell)

You need an Azure subscription and permission to create storage resources and
assign data roles. Install the [Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli-windows)
if `az --version` is unavailable. These commands are for you to run; the plugin
does not create a billed Azure account automatically.

### 1. Sign in and choose your subscription

```powershell
az login
az account list --output table
az account set --subscription '<your subscription ID>'
```

### 2. Create a resource group and one general-purpose v2 storage account

Blob containers and Table Storage can share this account. Use a new, dedicated
resource group so the trial is easy to identify. Account names must be globally
unique, 3-24 lowercase letters/digits. Change the example name before running.

```powershell
$azureGroup = 'expops-plugin-test'
$azureLocation = 'southeastasia'
$azureAccount = 'expopsyouruniquename'
az group create --name $azureGroup --location $azureLocation --output none
az storage account create --name $azureAccount --resource-group $azureGroup --location $azureLocation --kind StorageV2 --sku Standard_LRS --https-only true --min-tls-version TLS1_2 --allow-blob-public-access false --allow-shared-key-access false --output none
```

Standard Blob/Table Storage is enough; this test does not need Databricks,
Cosmos DB, AKS, Azure Files, or a hierarchical namespace. Storage/transactions
can incur charges. Follow your subscription's network policies; the executing
driver/workers need access to the Blob and Table endpoints.

### 3. Grant your signed-in user data access

```powershell
$azureScope = az storage account show --name $azureAccount --resource-group $azureGroup --query id --output tsv
$azureUser = az ad signed-in-user show --query id --output tsv
az role assignment create --assignee-object-id $azureUser --assignee-principal-type User --role 'Storage Blob Data Contributor' --scope $azureScope --output none
az role assignment create --assignee-object-id $azureUser --assignee-principal-type User --role 'Storage Table Data Contributor' --scope $azureScope --output none
```

Subscription Contributor permissions alone do not grant these data operations.
If role assignment is denied, the subscription administrator must grant these
roles. Role propagation can take several minutes. No account key is needed:
`DefaultAzureCredential` uses your Azure CLI login for local development.

### 4. Create a private deployment configuration

From the package directory:

```powershell
$azureConfig = Get-Content examples/compute.azure.example.yaml -Raw
$azureConfig.Replace('YOURACCOUNT', $azureAccount) | Set-Content examples/compute.azure.yaml -Encoding utf8
```

The generated file contains endpoints, container names and the table name, not
passwords. The code mount points to the supplied demo. The proposed configuration
types are implemented by this installed package, not by built-in ExpOps code.

### 5. Create the containers/table and upload a tiny sample

```powershell
expops-azure init --compute examples/compute.azure.yaml --sample examples/data/samples.csv
```

This explicit setup command creates missing resources (leaving existing ones in
place), then uploads/overwrites only `input/samples.csv` if `--sample` is given:

- Blob containers: `expops-input`, `expops-cache`, `expops-artefact`.
- Table: `ExpOpsMetadata`.

Normal adapter construction never creates accounts, containers or tables.

### 6. Check the integrations and then run an experiment

```powershell
expops-azure smoke --compute examples/compute.azure.yaml
expops-azure smoke --compute examples/compute.azure.yaml --workers 2
```

The smoke check verifies aborted writes, managed publication/checksum reads,
metadata operations, real `StepStateManager` cache publication/reuse, and
optionally independent Dask worker processes. It prints a unique run ID and
artefact address, and intentionally retains records/files as evidence.

The experiment code imports only ExpOps and the standard library:

```powershell
$env:PIP_FIND_LINKS = "$(Resolve-Path dist) $(Resolve-Path .build/wheels)"
Set-Location examples
expops run --project code --compute compute.azure.yaml
expops run --project code --compute compute.azure.yaml
```

The demo declares a named environment with `expops-azure==0.1.0` in its
requirements. `PIP_FIND_LINKS` makes the unpublished plugin and compatible platform
wheels available when ExpOps creates that environment. On another machine,
substitute your own wheelhouse/private index. Use paths without spaces for this
simple space-separated pip setting. When using the prepared environment without
activation, call `..\.venv\Scripts\expops.exe` from the examples directory.

The first run reads the CSV and records results. The second should reuse cached
process results. The tiny fixture is a storage demo, not a model-quality test.
In Azure Portal, use Storage browser/Storage Explorer to inspect the containers
and `ExpOpsMetadata`. Row IDs are hashes; record contents are chunked UTF-8 JSON
stored in binary properties. Metadata contains references, not model/file bytes.

## Credentials for remote workers (for example NUS SLURM)

Your workstation's `az login` is not a credential provisioner for remote hosts.
For an initial remote trial, create a dedicated service principal in Azure Portal:
Microsoft Entra ID -> App registrations -> New registration. Record its Directory
(tenant) ID and Application (client) ID. Under Certificates & secrets, create a
client secret and retain its **value** in your private secret manager or worker
environment file. Do not paste it into YAML, this repository, or chat.

On the storage account's Access control (IAM), assign that application's service
principal both data roles used above. In every executing environment supply:

```text
AZURE_TENANT_ID=<directory ID>
AZURE_CLIENT_ID=<application ID>
AZURE_CLIENT_SECRET=<secret value>
```

The plugin declares these environment names to ExpOps' worker deployment system.
It resolves credentials at client construction time; plans store no secret values.
On Azure-hosted compute, managed identity is another option. This plugin supports
the ordinary default identity chain and explicit environment service principals;
it does not implement custom certificate/workload-identity file relocation.

Install `expops-azure` in the driver, Dask worker, and named process/reporting
environments that use storage. The runtime descriptor declares
`expops-azure==0.1.0`, but this trial package is **not published to PyPI**. For
automatic worker environment preparation, put its wheel and dependencies in an
accessible wheelhouse/private package index and configure pip there. Merely
installing an editable package on the driver is insufficient. If using
`PIP_FIND_LINKS`, the path/URL must be accessible on the driver and workers, or
use the platform's worker wheel packaging where supported. Start with local
workers before changing your existing fraud/SLURM deployment.

## Alternative: account-key authentication for the cloud trial

The initial setup above explicitly disables Shared Key. This is a choice made
by that setup command, not evidence that the tenant requires it. If you administer
the test account and its configuration permits Shared Key, you can use this
plugin's existing connection-string mode instead of Azure CLI authentication.

In the storage account's Configuration page, set **Allow storage account key
access** to Enabled and Save. Keep secure transfer enabled and anonymous blob
access disabled. If Azure rejects the save with a policy error, inspect that
error rather than assuming the setting changed.

Under **Security + networking -> Access keys**, reveal and copy the complete
**Connection string** for key1. Treat it as a password; do not paste it into chat
or commit it. Shared Key grants account-wide data access and does not apply the
user's Conditional Access controls to data requests.

Each `azure-table`/`azure-blob` binding needs this additional configuration:

```yaml
connection_string_ref:
  source: environment
  name: EXPOPS_AZURE_CONNECTION_STRING
  required: true
```

The workspace's private `examples/compute.azure.yaml` has been configured with
these references for metadata, input, cache and artefact. The public
`compute.azure.example.yaml` remains the default Entra example.

In PowerShell, prompt for the connection string without writing it into command
history. The plaintext exists in the process environment so the SDK can use it:

```powershell
$azureSecret = Read-Host 'Paste the storage connection string' -AsSecureString
$env:EXPOPS_AZURE_CONNECTION_STRING = [System.Net.NetworkCredential]::new('', $azureSecret).Password
.\.venv\Scripts\expops-azure.exe init --compute examples/compute.azure.yaml --sample examples/data/samples.csv
.\.venv\Scripts\expops-azure.exe smoke --compute examples/compute.azure.yaml --workers 2
```

The connection string must belong to the same account as the configured Blob
and Table endpoints. No `az login` is used in this mode. When finished, clear it
from this session with `Remove-Item Env:EXPOPS_AZURE_CONNECTION_STRING` and
`Remove-Variable azureSecret`.

See Microsoft's [connection-string documentation](https://learn.microsoft.com/azure/storage/common/storage-configure-connection-string)
and [access-key management](https://learn.microsoft.com/azure/storage/common/storage-account-keys-manage).

## Local trial with Azurite (no Azure subscription)

Install [Azurite](https://learn.microsoft.com/azure/storage/common/storage-use-azurite)
using npm, VS Code or Docker. For a project-local npm install on Windows:

```powershell
npm.cmd install --prefix .tools azurite --cache .npm-cache
node .tools/node_modules/azurite/dist/src/azurite.js --location .azurite --skipApiVersionCheck
```

In another PowerShell window, set the public emulator-only credential:

```powershell
$env:EXPOPS_AZURE_CONNECTION_STRING = 'DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;TableEndpoint=http://127.0.0.1:10002/devstoreaccount1;'
expops-azure init --compute examples/compute.azurite.yaml --sample examples/data/samples.csv
expops-azure smoke --compute examples/compute.azurite.yaml --workers 2
```

The key above is Azurite's documented public test key, never a cloud credential.
Run the demo using `compute.azurite.yaml` in place of `compute.azure.yaml` above.
`--skipApiVersionCheck` allows the current Azure SDK's service API header against
the emulator; it does not establish full cloud feature parity.

## Move the fraud sample onto Azure later

Keep the current fraud files unchanged initially. Make a separate compute config
with its existing compute settings and local `code` mount, selecting these Azure
metadata/input/cache/artefact providers. Upload the real CSV under the input key
the project declares (`creditcard.csv`); the small demo upload is a different file.
Keep secrets in the existing private deployment environment. First run with local
compute, then repeat on SLURM with packaged worker dependencies and credentials.

## Scope and limits

- Azure HTTP request/response header logs default to WARNING in every process
  constructing a client. SDK warnings/errors and ExpOps progress remain visible.
  `MLOPS_PROVIDER_LOG_LEVEL=INFO` (or ExpOps `--provider-log-level INFO`) opts into
  the header logs; DEBUG opts into additional diagnostics. Reinstall a rebuilt
  wheel in existing driver/process environments to pick up plugin changes.
- Blob reads are sequential streams; writes spool locally, spilling to disk
  above 8 MiB. Configure a suitable native temp directory for large outputs.
- Blob `r`, `w`, `x` binary/text modes are supported; append/update and native
  filesystem paths are not. No immutable-version capability is advertised.
- Directory information is inferred from prefixes. Exact blobs take precedence
  over same-named virtual directories. Containers/prefixes are declared bindings.
- Complete publication is not a transaction across files and metadata. Avoid
  concurrent overwrites of one user-selected blob; managed publication uses
  unique names. An ambiguous network failure after server commit is possible.
- Table records use one project partition and typed row-key ranges. Counters use
  ETag retries across processes; each other record is atomically replaced.
  Multi-record operations and read/merge/write metrics are not transactions.
- Metadata JSON is split into <=60,000-byte binary properties in one entity;
  the plugin caps the total at 900 KiB. Store large payloads through file mounts.
  JSON must be finite and serializable; values are never silently truncated.
- Cache index rows are retained. ExpOps controls logical cache TTL independently;
  this plugin performs no automatic physical garbage collection.
- Run listing sorts the project's run records client-side; batch cache lookup is
  a loop of point reads. This is a functional extension demonstration, not a
  claim of large-scale metadata query optimization.
- Azure service/authentication errors propagate; a missing account/container/table
  is not a successful cache miss. `close()` releases clients and never deletes
  persisted resources. Check `VALIDATION.md` for tested versus unverified behavior.

## Tests and references

```powershell
python -m pytest
$env:EXPOPS_AZURE_TEST_COMPUTE = (Resolve-Path examples/compute.azurite.yaml).Path
python -m pytest -m integration
```

Integration tests write uniquely named records/files to the selected test account.
Use a dedicated account/table/container set, not production data. All cloud setup
commands above are manual; no cloud resources are created by running unit tests.

- [ExpOps plugin contracts](../expops-platform/docs/storage-plugins.md)
- [Azure Blob Python SDK](https://learn.microsoft.com/azure/storage/blobs/storage-quickstart-blobs-python)
- [Azure Tables Python SDK](https://learn.microsoft.com/python/api/overview/azure/data-tables-readme?view=azure-python)
- [Azure Storage account creation](https://learn.microsoft.com/azure/storage/common/storage-account-create?tabs=azure-cli)
- [Blob data authorization with Azure CLI](https://learn.microsoft.com/azure/storage/blobs/authorize-data-operations-cli)
- [Azure Table data authorization](https://learn.microsoft.com/azure/storage/tables/authorize-access-azure-active-directory)

To remove the trial services later, review the dedicated resource group in Azure
Portal and delete that group once you no longer need its records and files.
