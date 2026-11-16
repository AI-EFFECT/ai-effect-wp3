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
    assert processed == [WF] and len(probes) == 1
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
    assert client.post("/control/supervised/bind", json={**body, "policy_sha256": None}).status_code == 409
    body["policy_sha256"] = check.json()["policy_sha256"]
    assert client.post("/control/supervised/bind", json=body).status_code == 200
    assert client.post("/control/supervised/bind", json=body).status_code == 409
    assert not chain["calls"]


class Clock:
    def __init__(self): self.now = 0
    def monotonic(self): return self.now
    def sleep(self, seconds): self.now += seconds


def test_transient_read_only_startup_recovers_then_processes_exactly_once(chain):
    from experimental.portuguese_parameters.dependency_probe import TransientProbeError
    value = prepared(chain); clock = Clock(); probes = []; processed = []
    def probe():
        probes.append("GET-only")
        if len(probes) <= 2: raise TransientProbeError("DNS startup")
    def sleep(seconds):
        clock.sleep(seconds)
        if len(probes) == 3:
            assert gate.inspect(chain["operator"], value)["status"] == "ready"
            gate.bind(chain["operator"], value, WF, "test-owner")
        else:
            with pytest.raises(ValueError): gate.inspect(chain["operator"], value)
    serve(chain["operator"], value["authorization_id"], probe, processed.append,
          wait_seconds=10, startup_seconds=5, sleep=sleep, monotonic=clock.monotonic)
    assert len(probes) == 3 and processed == [WF]
    assert not chain["calls"] and not chain["operator"].view()["scoped_jobs"]


def test_transient_deadline_blocks_and_never_creates_jobs_or_binding(chain):
    from experimental.portuguese_parameters.dependency_probe import TransientProbeError
    value = prepared(chain); clock = Clock()
    def probe(): raise TransientProbeError("Connection starting")
    with pytest.raises(ValueError, match="startup deadline"):
        serve(chain["operator"], value["authorization_id"], probe,
              lambda _: pytest.fail("No processing"), wait_seconds=10, startup_seconds=3,
              sleep=clock.sleep, monotonic=clock.monotonic)
    with pytest.raises(ValueError): gate.inspect(chain["operator"], value)
    assert chain["operator"].view()["authorizations"][0]["workflow_id"] is None
    assert not chain["calls"] and not chain["operator"].view()["scoped_jobs"]


def test_known_failed_probe_immediately_revokes_old_healthy_lease(chain):
    from experimental.portuguese_parameters.dependency_probe import TransientProbeError
    value = ready(chain)
    def fail(): raise TransientProbeError("DNS")
    with pytest.raises(TransientProbeError):
        gate.heartbeat(chain["operator"], value["authorization_id"], "worker-one", fail)
    with pytest.raises(ValueError, match="processing is unavailable"): gate.inspect(chain["operator"], value)
    assert not chain["calls"]


def test_permanent_failure_and_policy_drift_do_not_retry(chain):
    from experimental.portuguese_parameters.dependency_probe import PermanentProbeError
    value = prepared(chain); probes = []
    def fail():
        probes.append(1); raise PermanentProbeError("Adapter authentication rejected")
    with pytest.raises(PermanentProbeError, match="authentication"):
        serve(chain["operator"], value["authorization_id"], fail, lambda _: pytest.fail("No execution"))
    assert probes == [1] and not chain["calls"]


def test_no_retry_or_second_process_after_binding_or_uncertain_processing(chain):
    value = prepared(chain); clock = Clock(); calls = []
    def sleep(seconds):
        clock.sleep(seconds); gate.bind(chain["operator"], value, WF, "test-owner")
    def process(wf):
        calls.append(wf); raise RuntimeError("Uncertain processing double")
    with pytest.raises(RuntimeError, match="Uncertain"):
        serve(chain["operator"], value["authorization_id"], lambda: None, process,
              sleep=sleep, monotonic=clock.monotonic)
    assert calls == [WF] and not chain["calls"]


def test_policy_drift_stops_before_another_probe_or_processing(chain):
    value = prepared(chain); probes = []
    def probe(): probes.append(1)
    def sleep(_):
        with chain["operator"].db() as db:
            db.execute("UPDATE authorizations SET policy_sha256=?", ("0"*64,))
    with pytest.raises(ValueError, match="policy drift"):
        serve(chain["operator"], value["authorization_id"], probe,
              lambda _: pytest.fail("No execution"), sleep=sleep)
    assert probes == [1] and not chain["calls"]


def test_probe_only_cli_never_constructs_authority_or_processor(tmp_path, monkeypatch, capsys):
    from experimental.portuguese_parameters import supervised_worker as module
    from types import SimpleNamespace
    token=tmp_path/"token";token.write_text("test-token")
    monkeypatch.setattr("sys.argv",["probe","--probe-only"])
    monkeypatch.delenv("PT_SANDBOX_SUPERVISED",raising=False)
    for key,value in {"REDIS_URL":"redis://offline","PT_SANDBOX_TOKEN_FILE":str(token),"PT_SANDBOX_UPSTREAM":"http://ml","PT_SANDBOX_MODEL_PROOF_URL":"http://observer","PT_SANDBOX_SELF_URL":"http://adapter"}.items():monkeypatch.setenv(key,value)
    monkeypatch.setitem(__import__('sys').modules,"redis",SimpleNamespace(Redis=SimpleNamespace(from_url=lambda *a,**kw:object())))
    def forbidden(*a,**kw):pytest.fail("No journal authority or processor construction")
    monkeypatch.setattr(module,"AuthorizationOperator",forbidden)
    calls=[];monkeypatch.setattr(module,"dependency_probe",lambda *a,**kw:calls.append("read-only"))
    module.main()
    assert calls==["read-only"] and '"algorithm_reservations": 0' in capsys.readouterr().out
