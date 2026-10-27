"""Offline HTTP/Redis fixtures, never algorithm execution evidence."""
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock
import json

import fakeredis
import pytest
from fastapi.testclient import TestClient

from api.app import OrchestratorAPI
from services.control_client import ControlClient, ControlClientError, ExecuteResponse, StatusResponse, OutputResponse
from services.configuration_readiness import check_configurations
from services.dockerinfo_parser import ServiceEndpoint
from services.parameter_contract import match_wrapper_configuration, parameter_contract_digest
from services.workflow_engine import WorkflowEngine
from services.worker import Worker
from models.state import TaskState, TaskStatus

DEFINITION = {"schema_version": "1.0", "transport": "http-parameters-v1", "properties": {
    "seed": {"type": "integer", "default": 42, "enum": [42, 43]}}}
CAPABILITY = {"capability_schema_version": "1.0", "transports": ["http-parameters-v1"], "operations": [{
    "operation_name": "Process", "operation_identity": "urn:test:process", "contract_version": "1.0.0",
    "execution_parameters": DEFINITION}]}


def configuration(seed=42, instance="first"):
    return {"contract_id": "portal-contract", "contract_version": "1.0.0", "schema_version": "1.0",
            "operation_identity": "urn:test:process", "parameters": {"seed": seed}, "instance_id": instance}


def graph():
    return SimpleNamespace(all_nodes={key: SimpleNamespace(node=SimpleNamespace(container_name=key.split(":")[0]),
        operation=SimpleNamespace(operation_signature=SimpleNamespace(operation_name="Process")))
        for key in ("first:Process", "second:Process")})


@pytest.mark.parametrize("mutation", ["unknown", "protocol", "version", "identity", "missing-default", "extra", "null", "ambiguous", "malformed-transports", "malformed-operations"])
def test_unsupported_wrapper_never_dispatches(httpx_mock, mutation):
    conf, cap = configuration(), deepcopy(CAPABILITY)
    if mutation == "unknown": cap = {}
    if mutation == "protocol": cap["capability_schema_version"] = "99.0"
    if mutation == "version": cap["operations"][0]["contract_version"] = "2.0.0"
    if mutation == "identity": cap["operations"][0]["operation_identity"] = "other"
    if mutation == "missing-default": conf["parameters"] = {}
    if mutation == "extra": conf["parameters"]["path"] = "/tmp"
    if mutation == "null": conf["parameters"] = None
    if mutation == "ambiguous": cap["operations"] *= 2
    if mutation == "malformed-transports": cap["transports"] = "http-parameters-v1"
    if mutation == "malformed-operations": cap["operations"] = {}
    # Malformed envelopes fail before even probing the wrapper.
    if mutation != "null": httpx_mock.add_response(method="GET", url="http://fixture/control/capabilities", json=cap)
    with pytest.raises((ControlClientError, ValueError)):
        ControlClient().execute("http://fixture", "Process", "wf-fixture", "task-fixture", configuration=conf)
    assert all(request.method == "GET" for request in httpx_mock.get_requests())


def test_configured_and_legacy_wire_compatibility(httpx_mock):
    httpx_mock.add_response(method="GET", url="http://fixture/control/capabilities", json=CAPABILITY)
    httpx_mock.add_response(method="POST", url="http://fixture/control/execute", json={"status": "complete"})
    ControlClient().execute("http://fixture", "Process", "wf-fixture", "runtime-id", configuration=configuration())
    assert json.loads(httpx_mock.get_requests()[-1].content)["configuration"] == configuration()
    httpx_mock.add_response(method="POST", url="http://legacy/control/execute", json={"status": "complete"})
    ControlClient().execute("http://legacy", "Process", "wf-legacy", "runtime-id")
    body = json.loads(httpx_mock.get_requests()[-1].content)
    assert "configuration" not in body and "parameters" not in body
    assert len(httpx_mock.get_requests()) == 3  # Legacy path has no capability dependency.


def test_readiness_is_read_only_and_auth_preserved(monkeypatch):
    engine, redis, bp, docker = MagicMock(), fakeredis.FakeRedis(), MagicMock(), MagicMock()
    bp.parse_json.return_value = graph()
    docker.parse_json.return_value = {name: ServiceEndpoint(address=name, port=8000) for name in ("first", "second")}
    probes = []
    def check(self, base_url, method, conf):
        probes.append((base_url, conf["parameters"]))
        return match_wrapper_configuration(method, conf, CAPABILITY)
    monkeypatch.setattr(ControlClient, "check_configuration", check)
    monkeypatch.setenv("ORCHESTRATOR_API_KEY", "fixture-only-key")
    client = TestClient(OrchestratorAPI(engine, bp, docker, redis).create_app())
    payload = {"blueprint": {"fixture": True}, "dockerinfo": {"fixture": True}, "task_configurations": {
        "first:Process": configuration(), "second:Process": configuration(43, "second")}}
    for path in ("/capabilities", "/workflows/wf-fixture/instance-associations"):
        assert client.get(path).status_code == 401
    assert client.post("/workflows/check-compatibility", json=payload).status_code == 401
    headers = {"Authorization": "Bearer fixture-only-key"}
    response = client.post("/workflows/check-compatibility", json=payload, headers=headers)
    assert response.status_code == 200 and response.json()["status"] == "ready"
    assert probes == [("http://first:8000", {"seed": 42}), ("http://second:8000", {"seed": 43})]
    assert redis.dbsize() == 0 and engine.mock_calls == []
    monkeypatch.setattr(ControlClient, "check_configuration", lambda *a: (_ for _ in ()).throw(ControlClientError("Incompatible")))
    assert client.post("/workflows", json=payload, headers=headers).status_code == 409
    assert redis.dbsize() == 0 and engine.mock_calls == []


def test_configurations_isolated_immutable_and_corruption_rejected():
    redis = fakeredis.FakeRedis()
    engine = WorkflowEngine(MagicMock(), MagicMock(), redis)
    configs = {"first:Process": configuration(), "second:Process": configuration(43, "second")}
    engine.initialize_instance_configurations("wf-a", configs)
    engine.initialize_instance_configurations("wf-b", {"first:Process": configuration(43)})
    configs["first:Process"]["parameters"]["seed"] = 43
    assert engine.get_instance_configuration("wf-a", "first:Process")["parameters"] == {"seed": 42}
    assert engine.get_instance_configuration("wf-a", "second:Process")["parameters"] == {"seed": 43}
    assert engine.get_instance_configuration("wf-b", "first:Process")["parameters"] == {"seed": 43}
    with pytest.raises(ValueError, match="immutable"):
        engine.initialize_instance_configurations("wf-a", configs)
    engine.record_service_task("wf-a", "runtime-id", "first:Process", "external-id")
    assert engine.get_instance_associations("wf-a") == [{"task_id": "runtime-id", "node_key": "first:Process",
        "instance_id": "first", "service_task_id": "external-id"}]
    with pytest.raises(ValueError, match="immutable"):
        engine.record_service_task("wf-a", "runtime-id", "first:Process", "replacement-id")
    redis.hset("instance-configurations:wf-a", "first:Process", "[]")
    with pytest.raises(ValueError): engine.get_instance_configuration("wf-a", "first:Process")


def test_external_task_id_is_persisted_and_used_for_polling():
    engine, client = MagicMock(), MagicMock(spec=ControlClient)
    task = TaskState(task_id="runtime-id", workflow_id="wf-fixture", node_key="first:Process",
        status=TaskStatus.RUNNING, created_at=datetime.now(), updated_at=datetime.now())
    engine.claim_task.return_value = task
    engine.get_instance_configuration.return_value = configuration()
    client.execute.return_value = ExecuteResponse(status="running", task_id="external-id")
    client.get_status.return_value = StatusResponse(status="complete")
    from models.data_reference import DataReference, Format, Protocol
    client.get_output.return_value = OutputResponse(output=DataReference(protocol=Protocol.S3, uri="s3://fixture/result.json", format=Format.JSON))
    worker = Worker(engine, client, {"first": ServiceEndpoint(address="fixture", port=8000)})
    assert worker.process_task("wf-fixture")
    assert client.execute.call_args.kwargs["configuration"] == configuration()
    engine.record_service_task.assert_called_once_with("wf-fixture", "runtime-id", "first:Process", "external-id")
    client.get_status.assert_called_once_with("http://fixture:8000", "external-id")
    client.get_output.assert_called_once_with("http://fixture:8000", "external-id")
    client.execute.return_value = ExecuteResponse(status="running")
    worker.process_task("wf-fixture")
    assert "external task identifier" in engine.fail_task.call_args.args[-1]


def test_legacy_readiness_and_duplicate_logical_identity(monkeypatch):
    monkeypatch.setattr(ControlClient, "check_configuration", lambda *a: pytest.fail("No legacy capability probe"))
    assert check_configurations(graph(), {}, {})["mode"] == "legacy"
    with pytest.raises(ValueError, match="Duplicate"):
        check_configurations(graph(), {}, {"first:Process": configuration(), "second:Process": configuration(43)})


def test_digest_normalizes_registry_serialization():
    equivalent = deepcopy(DEFINITION)
    equivalent["properties"]["seed"].update(required=False, minimum=None, maximum=None)
    assert parameter_contract_digest(DEFINITION) == parameter_contract_digest(equivalent)
    for field, value in (("schema_version", "2.0"), ("transport", "unknown")):
        with pytest.raises(ValueError): parameter_contract_digest({**DEFINITION, field: value})


def test_configuration_is_stored_before_enqueue(monkeypatch):
    engine, redis, bp, docker = MagicMock(), fakeredis.FakeRedis(), MagicMock(), MagicMock()
    bp.parse_json.return_value = graph()
    docker.parse_json.return_value = {"first": ServiceEndpoint(address="fixture", port=8000)}
    monkeypatch.setattr(ControlClient, "check_configuration", lambda self, url, method, conf: match_wrapper_configuration(method, conf, CAPABILITY))
    monkeypatch.delenv("ORCHESTRATOR_API_KEY", raising=False)
    client = TestClient(OrchestratorAPI(engine, bp, docker, redis).create_app())
    payload = {"blueprint": {"fixture": True}, "dockerinfo": {"fixture": True},
        "task_configurations": {"first:Process": configuration()}, "services_api_key": "test-service-key"}
    response = client.post("/workflows", json=payload)
    assert response.status_code == 200, response.text
    wid = response.json()["workflow_id"]
    assert [call[0] for call in engine.mock_calls] == ["initialize_workflow", "initialize_instance_configurations", "start_workflow"]
    engine.initialize_instance_configurations.assert_called_once_with(wid, payload["task_configurations"])
    assert redis.get(f"services_key:{wid}") == b"test-service-key"


def test_vendored_validator_release_digest():
    from pathlib import Path
    import hashlib
    root = Path(__file__).resolve().parents[2]
    provenance = json.loads((root / "parameter_contract_provenance.json").read_text())
    assert provenance["validator_version"] == "1.0"
    assert hashlib.sha256((root / "src/services/parameter_contract.py").read_bytes()).hexdigest() == provenance["sha256"]


def test_concurrent_workflows_have_independent_configurations():
    from concurrent.futures import ThreadPoolExecutor
    engine = WorkflowEngine(MagicMock(), MagicMock(), fakeredis.FakeRedis())
    def save(index):
        engine.initialize_instance_configurations(f"wf-{index}", {"first:Process": configuration(42 + index % 2)})
        return engine.get_instance_configuration(f"wf-{index}", "first:Process")["parameters"]["seed"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(save, range(12))) == [42, 43] * 6
