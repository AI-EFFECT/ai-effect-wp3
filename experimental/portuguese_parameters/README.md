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
