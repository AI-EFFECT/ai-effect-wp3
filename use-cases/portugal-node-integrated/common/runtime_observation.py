"""Explicit algorithm-settings-v1 observations, separate from delivery receipts."""
import hashlib
import json
import math
import re


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def validate(document, *, workflow_id, task_id, configuration, parameter_contract_sha256,
             input_sha256, source_response_sha256):
    if not isinstance(document, dict) or set(document) != {'schema_version', 'kind', 'binding', 'requested_parameters', 'observed_parameters', 'model', 'implementation', 'basis'}:
        raise ValueError('Unsupported runtime observation shape')
    if document['schema_version'] != '1.0' or document['kind'] != 'algorithm-settings-v1':
        raise ValueError('Unsupported runtime observation contract')
    expected = {'workflow_id': workflow_id, 'task_id': task_id, 'instance_id': configuration['instance_id'],
        'operation_identity': configuration['operation_identity'], 'configuration_sha256': digest(configuration),
        'parameter_contract_sha256': parameter_contract_sha256, 'input_sha256': input_sha256,
        'source_response_sha256': source_response_sha256}
    if canonical(document['binding']) != canonical(expected) or canonical(document['requested_parameters']) != canonical(configuration['parameters']):
        raise ValueError('Runtime observation binding differs from immutable task')
    observed = document['observed_parameters']
    if not isinstance(observed, dict) or set(observed) != set(configuration['parameters']):
        raise ValueError('Runtime observation parameter keys differ')
    for key, value in observed.items():
        requested = configuration['parameters'][key]
        numeric = type(requested) in (int, float)
        if ((numeric and (type(value) not in (int, float) or not math.isfinite(value)))
                or (not numeric and type(value) is not type(requested))):
            raise ValueError('Runtime observation parameter types differ')
    model = document['model']; implementation = document['implementation']
    for value in (model['sha256'], model['weights_sha256'], implementation['source_sha256'],
                  implementation['image_sha256'].removeprefix('sha256:'), input_sha256, source_response_sha256):
        if not isinstance(value, str) or not re.fullmatch('[a-f0-9]{64}', value):
            raise ValueError('Runtime observation digest is invalid')
    if type(model['weight_elements']) is not int or model['weight_elements'] <= 0 or not implementation.get('name') or not document['basis']:
        raise ValueError('Runtime observation lacks actual model evidence')
    return observed
