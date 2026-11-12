"""OFFLINE actual FastAPI routes with upstream doubles; no ML/network execution."""
import base64
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import threading

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from experimental.portuguese_parameters.runtime import Sandbox, CONTRACTS, VERSION, create_app, capabilities, definition, verified_count


@pytest.fixture
def env(tmp_path):
    dataset = b"datetime,value\n2026-01-01,1\n"
    (tmp_path / "dataset.csv").write_bytes(dataset)
    policy = {"owner_id": "test-designer", "sequence_len": 100, "model_ref": "approved-model", "dataset_ref": "approved-data",
        "dataset_sha256": hashlib.sha256(dataset).hexdigest()}
    calls = []
    def upstream(request):
        calls.append(request)
        if request.url.path == "/train": return httpx.Response(200, text="Model trained successfully.")
        if request.url.path == "/models":
            with sandbox.db() as db:
                jobs = db.execute("SELECT id FROM jobs WHERE method='TrainModel'").fetchall()
            return httpx.Response(200, json=[{"name": "model_" + jobs[-1][0][:32]}])
        count = int(request.url.params["number_of_examples"])
        return httpx.Response(200, text="datetime,value,example\n" + "".join(f"2026-01-01,{row},{example}\n" for example in range(1, count + 1) for row in range(100)))
    sandbox = Sandbox(tmp_path, policy, token="x" * 40, upstream="http://upstream", self_url="http://adapter",
        transport=httpx.MockTransport(upstream))
    client = TestClient(create_app(sandbox), headers={"Authorization": "Bearer " + "x" * 40})
    yield sandbox, client, calls
    sandbox.executor.shutdown(wait=True)


def body(method="GenerateData", params=None, task="task-one", **extra):
    from experimental.portuguese_parameters.runtime import canonical
    return {"method": method, "workflow_id": "wf-one", "task_id": task,
        "inputs": [{"protocol": "inline", "format": "json", "uri": base64.b64encode(json.dumps(
            {"model_ref": "approved-model"} if method == "GenerateData" else {"dataset_ref": "approved-data"}).encode()).decode()}],
        "configuration": {"schema_version": "1.0", "contract_id": CONTRACTS[method]["id"], "contract_version": VERSION,
            "operation_identity": CONTRACTS[method]["identity"], "instance_id": "instance-" + task,
            "parameters": canonical.effective_parameters(definition(method), {}) if params is None else params}, **extra}


def model_ready(sandbox):
    with sandbox.db() as db: db.execute("INSERT INTO models VALUES(?,?,?,?)", ("approved-model", "test-designer", "derived-user", "isolated-model"))


def complete(env, payload):
    sandbox, client, _ = env
    response = client.post("/control/execute", json=payload)
    assert response.status_code == 200, response.text
    identity = response.json()["task_id"]
    # Deterministic synchronization with the real adapter executor, not sleeps/retries.
    sandbox.executor.submit(lambda: None).result(timeout=10)
    return identity, client.get("/control/status/" + identity).json()


def test_defaults_exact_mapping_output_semantics_and_independent_instances(env):
    sandbox, client, calls = env; model_ready(sandbox)
    first, status = complete(env, body())
    assert status["status"] == "complete"
    second, status = complete(env, body(params={"number_of_examples": 20}, task="task-two"))
    assert status["status"] == "complete" and first != second
    assert [dict(r.url.params) for r in calls] == [
        {"user_id": "derived-user", "model_name": "isolated-model", "number_of_examples": str(n), "return_csv": "true"} for n in (10, 20)]
    one = client.get("/control/data/" + first).content; two = client.get("/control/data/" + second).content
    assert verified_count(one, 10)["csv_rows"] == 1000 and verified_count(two, 20)["csv_rows"] == 2000
    assert client.get("/control/data/" + first).content == one
    assert client.post("/control/execute", json=body(task="third")).status_code == 422
    assert len(calls) == 2


@pytest.mark.parametrize("params", [{"number_of_examples": n} for n in (True, "10", 10.0, None, 0, 21)] + [{"unknown": 1}])
def test_invalid_values_rejected_before_execution_or_mutation(env, params):
    sandbox, client, calls = env; model_ready(sandbox)
    assert client.post("/control/execute", json=body(params=params)).status_code == 422
    with sandbox.db() as db: assert db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0
    assert not calls


@pytest.mark.parametrize("field,value", [("schema_version", "99"), ("contract_version", "1.0.0"), ("operation_identity", "foreign"), ("contract_id", "foreign")])
def test_version_and_identity_drift(env, field, value):
    sandbox, client, calls = env; model_ready(sandbox); value_body = body(); value_body["configuration"][field] = value
    assert client.post("/control/execute", json=value_body).status_code == 422 and not calls


def test_conflicting_legacy_sources_and_reference_ownership(env):
    sandbox, client, calls = env; model_ready(sandbox)
    assert client.post("/control/execute", json=body(parameters={"number_of_examples": 20})).status_code == 422
    for value in ({"model_ref": "someone-elses-model"}, {"model_ref": "approved-model", "user_id": "victim"}, {"model_name": "../../escape"}):
        request = body();request["inputs"][0]["uri"] = base64.b64encode(json.dumps(value).encode()).decode()
        assert client.post("/control/execute", json=request).status_code == 422
    with sandbox.db() as db: db.execute("UPDATE models SET owner='other'")
    assert client.post("/control/execute", json=body()).status_code == 422
    assert not calls and client.get("/control/capabilities", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_replay_is_idempotent_and_configuration_immutable(env):
    sandbox, client, calls = env; model_ready(sandbox)
    identity, _ = complete(env, body())
    assert client.post("/control/execute", json=body()).json()["task_id"] == identity
    assert client.post("/control/execute", json=body(params={"number_of_examples": 20})).status_code == 422
    assert len(calls) == 1
    (sandbox.storage / (identity + ".csv")).write_bytes(b"corrupt")
    assert client.get("/control/data/" + identity).status_code == 409


def test_synchronous_train_mapping_isolated_model_and_one_job(env):
    sandbox, client, calls = env
    identity, status = complete(env, body("TrainModel"))
    assert status["status"] == "complete"
    args = dict(calls[0].url.params)
    assert args == {"user_id": "sandbox_" + hashlib.sha256(b"test-designer").hexdigest()[:20],
        "model_name": "model_" + identity[:32], "index_col": "datetime", "sequence_len": "100", "overwrite": "false", "epochs": "1", "batch_size": "4"}
    assert calls[0].method == "POST" and b'name="uploaded_file"' in calls[0].content
    assert [r.url.path for r in calls] == ["/train", "/models"]
    assert client.post("/control/execute", json=body("TrainModel", task="other")).status_code == 422
    assert json.loads(client.get("/control/data/" + identity).content) == {"model_ref": "approved-model"}


@pytest.mark.parametrize("failure", ["timeout", "accepted", "wrong-count"])
def test_failure_uncertainty_and_no_automatic_redispatch(env, failure):
    sandbox, client, calls = env;model_ready(sandbox)
    def bad(request):
        calls.append(request)
        if failure == "timeout": raise httpx.ReadTimeout("unknown")
        return httpx.Response(202 if failure == "accepted" else 200, text="value,example\n1,1\n")
    sandbox.transport = httpx.MockTransport(bad)
    identity, status = complete(env, body())
    assert status["status"] == "failed"
    assert client.post("/control/execute", json=body()).json()["status"] == "failed"
    assert client.post("/control/execute", json=body(task="replacement")).status_code == 422
    assert len(calls) == 1 and client.get("/control/output/" + identity).status_code == 409


def test_capability_declarations_are_only_implemented_operations(env):
    _, client, _ = env
    value = client.get("/control/capabilities").json()
    assert value == capabilities() and not value["partner_native"]
    assert {o["operation_name"] for o in value["operations"]} == {"GenerateData", "TrainModel"}


@pytest.mark.parametrize("params", [{"epochs": v, "batch_size": 4} for v in (True, "1", 0, 3)] + [{"epochs": 1, "batch_size": v} for v in (True, 1, 17)])
def test_training_constraints_are_strict_before_upstream(env, params):
    sandbox, client, calls = env
    assert client.post("/control/execute", json=body("TrainModel", params)).status_code == 422
    assert not calls


def test_training_acceptance_is_not_completion(env):
    sandbox, client, calls = env
    def accepted(request):
        calls.append(request); return httpx.Response(202, text="accepted only")
    sandbox.transport = httpx.MockTransport(accepted)
    identity, status = complete(env, body("TrainModel"))
    assert status["status"] == "failed" and len(calls) == 1
    with sandbox.db() as db: assert db.execute("SELECT count(*) FROM models").fetchone()[0] == 0


def test_concurrent_replay_reserves_one_dispatch(env):
    from concurrent.futures import ThreadPoolExecutor
    sandbox, client, calls = env; model_ready(sandbox)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses=list(pool.map(lambda _:client.post("/control/execute", json=body()), range(2)))
    sandbox.executor.submit(lambda:None).result(timeout=10)
    assert all(r.status_code == 200 for r in responses)
    assert len({r.json()["task_id"] for r in responses}) == 1 and len(calls) == 1


def test_restart_preserves_uncertain_state_and_blocks_new_dispatch(env):
    sandbox, client, calls = env; model_ready(sandbox)
    with sandbox.db() as db:
        db.execute("INSERT INTO jobs VALUES('crashed','fingerprint','GenerateData','{}','running',NULL)")
    restarted=Sandbox(sandbox.storage,sandbox.policy,token=sandbox.token,upstream=sandbox.upstream,self_url=sandbox.self_url,transport=sandbox.transport)
    try:
        with TestClient(create_app(restarted),headers={"Authorization":"Bearer "+sandbox.token}) as other:
            assert other.get("/control/status/crashed").json()["status"] == "failed"
            assert other.post("/control/execute",json=body(task="replacement")).status_code == 422
        assert not calls
    finally: restarted.executor.shutdown(wait=True)
