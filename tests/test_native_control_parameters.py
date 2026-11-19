"""Real source-runtime consumption through native HTTP Control handlers.

All contracts/accounts/identities are disposable test data. Limits here are
synthetic fixture limits, not Danish provider approval or scientific bounds.
"""
from copy import deepcopy
import importlib.util
import hashlib
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
NODE = ROOT / "use-cases/denmark-node"
sys.path.insert(0, str(NODE))
sys.path.insert(0, str(NODE / "services/topology_generator"))
sys.path.insert(0, str(NODE / "services/draw_topology"))
from common import concurrent, sequential
from common.parameter_contract import parameter_contract_digest


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    sys.modules[name] = value
    spec.loader.exec_module(value)
    return value


topology = module("native_topology_test", NODE / "services/topology_generator/service.py")
renderer = module("native_renderer_test", NODE / "services/draw_topology/service.py")
CAPABILITY = {"capability_schema_version": "1.0", "transports": ["http-parameters-v1"], "operations": [{
    "operation_name": "GenerateTopology", "operation_identity": "urn:test:topology:source-runtime", "contract_version": "1.0.0",
    "execution_parameters": {"schema_version": "1.0", "transport": "http-parameters-v1", "properties": {
        "allow_hx": {"type": "boolean", "title": "Heat exchangers", "description": "Synthetic runtime test"},
        "allow_bypass": {"type": "boolean", "title": "Bypasses", "description": "Synthetic runtime test"},
        "how_many": {"type": "integer", "title": "Topology cap", "description": "Synthetic runtime test limits, not node-approved", "minimum": 1, "maximum": 4},
    }}}]}


def configuration(count=1):
    return {"contract_id": "test-contract", "contract_version": "1.0.0", "operation_identity": "urn:test:topology:source-runtime",
            "schema_version": "1.0", "instance_id": "test-instance", "parameters": {"allow_hx": True, "allow_bypass": True, "how_many": count}}


def test_draft_export_requires_explicit_isolated_qualification(tmp_path,monkeypatch):
    from common.configuration import load_contract
    from fastapi import HTTPException
    path=tmp_path/'qualification.json';path.write_text(json.dumps({**CAPABILITY,'qualification_only':True}))
    monkeypatch.delenv('CONTROL_PARAMETER_QUALIFICATION_MODE',raising=False)
    with pytest.raises(HTTPException):load_contract(str(path),'GenerateTopology',{'allow_hx':'boolean','allow_bypass':'boolean','how_many':'integer'})
    monkeypatch.setenv('CONTROL_PARAMETER_QUALIFICATION_MODE','true')
    assert load_contract(str(path),'GenerateTopology',{'allow_hx':'boolean','allow_bypass':'boolean','how_many':'integer'})['qualification_only'] is True


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    contract = tmp_path / "installed.json"; contract.write_text(json.dumps(CAPABILITY))
    upstream = tmp_path / "input.json"
    upstream.write_text(json.dumps({"run_config": {"supplies": ["310-D::CHP"], "loads": ["716-D::Dumpload"],
                                              "allow_hx": False, "allow_bypass": False, "how_many": 20}}))
    monkeypatch.setenv("SERVICE_PARAMETER_CONTRACT_FILE", str(contract))
    monkeypatch.delenv("SERVICE_API_KEY", raising=False)
    manager = concurrent.TaskManager()
    monkeypatch.setattr(concurrent, "task_manager", manager)
    for service in (topology, renderer):
        monkeypatch.setattr(service, "task_manager", manager)
        monkeypatch.setattr(service, "DATA_DIR", tmp_path / "outputs")
    calls = []
    original = topology.enumerate_networks
    def observed(*args, **kwargs):
        calls.append(deepcopy(kwargs))
        return original(*args, **kwargs)  # The actual enumerator runs, no result double.
    monkeypatch.setattr(topology, "enumerate_networks", observed)
    with TestClient(concurrent.create_app(topology)) as client:
        yield SimpleNamespace(client=client, upstream=upstream, calls=calls, root=tmp_path, manager=manager, contract=contract)


def request(runtime, task, count=1, configured=True):
    value = {"method": "GenerateTopology", "workflow_id": "wf-source-runtime-test", "task_id": task,
             "inputs": [{"protocol": "file", "uri": str(runtime.upstream), "format": "json"}]}
    if configured: value["configuration"] = configuration(count)
    return value


def completed(client, task):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        value = client.get(f"/control/status/{task}").json()
        if value["status"] != "running":
            assert value["status"] == "complete", value
            reference = client.get(f"/control/output/{task}").json()["output"]
            return reference, json.loads(Path(reference["uri"]).read_text())
        time.sleep(0.01)
    pytest.fail("Native task did not finish")


def test_two_http_configurations_reach_real_enumerator_and_keep_own_results(runtime):
    client = runtime.client
    assert client.get("/control/capabilities").json() == CAPABILITY
    outputs = []
    original_input = runtime.upstream.read_bytes()
    for task, count in [("task_first", 1), ("task_second", 3)]:
        value = request(runtime, task, count)
        response = client.post("/control/execute", json=value)
        assert response.status_code == 200
        ref, output = completed(client, response.json()["task_id"])
        outputs.append((ref, Path(ref["uri"]).read_bytes()))
        assert output["topology_count"] == count
        assert output["execution"]["configuration"] == value["configuration"]
        assert output["execution"]["algorithm_consumed"] == value["configuration"]["parameters"]
        assert output["execution"]["task_id"] == task
        assert output["execution"]["parameter_contract_sha256"] == parameter_contract_digest(CAPABILITY["operations"][0]["execution_parameters"])
        assert output["run_config"]["supplies"] == ["310-D::CHP"]
    assert runtime.calls == [{"allow_hx": True, "allow_bypass": True, "max_results": 1},
                             {"allow_hx": True, "allow_bypass": True, "max_results": 3}]
    assert runtime.upstream.read_bytes() == original_input
    assert outputs[0][0]["uri"] != outputs[1][0]["uri"]
    assert Path(outputs[0][0]["uri"]).read_bytes() == outputs[0][1]


def test_renderer_uses_actual_generated_networks_and_retains_producer_settings(runtime):
    response = runtime.client.post("/control/execute", json=request(runtime, "task_producer", 3))
    assert response.status_code == 200
    ref, producer = completed(runtime.client, response.json()["task_id"])
    with TestClient(concurrent.create_app(renderer)) as client:
        response = client.post("/control/execute", json={"method": "RenderTopologies", "workflow_id": "wf-source-runtime-test",
            "task_id": "task_renderer", "inputs": [ref]})
        assert response.status_code == 200
        result_ref, output = completed(client, "task_renderer")
        original = Path(result_ref['uri']).read_bytes()
        files = {item['uri']: Path(item['uri']).read_bytes() for item in output['drawings']}
        manifest = output['artifact_manifest']
        assert manifest['kind'] == 'task-artifacts-v1' and manifest['schema_version'] == '1.0'
        assert manifest['workflow_id'] == 'wf-source-runtime-test' and manifest['task_id'] == 'task_renderer'
        for item in manifest['files']:
            assert item['sha256'] == hashlib.sha256(files[item['uri']]).hexdigest()
            assert item['bytes'] == len(files[item['uri']])
        runtime.manager._tasks.clear()
        replay = {'method': 'RenderTopologies', 'workflow_id': 'wf-source-runtime-test',
                  'task_id': 'task_renderer', 'inputs': [ref]}
        assert client.post('/control/execute', json=replay).status_code == 409
        assert Path(result_ref['uri']).read_bytes() == original
        assert all(Path(uri).read_bytes() == raw for uri, raw in files.items())
        replay['workflow_id'] = '../outside'
        assert client.post('/control/execute', json=replay).status_code == 422
    assert output["drawing_count"] == 3
    canonical = lambda value: json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
    observation = output['input_consumption']
    assert observation['kind'] == 'json-networks-render-v1'
    assert observation['workflow_id'] == 'wf-source-runtime-test' and observation['task_id'] == 'task_renderer'
    assert observation['input_sha256'] == hashlib.sha256(canonical(producer)).hexdigest()
    assert observation['networks_sha256'] == hashlib.sha256(canonical(producer['networks'])).hexdigest()
    assert observation['processed_network_count'] == 3
    assert observation['processed_network_sha256'] == [hashlib.sha256(canonical(n)).hexdigest() for n in producer['networks']]
    assert output["upstream_execution"] == producer["execution"]
    assert all("<svg" in Path(item["uri"]).read_text() for item in output["drawings"])


def test_io_only_export_is_hash_checked_and_does_not_invent_renderer_settings(runtime,monkeypatch):
    definition={'binding_transport':'whole-output-v1','inputs':[],'outputs':[]}
    hashed=hashlib.sha256(json.dumps(definition,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
    export={'capability_schema_version':'1.0','transports':['whole-output-v1'],'operations':[{
        'operation_name':'RenderTopologies','operation_identity':'urn:test:renderer','contract_version':'1.0.0',
        'binding_transport':'whole-output-v1','io_definition':definition,'io_contract_sha256':hashed}]}
    runtime.contract.write_text(json.dumps(export))
    with TestClient(concurrent.create_app(renderer)) as client:
        assert client.get('/control/capabilities').json()==export
        configured={'method':'RenderTopologies','workflow_id':'wf-test','task_id':'task-test','configuration':configuration()}
        assert client.post('/control/execute',json=configured).status_code==422
        export['operations'][0]['io_contract_sha256']='0'*64;runtime.contract.write_text(json.dumps(export))
        assert client.get('/control/capabilities').status_code==409


@pytest.mark.parametrize("mutation", ["null", "version", "type", "limit", "unknown", "mixed", "path", "method"])
def test_invalid_settings_are_rejected_before_task_or_algorithm(runtime, mutation):
    value = request(runtime, "task_invalid")
    if mutation == "null": value["configuration"] = None
    if mutation == "version": value["configuration"]["contract_version"] = "9.0.0"
    if mutation == "type": value["configuration"]["parameters"]["allow_hx"] = "true"
    if mutation == "limit": value["configuration"]["parameters"]["how_many"] = 999
    if mutation == "unknown": value["configuration"]["parameters"]["unsupported"] = 1
    if mutation == "mixed": value["parameters"] = {}
    if mutation == "path": value["workflow_id"] = "../outside"
    if mutation == "method": value["method"] = "ValidateTopology"
    assert runtime.client.post("/control/execute", json=value).status_code in {409, 422}
    assert runtime.manager.get_status("task_invalid") is None
    assert runtime.calls == []


def test_duplicate_configured_task_cannot_overwrite_result_or_restart_algorithm(runtime):
    value = request(runtime, "task_duplicate")
    response = runtime.client.post("/control/execute", json=value)
    assert response.status_code == 200
    ref, _ = completed(runtime.client, response.json()["task_id"])
    old = Path(ref["uri"]).read_bytes()
    value["configuration"]["parameters"]["how_many"] = 3
    assert runtime.client.post("/control/execute", json=value).status_code == 409
    legacy = deepcopy(value); legacy.pop("configuration")
    assert runtime.client.post("/control/execute", json=legacy).status_code == 409
    # A restart loses the in-memory TaskManager; the existing output still denies replay.
    runtime.manager._tasks.clear()
    assert runtime.client.post("/control/execute", json=value).status_code == 409
    assert len(runtime.calls) == 1 and Path(ref["uri"]).read_bytes() == old


def test_missing_or_incompatible_runtime_contract_denies_configuration(runtime, monkeypatch):
    monkeypatch.delenv("SERVICE_PARAMETER_CONTRACT_FILE")
    assert runtime.client.get("/control/capabilities").status_code == 409
    assert runtime.client.post("/control/execute", json=request(runtime, "task_missing")).status_code == 409
    assert runtime.calls == []


@pytest.mark.parametrize("handler", [concurrent, sequential])
def test_services_without_configuration_support_do_not_silently_ignore_it(handler):
    calls = []
    service = SimpleNamespace(execute_Echo=lambda request: (calls.append(request) or handler.ExecuteResponse(status="complete")))
    with TestClient(handler.create_app(service)) as client:
        value = {"method": "Echo", "workflow_id": "test-workflow", "task_id": "test-task", "configuration": configuration()}
        assert client.post("/control/execute", json=value).status_code == 409
        assert calls == []
        value.pop("configuration")
        assert client.post("/control/execute", json=value).status_code == 200
        assert len(calls) == 1


def test_existing_authentication_applies_to_capabilities_and_dispatch(runtime, monkeypatch):
    monkeypatch.setenv("SERVICE_API_KEY", "disposable-test-token")
    assert runtime.client.get("/control/capabilities").status_code == 401
    assert runtime.client.post("/control/execute", json=request(runtime, "task_auth")).status_code == 401
    assert runtime.client.get("/control/capabilities", headers={"Authorization": "Bearer disposable-test-token"}).status_code == 200
    assert runtime.calls == []


def test_vendored_handlers_and_validators_match_maintained_sources():
    for name in ("configuration.py", "concurrent.py", "sequential.py", "parameter_contract.py"):
        assert (ROOT / "use-cases/common" / name).read_bytes() == (NODE / "common" / name).read_bytes()
    assert (NODE / "common/parameter_contract.py").read_bytes() == (ROOT / "orchestrator/src/services/parameter_contract.py").read_bytes()
