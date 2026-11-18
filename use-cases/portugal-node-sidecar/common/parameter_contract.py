"""Portable parameter validation shared by Studio and isolated node wrappers.

No paths, deployment commands, environment variables or secrets are interpreted.
"""
from copy import deepcopy
import math
import hashlib
import json


def effective_parameters(definition, requested):
    if not isinstance(requested, dict):
        raise ValueError("Parameters must be a JSON object; null is not supported")
    properties = definition.get("properties", {})
    unknown = set(requested) - set(properties)
    if unknown:
        raise ValueError("Unsupported parameters: " + ", ".join(sorted(unknown)))
    result = {}
    for name, spec in properties.items():
        if name in requested:
            value = requested[name]
        elif "default" in spec:
            value = deepcopy(spec["default"])
        elif spec.get("required"):
            raise ValueError(f"{name}: a value is required")
        else:
            continue
        kind = spec["type"]
        valid = ((kind == "integer" and type(value) is int)
                 or (kind == "number" and type(value) in (int, float) and math.isfinite(value))
                 or (kind == "boolean" and type(value) is bool)
                 or (kind == "string" and type(value) is str))
        if not valid:
            raise ValueError(f"{name}: expected {kind}; null is not supported")
        if "enum" in spec and value not in spec["enum"]:
            raise ValueError(f"{name}: choose one of {spec['enum']}")
        if kind in ("integer", "number"):
            if spec.get("minimum") is not None and value < spec["minimum"]:
                raise ValueError(f"{name}: minimum is {spec['minimum']}")
            if spec.get("maximum") is not None and value > spec["maximum"]:
                raise ValueError(f"{name}: maximum is {spec['maximum']}")
        result[name] = deepcopy(value)
    return result


TRANSPORT = "http-parameters-v1"
CAPABILITY_SCHEMA = "1.0"


def parameter_contract_digest(definition):
    """Stable digest for the registry export, including labels and constraints.

    Stored Pydantic defaults and JSON numeric spelling do not create a second
    contract identity. Explicit null defaults remain invalid, not omitted.
    """
    if (not isinstance(definition, dict) or definition.get("schema_version") != "1.0"
            or definition.get("transport") != TRANSPORT or not isinstance(definition.get("properties"), dict)
            or set(definition) - {"schema_version", "transport", "properties"}):
        raise ValueError("Unsupported execution parameter contract/protocol version")
    properties = {}
    for name, original in definition["properties"].items():
        if not isinstance(original, dict):
            raise ValueError("Malformed parameter definition")
        spec = {k: deepcopy(v) for k, v in original.items() if v is not None or k == "default"}
        if spec.get("required") is False:
            spec.pop("required")
        if spec.get("type") not in {"integer", "number", "boolean", "string"}:
            raise ValueError("Unsupported parameter type")
        for bound in ("minimum", "maximum"):
            if bound in spec:
                value = spec[bound]
                if type(value) not in (int, float) or not math.isfinite(value):
                    raise ValueError("Parameter bounds must be finite numbers")
                if value == int(value):
                    spec[bound] = int(value)
        for value in ([spec["default"]] if "default" in spec else []) + spec.get("enum", []):
            effective_parameters({"properties": {name: spec}}, {name: value})
        properties[name] = spec
    canonical = {"schema_version": "1.0", "transport": TRANSPORT, "properties": properties}
    return hashlib.sha256(json.dumps(canonical, sort_keys=True, ensure_ascii=False, allow_nan=False,
                                     separators=(",", ":")).encode()).hexdigest()


def validate_configuration_envelope(configuration):
    keys = {"contract_id", "contract_version", "operation_identity", "schema_version", "parameters", "instance_id"}
    if not isinstance(configuration, dict) or set(configuration) != keys:
        raise ValueError("Unsupported instance configuration envelope")
    if configuration["schema_version"] != "1.0":
        raise ValueError("Unsupported parameter schema version")
    for key in keys - {"parameters"}:
        if not isinstance(configuration[key], str) or not configuration[key].strip() or len(configuration[key]) > 300:
            raise ValueError(f"Invalid configuration {key}")
    if not isinstance(configuration["parameters"], dict):
        raise ValueError("Parameters must be a JSON object")
    json.dumps(configuration, allow_nan=False)
    return deepcopy(configuration)


def match_wrapper_configuration(method, configuration, capability):
    """Generic protocol negotiation; no Dutch/Danish definitions live here."""
    configuration = validate_configuration_envelope(configuration)
    if (not isinstance(capability, dict) or capability.get("capability_schema_version") != CAPABILITY_SCHEMA
            or not isinstance(capability.get("transports"), list)
            or TRANSPORT not in capability["transports"]
            or not isinstance(capability.get("operations"), list)):
        raise ValueError("Wrapper does not declare a supported configuration protocol")
    matches = [op for op in capability.get("operations", []) if isinstance(op, dict)
               and op.get("operation_name") == method
               and op.get("operation_identity") == configuration["operation_identity"]
               and op.get("contract_version") == configuration["contract_version"]]
    if len(matches) != 1:
        raise ValueError("Wrapper operation/contract version is incompatible or ambiguous")
    definition = matches[0].get("execution_parameters")
    digest = parameter_contract_digest(definition)
    effective = effective_parameters(definition, configuration["parameters"])
    if effective != configuration["parameters"]:
        raise ValueError("Submitted parameters must already contain validated effective values")
    return {"operation_identity": configuration["operation_identity"], "contract_version": configuration["contract_version"],
            "schema_version": "1.0", "parameter_contract_sha256": digest}
