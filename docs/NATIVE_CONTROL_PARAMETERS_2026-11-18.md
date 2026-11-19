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

## Normal conversational designer acceptance — 2026-11-19

The paired Portal now exports an active whole-output I/O definition and its exact
compiler digest from the same owned `runtime-capability` route. Parameter exports
retain their current provider review/runtime checks; an I/O-only operation needs an
active supported I/O contract and eligible owned service. Install each service's
export read-only and set `SERVICE_PARAMETER_CONTRACT_FILE` independently in each
process. The loader verifies the installed operation, supported parameter mappings,
I/O definition hash and transport. The renderer declares I/O without inventing
adjustable settings. A parameter-only older runtime fails normal connection preflight.

Qualification exports remain draft: `qualification_only` is accepted only with
`CONTROL_PARAMETER_QUALIFICATION_MODE=true` in an isolated qualification environment.
An export does not activate a provider contract. Never enable this flag in an
unreviewed normal or production environment to bypass publication.

Renderer outputs now include `artifact_manifest` version `1.0`, kind
`task-artifacts-v1`, exact workflow/logical task IDs and a bounded list of SVG file
references with SHA-256 and byte counts. SVG and report files use exclusive creation;
unsafe identities and replay after a restart fail before dispatch. In-process status
storage remains a single-process limitation, not a distributed restart guarantee.

Portal derives preview authority only from the owned deployment/submission, exact
compiled task, server-observed parent output, workflow/task-local paths and verified
file hashes. It pins the parent report in its existing receipt store. It does not
fetch arbitrary URLs or grant service-ID exceptions. `upstream_execution` continues
to describe the producer; it is not renderer algorithm-consumption evidence.

Fresh Windows acceptance used Python 3.12.14, PyYAML 6.0.3, existing source dependencies
and new isolated Redis/state. These are host source processes, not a new container
image qualification. The existing ML image's PyYAML 5.4.1 is below the Danish declared
minimum and was not substituted. No dependency installation or full draft-stack
deployment was necessary.

A separate designer used a fresh goal, actual fixture catalogue discovery,
clarification, reviewed connected proposal, explanation, setting edit, compatible
add and replacement on one canonical saved solution. The assistant replies were
explicit scripted doubles, not live model acceptance. Normal validation/preflight
and explicit browser Run produced workflow `wf-7596a65908e7` (2 topologies/2 SVGs)
then `wf-04a2b43ae794` (3 topologies and 3 SVGs from each of two drawing instances).
Actual generator instrumentation reports consumed `how_many` 2 then 3; output/input
and SVG hashes are captured per workflow/task. Original results remain unchanged.
The matching Portal evidence report contains safe details and ownership checks.

These are DEVELOPMENT SOFTWARE FIXTURES approved through normal development routes,
not Danish/Portuguese partner scientific approval. Owner-approved inputs, semantic
contracts, safe defaults/limits, partner-hosted runtime and release operations remain
separate. Portuguese DGAN runs/demos and closed provider spending scopes were preserved.
