"""Observation transport/association tests with explicit source and model doubles.

Real DGAN/browser acceptance is recorded separately in the Portal report.
"""
from copy import deepcopy
import hashlib
import json

import httpx
import pytest

from test_portuguese_configured_runtime import runtime, request, finished, COMMON, ROOT
from configured_portugal_test import runtime_observation as observation


def install(runtime, mutation=None):
    delegate = runtime.connector.transport
    records = {}
    class ObservedTransport(httpx.BaseTransport):
        def handle_request(self, req):
            if req.url.path.startswith('/runtime-observations/'):
                assert req.headers['authorization'] == 'Bearer observation-fixture-key'
                value = deepcopy(records[req.url.path.rsplit('/', 1)[-1]])
                if mutation: mutation(value)
                return httpx.Response(200, json=value)
            response = delegate.handle_request(req)
            if req.url.path in {'/train', '/generate'}:
                assert req.headers['x-ai-effect-observation-key'] == 'observation-fixture-key'
                context = json.loads(req.headers['x-ai-effect-execution'])
                config = context['configuration']
                assert 'observation-fixture-key' not in json.dumps(context)
                observed = dict(config['parameters'])
                if 'batch_size' in observed: observed['batch_size'] = 2
                records[context['service_task_id']] = {
                    'schema_version':'1.0', 'kind':'algorithm-settings-v1',
                    'binding':{'workflow_id':context['workflow_id'],'task_id':context['task_id'],
                        'instance_id':config['instance_id'],'operation_identity':config['operation_identity'],
                        'configuration_sha256':observation.digest(config),
                        'parameter_contract_sha256':context['parameter_contract_sha256'],
                        'input_sha256':context['input_sha256'],
                        'source_response_sha256':hashlib.sha256(response.read()).hexdigest()},
                    'requested_parameters':config['parameters'], 'observed_parameters':observed,
                    'model':{'sha256':'a'*64,'weights_sha256':'b'*64,'weight_elements':12},
                    'implementation':{'name':'EXPLICIT TEST DOUBLE','source_sha256':'c'*64,'image_sha256':'sha256:'+'d'*64},
                    'basis':'Synthetic observation for connector association tests only'}
            return response
    runtime.connector.transport = ObservedTransport()
    runtime.connector.environment.update(SYNTHETIC_DATA_OBSERVATION_URL='http://source.test/runtime-observations',
        SYNTHETIC_DATA_OBSERVATION_KEY='observation-fixture-key')
    return records


def test_opt_in_observation_retains_requested_and_different_observed_values(runtime):
    install(runtime)
    train = finished(runtime, runtime.client.post('/control/execute', json=request(runtime)))
    assert train['state'] == 'complete', train
    assert train['configuration']['parameters']['batch_size'] == train['delivered_query']['batch_size'] == 4
    assert train['runtime_observation']['observed_parameters']['batch_size'] == 2
    assert observation.digest(train['runtime_observation']) == train['runtime_observation_sha256']
    generated = finished(runtime, runtime.client.post('/control/execute',json=request(runtime,'GenerateData','generate',ref=train['output'],number_of_examples=2)))
    assert generated['state'] == 'complete', generated
    assert generated['input_sha256'] == train['output_sha256']
    assert generated['runtime_observation']['model'] == train['runtime_observation']['model']
    assert 'observation-fixture-key' not in json.dumps(generated)


@pytest.mark.parametrize('field',['workflow_id','task_id','instance_id','operation_identity','configuration_sha256','parameter_contract_sha256','input_sha256','source_response_sha256'])
def test_wrong_observation_never_publishes_output_or_redispatches(runtime,field):
    install(runtime,lambda value: value['binding'].update({field:'wrong'}))
    record = finished(runtime,runtime.client.post('/control/execute',json=request(runtime)))
    assert record['state'] == 'uncertain'
    assert 'runtime_observation' not in record and not record.get('output')
    assert runtime.client.get('/control/data/'+record['service_task_id']).status_code == 409
    assert runtime.client.post('/control/execute',json=request(runtime,task='no-retry')).status_code == 409
    assert len(runtime.calls) == 1


def test_both_connectors_share_exact_generic_observation_validator():
    assert (COMMON/'runtime_observation.py').read_bytes() == (ROOT/'use-cases/portugal-node-integrated/common/runtime_observation.py').read_bytes()
