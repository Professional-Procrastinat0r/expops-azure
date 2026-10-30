# ExpOps Azure storage plugins

This is an independent Python distribution providing `azure-blob` filesystem
mounts and an `azure-table` metadata backend. It registers installed entry points;
there are no modifications, monkeypatches or manual registrations in ExpOps.

The current source additionally implements the expanded
`MetadataStore`/`MetadataObservationStore` contract. Azure Table exposes
`store.listen(...)` with polling, and its registered `create_reader` supports
`open_metadata_reader(...).listen(...)` without table creation or write access.
This needs an ExpOps version exposing `MetadataObservationMixin` and the
metadata observation APIs. Observation rejects metadata mutations; provider
read-only credentials remain the deployment permission boundary.

Requires ExpOps >= `0.1.28.dev37`, exposing `MountPlugin`, `MetadataStorePlugin` and
schema-3 storage plans, plus the observation interfaces above. The numeric
dependency floor alone does not guarantee those newer interfaces are present.
See [VALIDATION.md](VALIDATION.md) for the historical tested platform snapshot and
its limits. Use a compatible platform release, wheel, or source checkout.

## Install

Run these commands from this repository's root. Use the same Python environment
that runs ExpOps. For a new environment on Windows:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

On Linux/macOS, activate with `source .venv/bin/activate` instead. If ExpOps is
already installed in a compatible environment, use that environment directly.
Otherwise, obtain a compatible ExpOps wheel and replace the placeholder below
with its path or URL; the wheel is not bundled in this repository:

```powershell
$expopsWheel = '<path-or-URL-to-compatible-ExpOps-wheel>'
python -m pip install $expopsWheel
python -m pip install .
expops-azure --help
```

For development, install the plugin and test dependencies in editable mode:

```powershell
python -m pip install -e '.[dev]'
```

Editable installs pick up source edits in newly started processes. Reinstall
after entry-point or dependency changes, and restart long-running observers
after updating either package. For a regular wheel install, rebuild and reinstall
after source edits too.

To build a distributable wheel and install it into the active environment:

```powershell
python -m pip install build
python -m build --outdir wheelhouse
python -m pip install --force-reinstall --no-deps wheelhouse/expops_azure-0.1.0-py3-none-any.whl
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
passwords. It is ignored by Git. The code mount points to the included demo.
The Azure configuration types are implemented by this installed package.

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

The experiment code imports only ExpOps and the standard library.

Build the plugin wheel as shown under Install, and copy a compatible ExpOps wheel
into `wheelhouse/` as well. From the repository root in the activated environment:

```powershell
$wheelhouse = (Resolve-Path wheelhouse).Path
$env:PIP_FIND_LINKS = ([System.Uri]::new($wheelhouse + '/')).AbsoluteUri
Set-Location examples
expops run --project code --compute compute.azure.yaml
expops run --project code --compute compute.azure.yaml
Set-Location ..
```

The demo declares a named environment with `expops-azure==0.1.0` in its
requirements. `PIP_FIND_LINKS` makes the locally built plugin and compatible
platform wheels available when ExpOps creates that environment. On another machine,
use an accessible wheelhouse or package index. The file URI above supports local
paths containing spaces. Both example configurations use `provider: local`:
computation runs on local Dask workers while storage uses Azure or Azurite.

The first run reads the CSV and records results. The second should reuse cached
process results. The tiny fixture is a storage demo, not a model-quality test.
In Azure Portal, use Storage browser/Storage Explorer to inspect the containers
and `ExpOpsMetadata`. Row IDs are hashes; record contents are chunked UTF-8 JSON
stored in binary properties. Metadata contains references, not model/file bytes.

## Credentials for remote workers

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
`expops-azure==0.1.0`. For automatic worker environment preparation, put its wheel
and dependencies in an accessible wheelhouse/private package index and configure
pip there. Merely
installing an editable package on the driver is insufficient. If using
`PIP_FIND_LINKS`, the path/URL must be accessible on the driver and workers, or
use the platform's worker wheel packaging where supported. Start with local
workers before moving to remote compute, such as a Slurm cluster or cloud hosts.

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

Add this reference to the metadata binding and each Azure mount (`input`, `cache`
and `artefact`) in your local `examples/compute.azure.yaml`. The checked-in
`compute.azure.example.yaml` uses Entra authentication by default.

In PowerShell, prompt for the connection string without writing it into command
history. The plaintext exists in the process environment so the SDK can use it:

```powershell
$azureSecret = Read-Host 'Paste the storage connection string' -AsSecureString
$env:EXPOPS_AZURE_CONNECTION_STRING = [System.Net.NetworkCredential]::new('', $azureSecret).Password
expops-azure init --compute examples/compute.azure.yaml --sample examples/data/samples.csv
expops-azure smoke --compute examples/compute.azure.yaml --workers 2
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

## Use Azure storage with your own project

Create a compute configuration with your project's compute settings and `code`
mount, selecting `azure-table` for metadata and `azure-blob` for the desired
input/cache/artefact mounts. Upload input files under the keys declared by your
project. The `init --sample` option uploads only the included demo's
`input/samples.csv`; upload other datasets separately using Azure tools or VFS.
Only the small demo CSV is included in this repository and source distributions.
Keep deployment credentials in your environment or secret manager. Validate with
local compute before moving to remote workers with packaged dependencies.

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
python -m pytest -m 'not integration'
$env:EXPOPS_AZURE_TEST_COMPUTE = (Resolve-Path examples/compute.azurite.yaml).Path
python -m pytest -m integration
```

Integration tests write uniquely named records/files to the selected test account.
Use a dedicated account/table/container set, not production data. All cloud setup
commands above are manual; no cloud resources are created by running unit tests.

- [ExpOps plugin contracts](https://github.com/local-minima-lab/expops-platform/blob/staging/docs/storage-plugins.md)
- [Azure Blob Python SDK](https://learn.microsoft.com/azure/storage/blobs/storage-quickstart-blobs-python)
- [Azure Tables Python SDK](https://learn.microsoft.com/python/api/overview/azure/data-tables-readme?view=azure-python)
- [Azure Storage account creation](https://learn.microsoft.com/azure/storage/common/storage-account-create?tabs=azure-cli)
- [Blob data authorization with Azure CLI](https://learn.microsoft.com/azure/storage/blobs/authorize-data-operations-cli)
- [Azure Table data authorization](https://learn.microsoft.com/azure/storage/tables/authorize-access-azure-active-directory)

To remove the trial services later, review the dedicated resource group in Azure
Portal and delete that group once you no longer need its records and files.
