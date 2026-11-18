# Portuguese source-API configuration connector — 18 November 2026

Status: implemented and source-wiring verified. Real DGAN/model, Docker, Windows and independent user qualification remain pending. This is a node connector for the existing shared `http-parameters-v1` contract, not a new Portal workflow architecture. The original fixed-value handlers and source ML API are unchanged.

## Supported mapping

| Reviewed setting | Source endpoint and argument |
| --- | --- |
| `epochs` | `POST /train` query `epochs`, passed to `train(..., epochs=...)` |
| `batch_size` | `POST /train` query `batch_size`, passed to `train(..., batch_size=...)` |
| `sequence_len` | `POST /train` query `sequence_len`, passed to `train(..., sequence_len=...)` |
| `number_of_examples` | `GET /generate` query `number_of_examples`, passed to `generate(..., num_examples=...)` |

The legacy adapter's `username`/`max_epochs` query names differ from this source API's `user_id`/`epochs`. Enabling this opt-in connector avoids that mismatch. It does not modify a retained legacy deployment.

No scientific limits, approved defaults, service IDs, reviewer IDs or fixed workflows are installed in code. Contracts supply meanings, values, limits, identity and version. Dataset bytes and model selection remain separate input references. `return_csv=true`, unique model naming, `overwrite=false`, namespace and the dataset index column are connector/operator behavior, not invented user-adjustable parameters.

The connector checks the actual upstream OpenAPI query names/types before dispatch. This establishes interface agreement, not implementation/version equivalence. Operators still need image/source provenance and actual algorithm evidence.

## Isolated operator configuration

Both `portugal-node-sidecar/common` and `portugal-node-integrated/common` carry the identical connector and maintained portable validator. The existing sidecar Dockerfile already copies the complete `common/` package. No compose file, running image or old service volume has been changed.

| Environment | Required meaning |
| --- | --- |
| `SERVICE_PARAMETER_CONTRACT_DIR` | Read-only directory containing only the normal Portal capability exports for installed operations. Without it, the legacy runtime remains selected. |
| `CONTROL_PARAMETER_STATE_DIR` | New, dedicated, persistent writable directory. Existing unrecognised nonempty directories and symlink roots are refused. One running connector process per directory. |
| `SERVICE_API_KEY` | Private Control bearer key, provided through the deployment's secret mechanism; never a parameter or exported definition. Use the existing WP3 `services_api_key` transport. |
| `SYNTHETIC_DATA_URL` | Exact upstream source-API base URL, reachable on a private network. Its direct training/generation endpoints must not be exposed as an unauthenticated public bypass. |
| `SELF_URL` | Reachable configured connector base URL, used for output references. |
| `SYNTHETIC_DATA_USER_NAMESPACE` | Fresh operator-owned namespace for qualification; never reuse the retained demo's models or namespace. |
| `SYNTHETIC_DATA_INDEX_COLUMN` | Actual selected training CSV index column, required when training is installed. |
| `SYNTHETIC_DATA_INPUT_ROOT` | Optional approved directory for separately selected local CSVs; paths outside it are refused. |
| `SYNTHETIC_DATA_INPUT_ORIGINS` | Optional comma-separated exact HTTP(S) origins for separately selected remote data. Redirects are refused. |
| `SYNTHETIC_DATA_INPUT_TOKEN` | Optional separate data-provider bearer token; ML/Control keys are not forwarded to a data provider. |
| `SYNTHETIC_DATA_API_KEY` | Optional upstream ML API bearer token. |
| `CONTROL_PARAMETER_QUALIFICATION_MODE=true` | Required to install exports marked `qualification_only`. Use only in a separate qualification runtime. This does not publish a draft or make it available to normal designers. |
| `SYNTHETIC_DATA_SHARED_MODELS_FILE` | Optional operator-installed JSON mapping opaque model references to explicit `service_callers` shares and source model identities. Enables standalone generation without a mandatory training step. |

Runtime capability exports require normal reviewed/active contracts. Draft qualification exports come from the existing authenticated `/operation-contracts/{id}/qualification-capability` endpoint and stay isolated. Do not tick provider attestations based on these source-wiring fixtures.

## Dispatch and result behavior

A configured request must contain the exact reviewed effective values in its configuration envelope and one separate input reference. Invalid types, versions, unknown keys, unsupported mappings, legacy parameter mixing and missing configuration for an installed operation are rejected before dispatch. Other unconfigured deployments retain their existing path.

Workflow and logical task identities produce a unique physical task ID. The connector writes a durable acceptance journal before queueing. Repeated requests cannot overwrite a result or redispatch, including after restart. A single worker serialises source operations. A restart with unresolved work, a timeout, an unexpected synchronous response or an inconsistent output leaves an uncertain result; new and already queued work stop until an operator reconciles it. No automatic retry is performed. Reconciliation tooling is not implemented: preserve the directory and resolve the upstream outcome before an operator-led recovery; do not erase it or select a fresh directory to bypass uncertainty.

Training completion requires synchronous HTTP 200 plus the new model appearing in `/models`. Progress counters do not establish completion. Outputs expose an opaque model reference; a private model may only be consumed inside its originating workflow. An explicitly declared shared model can instead be selected for a standalone generation operation. Generated CSVs must contain source-declared `example` markers and the requested sequence count. Downloaded inputs and streamed outputs have operator/connector byte ceilings, separate from scientific limits.

Authenticated `/control/receipt/{physical_task_id}` records the immutable envelope, delivered query, source OpenAPI hash, input hash, task association, output hash and CSV observations. `/control/data/{id}` verifies output bytes against their recorded hash. An HTTP acknowledgement never produces an `algorithm consumed: verified` claim. Real model inspection/instrumentation is still necessary.

Normal Portal result inspection currently qualifies owned file/inline references. These new authenticated HTTP references and runtime receipts are **not yet integrated into that normal preview/evidence path**. Do not expose unrestricted remote fetching or remove ownership guards. This is an explicit remaining connection after runtime qualification.

## Verification and remaining dependencies

`tests/test_portuguese_configured_runtime.py` loads the unchanged real Portuguese FastAPI source, executes its query/multipart binding, training dispatch, model listing and CSV construction, and substitutes only the DGAN dependency with an explicit double. It verifies two different configurations, retained output bytes, model associations, standalone declared-model generation, failure/authentication/ownership/uncertainty cases and both deployment copies. Synthetic bounds and model files are labelled fixtures. This is not real ML training or scientific acceptance.

Run with the node's pandas dependency and test dependencies available: `pytest -q tests/test_portuguese_configured_runtime.py tests/test_native_control_parameters.py`. The accompanying Portal evidence file records delivered arguments and CSV observations without credentials. The canonical validator can be checked from Portal with `python deploy/aie18/sync_wp3_contract.py --wp3-root /path/to/wp3 --check`.

The actual DGAN package/image, its observed effective batch handling, fresh persisted model/output access, owner-confirmed limits/defaults, index/data suitability and runtime provenance require Windows/node inspection. Docker, a real model run, Portal HTTP result/receipt ingestion and normal-user acceptance are not established by these tests. Production and paid-model acceptance remain separate.
