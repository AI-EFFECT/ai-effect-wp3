"""Opt-in configuration for the source-declared Synthetic Data API.

No service IDs, saved workflows, reviewer IDs, scientific limits or parameter
values are registered here. Operators install provider-reviewed contract exports.
The canonical parameter validator is shared with Studio and WP3.
"""
from __future__ import annotations

import base64
import csv
import io
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import threading
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from fastapi import HTTPException

from .parameter_contract import match_wrapper_configuration, parameter_contract_digest

TYPES = {"TrainModel": {"epochs": "integer", "batch_size": "integer", "sequence_len": "integer"},
         "GenerateData": {"number_of_examples": "integer"}}
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def atomic_json(path, value):
    temporary = path.with_suffix('.' + uuid4().hex + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8') as stream:
            json.dump(value, stream, sort_keys=True, allow_nan=False); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists(): temporary.unlink()


def base_url(value):
    parsed = urlsplit(value)
    if parsed.scheme not in {'http', 'https'} or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('Declare an HTTP service base URL without credentials, query or fragment')
    return value.rstrip('/')


class Runtime:
    def __init__(self, environment, handlers, transport=None):
        self.environment = dict(environment)
        self.contract_directory = Path(environment['SERVICE_PARAMETER_CONTRACT_DIR']).resolve()
        raw_storage = Path(environment['CONTROL_PARAMETER_STATE_DIR'])
        if raw_storage.is_symlink(): raise ValueError('Use a dedicated non-symlink state directory')
        self.storage = raw_storage.resolve()
        self.upstream = base_url(environment['SYNTHETIC_DATA_URL'])
        self.self_url = base_url(environment['SELF_URL'])
        self.namespace = environment.get('SYNTHETIC_DATA_USER_NAMESPACE', '')
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', self.namespace):
            raise ValueError('Declare an operator-controlled Synthetic Data user namespace')
        if not environment.get('SERVICE_API_KEY'):
            raise ValueError('Configured Control requires a private SERVICE_API_KEY')
        self.index_column = environment.get('SYNTHETIC_DATA_INDEX_COLUMN', '')
        self.input_root = Path(environment['SYNTHETIC_DATA_INPUT_ROOT']).resolve() if environment.get('SYNTHETIC_DATA_INPUT_ROOT') else None
        self.origins = set(filter(None, environment.get('SYNTHETIC_DATA_INPUT_ORIGINS', '').split(',')))
        self.max_bytes = int(environment.get('CONTROL_MAX_INPUT_BYTES', '2000000'))
        if not 1 <= self.max_bytes <= 100_000_000: raise ValueError('Invalid operator input byte ceiling')
        self.storage.mkdir(parents=True, exist_ok=True)
        marker = self.storage / 'configured-state-v1.json'
        if marker.exists():
            if json.loads(marker.read_text()).get('kind') != 'configured-synthetic-v1': raise ValueError('State directory belongs to another runtime')
        else:
            if any(self.storage.iterdir()): raise ValueError('Choose a new empty state directory; existing data is preserved')
            with marker.open('x') as stream: json.dump({'kind': 'configured-synthetic-v1'}, stream)
        self.qualification = environment.get('CONTROL_PARAMETER_QUALIFICATION_MODE') == 'true'
        self.handlers = handlers; self.transport = transport
        self.boot = uuid4().hex; self.lock = threading.RLock()
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='configured-synthetic')
        self.capabilities()  # Reject incomplete operator configuration before serving.

    @classmethod
    def from_environment(cls, handlers):
        if not os.environ.get('SERVICE_PARAMETER_CONTRACT_DIR'): return None
        try: return cls(os.environ, handlers)
        except (ValueError, KeyError, OSError, TypeError) as exc:
            raise RuntimeError('Configured Synthetic Data setup is incomplete or incompatible: ' + str(exc)) from exc

    def capabilities(self):
        operations = []; qualification_only = False
        for path in sorted(self.contract_directory.glob('*.json')):
            if path.stat().st_size > 256000: raise ValueError('Contract export exceeds byte ceiling')
            document = json.loads(path.read_text())
            if document.get('capability_schema_version') != '1.0' or document.get('transports') != ['http-parameters-v1']:
                raise ValueError('Unsupported provider capability export')
            if document.get('qualification_only'):
                if not self.qualification: raise ValueError('Draft exports require an explicitly enabled isolated qualification runtime')
                qualification_only = True
            for operation in document['operations']:
                method = operation['operation_name']; properties = operation['execution_parameters']['properties']
                if method not in self.handlers or method not in TYPES or not properties:
                    raise ValueError('No verified parameter mapping for this operation')
                if any(name not in TYPES[method] or spec['type'] != TYPES[method][name] for name, spec in properties.items()):
                    raise ValueError('Runtime does not implement the exported parameter mapping')
                if method == 'TrainModel' and not self.index_column:
                    raise ValueError('Declare the training dataset index column separately from settings')
                parameter_contract_digest(operation['execution_parameters'])
                operations.append(deepcopy(operation))
        identities = [(o['operation_name'], o['operation_identity'], o['contract_version']) for o in operations]
        if not operations or len(set(identities)) != len(identities): raise ValueError('Missing or ambiguous installed operations')
        result = {'capability_schema_version': '1.0', 'transports': ['http-parameters-v1'], 'operations': operations}
        if qualification_only: result['qualification_only'] = True
        return result

    def path(self, identity, suffix='json'):
        if not re.fullmatch(r'cfg-[a-f0-9]{64}', identity): raise HTTPException(404, 'Unknown configured task')
        return self.storage / (identity + '.' + suffix)

    def write(self, path, value):
        # Windows readers do not share delete access. Keep journal reads and
        # atomic replacement under the same reentrant per-runtime lock.
        with self.lock:
            atomic_json(path, value)

    def read(self, identity):
        path = self.path(identity)
        with self.lock:
            if not path.is_file() or path.is_symlink(): raise HTTPException(404, 'Unknown configured task')
            value = json.loads(path.read_text())
        if value['state'] in {'queued', 'running'} and value['boot'] != self.boot:
            value.update(state='uncertain', error='Service restarted with an unresolved execution. Reconcile upstream before creating another attempt.')
        return value

    def submit(self, request):
        if not SAFE_ID.fullmatch(request.workflow_id) or not SAFE_ID.fullmatch(request.task_id):
            raise HTTPException(422, 'Configured workflow/task identities must be safe identifiers')
        try:
            capability = self.capabilities()
            match = match_wrapper_configuration(request.method, request.configuration, capability)
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(422, 'Configuration differs from installed reviewed definitions') from exc
        if len(request.inputs) != 1: raise HTTPException(422, 'This operation needs exactly one separately selected input reference')
        identity = 'cfg-' + hashlib.sha256((request.workflow_id + '\0' + request.task_id).encode()).hexdigest()
        value = {'workflow_id': request.workflow_id, 'task_id': request.task_id, 'service_task_id': identity,
                 'method': request.method, 'configuration': deepcopy(request.configuration),
                 'parameter_contract_sha256': match['parameter_contract_sha256'], 'boot': self.boot,
                 'state': 'queued', 'dispatch_started': False, 'output': None}
        with self.lock:
            # Unresolved earlier dispatches must be reconciled, not worked around
            # by sending the same algorithm under a different task identity.
            if any(self.read(p.stem)['state'] == 'uncertain' for p in self.storage.glob('cfg-*.json')):
                raise HTTPException(409, 'An earlier upstream outcome is unresolved; reconcile it before dispatch')
            try:
                with self.path(identity).open('x', encoding='utf-8') as stream:
                    json.dump(value, stream); stream.flush(); os.fsync(stream.fileno())
            except FileExistsError as exc:
                raise HTTPException(409, 'Configured task already accepted; inspect it, do not redispatch') from exc
        self.pool.submit(self.run, identity, deepcopy(request.inputs[0]))
        return {'status': 'running', 'task_id': identity}

    def client(self):
        token = self.environment.get('SYNTHETIC_DATA_API_KEY', '')
        return httpx.Client(timeout=600, follow_redirects=False, trust_env=False, transport=self.transport,
                            headers={'Authorization': 'Bearer ' + token} if token else {})

    def verify_query_interface(self, client, method, parameters):
        document = client.get(self.upstream + '/openapi.json'); document.raise_for_status()
        path, verb = ('/train', 'post') if method == 'TrainModel' else ('/generate', 'get')
        operation = document.json()['paths'][path][verb]
        declarations = {p['name']: p for p in operation.get('parameters', []) if p.get('in') == 'query'}
        needed = set(parameters) | ({'user_id', 'model_name', 'index_col', 'overwrite'} if method == 'TrainModel' else {'user_id', 'model_name', 'return_csv'})
        if not needed <= set(declarations): raise ValueError('Upstream query names differ from the reviewed connector; no algorithm dispatched')
        if any(declarations[name].get('schema', {}).get('type') != TYPES[method][name] for name in parameters):
            raise ValueError('Upstream parameter types differ; no algorithm dispatched')
        return hashlib.sha256(document.content).hexdigest()

    def input_bytes(self, reference, client):
        if reference.get('protocol') == 'file' and self.input_root:
            path = Path(reference['uri']).resolve()
            if self.input_root not in path.parents or not path.is_file() or path.stat().st_size > self.max_bytes:
                raise ValueError('Selected input is outside the operator-approved input directory or byte ceiling')
            return path.read_bytes()
        if reference.get('protocol') in {'http', 'https'}:
            parsed = urlsplit(reference['uri'])
            origin = parsed.scheme + '://' + parsed.netloc
            if origin not in self.origins or parsed.username or parsed.password or parsed.fragment:
                raise ValueError('Selected input origin is not operator-approved')
            # Never forward an ML service key to a data provider.
            token = self.environment.get('SYNTHETIC_DATA_INPUT_TOKEN', '')
            with httpx.Client(timeout=60, follow_redirects=False, trust_env=False, transport=self.transport) as reader:
                with reader.stream('GET', reference['uri'], headers={'Authorization': 'Bearer ' + token} if token else {}) as response:
                    response.raise_for_status(); data = bytearray()
                    for chunk in response.iter_bytes():
                        data.extend(chunk)
                        if len(data) > self.max_bytes: raise ValueError('Selected input exceeds the operator byte ceiling')
                    return bytes(data)
        raise ValueError('Select a supported, operator-authorised data reference; data is not a parameter')

    def resolve_model(self, reference, workflow_id):
        if reference.get('protocol') == 'inline' and reference.get('format') == 'json':
            raw = base64.b64decode(reference['uri'], validate=True)
        elif reference.get('protocol') in {'http', 'https'} and reference['uri'].startswith(self.self_url + '/control/data/'):
            producer = self.read(reference['uri'].removeprefix(self.self_url + '/control/data/'))
            if producer['state'] != 'complete' or producer['method'] != 'TrainModel': raise ValueError('Model producer is not complete')
            raw = self.path(producer['service_task_id'], 'data').read_bytes()
            if hashlib.sha256(raw).hexdigest() != producer['output_sha256']: raise ValueError('Model producer output changed')
        else:
            raise ValueError('Select a configured model reference or a declared shared-model reference')
        if len(raw) > 16000: raise ValueError('Model reference is too large')
        value = json.loads(raw)
        if set(value) != {'model_ref'} or not isinstance(value['model_ref'], str): raise ValueError('Model input must be an opaque model_ref')
        key = hashlib.sha256(value['model_ref'].encode()).hexdigest()
        local = self.storage / ('model-' + key + '.json')
        if local.is_file():
            model = json.loads(local.read_text())
            if model['workflow_id'] != workflow_id: raise ValueError('A private model belongs to a different workflow')
            return model
        catalogue = self.environment.get('SYNTHETIC_DATA_SHARED_MODELS_FILE')
        shared = json.loads(Path(catalogue).read_text()).get(value['model_ref']) if catalogue else None
        if not shared or shared.get('share_scope') != 'service_callers': raise ValueError('Model reference is not authorised')
        if not SAFE_ID.fullmatch(shared['user_id']) or not SAFE_ID.fullmatch(shared['model_name']): raise ValueError('Shared model identity is invalid')
        return {'user_id': shared['user_id'], 'model_name': shared['model_name'], 'shared_reference': value['model_ref']}

    def run(self, identity, reference):
        value = self.read(identity); value['state'] = 'running'
        self.write(self.path(identity), value)
        try:
            with self.lock:
                if any(p.stem != identity and self.read(p.stem)['state'] == 'uncertain'
                       for p in self.storage.glob('cfg-*.json')):
                    raise ValueError('An earlier upstream outcome became unresolved; queued work was not dispatched')
            with self.client() as client:
                parameters = value['configuration']['parameters']
                value['upstream_interface_sha256'] = self.verify_query_interface(client, value['method'], parameters)
                if value['method'] == 'TrainModel':
                    data = self.input_bytes(reference, client)
                    args = {'user_id': self.namespace, 'model_name': 'model_' + identity[4:36], 'index_col': self.index_column,
                            'overwrite': False, **parameters}
                    value.update(input_sha256=hashlib.sha256(data).hexdigest(), delivered_query=args, dispatch_started=True)
                    self.write(self.path(identity), value)
                    response = client.post(self.upstream + '/train', params=args, files={'uploaded_file': ('selected.csv', data, 'text/csv')})
                    if response.status_code != 200: raise ValueError('Synchronous training did not establish completion')
                    models = client.get(self.upstream + '/models', params={'user_id': self.namespace}); models.raise_for_status()
                    if args['model_name'] not in [m.get('name') for m in models.json()]: raise ValueError('The completed model was not listed')
                    model_ref = 'urn:aieffect:configured-model:' + uuid4().hex
                    self.write(self.storage / ('model-' + hashlib.sha256(model_ref.encode()).hexdigest() + '.json'),
                                {'user_id': self.namespace, 'model_name': args['model_name'], 'workflow_id': value['workflow_id'], 'producer_task_id': identity})
                    data, fmt = json.dumps({'model_ref': model_ref}).encode(), 'json'
                    value['completion_basis'] = 'Synchronous HTTP 200 plus model listing; not progress counters'
                else:
                    model = self.resolve_model(reference, value['workflow_id'])
                    args = {'user_id': model['user_id'], 'model_name': model['model_name'], 'return_csv': True, **parameters}
                    value.update(model_binding=model, delivered_query=args, dispatch_started=True)
                    self.write(self.path(identity), value)
                    with client.stream('GET', self.upstream + '/generate', params=args) as response:
                        if response.status_code != 200 or response.headers.get('content-type', '').split(';')[0] != 'text/csv':
                            raise ValueError('Generation did not return the declared CSV representation')
                        collected = bytearray()
                        for chunk in response.iter_bytes():
                            collected.extend(chunk)
                            if len(collected) > 20_000_000: raise ValueError('Generation response exceeds the connector output byte ceiling')
                        data, fmt = bytes(collected), 'csv'
                    rows = list(csv.DictReader(io.StringIO(data.decode('utf-8'))))
                    if not rows or any(not row.get('example') for row in rows): raise ValueError('CSV lacks source-declared example markers')
                    observed = len({row['example'] for row in rows})
                    if 'number_of_examples' in parameters and observed != parameters['number_of_examples']:
                        raise ValueError('CSV sequence count differs from the delivered configuration')
                    value.update(observed_sequence_count=observed, csv_data_rows=len(rows))
                with self.path(identity, 'data').open('xb') as stream:
                    stream.write(data); stream.flush(); os.fsync(stream.fileno())
                value.update(state='complete', output={'protocol': urlsplit(self.self_url).scheme, 'uri': self.self_url + '/control/data/' + identity, 'format': fmt},
                             output_sha256=hashlib.sha256(data).hexdigest(), output_bytes=len(data),
                             algorithm_consumption='Not established by HTTP acknowledgement; qualify against the actual model/runtime evidence')
        except Exception as exc:
            value.update(state='uncertain' if value['dispatch_started'] else 'failed', error_kind=type(exc).__name__,
                         error=str(exc) if isinstance(exc, ValueError) else 'Configured processing did not establish completion; inspect private operator logs and upstream state')
        self.write(self.path(identity), value)
