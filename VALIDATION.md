# Validation record

Validated on 26 October 2026 (Asia/Singapore), Windows, Python 3.14.7.

## Installation boundary

- External distribution: `expops-azure==0.1.0`, installed as a non-editable wheel
  in `expops-azure/.venv`.
- Host platform snapshot: `expops-platform` branch `staging`, commit
  `51abb9aa3f303246df82c859688c0f534ab4a8b2`.
- A clean `git archive HEAD` export was built outside the platform repository
  as `expops-0.1.28.dev37-py3-none-any.whl`, then installed in the isolated
  environment. The development version was supplied explicitly for that
  source export; it is not a newly published platform release.
- No source edits, manual registry injection, monkeypatches, or editable imports
  into the platform were needed. Its working tree remained clean.
- The plugin imports ExpOps' public storage contracts, descriptors and credential
  interfaces. The smoke command additionally exercises the existing public
  storage factory, managed-files service and state manager.
- Entry points were found from installed distribution metadata, including in
  independent Dask worker processes.

## Dependencies actually exercised

| Dependency | Version |
| --- | --- |
| azure-storage-blob | 12.31.0 |
| azure-data-tables | 12.7.0 |
| azure-identity | 1.26.0 |
| Azurite | 3.37.0 |

Azurite ran as a project-local Node process on loopback ports 10000/10001/10002
with `--skipApiVersionCheck`. Its public emulator account key supplied test
authentication. No live Azure credentials were inspected or used.

## Checks passed

`python -m pytest -q`: **23 passed**, including seven integration tests.

- Installed entry-point discovery and typed-spec JSON reconstruction.
- Offline configuration validation, endpoint credential rejection, path containment.
- Declared worker dependencies and explicit environment credential handling.
- VFS text/binary I/O, file sizes, listing, globbing, missing files, capability
  errors, exclusive writes, exact removal and closed-session behavior.
- Exceptions inside write contexts preserve old bytes and publish no new file.
- A 10 MiB blob write crosses the spool's memory threshold and streams back with
  a matching SHA-256 digest.
- Cache/step indices, strict hash misses, failed-record exclusion, batch lookup,
  run lifecycle/history, probe metrics, chart indices/copying and project isolation.
- Large Unicode metadata spans binary properties and round-trips without loss;
  records beyond the explicit size limit are rejected.
- Forty-eight counter increments through four independent Table clients, without
  lost updates, exercising create races and ETag compare-and-swap.
- Missing containers/tables raise service errors rather than pretending to be
  successful empty storage or cache misses.
- Managed file publication/read-back and actual `StepStateManager` cache reuse.
- Two process-based Dask workers reconstruct the public storage plan, use their
  own clients, publish/read managed files and update the shared counter.

## Ordinary experiment UX

Both runs used the installed `expops` CLI and `examples/demo`, with the Azure
plugins installed through wheels, using the Azurite configuration:

| Run | Run ID | Executed | Cached | Failed | CLI exit |
| --- | --- | --- | --- | --- | --- |
| First | project-azure-plugin-demo-20261025192242-5c252fdd | 2 | 0 | 0 | 0 |
| Second | project-azure-plugin-demo-20261025192330-b3abdfa9 | 0 | 2 | 0 | 0 |

Run IDs use the platform's UTC timestamp; the Singapore calendar date is 26
October. Logs are retained in `.build/demo-first.log`, `.build/demo-second.log`
and `examples/.azure-plugin-demo/logs/`.

The experiment itself imports no Azure SDK. It reads the declared CSV, logs
metrics, returns values, and consumes the predecessor result. The smoke check
separately verifies managed artefact publication and indexing.

## UX findings and limits

- At validation time PyPI's latest stable `expops` was 0.1.27, which predates the
  required extension interfaces. An unbounded dependency could select it in a
  newly created named environment. The external package now requires
  `expops>=0.1.28.dev37`; a compatible supplied wheel is necessary until a
  suitable platform release is published.
- The unpublished plugin wheel must also be available to automatic environment
  creation. The trial used `PIP_FIND_LINKS` with `dist/` and `.build/wheels/`.
  Remote deployments need an accessible wheelhouse/private index or configured
  platform wheel transfer; a driver-only editable install does not prove this.
- The ordinary CLI writes a project interpreter pointer into the current user's
  home cache. Sandbox permission prevented this initial attempt; the CLI trial
  needed normal user-cache write access. No platform path logic was patched.
- Python's wheel build initially produced restrictive temporary-file ACLs on
  Windows. Inheritance was restored on the two generated distribution files so
  the interactive user can read/install them. No general workspace ACL changes
  were performed.
- Real Azure Entra authentication, RBAC propagation, account networking, service
  limits and cloud fault behavior remain **unverified**. Azurite is not full
  Azure parity. The explicit service-principal path has a construction unit
  test; it has not contacted Entra ID.
- NUS SLURM, geographically separate workers, automatic remote environment
  bootstrap, full fraud training and scale/performance benchmarks were not run.
- This is an independently packaged functional implementation. See README's
  scope/limits for spooling, supported operations, metadata size/query design,
  retention and concurrency boundaries.

No Azure subscription resources were provisioned. The setup guide describes the
manual cloud steps needed next.
