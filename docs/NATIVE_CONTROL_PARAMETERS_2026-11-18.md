# Native Control instance settings

This change joins the existing `http-parameters-v1` transport to native service handlers. It reuses the maintained portable validator. There is no runtime service-ID registry or automatic provider approval.

## Current supported operation

The Danish `GenerateTopology` implementation consumes:

| Published setting | Actual enumerator argument |
|---|---|
| `allow_hx` | `enumerate_networks(..., allow_hx=value)` |
| `allow_bypass` | `enumerate_networks(..., allow_bypass=value)` |
| `how_many` | `enumerate_networks(..., max_results=value)` |

Definitions, defaults, permitted limits and operation identity/version come from a provider-reviewed contract installed by the operator. The repository does not ship a production-approved card. For configured values there is no silent fallback or clamping. Existing unconfigured fallback behaviour is retained. Scientific validity and resource limits require the Danish owner; the placeholder ValidateTopology operation is not qualification.

## Provider and operator handoff

1. Review the parameter card and actual runtime mappings through normal Portal onboarding, and supply real normal/invalid configuration test evidence. Complete normal service approval and publish the reviewed card.
2. Download the active card's runtime capability through the normal publisher (or its authenticated `/operation-contracts/{id}/runtime-capability` endpoint).
3. After runtime/release review, install this service source and mount that downloaded JSON read-only in the topology-generator container. Set `SERVICE_PARAMETER_CONTRACT_FILE` to its operator-controlled path. Keep `SERVICE_API_KEY` in the existing secret/configuration mechanism, outside parameter definitions.
4. Verify authenticated `/control/capabilities` and WP3 readiness against the exact saved contract before an explicit Run. A missing or incompatible installed card rejects configured requests. It does not fall back to a demo identity or inferred limits.
5. Keep the real shared data volume available to downstream services. Portal inspection additionally needs its authorised read-only artifact mount. This guide does not deploy or modify any running stack.

The enumerator input is a JSON object containing `run_config` with the actual supplies and loads. Instance settings override the corresponding upstream routing options without modifying the upstream artifact. This supports structured input without a mandatory Ollama/free-text step. Existing config-assistant defaults are not asserted as approved parameter defaults.

## Identity, outputs and results

The configured service returns its own task identity, derived from both workflow and logical task IDs. WP3 already records that external association and polls it. This prevents the same logical step in two runs from colliding in the service's in-memory task store. Results are written exclusively to the workflow/logical-task namespace; existing configured artifacts and in-memory task records cannot be overwritten by a replay.

The generator's output has an `execution` member containing the reviewed envelope, workflow/task/instance identities, actual enumerator arguments, consumed settings, parameter digest and input-content hash. The renderer carries the producer record as `upstream_execution`. These are node/runtime instrumentation records, not scientific certification. Portal inspection matches them against its immutable submission, observed output/input references and its existing digest receipts.

The configured output path uses exclusive creation, including after a restart. The existing TaskManager remains single-process and in-memory. A restart may remove status records; this change does not add a distributed task database or establish safe multi-process deployment.

## Shared handler contract

Services may expose a `configuration_capabilities()` hook and implement the advertised values in their own algorithm. Both shared concurrent and sequential handlers validate configured requests before dispatch. Unsupported services reject them. A capability declaration alone is not consumption proof; the provider must test the actual mapping. Legacy requests without configuration retain their existing handling.

`use-cases/common` and `use-cases/denmark-node/common` carry exact copies of the maintained parameter validator and provenance. Use the Portal's existing `deploy/aie18/sync_wp3_contract.py` to regenerate/check all copies; do not independently edit validators. Other services adopting this handler must use the matching complete `common/` package, rather than copying a handler file alone.

## Verification

`tests/test_native_control_parameters.py` runs the actual topology enumerator through the native HTTP handler, generates SVGs from its output, checks invalid/missing contracts, authentication and replay protection, and verifies vendor consistency. Numeric test limits and identities are explicitly synthetic fixtures.

The matching Portal's `test_native_control_execution.py` additionally runs normal provider/admin/designer API routes, real localhost HTTP, native Redis, the real WP3 engine/Worker and the two services. It verifies two configurations (1 and 3 topology/SVG results), owned preview, linked settings and unchanged earlier history. It uses an isolated SQLite Portal store and is not browser, PostgreSQL, Docker, partner-hosted or production acceptance.

No production service or retained demonstration is changed by these source commits.
