"""Optional configuration transport for native Control services.

Services opt in with configuration_capabilities() and explicitly consume values
in their own algorithm. No service IDs or parameter defaults live here.
"""
from copy import deepcopy
import json
import hashlib
import os
from pathlib import Path
import re

from fastapi import HTTPException
from pydantic import BaseModel, Field, model_validator

from .parameter_contract import match_wrapper_configuration


class ConfigurableRequest(BaseModel):
    method: str
    workflow_id: str
    task_id: str
    inputs: list[dict] = Field(default_factory=list)
    parameters: dict = Field(default_factory=dict)
    configuration: dict | None = None

    @model_validator(mode="after")
    def explicit_configuration(self):
        if "configuration" in self.model_fields_set:
            if self.configuration is None:
                raise ValueError("Explicit configuration cannot be null")
            if "parameters" in self.model_fields_set:
                raise ValueError("Legacy parameters and instance configuration cannot be combined")
            for value in (self.workflow_id, self.task_id):
                if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value):
                    raise ValueError("Configured workflow/task identifiers must be safe path components")
        return self


def capabilities(service_module):
    hook = getattr(service_module, "configuration_capabilities", None)
    if not callable(hook):
        raise HTTPException(409, "This service has not declared instance configuration support")
    return hook()


def validate_request(service_module, request):
    if request.configuration is not None:
        try:
            return match_wrapper_configuration(request.method, request.configuration, capabilities(service_module))
        except (ValueError, TypeError, KeyError) as exc:
            raise HTTPException(422, "Instance configuration differs from the installed runtime contract") from exc
    return None


def load_contract(path: str | None, operation_name: str, supported_types: dict[str, str]):
    """Load an operator-installed export, with no fallback/default contract."""
    if not path:
        raise HTTPException(409, "Install the provider-reviewed runtime contract before using instance settings")
    try:
        source = Path(path)
        if source.stat().st_size > 256_000:
            raise ValueError("Contract too large")
        value = json.loads(source.read_text(encoding="utf-8"))
        if value.get('qualification_only') and os.environ.get('CONTROL_PARAMETER_QUALIFICATION_MODE') != 'true':
            raise ValueError('Draft contracts require an explicitly isolated qualification environment')
        operations = value["operations"]
        transports=value['transports']
        if (value["capability_schema_version"] != "1.0" or not isinstance(transports,list) or not transports
                or len(transports)!=len(set(transports)) or set(transports)-{'http-parameters-v1','whole-output-v1'}
                or not isinstance(operations, list) or len(operations) != 1):
            raise ValueError("Unsupported capability export")
        operation = operations[0]
        properties = operation.get('execution_parameters',{}).get('properties',{})
        if (operation["operation_name"] != operation_name or (supported_types and not properties)
                or any(name not in supported_types or spec["type"] != supported_types[name] for name, spec in properties.items())):
            raise ValueError("The runtime does not implement these parameter mappings")
        from .parameter_contract import parameter_contract_digest
        if properties:
            if 'http-parameters-v1' not in transports:raise ValueError('Missing parameter transport')
            parameter_contract_digest(operation["execution_parameters"])
        elif supported_types or 'http-parameters-v1' in transports:raise ValueError('Unexpected parameter declaration')
        if 'whole-output-v1' in transports:
            definition=operation['io_definition']
            hashed=hashlib.sha256(json.dumps(definition,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()
            if (operation['binding_transport']!='whole-output-v1' or definition['binding_transport']!='whole-output-v1'
                    or operation['io_contract_sha256']!=hashed or len(definition['inputs'])>1 or len(definition['outputs'])>1
                    or definition.get('execution_parameters',{}).get('properties',{})!=properties):
                raise ValueError('Installed I/O declaration differs')
        return deepcopy(value)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise HTTPException(409, "Installed parameter contract is unavailable or incompatible with this operation") from exc


def output_path(data_dir, workflow_id, task_id):
    root = Path(data_dir).resolve()
    directory = root / workflow_id
    if directory.is_symlink() or not directory.resolve().is_relative_to(root):
        raise ValueError("Output directory leaves the service data volume")
    path = directory / f"{task_id}.json"
    if path.exists() or path.is_symlink():
        raise HTTPException(409, "This task already has an output; use a new execution identity")
    return path


def write_output(data_dir, workflow_id, task_id, payload):
    """Configured outputs are task-specific and cannot overwrite earlier results."""
    path = output_path(data_dir, workflow_id, task_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, allow_nan=False)
    return {"protocol": "file", "uri": str(path), "format": "json"}
