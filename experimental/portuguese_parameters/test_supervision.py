"""OFFLINE journal/processor tests; no Redis, HTTP network or ML execution."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import sqlite3
import time
import pytest
from .test_chain import chain, WF
from experimental.portuguese_parameters import supervision as gate
from experimental.portuguese_parameters.supervised_worker import serve


def prepared(env):
    env["operator"].create_authorization(env["grant"])
    return {k: deepcopy(env["grant"][k]) for k in ("authorization_id", "owner_id", "request_sha256", "tasks", "binding")}


def ready(env):
    value = prepared(env)
    gate.register(env["operator"], value["authorization_id"], "worker-one")
    gate.heartbeat(env["operator"], value["authorization_id"], "worker-one", lambda: None)
    return value


def test_readiness_never_consumes_or_dispatches_and_requires_waiting_processor(chain):
    value = prepared(chain)
    gate.initialize(chain["operator"])
    before = chain["operator"].view()
    with pytest.raises(ValueError, match="processing is unavailable"): gate.inspect(chain["operator"], value)
    gate.register(chain["operator"], value["authorization_id"], "worker-one")
    with pytest.raises(ValueError): gate.inspect(chain["operator"], value)
    gate.heartbeat(chain["operator"], value["authorization_id"], "worker-one", lambda: None)
    assert gate.inspect(chain["operator"], value)["status"] == "ready"
    assert gate.inspect(chain["operator"], value)["status"] == "ready"
    after = chain["operator"].view()
    assert before["authorizations"] == after["authorizations"]
    assert before["scoped_jobs"] == after["scoped_jobs"] == []
    assert chain["calls"] == []


@pytest.mark.parametrize("field", ["owner_id", "request_sha256", "tasks", "binding", "authorization_id"])
def test_exact_authority_drift_denied_without_mutation(chain, field):
    value = ready(chain)
    value[field] = {} if field in {"tasks", "binding"} else "changed"
    before = chain["operator"].view()
    with pytest.raises(ValueError): gate.inspect(chain["operator"], value)
    assert before == chain["operator"].view() and not chain["calls"]


def test_expiry_failed_probe_closed_scope_and_dataset_corruption_fail_closed(chain):
    value = ready(chain)
    with pytest.raises(ValueError): gate.inspect(chain["operator"], value, now=time.time() + 20)
    def failed(): raise RuntimeError("Redis or upstream unavailable")
    with pytest.raises(RuntimeError): gate.heartbeat(chain["operator"], value["authorization_id"], "worker-one", failed)
    (chain["sandbox"].storage / "dataset.csv").write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="integrity"): gate.inspect(chain["operator"], value)
    chain["operator"].close_authorization(value["authorization_id"], "test-owner")
    with pytest.raises(ValueError): gate.inspect(chain["operator"], value)
    assert not chain["operator"].view()["scoped_jobs"] and not chain["calls"]


def test_bind_and_processor_claim_are_one_shot_under_concurrency(chain):
    value = ready(chain)
    def bind():
        try: return gate.bind(chain["operator"], value, WF, "reviewer")["status"]
        except ValueError: return "denied"
    with ThreadPoolExecutor(2) as pool: assert sorted(pool.map(lambda _: bind(), range(2))) == ["bound", "denied"]
    def claim():
        try: return gate.claim(chain["operator"], value["authorization_id"], "worker-one")
        except ValueError: return "denied"
    with ThreadPoolExecutor(2) as pool: assert sorted(pool.map(lambda _: claim(), range(2))) == ["denied", WF]
    with pytest.raises(ValueError): gate.inspect(chain["operator"], value)
    with pytest.raises((ValueError, sqlite3.IntegrityError)): gate.register(chain["operator"], value["authorization_id"], "replacement")
    assert not chain["calls"] and not chain["operator"].view()["scoped_jobs"]


def test_waiting_loop_never_processes_before_binding_then_only_exact_workflow(chain):
    value = prepared(chain)
    processed, probes = [], []
    def sleep(_): gate.bind(chain["operator"], value, WF, "reviewer")
    serve(chain["operator"], value["authorization_id"], lambda: probes.append("checked"), processed.append, sleep=sleep)
    assert processed == [WF] and len(probes) == 2
    with chain["operator"].db() as db:
        assert db.execute("SELECT state FROM supervised_processors").fetchone()["state"] == "finished"
    assert not chain["calls"]


def test_failed_dependency_probe_stops_without_processing_or_consuming_authority(chain):
    value = prepared(chain)
    processed = []
    def failed(): raise RuntimeError("Required dependency unavailable")
    with pytest.raises(RuntimeError): serve(chain["operator"], value["authorization_id"], failed, processed.append)
    assert not processed and not chain["calls"]
    assert chain["operator"].view()["authorizations"][0]["state"] == "prepared"
    with pytest.raises(ValueError): gate.inspect(chain["operator"], value)


def test_standby_cannot_start_without_explicit_opt_in(monkeypatch):
    from experimental.portuguese_parameters.supervised_worker import main
    monkeypatch.delenv("PT_SANDBOX_SUPERVISED", raising=False)
    with pytest.raises(ValueError, match="disabled"): main()


def test_supervised_http_requires_separate_opt_in_and_private_auth(chain, monkeypatch):
    value = ready(chain)
    client = chain["client"]
    assert client.post("/control/supervised/check", json=value).status_code == 409
    monkeypatch.setenv("PT_SANDBOX_SUPERVISED", "1")
    assert client.post("/control/supervised/check", json=value, headers={"Authorization": "Bearer wrong"}).status_code == 401
    check = client.post("/control/supervised/check", json=value)
    assert check.status_code == 200
    body = {"request": value, "workflow_id": WF, "policy_sha256": "0" * 64}
    assert client.post("/control/supervised/bind", json=body).status_code == 409
    body["policy_sha256"] = check.json()["policy_sha256"]
    assert client.post("/control/supervised/bind", json=body).status_code == 200
    assert client.post("/control/supervised/bind", json=body).status_code == 409
    assert not chain["calls"]
