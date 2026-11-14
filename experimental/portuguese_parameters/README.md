# EXPERIMENTAL Portuguese instance parameters

Platform-controlled **sidecar**, not a Portuguese-owner-approved/native service.
Dependency: maintained WP3 PR #3 at `0221b7ecd4a2d5f072de640350ebc599df22131a`.
Original `use-cases/portugal-node-*` paths, images, registrations and volumes are
unchanged. This directory never imports their handlers or introduces another
configuration transport.

## Source-backed target and explicit mapping

Selected existing image:
`sha256:557246d79612eaa2106266d60cc6b36970634afebb659e371658cce6a1f1f61f`
(`pt33-tef-integrated-synthetic-data`, linux/amd64, 6,439,906,123 bytes).
Its `/app/main.py` SHA256 is
`0e4caddd200e5366b504c3cce2df957fbd6a97beb4418b25613fa8d778e6e322`,
`dgan.py` SHA256
`0a027576bb25656e4541dd822bf0f2395a79bb1cb377e1d11fb509dca9f79d54`.
Installed: Python 3.9 image family, torch 1.13.1+cpu, gretel-synthetics 0.22.20,
pandas 1.5.3, numpy 1.23.5, FastAPI 0.115.14. These are observed inputs, not an
owner attestation or whole-image reproducibility claim. No pull is required.

| Operation | Actual selected API | Experimental settings |
|---|---|---|
| GenerateData | GET `/generate`: `user_id`, `model_name`, `number_of_examples` (API default 1), `return_csv` (default true) | integer `number_of_examples`, adapter default 10, sandbox range 1–20; CSV fixed |
| TrainModel | POST `/train`: multipart `uploaded_file`; `user_id`, `model_name`, `index_col` (default datetime), `overwrite` (false), `sequence_len` (100), `batch_size` (1000), `epochs` (10) | integer epochs default 1, sandbox 1–2; batch_size default 4, sandbox 2–16 |

These upper/lower sandbox limits are resource policy, **not API-enforced bounds
or scientific recommendations**. The API accepts integers without these numeric
constraints. DGAN forms `floor(rows/sequence_len)` training sequences, truncates
leftover rows and internally clamps batch_size to the number of sequences. A
real qualification must record that effective value separately if it differs.
Sequence length is fixed at the selected API default 100 and is not exposed.

Training is **synchronous** in this image: it calls train and saves artifacts
before returning HTTP 200. This adapter uses a background worker and reports
running until that call completes and the new model is listed. HTTP 202, progress
epoch counters or a file partially written during training are not completion.
The original wrapper's `username`, `max_epochs`, `output_format`, overwrite=true
and async-poll assumptions are deliberately not reused. No fallback mapping exists.

Generation returns a list of time-series DataFrames. CSV attaches `example=1..N`
before concatenation; an example means **one sequence**, not one CSV row. With
fixed length 100, counts 10/20 must yield 1,000/2,000 rows and exactly 10/20
distinct sequence markers, each of length 100. The adapter validates actual bytes
and records their hash. Parquet/ZIP is unavailable in this first contract.

## Protocol, authority and persistence

The six-field `configuration` is exactly the maintained `http-parameters-v1`
envelope. `runtime.py` loads `orchestrator/src/services/parameter_contract.py`
directly, verifies it against the existing provenance SHA256
`1861736c1bd9c8a1e4d3aa86b1a2f58ef27bf5e272d682a619062cfb8b7171aa`,
and calls its envelope/capability/digest/effective-value validators. No duplicate
validation semantics or validator copy is maintained here. Defaults are materialized
by the normal platform compiler; submitted envelopes must already be effective.
Capabilities advertise only the two implemented operations at experimental 0.1.0.

Settings are separate from input references, authentication and deployment. The
single isolated principal requires a private service bearer token. Inputs carry
only an allowlisted opaque `model_ref` or `dataset_ref`; they cannot select a user,
path, arbitrary URL, overwrite flag or output format. Dataset bytes are pinned by
SHA256. Model user/name are derived server-side from the fixed sandbox owner and
workflow/task identities; no original model directory is mounted. This is a
single-principal experimental boundary, not a general multi-tenant model service.

A separate SQLite dispatch journal is FULL-synchronous. It binds immutable request
hashes to workflow/task identities **before** any upstream call, limits the entire
profile to one training and two generation jobs, and returns the prior task on an
identical replay without redispatch. Conflicting replays fail. Restart marks
in-flight work uncertain; failed/uncertain work stops further dispatch. HTTP
clients have no retry, redirect or environment-proxy fallback. Output files are
task-specific and downloads revalidate their hashes. A timeout consumes the
attempt; it is never permission to try again. Separate model/output storage is
mandatory. Run only one adapter process against a journal.

## ApplyFunction assessment — unavailable

The recorded Knowledge Store API uses POST `/functions/apply`, multipart `file`,
`feature_function_name` and JSON-encoded `feature_function_kwargs_str`. Existing
handlers select `DatetimeFeatures` with `{"season":"hour"}` and serialize JSON
records to CSV. The function registry and accepted kwargs/schema must be pinned
and tested against a selected image before an allowlisted option can be exposed.
No arbitrary function, code or kwargs are accepted here; ApplyFunction is absent
from capabilities and must not block GenerateData qualification.

## Offline checks and runtime opt-in

Run with the existing Python dependency environment:

```powershell
python -m pytest experimental/portuguese_parameters/test_runtime.py -q
```

29 focused HTTP tests with upstream doubles passed at the initial source
checkpoint: defaults/types/bounds, version/identity drift, mapping, sequence
semantics, isolated references/models/outputs, immutable/conflicting/concurrent
replay, failure/uncertainty, restart and asynchronous acceptance vs completion.
These are **not** real ML training or generation evidence.

The entry point requires explicit `PT_SANDBOX_POLICY`, `PT_SANDBOX_TOKEN_FILE`,
`PT_SANDBOX_STORAGE`, `PT_SANDBOX_UPSTREAM`, `PT_SANDBOX_SELF_URL`. The policy names
the one owner, opaque model/dataset references, dataset SHA and fixed sequence
length 100. The token/policy/data/models/journal stay outside Git/build contexts.
The adapter Dockerfile reuses the retained validated Portal dependency image;
it performs no dependency install. The Portal review package supplies the separate
loopback-only project, synthetic bootstrap, resource caps and before-dispatch
snapshots. Publish that integration checkpoint before running any actual job.

Hosted node testing still requires owner/API-version confirmation, portable image
distribution, private service auth/network/storage design and multi-tenant model
authority. This local experiment does not establish production compatibility or
scientific suitability. Provider and spending controls are unrelated and remain
disconnected/exhausted.

## Completed bounded platform sandbox

Portal draft PR #22 (IRESI-EU/ai-effect-solution-studio) records the isolated
integration and actual evidence. The sole training completed with epochs 1,
batch_size 4, max_sequence_len 100, sample_len 1 in the saved model. Two genuine
GenerateData workflows using that model produced 10/20 sequences and 1000/2000
CSV rows. First-run immutable settings/output and the model hash remained unchanged.
These are parameter/completion observations, not scientific quality assessment.
No original Portuguese file or deployment was edited. The sandbox's worker is
stopped and its durable one-training/two-generation ceiling is exhausted.

The initial 29 offline runtime tests were extended with public-health/private-
capability separation: 30 pass. The Portal report separates these upstream doubles
from genuine execution. build_images.py packages explicit committed source using
only verified retained local dependency images, without starting any service.
The original retained WP3 image had older readiness bytes; Wp3.Dockerfile adds
the exact maintained PR #3 source as a separate image rather than relabeling it.

## Version 0.2 chained path (separate authorization)

chain_contracts.json declares explicit whole-output-v1 model_ref forwarding from
TrainModel into GenerateData. The HTTP JSON reference is resolved locally against
the same authorized producer workflow/task/owner and current model artifact; no
arbitrary URL or path is fetched. Existing 0.1 contracts/references remain intact.
Models are run-specific. model_observer.py runs read-only with unchanged retained
ML dependencies to inspect saved configuration/hash, without an algorithm call.

authorization.py is the supported additive journal operator: create a versioned
attributable frozen policy, bind one returned workflow, inspect status, close.
It never resets legacy records/counts or marks running work uncertain merely to
read operator status. New tasks reserve once and cannot redispatch after an
uncertain response. Signed v2 captures permit read-only historical Portal checks.

52 focused experimental checks pass with protocol/model-loader doubles, including
legacy behavior, scoped dispatch, provenance, corruption, batch-size clamp and
uncertainty. The sole new combined real execution is pending the published
Portal integration checkpoint. Original Portuguese paths remain unchanged.

### Completed connected phase — 2026-11-13

The above pending statement records the source checkpoint. After publication of
Portal source/integration, exactly one newly authorized combined workflow
`wf-a891cece9438` completed: TrainModel `task_030152d9`, then GenerateData
`task_f6c5c14e`. Saved model configuration is epochs 2, batch_size 4,
max_sequence_len 100, sample_len 1. The consumer used the same producer model
SHA-256 `408519959182447598e94fe0e997d8ea5f1589b0f917e802dc6c2b67898f366e`;
CSV contains ten sequences of 100 observations (1000 rows), SHA-256
`f8b2ca463392571f573a899eabd414b7ee07101521782cfd3cfdca27e31d3d57`.

Runtime/contracts/observer/targeted-worker source was
`30e1ac403c7434149f3ff3568843f2cc75702413`. The new additive authorization
`pt-chain-20261113-01` is closed, targeted worker exited 0, daemon remains stopped
and queues are empty. Three original jobs, the prior model/CSV hashes, owner
designs and exhausted scope remain unchanged. Shared provider ledger stays 25/25;
no provider request or original Portuguese source/deployment change occurred.

[Portal PR #22](https://github.com/IRESI-EU/ai-effect-solution-studio/pull/22)
contains PORTUGUESE_CHAINED_WORKFLOW_2026-11-13.md, chain-verification.json and
rendered actual results. Historical evidence also passed a refreshed browser
inspection with the read-only observer stopped, then restored. These real local
argument/model/output observations are separate from the 52 protocol/loader
tests. They establish neither scientific suitability, partner approval nor
hosted/multi-tenant production readiness. No further execution is authorized.

### Owner's existing queued request recovered — 2026-11-14

Existing workflow `wf-375a888b8976` (solution
`d733ce01-6489-4865-aec5-c96ffdf2ec9b`) was reported running by WP3 after enqueue,
but both tasks remained pending and the root task was still queued. The daemon
and prior targeted worker had exited before submission, the adapter had no
reservation for this workflow, its derived upstream model identity was absent
and the earlier scope was closed. Missing capture alone was not treated as proof.

After the user's explicit authorization and immutable 1/4/10 effective-settings
checks, the existing supported journal operator created/bound a new attributable
scope `pt-owner-recovery-20261114-01` to this exact request. A uniquely named
targeted worker consumed the one existing root queue entry. No new workflow was
submitted, no prior scope reset, and no algorithm retried. This reused unchanged
runtime/targeted-worker source `30e1ac403c7434149f3ff3568843f2cc75702413`.

Both tasks completed once. Saved model records epochs 1, batch_size 4,
max_sequence_len 100 and sample_len 1; GenerateData consumed the exact producer
model, SHA-256 `b05b022b7c78d08f14877b66c1726481272794fad9c0eb3d3e7560a5740b12e5`.
CSV has ten sequences × 100 observations (1000 rows), SHA-256
`98fc83609c6690612b1fcd239ba45df167fa2915fdeee4ef13fa5b2ccf198f60`.
The scope is now closed, worker exited 0 and remaining queues are empty. Original
five jobs/model mappings/audit records and all earlier model/output hashes are
preserved; journal now contains seven completed jobs. Provider ledger unchanged
at 25/25. Original Portuguese source, algorithm, API and image remain unchanged.

Portal source checkpoint `002686f04fb584d5bea2f1556d8e994405f1db45` adds the
narrow inspection-only submission/readiness guard and task-fact status display,
without another WP3 job-management subsystem or runtime change. It refuses new
interactive submissions before enqueueing and distinguishes all-pending queued
tasks from claimed tasks. Terminal task evidence remains independently verified.

[Exact Portal recovery report](https://github.com/IRESI-EU/ai-effect-solution-studio/blob/002686f04fb584d5bea2f1556d8e994405f1db45/docs/PORTUGUESE_OWNER_RECOVERY_2026-11-14.md)
and [sanitized evidence](https://github.com/IRESI-EU/ai-effect-solution-studio/blob/002686f04fb584d5bea2f1556d8e994405f1db45/deploy/portuguese_sandbox/owner-recovery-verification.json)
are in existing draft PR #22. This WP3 follow-up changes documentation only;
no claim of a fresh WP3 test run is made. Scientific suitability, partner approval
and hosted production compatibility remain unverified. No further execution is
authorized by this record.
