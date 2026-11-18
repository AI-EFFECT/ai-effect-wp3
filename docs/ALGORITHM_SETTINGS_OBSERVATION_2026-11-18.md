# Optional task-bound algorithm observation

The connector can opt into `algorithm-settings-v1` independently of its existing
delivery receipt. Without explicit observation configuration, behavior is unchanged
and delivery establishes no model-consumption claim.

Install `SYNTHETIC_DATA_OBSERVATION_URL` as the trusted private runtime observation
base and `SYNTHETIC_DATA_OBSERVATION_KEY` as a separate private key. The source API
must implement the reviewed recorder; merely returning a successful HTTP response
does not satisfy this contract. The raw ML API stays private.

For each source request the connector sends `X-AI-Effect-Execution` containing only
the immutable workflow, logical/physical task, configuration, parameter-contract
digest and input digest. The separate key is sent in its own header, never the
document. After synchronous completion it reads the physical task's observation
with bearer authentication. No browser/catalogue URL installs this authority.

The exact top-level document fields are `schema_version: "1.0"`,
`kind: "algorithm-settings-v1"`, `binding`, `requested_parameters`,
`observed_parameters`, `model`, `implementation` and `basis`. The shared validator
checks all workflow/task/instance/operation/configuration/contract/input/source-response
bindings, exact parameter keys, finite numeric/type compatibility, actual model
file and weight SHA-256 values, positive weight-element count, implementation
source/image digests and a stated observation basis. Requested and observed
values can differ: a clamped batch remains a separately reported observation.

`model` carries `sha256`, `weights_sha256`, `weight_elements`. `implementation`
carries `name`, `source_sha256`, `image_sha256`. `binding` carries `workflow_id`,
`task_id`, `instance_id`, `operation_identity`, `configuration_sha256`,
`parameter_contract_sha256`, `input_sha256`, `source_response_sha256`.
Canonical JSON is UTF-8, sorted keys, compact separators and no nonfinite values.

An invalid/missing observation after a dispatched source call leaves an uncertain
outcome. No output is published and no retry bypasses reconciliation. Valid
observations and their digest are embedded in the existing immutable authenticated
receipt. Receipt/model-output storage and cross-workflow model restrictions remain.

The Portal requires a separately installed target declaration allowing this exact
observation contract, then revalidates ownership, immutable submission, endpoint,
task/input/output associations, receipt digest and bytes before projecting
`algorithm_consumed`. Delivery alone never populates that field. There are no
service-ID or workflow exceptions.

Windows software-fixture acceptance used the real DGAN image ending `1f1f61f`,
fresh models and the normal browser designer path. The Portal development branch
contains the recorder, independent saved-model reinspection and sanitised report.
The source/API double tests here verify transport and tamper rejection, not model
or browser acceptance. Provider scientific/data/index approval, production runtime
installation, PostgreSQL/release acceptance and paid assistant acceptance remain
separate gates. This opt-in recorder is development instrumentation, not a signature
from an independent scientific authority.
