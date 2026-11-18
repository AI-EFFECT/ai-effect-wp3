"""Source-API wiring checks, with an explicit ML double (not model qualification).

The real Portuguese FastAPI source binds requests and builds CSVs. Only its
DGAN dependency is replaced; fixture bounds are not Portuguese owner approval.
"""
import base64
from copy import deepcopy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import time
import threading
from types import ModuleType, SimpleNamespace

import httpx
import pandas as pd
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
COMMON = ROOT / 'use-cases/portugal-node-sidecar/common'
spec = importlib.util.spec_from_file_location('configured_portugal_test', COMMON / '__init__.py', submodule_search_locations=[str(COMMON)])
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
spec.loader.exec_module(package)
from configured_portugal_test import control_interface as control
from configured_portugal_test.configured_synthetic import Runtime
import configured_portugal_test.configured_synthetic as configured_module

CAPABILITY = {'capability_schema_version': '1.0', 'transports': ['http-parameters-v1'], 'operations': [
    {'operation_name': method, 'operation_identity': 'urn:test:source-api:' + method, 'contract_version': '1.0.0',
     'execution_parameters': {'schema_version': '1.0', 'transport': 'http-parameters-v1', 'properties': {
         name: {'type': 'integer', 'minimum': 1, 'maximum': 10, 'required': True,
                'title': name, 'description': 'Synthetic test bounds, not owner approval'} for name in names}}}
    for method, names in [('TrainModel', ['epochs', 'batch_size', 'sequence_len']), ('GenerateData', ['number_of_examples'])]]}


def configuration(method, values):
    return {'contract_id': 'test-contract-' + method, 'operation_identity': 'urn:test:source-api:' + method,
            'contract_version': '1.0.0', 'schema_version': '1.0', 'instance_id': 'test-instance-' + method, 'parameters': values}


def request(runtime, method='TrainModel', task='train', workflow='wf-test', ref=None, **values):
    return {'method': method, 'workflow_id': workflow, 'task_id': task,
            'inputs': [ref or {'protocol': 'file', 'uri': str(runtime.csv), 'format': 'csv'}],
            'configuration': configuration(method, values or {'epochs': 2, 'batch_size': 4, 'sequence_len': 3})}


def finished(runtime, response):
    assert response.status_code == 200, response.text
    identity = response.json()['task_id']
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        record = runtime.client.get('/control/receipt/' + identity).json()
        if record['state'] not in {'queued', 'running'}:
            return record
        time.sleep(.01)
    pytest.fail('Configured task failed to reach a terminal state')


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    calls = []; requests = []; model_settings = {}
    # An explicit algorithm double; execute the unchanged real API around it.
    dgan = ModuleType('dgan')
    def train(frame, **kwargs):
        calls.append(('train', {key: value for key, value in kwargs.items() if key != 'progress_callback'}))
        return {'sequence_len': kwargs['sequence_len']}
    def save(value, path):
        folder = Path(path); folder.mkdir(parents=True); (folder / 'model.pt').write_text('TEST DOUBLE, NOT A MODEL')
        model_settings[str(folder)] = value
    def generate(value, num_examples):
        calls.append(('generate', num_examples))
        return [pd.DataFrame({'datetime': list(range(value['sequence_len'])), 'value': [1] * value['sequence_len']}) for _ in range(num_examples)]
    dgan.train = train; dgan.save_train_result = save; dgan.load_train_result = lambda path: model_settings[str(Path(path))]; dgan.generate = generate
    source = ROOT / 'use-cases/portugal-node-integrated/synthetic_data_generation/main.py'
    module_spec = importlib.util.spec_from_file_location('portuguese_source_api_test', source)
    module = importlib.util.module_from_spec(module_spec)
    with monkeypatch.context() as imports:
        imports.setitem(sys.modules, 'dgan', dgan)
        imports.setitem(sys.modules, 'common', ModuleType('common'))  # Disable source's optional legacy Control router only.
        module_spec.loader.exec_module(module)
    module.model_Root = str(tmp_path / 'source-models')
    upstream = TestClient(module.app)
    behavior = {'train_status': None, 'wrong_schema': False, 'extra_csv': False, 'train_gate': None}
    class SourceTransport(httpx.BaseTransport):
        def handle_request(self, req):
            requests.append((req.method, req.url.path, dict(req.url.params)))
            if req.url.path == '/openapi.json' and behavior['wrong_schema']:
                doc = deepcopy(module.app.openapi())
                doc['paths']['/train']['post']['parameters'] = [p for p in doc['paths']['/train']['post']['parameters'] if p['name'] != 'epochs']
                return httpx.Response(200, json=doc)
            if req.url.path == '/train' and behavior['train_gate']:
                entered, released = behavior['train_gate']; entered.set(); assert released.wait(5)
            if req.url.path == '/train' and behavior['train_status']:
                return httpx.Response(behavior['train_status'], text='Not a completed synchronous response')
            if req.url.path == '/generate' and behavior['extra_csv']:
                return httpx.Response(200, text='example,value\n1,2\n', headers={'content-type': 'text/csv'})
            return upstream.send(req)
    contract_dir = tmp_path / 'contracts'; contract_dir.mkdir(); (contract_dir / 'reviewed.json').write_text(json.dumps(CAPABILITY))
    input_dir = tmp_path / 'inputs'; input_dir.mkdir(); csv = input_dir / 'selected.csv'; csv.write_text('datetime,value\n2026-01-01,1\n2026-01-02,2\n')
    env = {'SERVICE_PARAMETER_CONTRACT_DIR': str(contract_dir), 'CONTROL_PARAMETER_STATE_DIR': str(tmp_path / 'state'),
           'SYNTHETIC_DATA_URL': 'http://source.test', 'SELF_URL': 'https://adapter.test', 'SERVICE_API_KEY': 'private-test-key',
           'SYNTHETIC_DATA_USER_NAMESPACE': 'disposable_test', 'SYNTHETIC_DATA_INDEX_COLUMN': 'datetime', 'SYNTHETIC_DATA_INPUT_ROOT': str(input_dir)}
    handlers = {method: lambda req: pytest.fail('Configured operation fell through to legacy fixed values') for method in ['TrainModel', 'GenerateData']}
    connector = Runtime(env, handlers, transport=SourceTransport())
    monkeypatch.setenv('SERVICE_API_KEY', env['SERVICE_API_KEY'])
    monkeypatch.setattr(Runtime, 'from_environment', classmethod(lambda cls, ignored: connector))
    client = TestClient(control.create_app(handlers)); client.headers['Authorization'] = 'Bearer ' + env['SERVICE_API_KEY']
    result = SimpleNamespace(client=client, connector=connector, csv=csv, calls=calls, requests=requests, env=env,
                             handlers=handlers, behavior=behavior, upstream=upstream, root=tmp_path)
    yield result
    connector.pool.shutdown(wait=True); client.close(); upstream.close()


def test_reviewed_values_bind_to_actual_source_api_and_keep_configured_results(runtime):
    original = runtime.csv.read_bytes(); earlier = None; runs = []
    expected_capability = deepcopy(CAPABILITY)
    expected_capability['operations'][0]['input_transports'] = ['inline-csv-v1']
    assert runtime.client.get('/control/capabilities').json() == expected_capability
    for task, epochs, sequences in [('first', 2, 2), ('second', 3, 4)]:
        payload = request(runtime, task=task, epochs=epochs, batch_size=4, sequence_len=3)
        train = finished(runtime, runtime.client.post('/control/execute', json=payload))
        assert train['state'] == 'complete', train
        assert train['configuration'] == payload['configuration']
        assert train['delivered_query']['epochs'] == epochs
        assert set(train['delivered_query']) == {'user_id', 'model_name', 'index_col', 'overwrite', 'epochs', 'batch_size', 'sequence_len'}
        assert 'Not established' in train['algorithm_consumption']
        assert train['upstream_interface_sha256']
        assert runtime.calls[-1] == ('train', {'index_col': 'datetime', 'sequence_len': 3, 'batch_size': 4, 'epochs': epochs})
        ref = train['output']
        assert ref['protocol'] == 'https'
        generated = finished(runtime, runtime.client.post('/control/execute', json=request(runtime, 'GenerateData', task + '-generate', ref=ref, number_of_examples=sequences)))
        assert generated['state'] == 'complete', generated
        assert generated['observed_sequence_count'] == sequences and generated['csv_data_rows'] == sequences * 3
        assert runtime.calls[-1] == ('generate', sequences)
        assert generated['model_binding']['producer_task_id'] == train['service_task_id']
        assert runtime.client.get('/control/output/' + generated['service_task_id']).json()['output'] == generated['output']
        data = runtime.client.get('/control/data/' + generated['service_task_id']).content
        assert hashlib.sha256(data).hexdigest() == generated['output_sha256']
        if earlier:
            identity, old_bytes = earlier
            assert runtime.client.get('/control/data/' + identity).content == old_bytes
        earlier = generated['service_task_id'], data
        runs.append({'configuration': train['configuration'], 'source_train_function_arguments': runtime.calls[-2][1],
                     'source_generate_function_num_examples': sequences,
                     'observed_csv_sequences': generated['observed_sequence_count'],
                     'observed_csv_rows': generated['csv_data_rows'], 'output_sha256': generated['output_sha256'],
                     'training_task': train['service_task_id'], 'generation_task': generated['service_task_id']})
    assert runtime.csv.read_bytes() == original
    assert len([c for c in runtime.calls if c[0] == 'train']) == 2
    if os.environ.get('AIE_PT_WIRING_EVIDENCE_PATH'):
        evidence = {'date': '2026-11-18', 'boundary': 'Real unchanged source API with explicit DGAN dependency double',
                    'status': 'WIRING_VERIFIED_MODEL_QUALIFICATION_PENDING', 'runs': runs,
                    'source_api_sha256': hashlib.sha256((ROOT / 'use-cases/portugal-node-integrated/synthetic_data_generation/main.py').read_bytes()).hexdigest(),
                    'source_api_unchanged_from_baseline': True,
                    'limitations': ['No real ML training or algorithm acceptance', 'Fixture bounds are not provider approval',
                                    'No Windows, Docker, browser or normal-user acceptance',
                                    'No paid AI requests, production deployment or retained demo changes',
                                    'Portal authenticated remote-result preview and runtime receipt ingestion still need integration']}
        Path(os.environ['AIE_PT_WIRING_EVIDENCE_PATH']).write_text(json.dumps(evidence, indent=2) + '\n')


@pytest.mark.parametrize('mutation', ['null', 'mixed', 'type', 'unknown', 'range', 'version', 'omitted', 'path', 'input-count'])
def test_invalid_requests_never_dispatch_or_use_legacy_settings(runtime, mutation):
    payload = request(runtime)
    if mutation == 'null': payload['configuration'] = None
    if mutation == 'mixed': payload['parameters'] = {}
    if mutation == 'type': payload['configuration']['parameters']['epochs'] = True
    if mutation == 'unknown': payload['configuration']['parameters']['max_epochs'] = 3
    if mutation == 'range': payload['configuration']['parameters']['epochs'] = 99
    if mutation == 'version': payload['configuration']['contract_version'] = 'unknown'
    if mutation == 'omitted': payload.pop('configuration')
    if mutation == 'path': payload['workflow_id'] = '../outside'
    if mutation == 'input-count': payload['inputs'] = []
    assert runtime.client.post('/control/execute', json=payload).status_code == 422
    assert runtime.calls == [] and runtime.requests == []
    assert list(runtime.connector.storage.glob('cfg-*.json')) == []


def test_same_task_is_not_redispatched_after_completion_or_restart(runtime):
    payload = request(runtime)
    record = finished(runtime, runtime.client.post('/control/execute', json=payload))
    assert record['state'] == 'complete'
    assert runtime.client.post('/control/execute', json=payload).status_code == 409
    runtime.connector.pool.shutdown(wait=True)
    other = Runtime(runtime.env, runtime.handlers, transport=runtime.connector.transport)
    try:
        with pytest.raises(Exception) as rejected: other.submit(control.ExecuteRequest(**payload))
        assert rejected.value.status_code == 409
    finally: other.pool.shutdown(wait=True)
    assert len(runtime.calls) == 1


def test_acknowledgement_is_not_completion_and_unknown_outcome_blocks_new_attempts(runtime):
    runtime.behavior['train_status'] = 202
    record = finished(runtime, runtime.client.post('/control/execute', json=request(runtime)))
    assert record['state'] == 'uncertain' and 'algorithm_consumption' not in record
    assert runtime.client.get('/control/status/' + record['service_task_id']).json()['status'] == 'failed'
    assert runtime.client.get('/control/output/' + record['service_task_id']).status_code == 409
    assert runtime.client.post('/control/execute', json=request(runtime, task='different-id')).status_code == 409
    assert [path for _, path, _ in runtime.requests] == ['/openapi.json', '/train']
    assert runtime.calls == []


def test_incompatible_live_interface_fails_before_algorithm(runtime):
    runtime.behavior['wrong_schema'] = True
    record = finished(runtime, runtime.client.post('/control/execute', json=request(runtime)))
    assert record['state'] == 'failed' and record['dispatch_started'] is False
    assert runtime.calls == [] and [path for _, path, _ in runtime.requests] == ['/openapi.json']


def test_private_model_cannot_be_used_in_another_workflow_and_outputs_are_checked(runtime):
    record = finished(runtime, runtime.client.post('/control/execute', json=request(runtime)))
    generated = finished(runtime, runtime.client.post('/control/execute', json=request(runtime, 'GenerateData', 'generate', workflow='other-workflow', ref=record['output'], number_of_examples=2)))
    assert generated['state'] == 'failed' and not generated['dispatch_started']
    runtime.connector.path(record['service_task_id'], 'data').write_text('changed')
    assert runtime.client.get('/control/data/' + record['service_task_id']).status_code == 409
    generated = finished(runtime, runtime.client.post('/control/execute', json=request(runtime, 'GenerateData', 'tamper', ref=record['output'], number_of_examples=2)))
    assert generated['state'] == 'failed' and 'changed' in generated['error']
    assert len(runtime.calls) == 1


def test_explicitly_shared_model_supports_standalone_generation(runtime):
    folder = runtime.root / 'source-models' / 'shared_test' / 'declared_model'
    folder.mkdir(parents=True); (folder / 'model.pt').write_text('TEST DOUBLE')
    # Explicit fixture preparation through source's ML double, not a hidden training operation.
    endpoint = next(r.endpoint for r in runtime.upstream.app.routes if getattr(r, 'path', '') == '/generate')
    endpoint.__globals__['load_train_result'] = lambda path: {'sequence_len': 3}
    shared = runtime.root / 'shared.json'
    shared.write_text(json.dumps({'urn:test:shared:model': {'share_scope': 'service_callers', 'user_id': 'shared_test', 'model_name': 'declared_model'}}))
    runtime.connector.environment['SYNTHETIC_DATA_SHARED_MODELS_FILE'] = str(shared)
    ref = {'protocol': 'inline', 'format': 'json', 'uri': base64.b64encode(json.dumps({'model_ref': 'urn:test:shared:model'}).encode()).decode()}
    record = finished(runtime, runtime.client.post('/control/execute', json=request(runtime, 'GenerateData', 'standalone', ref=ref, number_of_examples=2)))
    assert record['state'] == 'complete', record
    assert runtime.calls == [('generate', 2)] and record['observed_sequence_count'] == 2


def test_restart_with_unresolved_job_is_uncertain_and_cannot_be_bypassed(runtime):
    identity = 'cfg-' + 'a' * 64
    runtime.connector.path(identity).write_text(json.dumps({'state': 'running', 'boot': 'earlier-boot'}))
    assert runtime.connector.read(identity)['state'] == 'uncertain'
    assert runtime.client.post('/control/execute', json=request(runtime)).status_code == 409
    assert runtime.requests == []


def test_control_records_and_data_require_service_authorization(runtime):
    record = finished(runtime, runtime.client.post('/control/execute', json=request(runtime)))
    runtime.client.headers.pop('Authorization')
    for path in ['capabilities', 'receipt/' + record['service_task_id'], 'status/' + record['service_task_id'], 'output/' + record['service_task_id'], 'data/' + record['service_task_id']]:
        assert runtime.client.get('/control/' + path).status_code == 401
    assert runtime.client.post('/control/execute', json=request(runtime, task='denied')).status_code == 401
    assert len(runtime.calls) == 1


def test_data_outside_approved_root_is_not_read_or_dispatched(runtime):
    outside = runtime.root / 'private.csv'; outside.write_text('PRIVATE')
    record = finished(runtime, runtime.client.post('/control/execute', json=request(runtime, ref={'protocol': 'file', 'uri': str(outside), 'format': 'csv'})))
    assert record['state'] == 'failed' and not record['dispatch_started']
    assert runtime.calls == []


def test_wrong_csv_sequence_count_does_not_claim_a_configured_result(runtime):
    record = finished(runtime, runtime.client.post('/control/execute', json=request(runtime)))
    runtime.behavior['extra_csv'] = True
    generated = finished(runtime, runtime.client.post('/control/execute', json=request(runtime, 'GenerateData', 'bad-csv', ref=record['output'], number_of_examples=2)))
    assert generated['state'] == 'uncertain' and 'sequence count' in generated['error']
    assert generated['output'] is None


def test_legacy_runtime_is_unchanged_and_configuration_is_never_silently_ignored(monkeypatch):
    monkeypatch.delenv('SERVICE_PARAMETER_CONTRACT_DIR', raising=False); monkeypatch.delenv('SERVICE_API_KEY', raising=False)
    calls = []
    with TestClient(control.create_app({'Echo': lambda req: (calls.append(req) or control.ExecuteResponse(status='complete'))})) as client:
        value = {'method': 'Echo', 'workflow_id': 'wf', 'task_id': 'old-task', 'inputs': []}
        assert client.post('/control/execute', json=value).json()['status'] == 'complete'
        assert client.get('/control/status/old-task').json()['status'] == 'complete'
        assert client.get('/control/capabilities').status_code == 409
        value['configuration'] = configuration('TrainModel', {'epochs': 2})
        assert client.post('/control/execute', json=value).status_code == 409
    assert len(calls) == 1


def test_both_deployments_use_identical_connector_and_canonical_validator():
    for name in ['control_interface.py', 'configured_synthetic.py', 'parameter_contract.py', 'parameter_contract_provenance.json']:
        assert (COMMON / name).read_bytes() == (ROOT / 'use-cases/portugal-node-integrated/common' / name).read_bytes()
    canonical = ROOT / 'orchestrator/src/services/parameter_contract.py'
    assert (COMMON / 'parameter_contract.py').read_bytes() == canonical.read_bytes()
    assert json.loads((COMMON / 'parameter_contract_provenance.json').read_text())['sha256'] == hashlib.sha256(canonical.read_bytes()).hexdigest()


def test_already_queued_work_stops_after_an_uncertain_dispatch(runtime):
    entered, released = threading.Event(), threading.Event()
    runtime.behavior.update(train_status=202, train_gate=(entered, released))
    first = runtime.client.post('/control/execute', json=request(runtime))
    try:
        assert entered.wait(3)
        second = runtime.client.post('/control/execute', json=request(runtime, task='already-queued'))
    finally:
        released.set()
    assert finished(runtime, first)['state'] == 'uncertain'
    queued = finished(runtime, second)
    assert queued['state'] == 'failed' and not queued['dispatch_started']
    assert [path for _, path, _ in runtime.requests] == ['/openapi.json', '/train']


def test_operator_must_choose_new_state_and_install_supported_contracts(runtime):
    unsafe = runtime.root / 'old-state'; unsafe.mkdir(); (unsafe / 'retain.txt').write_text('RETAIN')
    with pytest.raises(ValueError, match='existing data is preserved'):
        Runtime({**runtime.env, 'CONTROL_PARAMETER_STATE_DIR': str(unsafe)}, runtime.handlers)
    assert (unsafe / 'retain.txt').read_text() == 'RETAIN'
    with pytest.raises(ValueError, match='private SERVICE_API_KEY'):
        Runtime({**runtime.env, 'SERVICE_API_KEY': ''}, runtime.handlers)
    invalid = deepcopy(CAPABILITY)
    invalid['operations'][0]['execution_parameters']['properties']['not_implemented'] = {'type': 'integer'}
    path = runtime.root / 'contracts/reviewed.json'; path.write_text(json.dumps(invalid))
    assert runtime.client.post('/control/execute', json=request(runtime)).status_code == 422
    assert runtime.requests == []


def test_draft_export_requires_explicit_isolated_qualification_mode(runtime):
    document = {**CAPABILITY, 'qualification_only': True}
    (runtime.root / 'contracts/reviewed.json').write_text(json.dumps(document))
    assert runtime.client.post('/control/execute', json=request(runtime)).status_code == 422
    runtime.connector.qualification = True
    assert runtime.client.get('/control/capabilities').json()['qualification_only'] is True
    record = finished(runtime, runtime.client.post('/control/execute', json=request(runtime)))
    assert record['state'] == 'complete'  # Source wiring double only, no publication.


def test_receipt_read_and_atomic_replace_share_a_lock(runtime, monkeypatch):
    """Windows open readers otherwise deny os.replace during normal polling."""
    receipt = finished(runtime, runtime.client.post('/control/execute', json=request(runtime)))
    path = runtime.connector.path(receipt['service_task_id'])
    reading, release, attempting, replacing = [threading.Event() for _ in range(4)]
    original_read, original_write = Path.read_text, configured_module.atomic_json
    def held_read(candidate, *args, **kwargs):
        if candidate == path:
            reading.set()
            assert release.wait(3)
        return original_read(candidate, *args, **kwargs)
    def recorded_write(*args):
        replacing.set()
        return original_write(*args)
    monkeypatch.setattr(Path, 'read_text', held_read)
    monkeypatch.setattr(configured_module, 'atomic_json', recorded_write)
    reader = threading.Thread(target=runtime.connector.read, args=(receipt['service_task_id'],))
    def write():
        attempting.set()
        runtime.connector.write(path, receipt)
    writer = threading.Thread(target=write)
    reader.start()
    try:
        assert reading.wait(3)
        writer.start()
        assert attempting.wait(3)
        assert not replacing.wait(.05)
    finally:
        release.set()
        reader.join(3)
        if writer.ident is not None: writer.join(3)
    assert replacing.is_set() and not reader.is_alive() and not writer.is_alive()


def test_inline_csv_selected_in_normal_builder_reaches_source_without_paths(runtime):
    import base64
    raw = b'datetime,value\n2026-01-01,2\n'
    ref = {'protocol': 'inline', 'format': 'csv', 'uri': base64.b64encode(raw).decode()}
    record = finished(runtime, runtime.client.post('/control/execute', json=request(runtime, ref=ref)))
    assert record['state'] == 'complete', record
    assert record['input_sha256'] == hashlib.sha256(raw).hexdigest()
    assert runtime.calls == [('train', {'index_col': 'datetime', 'sequence_len': 3, 'batch_size': 4, 'epochs': 2})]
    for key, bad in [('wrong-base64', '%%%'), ('oversized', base64.b64encode(b'x'*(runtime.connector.max_bytes+1)).decode())]:
        failure = finished(runtime, runtime.client.post('/control/execute', json=request(runtime, task=key, ref={**ref, 'uri': bad})))
        assert failure['state'] == 'failed' and failure['dispatch_started'] is False
    assert len(runtime.calls) == 1
