# HTTP instance configuration 1.0

The Solution Studio operation registry owns execution parameter definitions. Saved
instances pin a registry contract ID and version. Registry IDs are local; wrappers
match the stable operation identity, contract version and protocol, while Studio
also compares a canonical definition digest (including constraints/defaults).

`services/parameter_contract.py` is an exact versioned export of Studio's portable
validator. `parameter_contract_provenance.json` identifies its source and digest.
Regenerate it in Studio; never edit a separate implementation here. No Dutch or
Danish operation definitions are embedded in the orchestrator.

Legacy workflow submissions omit `task_configurations`; they keep the original
control request body and do not require capability discovery. Configured requests
use `http-parameters-v1` and six envelope fields: `contract_id`, `contract_version`,
`operation_identity`, `schema_version`, `parameters` and `instance_id`.

GET `/capabilities` advertises wrapper verification. POST
`/workflows/check-compatibility` parses the graph and probes each configured target's
GET `/control/capabilities`. It writes no Redis state and dispatches no work.
POST `/workflows` repeats the same check before workflow initialization/enqueue.
The worker repeats target verification immediately before control execution.
Absent, unknown, ambiguous or incompatible declarations reject configuration;
there is no downgrade to service defaults. Studio additionally requires the exact
registry definition digest before submitting. Capability checks cannot guarantee
availability between preflight and dispatch or prove algorithm consumption.

Configurations are immutable Redis hashes scoped by workflow and graph node key;
atomic WATCH transactions reject replacement. Instance IDs, deterministic runtime
task IDs and external service task IDs are distinct. The worker records their
association before polling, and polls/reads output with the external ID. GET
`/workflows/{id}/instance-associations` returns these records under the existing
orchestrator bearer-key policy. Historical runs return an empty association list
when no record existed; synchronous wrappers need no external ID. No inferred or
invented external ID is added to old evidence.

Keep the existing ORCHESTRATOR_API_KEY and per-workflow services_api_key semantics.
Studio enforces caller ownership for snapshots/evidence. This boundary remains a
trusted internal API; deployments must configure the orchestrator key when exposed
outside that trust boundary. Configuration does not carry secrets or deployment
settings. Neither a capability response nor wrapper receipt proves consumption.

Rollback retains core workflow/task serialization, plus additive configuration and
association Redis keys. Old code ignores the extra keys; preserve them. Old runtimes
must receive legacy workflows only: current Studio rejects their configured runs.
Do not delete hashes, flush Redis or reset volumes. Existing algorithm evidence
belongs to its original image/source versions and remains readable on updated
images. This integration has offline HTTP/Redis tests and read-only local profile
checks; no additional real execution or production compatibility is claimed.

The separate legacy `ControlClient.execute(parameters=...)` helper was restored in
its own commit. It is not a workflow API escape hatch: unsupported workflow request
fields remain forbidden, and validated configuration cannot be mixed with it.
